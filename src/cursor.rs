//! Draws the system mouse cursor onto a captured BGRA frame when the cursor is
//! inside the captured monitor.  Mirrors the Python `draw_mouse_cursor` logic
//! using `GetCursorInfo` + `DrawIconEx`.

#![cfg(windows)]

use std::ptr;

use winapi::ctypes::c_void;
use winapi::shared::windef::{POINT, RECT};
use winapi::um::wingdi::{
    CreateCompatibleDC, DeleteDC, DeleteObject, SelectObject, BITMAPINFO, BITMAPINFOHEADER,
    DIB_RGB_COLORS,
};
use winapi::um::winuser::{
    DrawIconEx, GetCursorInfo, GetDC, MonitorFromPoint, MonitorFromRect, ReleaseDC,
    CURSORINFO, MONITOR_DEFAULTTONULL,
};

use crate::framehub::Frame;

/// If the system cursor is over the captured monitor, blit the cursor icon
/// onto `frame` (in place).  No-op otherwise.
pub fn overlay(frame: &mut Frame) {
    unsafe {
        let mut ci: CURSORINFO = std::mem::zeroed();
        ci.cbSize = std::mem::size_of::<CURSORINFO>() as u32;
        if GetCursorInfo(&mut ci) == 0 {
            return;
        }
        // flags == 0 means cursor hidden.
        if ci.flags == 0 {
            return;
        }

        let pt = POINT {
            x: ci.ptScreenPos.x,
            y: ci.ptScreenPos.y,
        };
        let h_mon_cursor = MonitorFromPoint(pt, MONITOR_DEFAULTTONULL);
        if h_mon_cursor.is_null() {
            return;
        }
        let rc = RECT {
            left: frame.left,
            top: frame.top,
            right: frame.left + frame.mon_w,
            bottom: frame.top + frame.mon_h,
        };
        let h_mon_target = MonitorFromRect(&rc, MONITOR_DEFAULTTONULL);
        if h_mon_target.is_null() || h_mon_cursor != h_mon_target {
            return;
        }

        // Position relative to the captured image.
        let rel_x = ci.ptScreenPos.x - frame.left;
        let rel_y = ci.ptScreenPos.y - frame.top;

        // Clamp the cursor hotspot roughly within the frame.
        if rel_x < -32 || rel_y < -32 || rel_x > frame.width as i32 + 32 || rel_y > frame.height as i32 + 32 {
            return;
        }

        let hdc_screen = GetDC(ptr::null_mut());
        if hdc_screen.is_null() {
            return;
        }
        let hdc_mem = CreateCompatibleDC(hdc_screen);
        if hdc_mem.is_null() {
            ReleaseDC(ptr::null_mut(), hdc_screen);
            return;
        }

        // 64x64 scratch surface.
        const SZ: i32 = 64;
        let mut bi: BITMAPINFO = std::mem::zeroed();
        bi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        bi.bmiHeader.biWidth = SZ;
        bi.bmiHeader.biHeight = -SZ;
        bi.bmiHeader.biPlanes = 1;
        bi.bmiHeader.biBitCount = 32;
        bi.bmiHeader.biCompression = 0; // BI_RGB

        let mut p_bits: *mut c_void = ptr::null_mut();
        let hbmp = winapi::um::wingdi::CreateDIBSection(
            hdc_mem,
            &bi,
            DIB_RGB_COLORS,
            &mut p_bits,
            ptr::null_mut(),
            0,
        );
        if hbmp.is_null() {
            DeleteDC(hdc_mem);
            ReleaseDC(ptr::null_mut(), hdc_screen);
            return;
        }
        let hbmp_old = SelectObject(hdc_mem, hbmp as *mut _);

        // Draw the cursor at its hotspot-relative position into the scratch DIB.
        let _ = DrawIconEx(
            hdc_mem,
            0,
            0,
            ci.hCursor as *mut _,
            0,
            0,
            0,
            ptr::null_mut(),
            0x00000003, // DI_NORMAL
        );

        // Copy 64x64 region centered on the cursor.
        let max_x = (frame.width as i32 - SZ).max(0);
        let max_y = (frame.height as i32 - SZ).max(0);
        let crop_x = (rel_x - SZ / 2).max(0).min(max_x);
        let crop_y = (rel_y - SZ / 2).max(0).min(max_y);

        // Read back the scratch DIB.
        let mut buf = vec![0u8; (SZ as usize) * (SZ as usize) * 4];
        let mut bi2: BITMAPINFO = std::mem::zeroed();
        bi2.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        bi2.bmiHeader.biWidth = SZ;
        bi2.bmiHeader.biHeight = -SZ;
        bi2.bmiHeader.biPlanes = 1;
        bi2.bmiHeader.biBitCount = 32;
        bi2.bmiHeader.biCompression = 0;

        let rows = winapi::um::wingdi::GetDIBits(
            hdc_mem,
            hbmp,
            0,
            SZ as u32,
            buf.as_mut_ptr() as *mut _,
            &mut bi2,
            DIB_RGB_COLORS,
        );

        if rows > 0 {
            // Alpha-blend the cursor pixels onto the frame.  The DIB is BGRA,
            // top-down.  Blit only non-transparent pixels (alpha > 0).
            let stride = frame.width as usize * 4;
            for yy in 0..SZ as usize {
                for xx in 0..SZ as usize {
                    let si = (yy * SZ as usize + xx) * 4;
                    let a = buf[si + 3];
                    if a == 0 {
                        continue;
                    }
                    let dx = (crop_x as usize) + xx;
                    let dy = (crop_y as usize) + yy;
                    if dx >= frame.width as usize || dy >= frame.height as usize {
                        continue;
                    }
                    let di = dy * stride + dx * 4;
                    if a == 255 {
                        frame.bgra[di] = buf[si];
                        frame.bgra[di + 1] = buf[si + 1];
                        frame.bgra[di + 2] = buf[si + 2];
                        frame.bgra[di + 3] = 255;
                    } else {
                        let af = a as u32;
                        let inv = 255 - af;
                        for c in 0..3 {
                            let s = buf[si + c] as u32;
                            let d = frame.bgra[di + c] as u32;
                            frame.bgra[di + c] = ((s * af + d * inv) / 255) as u8;
                        }
                        frame.bgra[di + 3] = 255;
                    }
                }
            }
        }

        SelectObject(hdc_mem, hbmp_old);
        DeleteObject(hbmp as *mut _);
        DeleteDC(hdc_mem);
        ReleaseDC(ptr::null_mut(), hdc_screen);
    }
}
