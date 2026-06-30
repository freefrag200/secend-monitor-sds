//! SSPD-RS — Spacedesk-style LAN second-monitor streaming server.
//!
//! Rust port of the Python `SSPD.py` transmitter.  Captures a monitor, encodes
//! JPEG frames, and streams them to any browser on the LAN over MJPEG, with a
//! system-tray control menu and mDNS discovery.

mod config;
mod cursor;
mod framehub;
mod http;
mod jpeg;
mod mdns;
mod pipeline;
mod web;

#[cfg(windows)]
mod capture;
#[cfg(windows)]
mod tray;
#[cfg(windows)]
mod winutil;

use std::process;

use framehub::FrameHub;
use tracing_subscriber::EnvFilter;

fn main() {
    // Initialize structured logging.
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .with_target(false)
        .init();

    println!("=====================================================");
    println!("  SSPD-RS — LAN Second-Monitor Streaming Server");
    println!("=====================================================\n");

    #[cfg(windows)]
    {
        winutil::set_dpi_aware();
        winutil::optimize_process();
        winutil::prevent_sleep();
        println!("[INIT] Windows: DPI-aware, power-throttling off, priority raised.");
    }

    // Pick a free port starting at 5000 (matches Python behaviour).
    let port = match http::find_free_port(5000) {
        Some(p) => p,
        None => {
            tracing::error!("No free ports found in range 5000-5050");
            process::exit(1);
        }
    };
    {
        let mut g = config::ACTIVE_PORT.write();
        *g = port;
    }

    // Print connection links.
    #[cfg(windows)]
    let ips = winutil::get_all_ips();
    #[cfg(not(windows))]
    let ips: Vec<String> = vec!["127.0.0.1".to_string()];

    println!("[INIT] Server bound on port {port}");
    println!("\n=== Client Connection Links ===");
    for ip in &ips {
        println!("  http://{ip}:{port}");
    }
    println!("  http://sspd-rs.local:{port}");
    println!("===============================\n");

    // Register mDNS service for LAN discovery.
    let _mdns = mdns::MdnsRegistration::register(port);

    // Start the capture + encode pipeline.
    let hub = FrameHub::new();
    pipeline::spawn(hub.clone());

    // Start the system tray (Windows only).
    #[cfg(windows)]
    tray::spawn();

    // Run the HTTP server on the main thread (blocks forever).
    println!("[INIT] Streaming pipeline active. Right-click the tray icon for controls.\n");
    http::run(hub, port);
}
