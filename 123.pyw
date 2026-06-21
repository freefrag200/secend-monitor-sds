import io
import os
import time
import socket
import threading
import subprocess
import sys
import ctypes
import tkinter as tk
from tkinter import messagebox
import mss
from PIL import Image, ImageDraw

# Import System Tray elements
import pystray
from pystray import MenuItem as item

# Import Flask framework elements
from flask import Flask, Response, request, jsonify, render_template_string

# Import Waitress production WSGI server dynamically
try:
    from waitress import serve
    WAITRESS_AVAILABLE = True
except ImportError:
    WAITRESS_AVAILABLE = False

# Configure console logging for debugging
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.INFO)

# PyInstaller Resource Path Helper
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)
    if os.path.exists(local_path):
        return local_path
    return os.path.join(os.path.abspath("."), relative_path)

# Dynamically resolve logo paths
LOGO_PNG = resource_path("Image.png")
LOGO_ICO = resource_path("Image.ico")

# Safe autonomous fallback image generator if physical logo is missing
def get_logo_image():
    if os.path.exists(LOGO_PNG):
        try:
            return Image.open(LOGO_PNG)
        except Exception:
            pass
    # Generate high-tech modern fallback logo dynamically
    img = Image.new("RGBA", (256, 256), color="#0f172a")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([40, 40, 216, 216], radius=20, fill="#1e293b", outline="#3b82f6", width=6)
    draw.rounded_rectangle([70, 70, 186, 186], radius=15, fill="#0f172a", outline="#60a5fa", width=4)
    draw.rectangle([110, 216, 146, 240], fill="#3b82f6")
    draw.ellipse([90, 235, 166, 250], fill="#3b82f6")
    return img

# Dynamic WebRTC detection and loader
try:
    import asyncio
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    import av
    import numpy as np
    AIORTC_AVAILABLE = True
except Exception as e:
    AIORTC_AVAILABLE = False
    print(f"\n[DEBUG] WebRTC inactive. Component missing: {e}\n")
    class VideoStreamTrack:
        pass

app = Flask(__name__)
server_active = True
tray_icon = None
pcs = set()

# Set high-quality scaling filters
try:
    RESAMPLE_METHOD = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE_METHOD = Image.LANCZOS

# Windows structural declarations for native mouse cursor rendering
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long)
    ]

class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", POINT)
    ]

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_ulong),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_ushort),
        ("biBitCount", ctypes.c_ushort),
        ("biCompression", ctypes.c_ulong),
        ("biSizeImage", ctypes.c_ulong),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_ulong),
        ("biClrImportant", ctypes.c_ulong),
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", ctypes.c_ulong * 3)
    ]

class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_ulong),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", ctypes.c_ulong),
        ("dmDisplayFixedOutput", ctypes.c_ulong),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong),
        ("dmPelsHeight", ctypes.c_ulong),
        ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong),
        ("dmICMMethod", ctypes.c_ulong),
        ("dmICMIntent", ctypes.c_ulong),
        ("dmMediaType", ctypes.c_ulong),
        ("dmDitherType", ctypes.c_ulong),
        ("dmReserved1", ctypes.c_ulong),
        ("dmReserved2", ctypes.c_ulong),
        ("dmPanningWidth", ctypes.c_ulong),
        ("dmPanningHeight", ctypes.c_ulong),
    ]

IS_WINDOWS = sys.platform.startswith('win')
user32 = ctypes.windll.user32 if IS_WINDOWS else None
gdi32 = ctypes.windll.gdi32 if IS_WINDOWS else None

if IS_WINDOWS:
    try:
        myappid = 'spacedesk.python.server.v1'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

# Retrieve active local network IP addresses
def get_all_ips():
    ips = ["127.0.0.1"]
    try:
        hostname = socket.gethostname()
        addresses = socket.getaddrinfo(hostname, None)
        for addr in addresses:
            ip = addr[4][0]
            if ":" not in ip and ip not in ips and not ip.startswith("169.254"):
                ips.append(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.254.254.254', 1))
        primary = s.getsockname()[0]
        if primary not in ips:
            ips.insert(1, primary)
        s.close()
    except Exception:
        pass
    return ips

# Dynamic monitor resolution synchronizer with current-resolution loop check
def set_monitor_resolution(monitor_idx, width, height):
    if not IS_WINDOWS or not user32:
        return False
    try:
        attached_devices = []
        class DISPLAY_DEVICEW(ctypes.Structure):
            _fields_ = [
                ('cb', ctypes.c_ulong),
                ('DeviceName', ctypes.c_wchar * 32),
                ('DeviceString', ctypes.c_wchar * 128),
                ('StateFlags', ctypes.c_ulong),
                ('DeviceID', ctypes.c_wchar * 128),
                ('DeviceKey', ctypes.c_wchar * 128)
            ]
        
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        i = 0
        while True:
            if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
                break
            if dd.StateFlags & 1:
                attached_devices.append(dd.DeviceName)
            i += 1
            
        device_name = None
        if 0 < monitor_idx <= len(attached_devices):
            device_name = attached_devices[monitor_idx - 1]
        else:
            device_name = attached_devices[-1] if attached_devices else None

        if not device_name:
            print("[DEBUG] Resolution Sync Error: Target display monitor not found.")
            return False

        supported_modes = []
        devmode = DEVMODEW()
        devmode.dmSize = ctypes.sizeof(DEVMODEW)
        
        mode_idx = 0
        while True:
            if not user32.EnumDisplaySettingsW(device_name, mode_idx, ctypes.byref(devmode)):
                break
            if devmode.dmBitsPerPel == 32:
                supported_modes.append((devmode.dmPelsWidth, devmode.dmPelsHeight))
            mode_idx += 1
            
        if not supported_modes:
            mode_idx = 0
            while True:
                if not user32.EnumDisplaySettingsW(device_name, mode_idx, ctypes.byref(devmode)):
                    break
                supported_modes.append((devmode.dmPelsWidth, devmode.dmPelsHeight))
                mode_idx += 1

        if not supported_modes:
            print("[DEBUG] No valid standard display modes detected.")
            return False

        # Calculate nearest standard resolution using Euclidean distance
        unique_modes = list(set(supported_modes))
        best_w, best_h = unique_modes[0]
        min_dist = (best_w - width) ** 2 + (best_h - height) ** 2
        
        for w, h in unique_modes:
            dist = (w - width) ** 2 + (h - height) ** 2
            if dist < min_dist:
                min_dist = dist
                best_w, best_h = w, h
                
        # Loop Check: If the monitor is already set to this optimal resolution, skip resetting graphics pipeline [2]
        if user32.EnumDisplaySettingsW(device_name, -1, ctypes.byref(devmode)):
            if devmode.dmPelsWidth == best_w and devmode.dmPelsHeight == best_h:
                print(f"[DEBUG] Monitor {device_name} is already set to the optimal resolution: {best_w}x{best_h}. Skipping API call.")
                return True

            devmode.dmPelsWidth = best_w
            devmode.dmPelsHeight = best_h
            devmode.dmFields = 0x00080000 | 0x00100000
            
            CDS_UPDATEREGISTRY = 0x00000001
            res = user32.ChangeDisplaySettingsExW(device_name, ctypes.byref(devmode), None, CDS_UPDATEREGISTRY, None)
            if res == 0:
                user32.ChangeDisplaySettingsExW(None, None, None, 0, None)
                print(f"[DEBUG] Display monitor {device_name} resolution successfully fixed to {best_w}x{best_h}.")
                return True
            else:
                print(f"[DEBUG] Resolution adjustment failed with code: {res}")
    except Exception as e:
        print(f"[DEBUG] Unexpected exception during resolution change: {e}")
    return False

# Real Background Mouse Cursor Rendering
def draw_mouse_cursor(img, monitor):
    if IS_WINDOWS and user32 and gdi32:
        try:
            ci = CURSORINFO()
            ci.cbSize = ctypes.sizeof(CURSORINFO)
            if user32.GetCursorInfo(ctypes.byref(ci)) and ci.flags == 1:
                hcursor = ci.hCursor
                mx, my = ci.ptScreenPos.x, ci.ptScreenPos.y
                
                rel_x = mx - monitor["left"]
                rel_y = my - monitor["top"]
                
                if 0 <= rel_x <= monitor["width"] and 0 <= rel_y <= monitor["height"]:
                    crop_x = max(0, min(img.width - 64, rel_x - 32))
                    crop_y = max(0, min(img.height - 64, rel_y - 32))
                    
                    sub_img = img.crop((crop_x, crop_y, crop_x + 64, crop_y + 64))
                    bgra_data = sub_img.convert("RGBA").tobytes("raw", "BGRA")
                    
                    bmi = BITMAPINFO()
                    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                    bmi.bmiHeader.biWidth = 64
                    bmi.bmiHeader.biHeight = -64
                    bmi.bmiHeader.biPlanes = 1
                    bmi.bmiHeader.biBitCount = 32
                    bmi.bmiHeader.biCompression = 0
                    
                    hdc_screen = user32.GetDC(0)
                    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
                    
                    p_bits = ctypes.c_void_p()
                    hbmp = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(p_bits), None, 0)
                    hbmp_old = gdi32.SelectObject(hdc_mem, hbmp)
                    
                    ctypes.memmove(p_bits, bgra_data, len(bgra_data))
                    
                    dx = rel_x - crop_x
                    dy = rel_y - crop_y
                    user32.DrawIconEx(hdc_mem, dx, dy, hcursor, 0, 0, 0, None, 3)
                    
                    modified_data = ctypes.string_at(p_bits, len(bgra_data))
                    
                    gdi32.SelectObject(hdc_mem, hbmp_old)
                    gdi32.DeleteObject(hbmp)
                    gdi32.DeleteDC(hdc_mem)
                    user32.ReleaseDC(0, hdc_screen)
                    
                    drawn_sub = Image.frombuffer("RGBA", (64, 64), modified_data, "raw", "BGRA", 0, 1)
                    img.paste(drawn_sub, (crop_x, crop_y))
        except Exception:
            pass
    return img

# Capture Engine (MJPEG Stream)
def gen_frames(monitor_idx, target_res, quality, fps):
    delay = 1.0 / fps
    with mss.MSS() as sct:
        if monitor_idx >= len(sct.monitors):
            monitor_idx = 1
            
        monitor = sct.monitors[monitor_idx]
        
        while server_active:
            start_time = time.time()
            sct_img = sct.grab(monitor)
            
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            img = draw_mouse_cursor(img, monitor)
            
            if target_res and target_res != "Native":
                try:
                    w, h = map(int, target_res.split('x'))
                    img = img.resize((w, h), RESAMPLE_METHOD)
                except Exception:
                    pass
            
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=quality, subsampling=0)
            frame = output.getvalue()
            
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            
            elapsed = time.time() - start_time
            sleep_time = max(0, delay - elapsed)
            time.sleep(sleep_time)

# High-Performance Video Stream Track for WebRTC
if AIORTC_AVAILABLE:
    class ScreenStreamTrack(VideoStreamTrack):
        kind = "video"

        def __init__(self, monitor_idx, target_res, quality, fps):
            super().__init__()
            self.monitor_idx = monitor_idx
            self.target_res = target_res
            self.quality = quality
            self.fps = fps
            self.sct = mss.MSS()

        async def recv(self):
            pts, time_base = await self.next_timestamp()
            
            idx = self.monitor_idx if self.monitor_idx < len(self.sct.monitors) else 1
            monitor = self.sct.monitors[idx]
            
            sct_img = self.sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            img = draw_mouse_cursor(img, monitor)
            
            if self.target_res and self.target_res != "Native":
                try:
                    w, h = map(int, self.target_res.split('x'))
                    img = img.resize((w, h), Image.Resampling.BILINEAR)
                except Exception:
                    pass
            
            frame_arr = np.array(img)
            new_frame = av.VideoFrame.from_ndarray(frame_arr, format="rgb24")
            new_frame.pts = pts
            new_frame.time_base = time_base
            return new_frame

        def stop(self):
            super().stop()
            try:
                self.sct.close()
            except Exception:
                pass

@app.route('/video_feed')
def video_feed():
    if not server_active:
        return "Server is Offline", 503
    monitor = int(request.args.get('monitor', 2))
    res = request.args.get('res', 'Native')
    quality = int(request.args.get('quality', 100))
    fps = int(request.args.get('fps', 30))
    return Response(gen_frames(monitor, res, quality, fps),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/monitors')
def get_monitors():
    with mss.MSS() as sct:
        monitors_list = []
        for i, mon in enumerate(sct.monitors):
            if i == 0: continue
            monitors_list.append({
                "id": i,
                "width": mon["width"],
                "height": mon["height"]
            })
        return jsonify(monitors_list)

# API endpoint to dynamically adapt Windows monitor resolution based on client dimensions
@app.route('/api/set_resolution', methods=['POST'])
def api_set_resolution():
    if not server_active:
        return jsonify({"status": "error", "message": "Server Offline"}), 503
    data = request.get_json() or {}
    width = data.get('width')
    height = data.get('height')
    monitor_idx = int(data.get('monitor', 2))
    
    print(f"[DEBUG] Requested resolution adaptation: {width}x{height} on display {monitor_idx}")
    if width and height:
        success = set_monitor_resolution(monitor_idx, int(width), int(height))
        return jsonify({"status": "success" if success else "failed"})
    return jsonify({"status": "error", "message": "Invalid Dimensions"}), 400

# Signaling endpoint for ultra-low latency WebRTC handshakes
@app.route('/offer', methods=['POST'])
def webrtc_offer():
    if not AIORTC_AVAILABLE:
        print("[DEBUG] WebRTC request blocked: aiortc/av modules are not loaded.")
        return jsonify({"error": "WebRTC components (aiortc/av) are not installed on server."}), 400
    
    params = request.get_json()
    print(f"[DEBUG] WebRTC SDP Offer received: sdpType={params.get('type')}")
    
    future = asyncio.run_coroutine_threadsafe(
        handle_webrtc_offer(params),
        webrtc_loop
    )
    try:
        response_data = future.result(timeout=10)
        print("[DEBUG] Signaling complete. SDP Answer dispatched back to client.")
        return jsonify(response_data)
    except Exception as e:
        print(f"[DEBUG_ERROR] WebRTC Signaling failed: {e}")
        return jsonify({"error": str(e)}), 500

async def handle_webrtc_offer(params):
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    pc = RTCPeerConnection()
    pcs.add(pc)
    
    monitor_idx = int(params.get("monitor", 2))
    res = params.get("res", "Native")
    quality = int(params.get("quality", 100))
    fps = int(params.get("fps", 30))
    
    track = ScreenStreamTrack(monitor_idx, res, quality, fps)
    pc.addTrack(track)
    
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print(f"[DEBUG] WebRTC Peer connection state transitioned to: {pc.connectionState}")
        if pc.connectionState in ["failed", "closed"]:
            await pc.close()
            pcs.discard(pc)
            
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    
    return {
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    }

@app.route('/')
def index():
    html_template = """
    <!DOCTYPE html>
    <html lang="en" dir="ltr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Spacedesk Web Receiver</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body {
                background-color: #020617;
                color: #f1f5f9;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                overflow: hidden;
                user-select: none;
                height: 100vh;
                width: 100vw;
                display: flex;
                flex-direction: column;
                justify-content: space-between;
            }
            #video-container {
                position: relative;
                width: 100%;
                height: 100%;
                display: flex;
                align-items: center;
                justify-content: center;
                background: #000;
                overflow: hidden;
            }
            #screen-stream, #webrtc-stream {
                width: 100%;
                height: 100%;
                object-fit: fill;
                transition: all 0.3s;
            }
            .hidden { display: none !important; }
            #loading {
                position: absolute;
                inset: 0;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                background-color: #020617;
                z-index: 10;
                transition: opacity 0.3s;
            }
            .spinner {
                border: 4px solid rgba(255, 255, 255, 0.1);
                width: 48px;
                height: 48px;
                border-radius: 50%;
                border-left-color: #3b82f6;
                animation: spin 1s linear infinite;
                margin-bottom: 16px;
            }
            @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
            #control-bar {
                position: absolute;
                bottom: 24px;
                left: 50%;
                transform: translate(-50%, 0);
                background: rgba(15, 23, 42, 0.85);
                backdrop-filter: blur(16px);
                -webkit-backdrop-filter: blur(16px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                padding: 12px 24px;
                border-radius: 16px;
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                justify-content: center;
                gap: 16px;
                box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
                transition: all 0.3s ease;
                z-index: 20;
                width: 95%;
                max-width: 1180px;
            }
            .control-group {
                display: flex;
                flex-direction: column;
                gap: 4px;
            }
            .control-label {
                font-size: 11px;
                color: #94a3b8;
                font-weight: 500;
            }
            select, input[type="range"] {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 4px 8px;
                font-size: 13px;
                color: #fff;
                outline: none;
                transition: border-color 0.2s;
            }
            select:focus { border-color: #3b82f6; }
            .slider-header {
                display: flex;
                justify-content: space-between;
                font-size: 11px;
                color: #94a3b8;
                font-weight: 500;
            }
            .range-slider {
                width: 96px;
                cursor: pointer;
            }
            .checkbox-container {
                display: flex;
                align-items: center;
                gap: 8px;
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 13px;
                color: #fff;
            }
            .checkbox-container input {
                cursor: pointer;
                accent-color: #3b82f6;
            }
            .btn-group {
                display: flex;
                align-items: center;
                gap: 8px;
            }
            button {
                border: none;
                color: #fff;
                font-size: 13px;
                font-weight: 600;
                padding: 8px 16px;
                border-radius: 12px;
                cursor: pointer;
                transition: all 0.2s;
            }
            #sync-res-btn { background-color: #059669; }
            #sync-res-btn:hover { background-color: #10b981; }
            #clear-cache-btn { background-color: #be123c; }
            #clear-cache-btn:hover { background-color: #e11d48; }
            #fullscreen-btn { background-color: #2563eb; }
            #fullscreen-btn:hover { background-color: #3b82f6; }
            .opacity-0 { opacity: 0; }
            .translate-y-10 { transform: translate(-50%, 40px) !important; }
            .pointer-events-none { pointer-events: none; }
            .hide-cursor { cursor: none !important; }
        </style>
    </head>
    <body>
        <div id="video-container">
            <img id="screen-stream" alt="Screen Stream">
            <video id="webrtc-stream" autoplay playsinline muted></video>
            
            <div id="loading">
                <div class="spinner"></div>
                <p style="font-size: 18px; color: #cbd5e1;">Connecting to transmitter...</p>
            </div>
        </div>

        <div id="control-bar">
            <div class="control-group">
                <label class="control-label">Protocol</label>
                <select id="protocol-select">
                    <option value="mjpeg" selected>MJPEG (Compatible)</option>
                    <option value="webrtc">WebRTC (Ultra-Fast)</option>
                </select>
            </div>

            <div class="control-group">
                <label class="control-label">Display</label>
                <select id="monitor-select"></select>
            </div>

            <div class="control-group">
                <label class="control-label">Resolution</label>
                <select id="res-select">
                    <option value="Native" selected>Native Quality</option>
                    <option value="3840x2160">3840x2160 (4K UHD)</option>
                    <option value="2560x1440">2560x1440 (2K WQHD)</option>
                    <option value="1920x1080">1920x1080 (FullHD)</option>
                    <option value="1280x720">1280x720 (HD - Optimized)</option>
                    <option value="1024x768">1024x768 (Square)</option>
                    <option value="800x600">800x600 (Max Speed)</option>
                </select>
            </div>

            <div class="control-group">
                <label class="control-label">Scale (Fit)</label>
                <select id="scale-select">
                    <option value="fill" selected>Fill Stretch</option>
                    <option value="contain">Contain Aspect</option>
                </select>
            </div>

            <div class="control-group">
                <div class="slider-header">
                    <span>Quality</span>
                    <span id="quality-val">100%</span>
                </div>
                <input type="range" id="quality-slider" min="10" max="100" value="100" class="range-slider">
            </div>

            <div class="control-group">
                <div class="slider-header">
                    <span>Max FPS</span>
                    <span id="fps-val">30</span>
                </div>
                <input type="range" id="fps-slider" min="5" max="60" value="30" class="range-slider">
            </div>

            <div class="checkbox-container">
                <input type="checkbox" id="auto-sync-check" checked>
                <label for="auto-sync-check" style="cursor: pointer;">Auto-Sync Resolution</label>
            </div>

            <div class="btn-group">
                <button id="sync-res-btn">🔄 Sync Dimensions</button>
                <button id="clear-cache-btn">🧹 Clear Cache</button>
                <button id="fullscreen-btn">Fullscreen</button>
            </div>
        </div>

        <script>
            const streamImg = document.getElementById('screen-stream');
            const webrtcVideo = document.getElementById('webrtc-stream');
            const loading = document.getElementById('loading');
            const monitorSelect = document.getElementById('monitor-select');
            const resSelect = document.getElementById('res-select');
            const scaleSelect = document.getElementById('scale-select');
            const protocolSelect = document.getElementById('protocol-select');
            const qualitySlider = document.getElementById('quality-slider');
            const qualityVal = document.getElementById('quality-val');
            const fpsSlider = document.getElementById('fps-slider');
            const fpsVal = document.getElementById('fps-val');
            const fullscreenBtn = document.getElementById('fullscreen-btn');
            const videoContainer = document.getElementById('video-container');
            const controlBar = document.getElementById('control-bar');
            const syncResBtn = document.getElementById('sync-res-btn');
            const clearCacheBtn = document.getElementById('clear-cache-btn');
            const autoSyncCheck = document.getElementById('auto-sync-check');

            let activeMonitor = 2; 
            let activeRes = "Native"; 
            let activeQuality = 100;  
            let activeFPS = 30;       
            let pc = null;
            let wakeLock = null;

            async function requestWakeLock() {
                if ('wakeLock' in navigator) {
                    try {
                        wakeLock = await navigator.wakeLock.request('screen');
                        console.log("Wake Lock active.");
                    } catch (err) {
                        console.error(`${err.name}, ${err.message}`);
                    }
                }
            }

            document.addEventListener('visibilitychange', async () => {
                if (wakeLock !== null && document.visibilityState === 'visible') {
                    await requestWakeLock();
                }
            });

            async function syncClientResolution() {
                const width = window.innerWidth;
                const height = window.innerHeight;
                try {
                    const response = await fetch('/api/set_resolution', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            width: width,
                            height: height,
                            monitor: activeMonitor
                        })
                    });
                    const res = await response.json();
                    if (res.status === "success") {
                        console.log(`Resolution adjusted to client standard: ${width}x${height}`);
                    }
                } catch (e) {
                    console.error("Resolution Sync failed:", e);
                }
            }

            async function fetchMonitors() {
                try {
                    const res = await fetch('/api/monitors');
                    const list = await res.json();
                    monitorSelect.innerHTML = '';
                    list.forEach(m => {
                        const opt = document.createElement('option');
                        opt.value = m.id;
                        opt.textContent = `Monitor ${m.id} (${m.width}x${m.height})`;
                        if (m.id === 2) { opt.selected = true; }
                        monitorSelect.appendChild(opt);
                    });
                    
                    const monitor2Exists = list.some(m => m.id === 2);
                    if (monitor2Exists) {
                        activeMonitor = 2;
                    } else if (list.length > 0) {
                        activeMonitor = list[0].id;
                    }
                    
                    if (autoSyncCheck.checked) {
                        await syncClientResolution();
                    }
                    updateStream();
                } catch (e) { console.error(e); }
            }

            async function startWebRTC() {
                stopWebRTC();
                pc = new RTCPeerConnection({
                    iceServers: [] // Zero external network ICE servers needed for offline LAN operations
                });
                
                pc.ontrack = function(event) {
                    webrtcVideo.srcObject = event.streams[0];
                    const receiver = pc.getReceivers().find(r => r.track && r.track.kind === 'video');
                    if (receiver && 'playoutDelayHint' in receiver) {
                        receiver.playoutDelayHint = 0; // Absolute low latency video playback with zero buffer
                    }
                    loading.style.opacity = '0';
                    setTimeout(() => loading.classList.add('hidden'), 300);
                };
                
                pc.addTransceiver('video', { direction: 'recvonly' });
                
                try {
                    const offer = await pc.createOffer();
                    await pc.setLocalDescription(offer);
                    
                    await new Promise((resolve) => {
                        if (pc.iceGatheringState === 'complete') {
                            resolve();
                        } else {
                            function checkState() {
                                if (pc.iceGatheringState === 'complete') {
                                    pc.removeEventListener('icegatheringstatechange', checkState);
                                    resolve();
                                }
                            }
                            pc.addEventListener('icegatheringstatechange', checkState);
                        }
                    });
                    
                    const response = await fetch('/offer', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            sdp: pc.localDescription.sdp,
                            type: pc.localDescription.type,
                            monitor: activeMonitor,
                            res: activeRes,
                            quality: activeQuality,
                            fps: activeFPS
                        })
                    });
                    
                    const data = await response.json();
                    if (data.error) {
                        alert("WebRTC is not supported on the server:\\n" + data.error + "\\n\\nSwitching back to MJPEG.");
                        protocolSelect.value = 'mjpeg';
                        updateStream();
                        return;
                    }
                    
                    await pc.setRemoteDescription(new RTCSessionDescription(data));
                } catch (e) {
                    console.error("WebRTC Setup failed, falling back to MJPEG:", e);
                    protocolSelect.value = 'mjpeg';
                    updateStream();
                }
            }

            function stopWebRTC() {
                if (pc) {
                    pc.close();
                    pc = null;
                }
                webrtcVideo.srcObject = null;
            }

            function updateStream() {
                loading.classList.remove('hidden');
                loading.style.opacity = '1';
                requestWakeLock();

                if (protocolSelect.value === 'webrtc') {
                    streamImg.classList.add('hidden');
                    webrtcVideo.classList.remove('hidden');
                    streamImg.src = "";
                    startWebRTC();
                } else {
                    stopWebRTC();
                    webrtcVideo.classList.add('hidden');
                    streamImg.classList.remove('hidden');
                    
                    streamImg.onload = () => {
                        loading.style.opacity = '0';
                        setTimeout(() => loading.classList.add('hidden'), 300);
                    };
                    const url = `/video_feed?monitor=${activeMonitor}&res=${activeRes}&quality=${activeQuality}&fps=${activeFPS}&_t=${Date.now()}`;
                    streamImg.src = url;
                }
            }

            monitorSelect.addEventListener('change', (e) => { 
                activeMonitor = e.target.value; 
                if (autoSyncCheck.checked) {
                    syncClientResolution().then(updateStream);
                } else {
                    updateStream();
                }
            });
            resSelect.addEventListener('change', (e) => { activeRes = e.target.value; updateStream(); });
            protocolSelect.addEventListener('change', () => { updateStream(); });
            
            scaleSelect.addEventListener('change', (e) => {
                const targetFit = e.target.value === 'fill' ? 'fill' : 'contain';
                streamImg.style.objectFit = targetFit;
                webrtcVideo.style.objectFit = targetFit;
            });
            
            qualitySlider.addEventListener('input', (e) => { activeQuality = e.target.value; qualityVal.textContent = activeQuality + '%'; });
            qualitySlider.addEventListener('change', () => updateStream());
            fpsSlider.addEventListener('input', (e) => { activeFPS = e.target.value; fpsVal.textContent = activeFPS; });
            fpsSlider.addEventListener('change', () => updateStream());

            fullscreenBtn.addEventListener('click', () => {
                if (!document.fullscreenElement) { videoContainer.requestFullscreen(); } else { document.exitFullscreen(); }
            });

            syncResBtn.addEventListener('click', async () => {
                await syncClientResolution();
                updateStream();
            });

            let resizeTimeout;
            window.addEventListener('resize', () => {
                if (autoSyncCheck.checked) {
                    clearTimeout(resizeTimeout);
                    resizeTimeout = setTimeout(async () => {
                        await syncClientResolution();
                        updateStream();
                    }, 1500); 
                }
            });

            clearCacheBtn.addEventListener('click', async () => {
                if (confirm("Are you sure you want to clear all browser caches and reload the application?")) {
                    if ('caches' in window) {
                        const keys = await caches.keys();
                        for (const key of keys) {
                            await caches.delete(key);
                        }
                    }
                    localStorage.clear();
                    sessionStorage.clear();
                    document.cookie.split(";").forEach((c) => {
                        document.cookie = c.replace(/^ +/, "").replace(/=.*/, "=;expires=" + new Date().toUTCString() + ";path=/");
                    });
                    window.location.href = window.location.pathname + '?nocache=' + Date.now();
                }
            });

            let mouseTimer;
            document.addEventListener('mousemove', () => {
                controlBar.classList.remove('opacity-0', 'translate-y-10', 'pointer-events-none');
                document.body.classList.remove('hide-cursor');
                clearTimeout(mouseTimer);
                mouseTimer = setTimeout(() => {
                    if(document.fullscreenElement) {
                        controlBar.classList.add('opacity-0', 'translate-y-10', 'pointer-events-none');
                        document.body.classList.add('hide-cursor');
                    }
                }, 3000);
            });
            
            fetchMonitors();
        </script>
    </body>
    </html>
    """
    return render_template_string(html_template)

# Start Flask Web Server
def start_flask():
    if WAITRESS_AVAILABLE:
        # Serve app on multi-threaded production WSGI server with Keep-Alive enabled
        print("[DEBUG] Launching high-performance Waitress WSGI Server on port 5000...")
        serve(app, host='0.0.0.0', port=5000, threads=8)
    else:
        # Fallback to default Werkzeug server if waitress is not installed
        print("[DEBUG] Waitress not found. Falling back to default server...")
        app.run(host='0.0.0.0', port=5000, threaded=True, debug=True, use_reloader=False)

flask_thread = threading.Thread(target=start_flask, daemon=True)
flask_thread.start()

# Initialize dedicated event loop for asynchronous WebRTC connections
webrtc_loop = None
if AIORTC_AVAILABLE:
    def start_webrtc_loop():
        global webrtc_loop
        webrtc_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(webrtc_loop)
        webrtc_loop.run_forever()

    webrtc_thread = threading.Thread(target=start_webrtc_loop, daemon=True)
    webrtc_thread.start()

# --- Desktop GUI Callbacks ---
def install_virtual_display():
    status_label_install.config(text="Installing driver... Please wait", fg="#eab308")
    root.update()
    
    def run_installer():
        try:
            cmd = ["winget", "install", "--id=VirtualDrivers.Virtual-Display-Driver", "-e", "--accept-source-agreements", "--accept-package-agreements"]
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=True)
            stdout, stderr = process.communicate()
            if process.returncode == 0:
                root.after(0, lambda: messagebox.showinfo("Success", "Driver installed successfully!\nNow press Win + P and choose 'Extend' to activate."))
                root.after(0, lambda: status_label_install.config(text="Driver installed.", fg="#22c55e"))
            else:
                root.after(0, lambda: messagebox.showerror("Error", f"Installation failed:\n{stderr or stdout}"))
                root.after(0, lambda: status_label_install.config(text="Installation failed", fg="#ef4444"))
        except Exception as e:
            root.after(0, lambda: messagebox.showerror("Error", f"Could not run installer: {e}"))
            root.after(0, lambda: status_label_install.config(text="Execution error", fg="#ef4444"))
            
    threading.Thread(target=run_installer, daemon=True).start()

def toggle_server():
    global server_active
    server_active = not server_active
    if server_active:
        btn_toggle.config(text="Stop Streaming Server", bg="#ef4444", activebackground="#dc2626")
        status_server.config(text="Active", fg="#22c55e")
    else:
        btn_toggle.config(text="Start Streaming Server", bg="#22c55e", activebackground="#16a34a")
        status_server.config(text="Stopped", fg="#ef4444")

def pc_run_loop():
    tray_icon.run()

# Configure native System Tray Icon
def setup_system_tray():
    global tray_icon
    
    def show_window(icon, item):
        root.after(0, root.deiconify)
        
    def quit_app(icon, item):
        global server_active
        server_active = False
        icon.stop()
        root.after(0, root.destroy)
        os._exit(0)

    tray_img = get_logo_image()

    menu = (
        item('Show Console', show_window, default=True),
        item('Exit', quit_app)
    )
    
    tray_icon = pystray.Icon("spacedesk_python", tray_img, "Spacedesk Python Server", menu)
    
    tray_thread = threading.Thread(target=pc_run_loop, daemon=True)
    tray_thread.start()


# Tkinter Window Configuration
root = tk.Tk()
root.title("Spacedesk Python Server")
root.geometry("540x580")
root.configure(bg="#0f172a")

# Load native taskbar and titlebar icon safely
if IS_WINDOWS:
    try:
        if os.path.exists(LOGO_ICO):
            root.iconbitmap(LOGO_ICO)
        else:
            fallback_img = get_logo_image()
            temp_png = os.path.join(os.environ.get("TEMP", "."), "temp_logo.png")
            fallback_img.save(temp_png, format="PNG")
            tk_icon = tk.PhotoImage(file=temp_png)
            root.iconphoto(False, tk_icon)
            try:
                os.remove(temp_png)
            except Exception:
                pass
    except Exception as e:
        print(f"[DEBUG] Error loading titlebar icon: {e}")

# Main Header
lbl_header = tk.Label(root, text="Spacedesk Python Server Console", font=("Segoe UI", 14, "bold"), bg="#0f172a", fg="#f8fafc")
lbl_header.pack(pady=15)

# Service Status Panel Card
card_frame = tk.Frame(root, bg="#1e293b", bd=0, padx=15, pady=15)
card_frame.pack(fill="x", padx=20, pady=5)

lbl_status_title = tk.Label(card_frame, text="Service Status:", font=("Segoe UI", 10), bg="#1e293b", fg="#94a3b8")
lbl_status_title.grid(row=0, column=0, sticky="w", pady=5)

status_server = tk.Label(card_frame, text="Active", font=("Segoe UI", 10, "bold"), bg="#1e293b", fg="#22c55e")
status_server.grid(row=0, column=1, sticky="w", padx=10, pady=5)

lbl_ip_title = tk.Label(card_frame, text="Client Connection Link:", font=("Segoe UI", 10), bg="#1e293b", fg="#94a3b8")
lbl_ip_title.grid(row=1, column=0, sticky="nw", pady=5)

ips = get_all_ips()
ip_text = "\n".join([f"http://{ip}:5000" for ip in ips if ip != "127.0.0.1"])
lbl_ips = tk.Label(card_frame, text=ip_text, font=("Consolas", 11, "bold"), bg="#1e293b", fg="#38bdf8", justify="left")
lbl_ips.grid(row=1, column=1, sticky="w", padx=10, pady=5)

lbl_webrtc_drv = tk.Label(card_frame, text="WebRTC Protocol Status:", font=("Segoe UI", 10), bg="#1e293b", fg="#94a3b8")
lbl_webrtc_drv.grid(row=2, column=0, sticky="w", pady=5)

webrtc_drv_text = "Active & Accelerated" if AIORTC_AVAILABLE else "MJPEG Fallback Only"
webrtc_drv_color = "#22c55e" if AIORTC_AVAILABLE else "#f43f5e"
lbl_webrtc_drv_status = tk.Label(card_frame, text=webrtc_drv_text, font=("Segoe UI", 10, "bold"), bg="#1e293b", fg=webrtc_drv_color)
lbl_webrtc_drv_status.grid(row=2, column=1, sticky="w", padx=10, pady=5)

# Service Control Button
btn_toggle = tk.Button(root, text="Stop Streaming Server", font=("Segoe UI", 10, "bold"), bg="#ef4444", fg="white", 
                       activebackground="#dc2626", activeforeground="white", bd=0, padx=20, pady=8, cursor="hand2", command=toggle_server)
btn_toggle.pack(pady=12)

# Software Virtual Monitor Installer Card
card_driver_frame = tk.Frame(root, bg="#1e293b", bd=0, padx=15, pady=15)
card_driver_frame.pack(fill="x", padx=20, pady=5)

lbl_driver_info = tk.Label(card_driver_frame, text="Need a virtual second monitor?", font=("Segoe UI", 11, "bold"), bg="#1e293b", fg="#f8fafc")
lbl_driver_info.pack(anchor="w", pady=2)

lbl_driver_desc = tk.Label(card_driver_frame, text="Click the button below to automatically download and register the Windows Virtual Display Driver as a secondary monitor.", 
                           font=("Segoe UI", 9), bg="#1e293b", fg="#94a3b8", wraplength=460, justify="left")
lbl_driver_desc.pack(anchor="w", pady=5)

btn_install = tk.Button(card_driver_frame, text="Automatically Install Virtual Display Driver (Win 10/11)", font=("Segoe UI", 9, "bold"), bg="#2563eb", fg="white", 
                        activebackground="#1d4ed8", activeforeground="white", bd=0, padx=15, pady=6, cursor="hand2", command=install_virtual_display)
btn_install.pack(pady=5)

status_label_install = tk.Label(card_driver_frame, text="Ready to install driver", font=("Segoe UI", 9), bg="#1e293b", fg="#94a3b8")
status_label_install.pack(pady=2)

# Quick Step-by-Step Guide
lbl_guide = tk.Label(root, text="Quick Guide:\n1. Install Virtual Driver -> 2. Press Win+P and choose 'Extend'\n3. Open the blue URL above on your second device's browser.", 
                     font=("Segoe UI", 9), bg="#0f172a", fg="#64748b", justify="center")
lbl_guide.pack(pady=15)

def on_close_button():
    root.withdraw()

root.protocol("WM_DELETE_WINDOW", on_close_button)

# Launch system tray and main console window loop
setup_system_tray()
root.mainloop()