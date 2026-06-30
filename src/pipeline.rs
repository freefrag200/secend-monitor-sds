//! Background capture pipeline.
//!
//! Equivalent of the Python `CaptureProducer` thread: in a loop, grab the
//! currently selected monitor, overlay the cursor, optionally resize, JPEG
//! encode, and publish to the [`FrameHub`] for MJPEG fan-out.

use std::thread;
use std::time::{Duration, Instant};

use crate::capture;
use crate::config;
use crate::cursor;
use crate::framehub::FrameHub;
use crate::jpeg;

/// Spawn the capture loop.  Returns immediately; the thread is daemon-equivalent
/// (it will be killed when the process exits).
pub fn spawn(hub: FrameHub) {
    thread::Builder::new()
        .name("sspd-capture".into())
        .spawn(move || run(hub))
        .expect("spawn capture thread");
}

fn run(hub: FrameHub) {
    let mut consecutive_failures = 0u32;

    loop {
        if !config::server_active() {
            thread::sleep(Duration::from_millis(100));
            continue;
        }

        let settings = config::snapshot();
        let target_fps = settings.fps.max(1);
        let frame_budget = Duration::from_secs_f64(1.0 / target_fps as f64);
        let start = Instant::now();

        let monitors = capture::enumerate_monitors();
        if monitors.is_empty() {
            thread::sleep(Duration::from_millis(200));
            continue;
        }

        match capture::capture_monitor(&monitors, settings.monitor) {
            Some(mut frame) => {
                consecutive_failures = 0;

                // Cursor overlay (Windows).
                #[cfg(windows)]
                cursor::overlay(&mut frame);

                // Optional downscale for non-Native target resolution.
                if settings.res != "Native" {
                    if let Some((w, h)) = parse_res(&settings.res) {
                        if w != frame.width || h != frame.height {
                            let resized = capture::resize_bgra(
                                &frame.bgra,
                                frame.width,
                                frame.height,
                                w,
                                h,
                            );
                            frame.width = w;
                            frame.height = h;
                            frame.bgra = resized;
                        }
                    }
                }

                // JPEG encode (BGRA -> RGBA -> JPEG).
                let quality = settings.quality.clamp(10, 100) as u8;
                let jpeg = jpeg::encode_bgra(&frame.bgra, frame.width, frame.height, quality);
                frame.jpeg = Some(jpeg);

                hub.put(frame);
            }
            None => {
                consecutive_failures += 1;
                if consecutive_failures >= 15 {
                    tracing::warn!(
                        "Monitor {} unreachable for {} captures; resetting to primary",
                        settings.monitor,
                        consecutive_failures
                    );
                    consecutive_failures = 0;
                    {
                        let mut g = config::SETTINGS.write();
                        g.monitor = 1;
                    }
                }
                thread::sleep(Duration::from_millis(100));
            }
        }

        // Pace to target FPS.
        let elapsed = start.elapsed();
        if elapsed < frame_budget {
            thread::sleep(frame_budget - elapsed);
        } else {
            // Running behind; yield to avoid a tight spin.
            thread::sleep(Duration::from_millis(1));
        }
    }
}

fn parse_res(s: &str) -> Option<(u32, u32)> {
    let (w, h) = s.split_once('x')?;
    Some((w.parse().ok()?, h.parse().ok()?))
}
