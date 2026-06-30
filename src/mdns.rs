//! mDNS / Bonjour-style LAN service registration.
//!
//! Rust counterpart of the Python `zeroconf` block: advertises an
//! `_http._tcp.local.` service so LAN clients can discover the server without
//! typing an IP.  Uses the pure-Rust `mdns-sd` crate (no native Avahi/Bonjour
//! dependency).

use mdns_sd::{ServiceDaemon, ServiceInfo};

/// Handle that unregisters the service when dropped.
pub struct MdnsRegistration {
    daemon: ServiceDaemon,
    info: ServiceInfo,
}

impl MdnsRegistration {
    /// Register the SSPD HTTP service on the given port.
    pub fn register(port: u16) -> Option<Self> {
        let daemon = ServiceDaemon::new().ok()?;
        let instance_name = "SSPD Rust Server";
        let host_name = "sspd-rs.local.";

        // Build service info manually to avoid API drift across mdns-sd versions.
        let my_ty = "_http._tcp.local.";
        let properties = [("path", "/")];
        let info = ServiceInfo::new(
            my_ty,
            instance_name,
            host_name,
            "",
            port,
            &properties[..],
        )
        .ok()?;

        match daemon.register(info.clone()) {
            Ok(_) => {
                tracing::info!("mDNS service registered: {instance_name} on port {port}");
                Some(MdnsRegistration { daemon, info })
            }
            Err(e) => {
                tracing::warn!("mDNS registration failed: {e}");
                None
            }
        }
    }
}

impl Drop for MdnsRegistration {
    fn drop(&mut self) {
        let _ = self.daemon.unregister(self.info.get_fullname());
    }
}
