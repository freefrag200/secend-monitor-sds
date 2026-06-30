//! Shared runtime configuration and global state.
//!
//! Mirrors the Python module-level globals (`current_protocol`, `current_monitor`,
//! etc.) but uses a `parking_lot::RwLock` for thread-safe access from the HTTP
//! handlers, the capture pipeline and the system tray.

use once_cell::sync::Lazy;
use parking_lot::RwLock;
use serde::Serialize;

/// Snapshot of every client-tunable setting. Serialized verbatim by the
/// `/api/current_settings` endpoint, matching the original JSON shape so the
/// embedded web receiver keeps working unchanged.
#[derive(Clone, Debug, Serialize)]
pub struct Settings {
    pub protocol: String,
    pub monitor: usize,
    pub res: String,
    pub quality: u32,
    pub fps: u32,
    pub bitrate: u32,
    pub engine: String,
    pub auto_sync: bool,
    pub adaptive_quality: bool,
    pub refresh_counter: u64,
    pub scale_mode: String,
    pub clear_cache_cmd: u64,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            protocol: "mjpeg".into(),
            monitor: 1,
            res: "Native".into(),
            quality: 92,
            fps: 60,
            bitrate: 20,
            engine: "auto".into(),
            auto_sync: true,
            adaptive_quality: true,
            refresh_counter: 0,
            scale_mode: "contain".into(),
            clear_cache_cmd: 0,
        }
    }
}

/// All client-tunable knobs. Written from HTTP handlers, read from the capture
/// loop and the tray menu.
pub static SETTINGS: Lazy<RwLock<Settings>> = Lazy::new(|| {
    RwLock::new(Settings {
        protocol: "mjpeg".into(),
        monitor: 1,
        res: "Native".into(),
        quality: 92,
        fps: 60,
        bitrate: 20,
        engine: "auto".into(),
        auto_sync: true,
        adaptive_quality: true,
        refresh_counter: 0,
        scale_mode: "contain".into(),
        clear_cache_cmd: 0,
    })
});

/// Whether the streaming server is currently serving frames. Toggled from the
/// tray / admin panel (equivalent to `server_active` in Python).
pub static SERVER_ACTIVE: Lazy<RwLock<bool>> = Lazy::new(|| RwLock::new(true));

/// Port the HTTP listener actually bound to (auto-selected on startup).
pub static ACTIVE_PORT: Lazy<RwLock<u16>> = Lazy::new(|| RwLock::new(5000));

/// Read-only adapters used by the HTTP layer.
pub fn snapshot() -> Settings {
    SETTINGS.read().clone()
}

pub fn server_active() -> bool {
    *SERVER_ACTIVE.read()
}
