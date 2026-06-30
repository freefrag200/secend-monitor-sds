//! Windows screen capture using GDI `BitBlt` against the desktop DC.
//!
//! This is the Rust equivalent of Python's `mss` backend.  It enumerates
//! monitors via `EnumDisplayMonitors`, captures the requested monitor into a
//! DIB section, and hands back BGRA pixels + virtual-screen coordinates.
//!
//! `BitBlt` from the desktop DC is reliable on every Windows version, captures
//! the composited desktop (including most hardware-accelerated windows since
//! Windows 8), and requires no third-party native dependencies — ideal for a
//! self-contained binary that must run on an offline LAN machine.

#![cfg(windows)]

use std::ptr;

use winapi::shared::minwindef::{BOOL, LPARAM};
use winapi::shared::windef::{HDC, HMONITOR, LPRECT};
use winapi::um::wingdi::{
    BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject, GetDIBits,
    SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS, SRCCOPY,
};
use winapi::um::errhandlingapi::GetLastError;
use winapi::um::winuser::{
    EnumDisplayMonitors, GetDesktopWindow, GetWindowDC, ReleaseDC,
};

use crate::framehub::Frame;

/// One attached monitor.
#[derive(Clone, Debug)]
pub struct Monitor {
    pub id: usize,
    pub left: i32,
    pub top: i32,
    pub width: i32,
    pub height: i32,
    pub is_primary: bool,
}

/// Mutable context threaded through the EnumDisplayMonitors callback.
struct Ctx {
    list: Vec<(i32, i32, i32, i32, bool)>,
}

/// Enumerate every attached monitor in stable order (primary first, matching
/// the order Python's `mss.monitors` returns).
pub fn enumerate_monitors() -> Vec<Monitor> {
    // Make sure coordinate APIs return real pixels, not scaled values.
    set_dpi_awareness();

    let mut ctx = Ctx { list: Vec::new() };

    unsafe {
        EnumDisplayMonitors(
            ptr::null_mut(),
            ptr::null(),
            Some(monitor_cb),
            &mut ctx as *mut Ctx as LPARAM,
        );
    }

    // Stable order: primary first.
    ctx.list.sort_by_key(|t| !t.4);

    ctx.list
        .into_iter()
        .enumerate()
        .map(|(i, (l, t, w, h, prim))| Monitor {
            id: i + 1,
            left: l,
            top: t,
            width: w,
            height: h,
            is_primary: prim,
        })
        .collect()
}

/// `EnumDisplayMonitors` callback.  Must be `extern "system"`.  The body is
/// `unsafe` because it dereferences the raw `LPARAM` context pointer.
unsafe extern "system" fn monitor_cb(
    _mon: HMONITOR,
    _hdc: HDC,
    lprect: LPRECT,
    data: LPARAM,
) -> BOOL {
    if lprect.is_null() {
        return 1;
    }
    let rect = &*lprect;
    let ctx = &mut *(data as *mut Ctx);
    let primary = rect.left == 0 && rect.top == 0;
    ctx.list.push((
        rect.left,
        rect.top,
        rect.right - rect.left,
        rect.bottom - rect.top,
        primary,
    ));
    1
}

/// Best-effort per-monitor DPI awareness so capture returns physical pixels.
fn set_dpi_awareness() {
    unsafe {
        type FnSet = unsafe extern "system" fn(u32) -> i32;
        let h = winapi::um::libloaderapi::LoadLibraryA(b"shcore.dll\0".as_ptr() as *const _);
        if !h.is_null() {
            let p = winapi::um::libloaderapi::GetProcAddress(
                h,
                b"SetProcessDpiAwareness\0".as_ptr() as *const _,
            );
            if let Some(f) = std::mem::transmute::<_, Option<FnSet>>(p) {
                // PROCESS_PER_MONITOR_DPI_AWARE = 2
                let _ = f(2);
            }
            winapi::um::libloaderapi::FreeLibrary(h);
            return;
        }

        // Fallback: user32!SetProcessDPIAware (Vista+).
        type FnSetUser = unsafe extern "system" fn() -> BOOL;
        let p = winapi::um::libloaderapi::GetProcAddress(
            winapi::um::libloaderapi::GetModuleHandleA(b"user32.dll\0".as_ptr() as *const _),
            b"SetProcessDPIAware\0".as_ptr() as *const _,
        );
        if let Some(f) = std::mem::transmute::<_, Option<FnSetUser>>(p) {
            let _ = f();
        }
    }
}

/// Capture a single monitor into a BGRA `Frame`.  Returns `None` on hard
/// failure (e.g. monitor index out of range).  The `Frame`'s virtual-screen
/// origin matches the monitor so the cursor overlay code can position correctly.
pub fn capture_monitor(monitors: &[Monitor], idx_1_based: usize) -> Option<Frame> {
    if monitors.is_empty() {
        return None;
    }
    // Clamp to a valid monitor (1-based).  Default to primary if out of range.
    let m = monitors
        .get(idx_1_based.saturating_sub(1))
        .or_else(|| monitors.iter().find(|m| m.is_primary))
        .or_else(|| monitors.first())?;

    let width = m.width.max(1);
    let height = m.height.max(1);

    unsafe {
        let hwnd_desktop = GetDesktopWindow();
        let hdc_screen = GetWindowDC(hwnd_desktop);
        if hdc_screen.is_null() {
            return None;
        }
        let hdc_mem = CreateCompatibleDC(hdc_screen);
        if hdc_mem.is_null() {
            ReleaseDC(hwnd_desktop, hdc_screen);
            return None;
        }
        let hbmp = CreateCompatibleBitmap(hdc_screen, width, height);
        if hbmp.is_null() {
            DeleteDC(hdc_mem);
            ReleaseDC(hwnd_desktop, hdc_screen);
            return None;
        }
        let hbmp_old = SelectObject(hdc_mem, hbmp as *mut _);

        // Copy the monitor rectangle from the desktop DC.
        let ok = BitBlt(
            hdc_mem,
            0,
            0,
            width,
            height,
            hdc_screen,
            m.left,
            m.top,
            SRCCOPY,
        );

        let mut bgra: Vec<u8>;

        if ok != 0 {
            // Read pixels into a top-down 32-bit DIB. BI_RGB stores bytes as
            // BGRA on Windows for this format.
            let mut bi: BITMAPINFO = std::mem::zeroed();
            bi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
            bi.bmiHeader.biWidth = width;
            bi.bmiHeader.biHeight = -height; // top-down
            bi.bmiHeader.biPlanes = 1;
            bi.bmiHeader.biBitCount = 32;
            bi.bmiHeader.biCompression = BI_RGB;

            bgra = vec![0u8; (width as usize) * (height as usize) * 4];

            let rows = GetDIBits(
                hdc_mem,
                hbmp,
                0,
                height as u32,
                bgra.as_mut_ptr() as *mut _,
                &mut bi,
                DIB_RGB_COLORS,
            );

            if rows == 0 {
                tracing::warn!(
                    "GetDIBits failed (err={}); producing blank frame",
                    GetLastError()
                );
                for px in bgra.chunks_exact_mut(4) {
                    px[0] = 0x0f; // B
                    px[1] = 0x17; // G
                    px[2] = 0x2a; // R
                    px[3] = 0xff; // A
                }
            }
        } else {
            tracing::warn!("BitBlt failed (err={})", GetLastError());
            bgra = vec![0u8; (width as usize) * (height as usize) * 4];
            for px in bgra.chunks_exact_mut(4) {
                px[0] = 0x0f;
                px[1] = 0x17;
                px[2] = 0x2a;
                px[3] = 0xff;
            }
        }

        // Cleanup.
        SelectObject(hdc_mem, hbmp_old);
        DeleteObject(hbmp as *mut _);
        DeleteDC(hdc_mem);
        ReleaseDC(hwnd_desktop, hdc_screen);

        Some(Frame {
            width: width as u32,
            height: height as u32,
            bgra,
            left: m.left,
            top: m.top,
            mon_w: m.width,
            mon_h: m.height,
            jpeg: None,
        })
    }
}

/// Resize a BGRA buffer to the target dimensions.  Uses nearest-neighbour
/// (allocation-cheap, dependency-free) — fine for downscaling a monitor feed.
pub fn resize_bgra(src: &[u8], sw: u32, sh: u32, dw: u32, dh: u32) -> Vec<u8> {
    if sw == 0 || sh == 0 || dw == 0 || dh == 0 {
        return src.to_vec();
    }
    if sw == dw && sh == dh {
        return src.to_vec();
    }

    let mut out = vec![0u8; (dw as usize) * (dh as usize) * 4];
    let x_ratio = sw as f64 / dw as f64;
    let y_ratio = sh as f64 / dh as f64;

    for y in 0..dh as usize {
        let sy = ((y as f64) * y_ratio) as usize;
        let src_row = sy * sw as usize * 4;
        let dst_row = y * dw as usize * 4;
        for x in 0..dw as usize {
            let sx = ((x as f64) * x_ratio) as usize;
            let si = src_row + sx * 4;
            let di = dst_row + x * 4;
            out[di] = src[si];
            out[di + 1] = src[si + 1];
            out[di + 2] = src[si + 2];
            out[di + 3] = src[si + 3];
        }
    }
    out
}
