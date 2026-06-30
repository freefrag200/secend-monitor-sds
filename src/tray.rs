//! Minimal Win32 system-tray (notification icon) integration.
//!
//! Mirrors the Python `pystray` usage: an icon in the notification area with a
//! right-click menu offering "Show Console", "Stop/Start Server", "Refresh
//! Client", "Clear Client Cache", and "Exit".  Implemented directly against
//! `Shell_NotifyIconW` + a hidden message-only window so no extra crate is
//! needed.

#![cfg(windows)]

use std::mem;
use std::ptr;

use once_cell::sync::Lazy;
use parking_lot::Mutex;
use winapi::shared::minwindef::{LPARAM, LRESULT, UINT, WPARAM};
use winapi::shared::windef::HWND;
use winapi::um::libloaderapi::GetModuleHandleA;
use winapi::um::shellapi::{
    Shell_NotifyIconW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NOTIFYICONDATAW,
};
use winapi::um::winuser::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DestroyMenu, DispatchMessageW, GetMessageW,
    LoadIconA, PostQuitMessage, RegisterClassW, TrackPopupMenu, TranslateMessage, MF_SEPARATOR,
    MF_STRING, TPM_BOTTOMALIGN, TPM_LEFTALIGN, WM_APP, WM_COMMAND, WM_DESTROY, WM_LBUTTONDBLCLK,
    WM_RBUTTONUP, WNDCLASSW, WS_EX_TOOLWINDOW,
};

use crate::config;

/// App-defined message IDs.
const WM_TRAYICON: UINT = WM_APP + 1;
const CMD_SHOW: usize = 1001;
const CMD_TOGGLE: usize = 1002;
const CMD_REFRESH: usize = 1003;
const CMD_CLEARCACHE: usize = 1004;
const CMD_EXIT: usize = 1005;

/// Shared mutable state for the tray (the menu items mutate global config).
static TRAY: Lazy<Mutex<TrayState>> = Lazy::new(|| Mutex::new(TrayState::new()));

struct TrayState {
    hwnd: HWND,
    added: bool,
}

unsafe impl Send for TrayState {}

impl TrayState {
    const fn new() -> Self {
        Self {
            hwnd: ptr::null_mut(),
            added: false,
        }
    }
}

/// Start the tray on a dedicated thread.  Must be called once from main.
pub fn spawn() {
    std::thread::Builder::new()
        .name("sspd-tray".into())
        .spawn(run)
        .expect("spawn tray thread");
}

fn run() {
    unsafe {
        let hinst = GetModuleHandleA(ptr::null());
        if hinst.is_null() {
            tracing::warn!("GetModuleHandleA failed");
            return;
        }

        // Register a window class with our message-only wndproc.
        let class_name: Vec<u16> = "SspdTrayClass\0".encode_utf16().collect();
        let mut wc: WNDCLASSW = mem::zeroed();
        wc.lpfnWndProc = Some(wnd_proc);
        wc.hInstance = hinst;
        wc.lpszClassName = class_name.as_ptr();
        if RegisterClassW(&wc) == 0 {
            tracing::warn!("RegisterClassW failed");
            return;
        }

        // Create a hidden message window.
        let window_name: Vec<u16> = "SSPD Tray\0".encode_utf16().collect();
        let hwnd = CreateWindowExW(
            WS_EX_TOOLWINDOW,
            class_name.as_ptr(),
            window_name.as_ptr(),
            0,
            0,
            0,
            0,
            0,
            ptr::null_mut(),
            ptr::null_mut(),
            hinst,
            ptr::null_mut(),
        );
        if hwnd.is_null() {
            tracing::warn!("CreateWindowExW failed");
            return;
        }

        {
            let mut t = TRAY.lock();
            t.hwnd = hwnd;
        }

        // Add the notification icon (use a stock icon — no .ico file needed).
        let mut nid: NOTIFYICONDATAW = mem::zeroed();
        nid.cbSize = mem::size_of::<NOTIFYICONDATAW>() as u32;
        nid.hWnd = hwnd;
        nid.uID = 1;
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
        nid.uCallbackMessage = WM_TRAYICON;
        nid.hIcon = LoadIconA(ptr::null_mut(), 32512 as *const _); // IDI_APPLICATION
        let tip: Vec<u16> = "SSPD Rust Server\0".encode_utf16().collect();
        for (i, c) in tip.iter().enumerate() {
            if i < nid.szTip.len() {
                nid.szTip[i] = *c;
            }
        }
        if Shell_NotifyIconW(NIM_ADD, &mut nid) != 0 {
            let mut t = TRAY.lock();
            t.added = true;
            tracing::info!("Tray icon added");
        } else {
            tracing::warn!("Shell_NotifyIconW (NIM_ADD) failed");
        }

        // Standard message loop for this thread.
        let mut msg: winapi::um::winuser::MSG = mem::zeroed();
        while GetMessageW(&mut msg, ptr::null_mut(), 0, 0) > 0 {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }

        // Cleanup: remove icon.
        let mut t = TRAY.lock();
        if t.added {
            let mut nid: NOTIFYICONDATAW = mem::zeroed();
            nid.cbSize = mem::size_of::<NOTIFYICONDATAW>() as u32;
            nid.hWnd = t.hwnd;
            nid.uID = 1;
            Shell_NotifyIconW(NIM_DELETE, &mut nid);
            t.added = false;
        }
    }
}

unsafe extern "system" fn wnd_proc(
    hwnd: HWND,
    msg: UINT,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match msg {
        WM_TRAYICON => {
            let mouse = (lparam & 0xFFFF) as u32;
            match mouse {
                WM_RBUTTONUP | WM_LBUTTONDBLCLK => {
                    show_menu(hwnd);
                }
                _ => {}
            }
            0
        }
        WM_COMMAND => {
            let id = (wparam & 0xFFFF) as usize;
            handle_command(id);
            0
        }
        WM_DESTROY => {
            PostQuitMessage(0);
            0
        }
        _ => winapi::um::winuser::DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

unsafe fn show_menu(hwnd: HWND) {
    let menu = CreatePopupMenu();
    if menu.is_null() {
        return;
    }

    let active = config::server_active();
    let toggle_label = if active {
        "Stop Streaming Server"
    } else {
        "Start Streaming Server"
    };

    append(menu, CMD_SHOW, "Show Console Info");
    append(menu, CMD_TOGGLE, toggle_label);
    append_sep(menu);
    append(menu, CMD_REFRESH, "Refresh Client");
    append(menu, CMD_CLEARCACHE, "Clear Client Cache");
    append_sep(menu);
    append(menu, CMD_EXIT, "Exit");

    // Position the menu at the cursor.
    let mut pt: winapi::shared::windef::POINT = mem::zeroed();
    winapi::um::winuser::GetCursorPos(&mut pt);

    // Foreground so the menu dismisses on click-away.
    winapi::um::winuser::SetForegroundWindow(hwnd);
    TrackPopupMenu(
        menu,
        TPM_LEFTALIGN | TPM_BOTTOMALIGN,
        pt.x,
        pt.y,
        0,
        hwnd,
        ptr::null_mut(),
    );
    DestroyMenu(menu);
}

unsafe fn append(menu: winapi::shared::windef::HMENU, id: usize, label: &str) {
    let mut text: Vec<u16> = label.encode_utf16().collect();
    text.push(0);
    AppendMenuW(menu, MF_STRING, id as usize, text.as_ptr());
}

unsafe fn append_sep(menu: winapi::shared::windef::HMENU) {
    AppendMenuW(menu, MF_SEPARATOR, 0, ptr::null());
}

fn handle_command(id: usize) {
    match id {
        CMD_SHOW => {
            // Print the connection URLs to the console.
            let port = *config::ACTIVE_PORT.read();
            #[cfg(windows)]
            let ips = crate::winutil::get_all_ips();
            #[cfg(not(windows))]
            let ips: Vec<String> = vec!["127.0.0.1".into()];
            println!("\n=== SSPD Connection Links ===");
            for ip in ips {
                println!("  http://{ip}:{port}");
            }
            println!("  http://sspd-rs.local:{port}");
            println!("=============================\n");
        }
        CMD_TOGGLE => {
            let mut g = config::SERVER_ACTIVE.write();
            *g = !*g;
            let state = if *g { "Active" } else { "Stopped" };
            println!("[INFO] Server {state}");
            tracing::info!("Server {}", state);
        }
        CMD_REFRESH => {
            config::SETTINGS.write().refresh_counter += 1;
            println!("[INFO] Refresh command issued");
        }
        CMD_CLEARCACHE => {
            config::SETTINGS.write().clear_cache_cmd += 1;
            println!("[INFO] Clear-cache command issued");
        }
        CMD_EXIT => {
            println!("[INFO] Exit requested");
            unsafe {
                winapi::um::winuser::PostMessageW(
                    TRAY.lock().hwnd,
                    winapi::um::winuser::WM_CLOSE,
                    0,
                    0,
                );
            }
            // Force-exit so all daemon threads die immediately.
            std::process::exit(0);
        }
        _ => {}
    }
}
