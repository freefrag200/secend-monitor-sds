//! Minimal HTTP/1.1 server built on raw TCP.
//!
//! We avoid framework HTTP servers because MJPEG streaming requires
//! per-frame flushing with no buffering — a tiny hand-written server gives us
//! full control over the socket, has no dependencies, and keeps the binary
//! self-contained for an offline-LAN deployment.
//!
//! Endpoints (matching the Python Flask app):
//!   GET  /                      -> HTML receiver page
//!   GET  /admin                 -> local control dashboard
//!   GET  /video_feed            -> multipart/x-mixed-replace MJPEG stream
//!   GET  /api/monitors          -> JSON monitor list
//!   GET  /api/current_settings  -> JSON settings snapshot
//!   GET  /api/network           -> JSON LAN receiver/admin links
//!   GET  /api/server_state      -> JSON active/listener state
//!   GET  /api/ping              -> "pong"
//!   POST /api/update_settings   -> update Python-compatible settings
//!   POST /api/set_resolution    -> change monitor resolution (Windows)
//!   POST /api/set_protocol      -> set mjpeg transport
//!   POST /api/toggle_server     -> start/stop streaming
//!   POST /api/refresh_client    -> force connected receivers to reopen stream
//!   POST /api/clear_cache       -> force receivers to clear local cache/reload
//!   POST /api/latency_feedback  -> adaptive quality ladder

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use serde_json::{json, Value};

use crate::config;
use crate::framehub::{recv_timeout, FrameHub};
use crate::web::{ADMIN_HTML, INDEX_HTML};

/// Start the HTTP server on `port`.  Blocks the calling thread.
pub fn run(hub: FrameHub, port: u16) {
    let listener = match TcpListener::bind(("0.0.0.0", port)) {
        Ok(l) => l,
        Err(e) => {
            tracing::error!("Failed to bind HTTP on port {port}: {e}");
            return;
        }
    };
    tracing::info!("HTTP server listening on http://0.0.0.0:{port}");

    let hub = Arc::new(hub);
    for incoming in listener.incoming() {
        match incoming {
            Ok(stream) => {
                let hub = hub.clone();
                thread::spawn(move || {
                    let _ = stream.set_read_timeout(Some(Duration::from_secs(30)));
                    let _ = stream.set_nodelay(true);
                    handle_connection(stream, hub);
                });
            }
            Err(e) => tracing::warn!("Accept failed: {e}"),
        }
    }
}

/// Find the first free TCP port starting at `start`.
pub fn find_free_port(start: u16) -> Option<u16> {
    for port in start..=start.saturating_add(50) {
        if TcpListener::bind(("0.0.0.0", port)).is_ok() {
            return Some(port);
        }
    }
    None
}

fn handle_connection(mut stream: TcpStream, hub: Arc<FrameHub>) {
    let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
    let mut request_line = String::new();
    if reader.read_line(&mut request_line).is_err() {
        let _ = stream.shutdown(Shutdown::Both);
        return;
    }

    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or("");
    let path = parts.next().unwrap_or("/");

    // Drain headers (and capture Content-Length for POSTs).
    let mut content_length = 0usize;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line).is_err() {
            break;
        }
        if line == "\r\n" || line.is_empty() {
            break;
        }
        if let Some(rest) = line.to_ascii_lowercase().strip_prefix("content-length:") {
            content_length = rest.trim().parse().unwrap_or(0);
        }
    }

    // Read POST body if present.
    let mut body = vec![0u8; content_length];
    if content_length > 0 {
        let _ = reader.read_exact(&mut body);
    }
    let body_str = String::from_utf8_lossy(&body).to_string();

    // Strip query string from path.
    let path_only = path.split('?').next().unwrap_or(path);

    match (method, path_only) {
        ("GET", "/") => respond_text(&mut stream, "200 OK", "text/html; charset=utf-8", INDEX_HTML.as_bytes()),
        ("GET", "/admin") => respond_text(&mut stream, "200 OK", "text/html; charset=utf-8", ADMIN_HTML.as_bytes()),
        ("GET", "/api/monitors") => respond_json(&mut stream, "200 OK", &monitors_json()),
        ("GET", "/api/current_settings") => respond_json(&mut stream, "200 OK", &settings_json()),
        ("GET", "/api/network") => respond_json(&mut stream, "200 OK", &network_json()),
        ("GET", "/api/server_state") => respond_json(&mut stream, "200 OK", &server_state_json()),
        ("GET", "/api/ping") => respond_text(&mut stream, "200 OK", "text/plain", b"pong"),
        ("OPTIONS", _) => respond_text(&mut stream, "204 No Content", "text/plain", b""),
        ("POST", "/api/update_settings") => {
            let v = serde_json::from_str(&body_str).unwrap_or(Value::Null);
            handle_update_settings(&mut stream, v);
        }
        ("POST", "/api/set_resolution") => {
            let v = serde_json::from_str(&body_str).unwrap_or(Value::Null);
            handle_set_resolution(&mut stream, v);
        }
        ("POST", "/api/set_protocol") => {
            let v = serde_json::from_str(&body_str).unwrap_or(Value::Null);
            handle_set_protocol(&mut stream, v);
        }
        ("POST", "/api/toggle_server") => {
            let v = serde_json::from_str(&body_str).unwrap_or(Value::Null);
            handle_toggle_server(&mut stream, v);
        }
        ("POST", "/api/refresh_client") => {
            config::SETTINGS.write().refresh_counter += 1;
            respond_json(&mut stream, "200 OK", &json!({"status":"success"}));
        }
        ("POST", "/api/clear_cache") => {
            config::SETTINGS.write().clear_cache_cmd += 1;
            respond_json(&mut stream, "200 OK", &json!({"status":"success"}));
        }
        ("POST", "/api/latency_feedback") => {
            let v = serde_json::from_str(&body_str).unwrap_or(Value::Null);
            handle_latency(&mut stream, v);
        }
        ("GET", p) if p.starts_with("/video_feed") => {
            stream_mjpeg(stream, hub);
        }
        _ => respond_text(&mut stream, "404 Not Found", "text/plain", b"Not Found"),
    }
}

/// MJPEG multipart stream. Writes frames directly to the socket with immediate
/// flushing. Runs until the client disconnects, so receiver pages do not stop
/// after a fixed session duration.
fn stream_mjpeg(mut stream: TcpStream, hub: Arc<FrameHub>) {
    if !config::server_active() {
        respond_text(&mut stream, "503 Service Unavailable", "text/plain", b"Server is Offline");
        return;
    }

    // Send the response header once; body is the unbounded multipart stream.
    let header = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(header.as_bytes()).is_err() {
        return;
    }
    let _ = stream.flush();

    let sub = hub.subscribe();
    tracing::info!("MJPEG client connected (id={}); {} active", sub.id, hub.subscriber_count());

    loop {
        // Get next frame, blocking briefly. Slow clients receive the newest
        // available frame from FrameHub and never hold the capture loop back.
        let frame = match recv_timeout(&sub.rx, Duration::from_millis(180)) {
            Some(f) => f,
            None => match hub.latest() {
                Some(f) => f,
                None => {
                    // Keep the connection alive with a standby frame.
                    let _ = write_mjpeg_part(&mut stream, crate::web::standby_jpeg());
                    continue;
                }
            },
        };

        let write_result = if let Some(jpeg) = frame.jpeg.as_ref().filter(|j| !j.is_empty()) {
            write_mjpeg_part(&mut stream, jpeg)
        } else {
            write_mjpeg_part(&mut stream, crate::web::standby_jpeg())
        };

        if write_result.is_err() {
            break; // client gone
        }
    }

    hub.unsubscribe(sub.id);
    tracing::info!("MJPEG client disconnected (id={})", sub.id);
    let _ = stream.shutdown(Shutdown::Both);
}

fn write_mjpeg_part(stream: &mut TcpStream, jpeg: &[u8]) -> std::io::Result<()> {
    let part = format!(
        "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n",
        jpeg.len()
    );
    stream.write_all(part.as_bytes())?;
    stream.write_all(jpeg)?;
    stream.write_all(b"\r\n")?;
    stream.flush()
}

// ---- endpoint helpers ---------------------------------------------------

fn monitors_json() -> Value {
    #[cfg(windows)]
    {
        let mons = crate::capture::enumerate_monitors();
        let arr: Vec<Value> = mons
            .iter()
            .map(|m| json!({ "id": m.id, "width": m.width, "height": m.height }))
            .collect();
        Value::Array(arr)
    }
    #[cfg(not(windows))]
    {
        Value::Array(vec![])
    }
}

fn settings_json() -> Value {
    let s = config::snapshot();
    json!({
        "protocol": s.protocol,
        "monitor": s.monitor,
        "res": s.res,
        "quality": s.quality,
        "fps": s.fps,
        "bitrate": s.bitrate,
        "engine": s.engine,
        "auto_sync": s.auto_sync,
        "adaptive_quality": s.adaptive_quality,
        "refresh_counter": s.refresh_counter,
        "scale_mode": s.scale_mode,
        "clear_cache_cmd": s.clear_cache_cmd,
        "server_active": config::server_active(),
        "active_port": *config::ACTIVE_PORT.read(),
    })
}

fn network_json() -> Value {
    let port = *config::ACTIVE_PORT.read();
    #[cfg(windows)]
    let mut ips = crate::winutil::get_all_ips();
    #[cfg(not(windows))]
    let mut ips: Vec<String> = vec!["127.0.0.1".to_string()];

    ips.sort();
    ips.dedup();
    let receiver_links: Vec<String> = ips
        .iter()
        .map(|ip| format!("http://{ip}:{port}/"))
        .chain(std::iter::once(format!("http://sspd-rs.local:{port}/")))
        .collect();
    let admin_links: Vec<String> = ips
        .iter()
        .map(|ip| format!("http://{ip}:{port}/admin"))
        .chain(std::iter::once(format!("http://sspd-rs.local:{port}/admin")))
        .collect();

    json!({
        "port": port,
        "ips": ips,
        "receiver_links": receiver_links,
        "admin_links": admin_links,
    })
}

fn server_state_json() -> Value {
    json!({
        "server_active": config::server_active(),
        "active_port": *config::ACTIVE_PORT.read(),
        "settings": settings_json(),
    })
}

fn handle_update_settings(stream: &mut TcpStream, v: Value) {
    if !v.is_object() {
        respond_json(stream, "400 Bad Request", &json!({"status":"error","message":"JSON object required"}));
        return;
    }

    {
        let mut s = config::SETTINGS.write();

        if let Some(monitor) = v.get("monitor").and_then(|x| x.as_u64()) {
            s.monitor = (monitor as usize).max(1);
        }
        if let Some(res) = v.get("res").and_then(|x| x.as_str()) {
            if valid_resolution(res) {
                s.res = res.to_string();
            }
        }
        if let Some(quality) = v.get("quality").and_then(|x| x.as_u64()) {
            s.quality = (quality as u32).clamp(35, 100);
        }
        if let Some(fps) = v.get("fps").and_then(|x| x.as_u64()) {
            s.fps = (fps as u32).clamp(10, 120);
        }
        if let Some(bitrate) = v.get("bitrate").and_then(|x| x.as_u64()) {
            s.bitrate = (bitrate as u32).clamp(1, 200);
        }
        if let Some(engine) = v.get("engine").and_then(|x| x.as_str()) {
            if matches!(engine, "auto" | "mss" | "dxcam") {
                s.engine = engine.to_string();
            }
        }
        if let Some(scale) = v.get("scale_mode").and_then(|x| x.as_str()) {
            if matches!(scale, "contain" | "fill") {
                s.scale_mode = scale.to_string();
            }
        }
        if let Some(auto_sync) = v.get("auto_sync").and_then(|x| x.as_bool()) {
            s.auto_sync = auto_sync;
        }
        if let Some(adaptive) = v.get("adaptive_quality").and_then(|x| x.as_bool()) {
            s.adaptive_quality = adaptive;
        }
        if let Some(proto) = v.get("protocol").and_then(|x| x.as_str()) {
            if proto == "mjpeg" {
                s.protocol = proto.to_string();
            }
        }
    }

    respond_json(stream, "200 OK", &json!({"status":"success","settings":settings_json()}));
}

fn handle_set_resolution(stream: &mut TcpStream, v: Value) {
    let width = v.get("width").and_then(|x| x.as_u64()).map(|x| x as u32);
    let height = v.get("height").and_then(|x| x.as_u64()).map(|x| x as u32);
    let monitor = v
        .get("monitor")
        .and_then(|x| x.as_u64())
        .map(|x| x as usize)
        .unwrap_or_else(|| config::snapshot().monitor);

    let (status, code) = match (width, height) {
        (Some(w), Some(h)) => {
            #[cfg(windows)]
            {
                let ok = crate::winutil::set_monitor_resolution(monitor, w as i32, h as i32);
                (if ok { "success" } else { "failed" }, "200 OK")
            }
            #[cfg(not(windows))]
            {
                let _ = (monitor, w, h);
                ("unsupported", "200 OK")
            }
        }
        _ => ("error", "400 Bad Request"),
    };

    let body = json!({
        "status": status,
        "message": if status == "error" { Some("Invalid Dimensions") } else { None }
    });
    respond_json(stream, code, &body);
}

fn handle_set_protocol(stream: &mut TcpStream, v: Value) {
    if let Some(proto) = v.get("protocol").and_then(|x| x.as_str()) {
        if proto == "mjpeg" {
            config::SETTINGS.write().protocol = proto.to_string();
            tracing::info!("Protocol switched to {proto}");
            respond_json(stream, "200 OK", &json!({"status":"success"}));
            return;
        }
    }
    respond_json(
        stream,
        "400 Bad Request",
        &json!({"status":"error","message":"Unsupported protocol; this Rust rewrite serves MJPEG"}),
    );
}

fn handle_toggle_server(stream: &mut TcpStream, v: Value) {
    let active = v
        .get("active")
        .and_then(|x| x.as_bool())
        .unwrap_or_else(|| !config::server_active());

    *config::SERVER_ACTIVE.write() = active;
    if active {
        config::SETTINGS.write().refresh_counter += 1;
    }
    respond_json(stream, "200 OK", &json!({"status":"success","server_active":active}));
}

fn handle_latency(stream: &mut TcpStream, v: Value) {
    let rtt = v.get("rtt").and_then(|x| x.as_f64()).unwrap_or(0.0) as u32;
    let (q, fps) = {
        let mut g = config::SETTINGS.write();
        if !g.adaptive_quality {
            return respond_json(
                stream,
                "200 OK",
                &json!({"status":"ok","adaptive":false,"current_quality":g.quality,"current_fps":g.fps}),
            );
        }
        if rtt < 45 {
            g.quality = (g.quality + 2).min(100);
            g.fps = (g.fps + 1).min(120);
        } else if rtt > 180 {
            g.quality = g.quality.saturating_sub(8).max(35);
            g.fps = g.fps.saturating_sub(2).max(15);
        } else if rtt > 90 {
            g.quality = g.quality.saturating_sub(3).max(60);
            g.fps = g.fps.saturating_sub(1).max(20);
        }
        (g.quality, g.fps)
    };
    respond_json(
        stream,
        "200 OK",
        &json!({"status":"ok","current_quality":q,"current_fps":fps}),
    );
}

fn valid_resolution(res: &str) -> bool {
    if res == "Native" {
        return true;
    }
    let Some((w, h)) = res.split_once('x') else {
        return false;
    };
    matches!(
        (w.parse::<u32>(), h.parse::<u32>()),
        (Ok(640..=7680), Ok(360..=4320))
    )
}

// ---- response writers ---------------------------------------------------

fn respond_text(stream: &mut TcpStream, status: &str, ctype: &str, body: &[u8]) {
    let resp = format!(
        "HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nConnection: close\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Headers: Content-Type\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\n\r\n",
        body.len()
    );
    let _ = stream.write_all(resp.as_bytes());
    let _ = stream.write_all(body);
    let _ = stream.flush();
    let _ = stream.shutdown(Shutdown::Both);
}

fn respond_json(stream: &mut TcpStream, status: &str, body: &Value) {
    let data = serde_json::to_vec(body).unwrap_or_default();
    respond_text(stream, status, "application/json", &data);
}
