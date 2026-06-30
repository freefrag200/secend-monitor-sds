//! Windows-specific integration helpers.
//!
//! Mirrors the Windows-only parts of the Python script:
//!   * Per-monitor DPI awareness (so capture returns physical pixels)
//!   * Disabling Power Throttling + raising process priority
//!   * `SetThreadExecutionState` to prevent sleep while streaming
//!   * `ChangeDisplaySettingsExW` for hardware resolution sync
//!   * LAN IP enumeration (used by the tray menu / console banner)

#![cfg(windows)]

use std::mem;
use std::net::UdpSocket;
use std::ptr;

use winapi::shared::minwindef::{BOOL, HINSTANCE, LPVOID};
use winapi::shared::ntdef::HRESULT;
use winapi::um::errhandlingapi::GetLastError;
use winapi::um::libloaderapi::{GetModuleHandleA, GetProcAddress, LoadLibraryA};
use winapi::um::processthreadsapi::{GetCurrentProcess, SetPriorityClass};
use winapi::um::winbase::ABOVE_NORMAL_PRIORITY_CLASS;
use winapi::um::wingdi::{DISPLAY_DEVICEW, DEVMODEW, DM_PELSHEIGHT, DM_PELSWIDTH};
use winapi::um::winnt::HANDLE;
use winapi::um::winuser::{
    ChangeDisplaySettingsExW, EnumDisplayDevicesW, EnumDisplaySettingsW, ENUM_CURRENT_SETTINGS,
};

// ---- DPI awareness ------------------------------------------------------

/// Best-effort per-monitor DPI awareness.  Tries shcore!SetProcessDpiAwareness
/// (Win 8.1+) first, then user32!SetProcessDPIAware (Vista+).
pub fn set_dpi_aware() {
    unsafe {
        type FnSet = unsafe extern "system" fn(u32) -> HRESULT;
        let h = LoadLibraryA(b"shcore.dll\0".as_ptr() as *const _);
        if !h.is_null() {
            let p = GetProcAddress(h, b"SetProcessDpiAwareness\0".as_ptr() as *const _);
            if let Some(f) = mem::transmute::<_, Option<FnSet>>(p) {
                let _ = f(2); // PROCESS_PER_MONITOR_DPI_AWARE
                free_library_checked(h);
                return;
            }
            free_library_checked(h);
        }

        type FnSetUser = unsafe extern "system" fn() -> BOOL;
        let p = GetProcAddress(
            GetModuleHandleA(b"user32.dll\0".as_ptr() as *const _),
            b"SetProcessDPIAware\0".as_ptr() as *const _,
        );
        if let Some(f) = mem::transmute::<_, Option<FnSetUser>>(p) {
            let _ = f();
        }
    }
}

unsafe fn free_library_checked(h: HINSTANCE) {
    type FnFree = unsafe extern "system" fn(HINSTANCE) -> BOOL;
    let p = GetProcAddress(
        GetModuleHandleA(b"kernel32.dll\0".as_ptr() as *const _),
        b"FreeLibrary\0".as_ptr() as *const _,
    );
    if let Some(f) = mem::transmute::<_, Option<FnFree>>(p) {
        let _ = f(h);
    }
}

// ---- Power throttling + priority ---------------------------------------

/// Disable EcoQoS power throttling for this process and raise its priority to
/// ABOVE_NORMAL (Python: `disable_windows_power_throttling`).
pub fn optimize_process() {
    unsafe {
        type FnSetInfo = unsafe extern "system" fn(
            HANDLE,
            u32,
            LPVOID,
            u32,
        ) -> BOOL;
        // SetProcessInformation(ProcessPowerThrottling = 4)
        #[repr(C)]
        #[derive(Default)]
        struct PowerThrottlingState {
            version: u32,
            control_mask: u32,
            state_mask: u32,
        }

        let p = GetProcAddress(
            GetModuleHandleA(b"kernel32.dll\0".as_ptr() as *const _),
            b"SetProcessInformation\0".as_ptr() as *const _,
        );
        if let Some(f) = mem::transmute::<_, Option<FnSetInfo>>(p) {
            let mut state = PowerThrottlingState {
                version: 1,
                control_mask: 1, // control ExecutionSpeed flag
                state_mask: 0,   // allow unhalted execution
            };
            let proc = GetCurrentProcess();
            let _ = f(
                proc,
                4, // ProcessPowerThrottling
                &mut state as *mut _ as *mut _,
                mem::size_of::<PowerThrottlingState>() as u32,
            );
        }

        // ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
        let _ = SetPriorityClass(GetCurrentProcess(), ABOVE_NORMAL_PRIORITY_CLASS as u32);
    }
}

/// Prevent the system from sleeping / turning off the display while streaming.
/// Equivalent of the Python `SetThreadExecutionState` call.
pub fn prevent_sleep() {
    unsafe {
        type FnExec = unsafe extern "system" fn(u32) -> u32;
        const ES_CONTINUOUS: u32 = 0x80000000;
        const ES_SYSTEM_REQUIRED: u32 = 0x00000001;
        const ES_DISPLAY_REQUIRED: u32 = 0x00000002;

        let p = GetProcAddress(
            GetModuleHandleA(b"kernel32.dll\0".as_ptr() as *const _),
            b"SetThreadExecutionState\0".as_ptr() as *const _,
        );
        if let Some(f) = mem::transmute::<_, Option<FnExec>>(p) {
            let _ = f(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED);
        }
    }
}

// ---- Hardware resolution sync ------------------------------------------

/// Change a monitor's resolution to the closest supported mode near the
/// requested dimensions.  Mirrors the Python `set_monitor_resolution`.
pub fn set_monitor_resolution(monitor_idx_1_based: usize, target_w: i32, target_h: i32) -> bool {
    unsafe {
        // 1. Enumerate attached display devices to find the device name.
        let device_name = match enum_display_device_name(monitor_idx_1_based) {
            Some(n) => n,
            None => return false,
        };

        // 2. Enumerate supported 32bpp modes; pick the one closest to target.
        let mut best = (0i32, 0i32);
        let mut best_dist = i64::MAX;
        let mut mode: DEVMODEW = mem::zeroed();
        mode.dmSize = mem::size_of::<DEVMODEW>() as u16;
        let mut i = 0u32;
        loop {
            if EnumDisplaySettingsW(device_name.as_ptr(), i, &mut mode) == 0 {
                break;
            }
            if mode.dmBitsPerPel == 32 {
                let dist =
                    (mode.dmPelsWidth as i64 - target_w as i64).pow(2)
                        + (mode.dmPelsHeight as i64 - target_h as i64).pow(2);
                if dist < best_dist {
                    best_dist = dist;
                    best = (mode.dmPelsWidth as i32, mode.dmPelsHeight as i32);
                }
            }
            i += 1;
        }
        if best.0 == 0 {
            return false;
        }

        // 3. Read current settings; if already at target, nothing to do.
        if EnumDisplaySettingsW(device_name.as_ptr(), ENUM_CURRENT_SETTINGS, &mut mode) == 0 {
            return false;
        }
        if mode.dmPelsWidth as i32 == best.0 && mode.dmPelsHeight as i32 == best.1 {
            return true;
        }

        // 4. Apply.
        mode.dmPelsWidth = best.0 as u32;
        mode.dmPelsHeight = best.1 as u32;
        mode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT;

        const CDS_UPDATEREGISTRY: u32 = 0x00000001;
        let res = ChangeDisplaySettingsExW(
            device_name.as_ptr(),
            &mut mode,
            ptr::null_mut(),
            CDS_UPDATEREGISTRY,
            ptr::null_mut(),
        );
        if res == 0 {
            // Apply globally.
            let _ = ChangeDisplaySettingsExW(
                ptr::null(),
                ptr::null_mut(),
                ptr::null_mut(),
                0,
                ptr::null_mut(),
            );
            tracing::info!("Resolution synced to {}x{}", best.0, best.1);
            true
        } else {
            tracing::warn!(
                "ChangeDisplaySettingsExW returned {res} (err={})",
                GetLastError()
            );
            false
        }
    }
}

/// Find the DISPLAY_DEVICE.DeviceName (e.g. `\\.\DISPLAY2`) for the Nth
/// attached monitor (1-based).  Returns None if not found.
unsafe fn enum_display_device_name(monitor_idx_1_based: usize) -> Option<Vec<u16>> {
    let mut attached: Vec<[u16; 32]> = Vec::new();

    let mut i = 0u32;
    loop {
        let mut dd: DISPLAY_DEVICEW = mem::zeroed();
        dd.cb = mem::size_of::<DISPLAY_DEVICEW>() as u32;
        if EnumDisplayDevicesW(ptr::null(), i, &mut dd, 0) == 0 {
            break;
        }
        if dd.StateFlags & 1 != 0 {
            // DISPLAY_DEVICE_ATTACHED_TO_DESKTOP
            let mut name = [0u16; 32];
            name.copy_from_slice(&dd.DeviceName);
            attached.push(name);
        }
        i += 1;
    }

    let idx = if monitor_idx_1_based >= 1 && monitor_idx_1_based <= attached.len() {
        monitor_idx_1_based - 1
    } else {
        attached.len().saturating_sub(1)
    };
    let name = attached.get(idx)?;
    // Build a null-terminated UTF-16 string.
    let mut out: Vec<u16> = name.to_vec();
    // Trim trailing zeros and re-add exactly one terminator.
    while out.last() == Some(&0) {
        out.pop();
    }
    out.push(0);
    Some(out)
}

// ---- LAN IP enumeration ------------------------------------------------

/// Enumerate all routable IPv4 addresses of this host.  Equivalent of the
/// Python `get_all_ips()` helper.
pub fn get_all_ips() -> Vec<String> {
    let mut ips = vec!["127.0.0.1".to_string()];

    // Primary outbound IP via a connected (but un-sent) UDP socket.
    if let Ok(s) = UdpSocket::bind(("0.0.0.0", 0)) {
        let _ = s.connect(("10.254.254.254", 1));
        if let Ok(addr) = s.local_addr() {
            let ip = addr.ip().to_string();
            if !ips.contains(&ip) {
                ips.insert(1, ip);
            }
        }
    }

    // Hostname-based enumeration as a secondary source.
    if let Ok(hostname) = hostname() {
        if let Ok(addrs) = std::net::ToSocketAddrs::to_socket_addrs(
            &(hostname.as_str(), 0u16),
        ) {
            for addr in addrs {
                let ip = addr.ip();
                if ip.is_ipv4() {
                    let s = ip.to_string();
                    if !ips.contains(&s) && !s.starts_with("169.254") {
                        ips.push(s);
                    }
                }
            }
        }
    }

    ips
}

fn hostname() -> std::io::Result<String> {
    // std doesn't expose gethostname directly; emulate via env on Windows.
    if let Ok(name) = std::env::var("COMPUTERNAME") {
        return Ok(name);
    }
    Ok("localhost".into())
}
