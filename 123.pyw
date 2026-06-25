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
from tkinter import ttk
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

# Safe fallback image generator if physical logo is missing
def get_logo_image():
    if os.path.exists(LOGO_PNG):
        try:
            return Image.open(LOGO_PNG)
        except Exception:
            pass
    # Generate fallback logo dynamically
    img = Image.new("RGBA", (256, 256), color="#0f172a")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([40, 40, 216, 216], radius=20, fill="#1e293b", outline="#3b82f6", width=6)
    draw.rounded_rectangle([70, 70, 186, 186], radius=15, fill="#0f172a", outline="#60a5fa", width=4)
    draw.rectangle([110, 216, 146, 240], fill="#3b82f6")
    draw.ellipse([90, 235, 166, 250], fill="#3b82f6")
    return img

# Dynamic DXcam Detection for Ultra-High FPS direct VRAM capture
try:
    import dxcam
    DXCAM_AVAILABLE = True
    print("[DEBUG] DXcam Engine is installed and available for direct VRAM capture.")
except ImportError:
    DXCAM_AVAILABLE = False
    print("[DEBUG] DXcam not found. Defaulting to MSS Capture.")

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

# Setup thread-safe global locks and configuration parameters
config_lock = threading.Lock()
current_protocol = "mjpeg"      
current_engine = "auto"         
current_monitor = 1             
current_resolution = "Native"   
current_quality = 100           
current_fps = 30                
current_bitrate = 10            # Bitrate parameter in Mbps
auto_sync_resolution = True    
refresh_counter = 0             

tray_icon = None
pcs = set()

# Set high-performance scaling filters
try:
    RESAMPLE_METHOD = Image.Resampling.BILINEAR
except AttributeError:
    RESAMPLE_METHOD = Image.BILINEAR

IS_WINDOWS = sys.platform.startswith('win')
user32 = ctypes.windll.user32 if IS_WINDOWS else None
gdi32 = ctypes.windll.gdi32 if IS_WINDOWS else None

# Windows structural declarations for native mouse cursor rendering
if IS_WINDOWS:
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
else:
    POINT = RECT = CURSORINFO = BITMAPINFOHEADER = BITMAPINFO = DEVMODEW = None

# Hardware H264 Encoder Detection & Module-Level Proxy Monkey Patching
GPU_CODEC = None
if AIORTC_AVAILABLE:
    for codec_candidate in ["h264_nvenc", "h264_amf", "h264_qsv"]:
        try:
            test_ctx = av.CodecContext.create(codec_candidate, "w")
            GPU_CODEC = codec_candidate
            print(f"[DEBUG] Hardware-accelerated GPU encoder verified and selected: {GPU_CODEC}")
            break
        except Exception:
            pass

    if GPU_CODEC:
        import aiortc.codecs.h264

        class PatchedCodecContext(av.CodecContext):
            @classmethod
            def create(cls, name, mode="r"):
                if name == "libx264" and mode == "w" and GPU_CODEC:
                    print(f"[DEBUG] Intercepting libx264 inside aiortc and routing to GPU: {GPU_CODEC}")
                    ctx = super(PatchedCodecContext, cls).create(GPU_CODEC, mode)
                else:
                    ctx = super(PatchedCodecContext, cls).create(name, mode)
                
                # Apply Dynamic custom bitrate setup
                if mode == "w":
                    try:
                        with config_lock:
                            target_br = current_bitrate * 1000000
                        ctx.bit_rate = target_br
                        print(f"[DEBUG] Hardware encoder Bitrate applied: {current_bitrate} Mbps")
                    except Exception as e:
                        print(f"[DEBUG] Error setting hardware options: {e}")
                return ctx

            @property
            def options(self):
                return av.CodecContext.options.__get__(self)

            @options.setter
            def options(self, value):
                codec_name = getattr(self, "name", "")
                if codec_name in ["h264_nvenc", "h264_amf", "h264_qsv"]:
                    filtered_opts = {}
                    if isinstance(value, dict):
                        for k, v in value.items():
                            if codec_name == "h264_nvenc":
                                if k == "tune":
                                    filtered_opts["tune"] = "ull"
                                elif k == "preset":
                                    filtered_opts["preset"] = "p1"
                                elif k in ["level", "zerolatency"]:
                                    continue
                                else:
                                    filtered_opts[k] = v
                            elif codec_name == "h264_amf":
                                if k in ["tune", "level", "preset"]:
                                    continue
                                else:
                                    filtered_opts[k] = v
                            else:
                                filtered_opts[k] = v
                    else:
                        filtered_opts = value

                    if codec_name == "h264_nvenc":
                        filtered_opts["zerolatency"] = "1"
                    elif codec_name == "h264_amf":
                        filtered_opts["usage"] = "lowlatency"
                        filtered_opts["quality"] = "speed"

                    value = filtered_opts

                try:
                    av.CodecContext.options.__set__(self, value)
                except Exception as e:
                    print(f"[DEBUG] Error setting hardware options: {e}")

        class AvModuleProxy:
            def __init__(self, real_av):
                self._real_av = real_av

            def __getattr__(self, name):
                if name == "CodecContext":
                    return PatchedCodecContext
                return getattr(self._real_av, name)

        aiortc.codecs.h264.av = AvModuleProxy(av)
        print("[DEBUG] WebRTC Codec monkeypatch applied successfully.")

app = Flask(__name__)

# Thread-safe server activation controller
server_active = threading.Event()
server_active.set()

# Helper to check if a local port is already occupied
def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

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

# Dynamic monitor resolution synchronizer
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
            print("[DEBUG] No valid standard display modes detected.")
            return False

        unique_modes = list(set(supported_modes))
        best_w, best_h = unique_modes[0]
        min_dist = (best_w - width) ** 2 + (best_h - height) ** 2
        
        for w, h in unique_modes:
            dist = (w - width) ** 2 + (h - height) ** 2
            if dist < min_dist:
                min_dist = dist
                best_w, best_h = w, h
                
        if user32.EnumDisplaySettingsW(device_name, -1, ctypes.byref(devmode)):
            if devmode.dmPelsWidth == best_w and devmode.dmPelsHeight == best_h:
                print(f"[DEBUG] Monitor {device_name} is already set to the optimal resolution: {best_w}x{best_h}.")
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
    except Exception as e:
        print(f"[DEBUG] Unexpected exception during resolution change: {e}")
    return False

# Safe GDI Resource Cleanup drawing mechanism
def draw_mouse_cursor(img, monitor):
    if IS_WINDOWS and user32 and gdi32:
        hdc_screen = None
        hdc_mem = None
        hbmp = None
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
                    if hdc_screen:
                        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
                        if hdc_mem:
                            p_bits = ctypes.c_void_p()
                            hbmp = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(p_bits), None, 0)
                            if hbmp:
                                hbmp_old = gdi32.SelectObject(hdc_mem, hbmp)
                                try:
                                    ctypes.memmove(p_bits, bgra_data, len(bgra_data))
                                    dx = rel_x - crop_x
                                    dy = rel_y - crop_y
                                    user32.DrawIconEx(hdc_mem, dx, dy, hcursor, 0, 0, 0, None, 3)
                                    
                                    modified_data = ctypes.string_at(p_bits, len(bgra_data))
                                    drawn_sub = Image.frombuffer("RGBA", (64, 64), modified_data, "raw", "BGRA", 0, 1)
                                    img.paste(drawn_sub, (crop_x, crop_y))
                                finally:
                                    # Ensure GDI DC restoration is always processed safely to prevent memory leak
                                    gdi32.SelectObject(hdc_mem, hbmp_old)
        except Exception:
            pass
        finally:
            if hdc_mem:
                if hbmp:
                    gdi32.DeleteObject(hbmp)
                gdi32.DeleteDC(hdc_mem)
            if hdc_screen:
                user32.ReleaseDC(0, hdc_screen)
    return img

# Modular Screen Capture Engine with Double Conversion Bypass
class ScreenCaptureEngine:
    def __init__(self):
        self.sct = mss.MSS()
        self.dx_camera = None
        self.current_monitor_idx = None
        self.last_pil_img = None
        self.is_native_cursor = False
        self.consecutive_failures = 0

    def init_dxcam(self, monitor_idx):
        global DXCAM_AVAILABLE
        if not DXCAM_AVAILABLE:
            return False
        try:
            if self.dx_camera:
                try: self.dx_camera.release()
                except Exception: pass
                self.dx_camera = None
            
            target_idx = max(0, monitor_idx - 1)
            self.dx_camera = dxcam.create(output_idx=target_idx, backend="winrt")
            if self.dx_camera:
                self.current_monitor_idx = monitor_idx
                self.is_native_cursor = True
                return True
        except Exception as e:
            try:
                self.dx_camera = dxcam.create(output_idx=target_idx)
                if self.dx_camera:
                    self.current_monitor_idx = monitor_idx
                    self.is_native_cursor = False
                    return True
            except Exception: pass
            self.dx_camera = None
            self.is_native_cursor = False
        return False

    def grab(self, monitor_idx, engine_preference="auto"):
        global DXCAM_AVAILABLE
        
        # Self-Healing: Fall back automatically if GDI BitBlt consistently fails on a dead virtual display
        if self.consecutive_failures >= 5:
            print(f"[WARNING] Monitor {monitor_idx} is unreachable (GDI BitBlt failed). Automatically falling back to Monitor 1.")
            self.consecutive_failures = 0
            with config_lock:
                global current_monitor
                current_monitor = 1
            monitor_idx = 1
            try:
                root.after(0, reset_gui_monitor_to_primary)
            except Exception:
                pass

        if monitor_idx >= len(self.sct.monitors):
            monitor_idx = 1
        monitor_bounds = self.sct.monitors[monitor_idx]

        use_dxcam = DXCAM_AVAILABLE and (engine_preference == "dxcam" or (engine_preference == "auto" and DXCAM_AVAILABLE))

        if use_dxcam:
            if self.current_monitor_idx != monitor_idx or self.dx_camera is None:
                self.init_dxcam(monitor_idx)
            if self.dx_camera:
                try:
                    frame = self.dx_camera.grab()
                    if frame is not None:
                        self.consecutive_failures = 0 # reset on success
                        return None, frame, monitor_bounds
                    elif self.last_pil_img is not None:
                        return self.last_pil_img, None, monitor_bounds
                except Exception as e:
                    print(f"[DEBUG] DXcam frame grab failed: {e}. Disabling DXcam and reverting to MSS.")
                    print("[WARNING] DXcam is disabled because 'opencv-python' is not installed. To capture virtual displays without BitBlt errors, run: pip install opencv-python")
                    DXCAM_AVAILABLE = False
        
        try:
            self.is_native_cursor = False
            sct_img = self.sct.grab(monitor_bounds)
            self.consecutive_failures = 0 # reset on success
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            return img, None, monitor_bounds
        except Exception as e:
            self.consecutive_failures += 1
            raise e

capture_engine = ScreenCaptureEngine()

# Capture Engine (MJPEG Stream)
def gen_frames(monitor_idx, target_res, quality, fps, engine_pref="auto"):
    delay = 1.0 / fps
    last_successful_frame = None
    
    while server_active.is_set():
        start_time = time.time()
        try:
            img, raw_np, monitor = capture_engine.grab(monitor_idx, engine_pref)
            
            # If we bypassed to raw numpy, convert to PIL only for standard MJPEG pipeline
            if img is None and raw_np is not None:
                img = Image.fromarray(raw_np)
            
            if not capture_engine.is_native_cursor:
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
            last_successful_frame = frame
        except Exception as e:
            print(f"[DEBUG] Desktop capture suspended: {e}")
            if last_successful_frame:
                frame = last_successful_frame
            else:
                standby = Image.new("RGB", (1280, 720), color="#0f172a")
                draw = ImageDraw.Draw(standby)
                draw.text((40, 40), "Desktop Paused (Admin/UAC State Active)", fill="#ef4444")
                output = io.BytesIO()
                standby.save(output, format="JPEG", quality=60)
                frame = output.getvalue()
            time.sleep(0.5)
            
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        
        elapsed = time.time() - start_time
        sleep_time = max(0, delay - elapsed)
        time.sleep(sleep_time)

# High-Performance Video Stream Track (No Dual Conversion Bottleneck)
if AIORTC_AVAILABLE:
    class ScreenStreamTrack(VideoStreamTrack):
        kind = "video"

        def __init__(self, monitor_idx, target_res, quality, fps, engine_pref="auto"):
            super().__init__()
            self.monitor_idx = monitor_idx
            self.target_res = target_res
            self.quality = quality
            self.fps = fps
            self.engine_pref = engine_pref
            self.last_img = None

        async def recv(self):
            pts, time_base = await self.next_timestamp()
            
            try:
                img, raw_np, monitor = capture_engine.grab(self.monitor_idx, self.engine_pref)
                
                # If NumPy is already fetched directly from DXcam VRAM, feed it natively!
                if raw_np is not None:
                    if self.target_res and self.target_res != "Native":
                        try:
                            w, h = map(int, self.target_res.split('x'))
                            # Only parse through PIL image if resizing is explicitly demanded
                            img = Image.fromarray(raw_np)
                            img = img.resize((w, h), Image.Resampling.BILINEAR)
                            frame_arr = np.array(img)
                        except Exception:
                            frame_arr = raw_np
                    else:
                        frame_arr = raw_np
                else:
                    if not capture_engine.is_native_cursor:
                        img = draw_mouse_cursor(img, monitor)
                    if self.target_res and self.target_res != "Native":
                        try:
                            w, h = map(int, self.target_res.split('x'))
                            img = img.resize((w, h), Image.Resampling.BILINEAR)
                        except Exception:
                            pass
                    frame_arr = np.array(img)
                    
                self.last_img = img
            except Exception as e:
                print(f"[DEBUG] WebRTC Capture suspended (Admin/UAC): {e}")
                if self.last_img:
                    frame_arr = np.array(self.last_img)
                else:
                    img = Image.new("RGB", (1280, 720), color="#0f172a")
                    draw = ImageDraw.Draw(img)
                    draw.text((40, 40), "Desktop Paused", fill="#ef4444")
                    frame_arr = np.array(img)
                await asyncio.sleep(0.1)
            
            new_frame = av.VideoFrame.from_ndarray(frame_arr, format="rgb24")
            new_frame.pts = pts
            new_frame.time_base = time_base
            return new_frame

@app.route('/video_feed')
def video_feed():
    if not server_active.is_set():
        return "Server is Offline", 503
    with config_lock:
        mon, res, qual, fps, eng = current_monitor, current_resolution, current_quality, current_fps, current_engine
    return Response(gen_frames(mon, res, qual, fps, eng),
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

@app.route('/api/current_settings')
def get_current_settings():
    with config_lock:
        return jsonify({
            "protocol": current_protocol,
            "monitor": current_monitor,
            "res": current_resolution,
            "quality": current_quality,
            "fps": current_fps,
            "bitrate": current_bitrate,
            "engine": current_engine,
            "auto_sync": auto_sync_resolution,
            "refresh_counter": refresh_counter
        })

@app.route('/api/set_resolution', methods=['POST'])
def api_set_resolution():
    if not server_active.is_set():
        return jsonify({"status": "error", "message": "Server Offline"}), 503
    data = request.get_json() or {}
    width = data.get('width')
    height = data.get('height')
    with config_lock:
        mon_idx = int(data.get('monitor', current_monitor))
    
    if width and height:
        success = set_monitor_resolution(mon_idx, int(width), int(height))
        return jsonify({"status": "success" if success else "failed"})
    return jsonify({"status": "error", "message": "Invalid Dimensions"}), 400

@app.route('/offer', methods=['POST'])
def webrtc_offer():
    if not AIORTC_AVAILABLE:
        return jsonify({"error": "WebRTC components not installed."}), 400
    
    params = request.get_json() or {}
    with config_lock:
        params["monitor"] = current_monitor
        params["res"] = current_resolution
        params["quality"] = current_quality
        params["fps"] = current_fps
        params["engine"] = current_engine
    
    future = asyncio.run_coroutine_threadsafe(
        handle_webrtc_offer(params),
        webrtc_loop
    )
    try:
        response_data = future.result(timeout=10)
        return jsonify(response_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

async def handle_webrtc_offer(params):
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    pc = RTCPeerConnection()
    pcs.add(pc)
    
    monitor_idx = int(params.get("monitor", 1))
    res = params.get("res", "Native")
    quality = int(params.get("quality", 100))
    fps = int(params.get("fps", 30))
    engine_pref = params.get("engine", "auto")
    
    track = ScreenStreamTrack(monitor_idx, res, quality, fps, engine_pref)
    pc.addTrack(track)
    
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        if pc.connectionState in ["failed", "closed"]:
            await pc.close()
            pcs.discard(pc)
            
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.02)
    
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
                align-items: center;
                justify-content: center;
                gap: 16px;
                box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
                transition: all 0.3s ease;
                z-index: 20;
                width: auto;
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
            select {
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
                <label class="control-label">Scale (Fit)</label>
                <select id="scale-select">
                    <option value="fill" selected>Fill Stretch</option>
                    <option value="contain">Contain Aspect</option>
                </select>
            </div>

            <div class="btn-group">
                <button id="clear-cache-btn">🧹 Clear Cache</button>
                <button id="fullscreen-btn">Fullscreen</button>
            </div>
        </div>

        <script>
            const streamImg = document.getElementById('screen-stream');
            const webrtcVideo = document.getElementById('webrtc-stream');
            const loading = document.getElementById('loading');
            const scaleSelect = document.getElementById('scale-select');
            const fullscreenBtn = document.getElementById('fullscreen-btn');
            const videoContainer = document.getElementById('video-container');
            const controlBar = document.getElementById('control-bar');
            const clearCacheBtn = document.getElementById('clear-cache-btn');

            let activeProtocol = ""; 
            let activeMonitor = 1; 
            let activeRes = "Native"; 
            let activeQuality = 100;  
            let activeFPS = 30;       
            let activeEngine = "auto";
            let autoSyncRes = true;
            
            let pc = null;
            let wakeLock = null;
            
            // FIX: Removed localStorage dependence to avoid mismatches on server restarts
            let lastRefreshCounter = null;
            let currentStreamOffline = false;
            let consecutiveFailures = 0;

            async function requestWakeLock() {
                if ('wakeLock' in navigator) {
                    try {
                        wakeLock = await navigator.wakeLock.request('screen');
                    } catch (err) {
                        console.error(`${err.name}, ${err.message}`);
                    }
                }
            }

            // FIX: Aggressively request Wake Lock on visibility recovery to keep screen and Wi-Fi active
            document.addEventListener('visibilitychange', async () => {
                if (document.visibilityState === 'visible') {
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
                } catch (e) {
                    console.error("Resolution Sync failed:", e);
                }
            }

            async function pollSettings() {
                try {
                    const response = await fetch('/api/current_settings?_ka=' + Date.now());
                    if (!response.ok) throw new Error("Offline State Detected");
                    
                    consecutiveFailures = 0; 
                    const settings = await response.json();
                    
                    // FIX: Initialize with current server state, hot reload on future changes
                    if (lastRefreshCounter === null) {
                        lastRefreshCounter = settings.refresh_counter;
                    } else if (settings.refresh_counter > lastRefreshCounter) {
                        lastRefreshCounter = settings.refresh_counter;
                        updateStream(true);
                        return; 
                    }
                    
                    let needsUpdate = false;
                    
                    if (settings.protocol !== activeProtocol || 
                        settings.monitor !== activeMonitor || 
                        settings.res !== activeRes || 
                        settings.quality !== activeQuality || 
                        settings.fps !== activeFPS || 
                        settings.engine !== activeEngine ||
                        settings.auto_sync !== autoSyncRes) {
                        
                        activeProtocol = settings.protocol;
                        activeMonitor = settings.monitor;
                        activeRes = settings.res;
                        activeQuality = settings.quality;
                        activeFPS = settings.fps;
                        activeEngine = settings.engine;
                        autoSyncRes = settings.auto_sync;
                        
                        needsUpdate = true;
                    }
                    
                    if (needsUpdate) {
                        console.log("Settings changed. Re-syncing...", settings);
                        if (autoSyncRes) {
                            await syncClientResolution();
                        }
                        updateStream();
                    }

                    // FIX: Force reconnect the video stream if the client recovered from an offline state
                    if (currentStreamOffline) {
                        currentStreamOffline = false;
                        loading.style.opacity = '0';
                        setTimeout(() => loading.classList.add('hidden'), 300);
                        console.log("[DEBUG] Network recovered. Re-connecting video stream...");
                        updateStream(true); 
                    }
                } catch (e) {
                    consecutiveFailures++;
                    if (consecutiveFailures >= 12) { 
                        if (!currentStreamOffline) {
                            currentStreamOffline = true;
                            loading.classList.remove('hidden');
                            loading.style.opacity = '1';
                        }
                    }
                }
            }

            async function startWebRTC(isSilent = false) {
                stopWebRTC();
                pc = new RTCPeerConnection({
                    iceServers: []
                });
                
                pc.ontrack = function(event) {
                    webrtcVideo.srcObject = event.streams[0];
                    const receiver = pc.getReceivers().find(r => r.track && r.track.kind === 'video');
                    if (receiver && 'playoutDelayHint' in receiver) {
                        receiver.playoutDelayHint = 0;
                    }
                    if (!isSilent) {
                        loading.style.opacity = '0';
                        setTimeout(() => loading.classList.add('hidden'), 300);
                    } else {
                        loading.classList.add('hidden');
                    }
                };
                
                pc.onconnectionstatechange = function() {
                    if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
                        currentStreamOffline = true;
                        setTimeout(() => {
                            if (currentStreamOffline) updateStream(true);
                        }, 2000);
                    }
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
                            type: pc.localDescription.type
                        })
                    });
                    
                    const data = await response.json();
                    if (data.error) {
                        alert("WebRTC Setup Error: " + data.error);
                        return;
                    }
                    
                    await pc.setRemoteDescription(new RTCSessionDescription(data));
                } catch (e) {
                    console.error("WebRTC Setup failed, falling back silently:", e);
                    updateStream(true);
                }
            }

            function stopWebRTC() {
                if (pc) {
                    pc.close();
                    pc = null;
                }
                webrtcVideo.srcObject = null;
            }

            function updateStream(isSilent = false) {
                if (!isSilent) {
                    loading.classList.remove('hidden');
                    loading.style.opacity = '1';
                }
                requestWakeLock();

                if (activeProtocol === 'webrtc') {
                    streamImg.classList.add('hidden');
                    webrtcVideo.classList.remove('hidden');
                    streamImg.src = "";
                    startWebRTC(isSilent);
                } else {
                    stopWebRTC();
                    webrtcVideo.classList.add('hidden');
                    streamImg.classList.remove('hidden');
                    
                    streamImg.onload = () => {
                        loading.style.opacity = '0';
                        setTimeout(() => loading.classList.add('hidden'), 300);
                    };
                    
                    streamImg.onerror = () => {
                        currentStreamOffline = true;
                        setTimeout(() => {
                            updateStream(true);
                        }, 1500); 
                    };
                    
                    // FIX: Force-terminate previous TCP MJPEG stream to avoid HTTP connection limits
                    streamImg.src = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
                    setTimeout(() => {
                        const url = `/video_feed?_t=${Date.now()}`;
                        streamImg.src = url;
                    }, 150);
                }
            }
            
            scaleSelect.addEventListener('change', (e) => {
                const targetFit = e.target.value === 'fill' ? 'fill' : 'contain';
                streamImg.style.objectFit = targetFit;
                webrtcVideo.style.objectFit = targetFit;
            });

            fullscreenBtn.addEventListener('click', () => {
                if (!document.fullscreenElement) { videoContainer.requestFullscreen(); } else { document.exitFullscreen(); }
            });

            let resizeTimeout;
            window.addEventListener('resize', () => {
                if (autoSyncRes) {
                    clearTimeout(resizeTimeout);
                    resizeTimeout = setTimeout(async () => {
                        await syncClientResolution();
                        updateStream(true);
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
            
            // FIX: Prevent connection accumulation with a self-scheduling recursive poll loop
            async function runPollLoop() {
                try {
                    await pollSettings();
                } catch (e) {
                    console.error("Settings Poll error:", e);
                }
                setTimeout(runPollLoop, 250);
            }
            runPollLoop();
        </script>
    </body>
    </html>
    """
    return render_template_string(html_template)

# Start Flask Web Server with Sleep Prevention
def start_flask():
    if is_port_in_use(5000):
        print("[WARNING] Port 5000 is already in use by another program.")
    
    # Enable Continuous execution state to prevent screen/host sleep
    if IS_WINDOWS:
        try:
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
            print("[DEBUG] Windows System Sleep Prevention mode activated.")
        except Exception as e:
            print(f"[DEBUG] Sleep prevention setup failed: {e}")
            
    if WAITRESS_AVAILABLE:
        print("[DEBUG] Launching high-performance Waitress WSGI Server on port 5000...")
        serve(app, host='0.0.0.0', port=5000, threads=8)
    else:
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

# Safe parse for unbounded configuration numbers
def safe_get_int(entry_widget, default_val):
    try:
        # Limit constraints on typing are completely removed
        return int(entry_widget.get())
    except Exception:
        return default_val

# --- Desktop GUI Callbacks & Global Update logic ---
def update_globals(*args):
    global current_protocol, current_engine, current_monitor, current_resolution, current_quality, current_fps, current_bitrate, auto_sync_resolution
    
    with config_lock:
        proto_map = {"MJPEG (Compatible)": "mjpeg", "WebRTC (Ultra-Fast)": "webrtc"}
        current_protocol = proto_map.get(cb_protocol.get(), "mjpeg")
        
        engine_map = {"Auto (DXcam / MSS)": "auto", "MSS (Standard)": "mss", "DXcam (Ultra-High FPS)": "dxcam"}
        current_engine = engine_map.get(cb_engine.get(), "auto")
        
        try:
            mon_str = cb_monitor.get()
            if "Monitor" in mon_str:
                current_monitor = int(mon_str.split()[1])
            else:
                current_monitor = int(mon_str)
        except Exception:
            current_monitor = 1
            
        current_resolution = cb_resolution.get()
        current_quality = safe_get_int(entry_quality, 100)
        current_fps = safe_get_int(entry_fps, 30)
        current_bitrate = safe_get_int(entry_bitrate, 10)
        auto_sync_resolution = bool(var_auto_sync.get())

def trigger_web_refresh():
    global refresh_counter
    with config_lock:
        refresh_counter += 1
    print(f"[DEBUG] Web Client forced refresh triggered. Current State Counter: {refresh_counter}")

# Reset GUI monitor combobox to primary
def reset_gui_monitor_to_primary():
    try:
        if cb_monitor.winfo_exists():
            vals = cb_monitor.cget("values")
            if vals:
                cb_monitor.set(vals[0])
                update_globals()
    except Exception:
        pass

def toggle_server():
    if server_active.is_set():
        server_active.clear()
        btn_toggle.config(text="Start Streaming Server", bg="#10b981")
        bind_hover(btn_toggle, "#10b981", "#059669")
        status_server.config(text="Stopped", fg="#ef4444")
    else:
        server_active.set()
        btn_toggle.config(text="Stop Streaming Server", bg="#ef4444")
        bind_hover(btn_toggle, "#ef4444", "#dc2626")
        status_server.config(text="Active", fg="#22c55e")

def copy_link_to_clipboard():
    try:
        ips_list = get_all_ips()
        ip_text = "\n".join([f"http://{ip}:5000" for ip in ips_list if ip != "127.0.0.1"])
        root.clipboard_clear()
        root.clipboard_append(ip_text)
        root.update()
        messagebox.showinfo("Clipboard", "Client Connection Links successfully copied!")
    except Exception as e:
        messagebox.showerror("Error", f"Failed to copy to clipboard: {e}")

def pc_run_loop():
    tray_icon.run()

# Configure native System Tray Icon
def setup_system_tray():
    global tray_icon
    
    def show_window(icon, item):
        root.after(0, root.deiconify)
        
    def quit_app(icon, item):
        server_active.clear()
        
        if AIORTC_AVAILABLE and pcs:
            async def close_pcs_and_stop():
                for pc in list(pcs):
                    try: await pc.close()
                    except Exception: pass
                if webrtc_loop:
                    webrtc_loop.stop()
            if webrtc_loop and webrtc_loop.is_running():
                asyncio.run_coroutine_threadsafe(close_pcs_and_stop(), webrtc_loop)
                time.sleep(0.3)
                
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

def populate_monitors():
    try:
        with mss.MSS() as sct:
            mon_list = []
            for i, mon in enumerate(sct.monitors):
                if i == 0: continue
                mon_list.append(f"Monitor {i} ({mon['width']}x{mon['height']})")
            if not mon_list:
                return ["Monitor 1"]
            return mon_list
    except Exception:
        return ["Monitor 1"]

# Hover effect configuration
def bind_hover(widget, normal_bg, hover_bg):
    widget.bind("<Enter>", lambda e: widget.config(bg=hover_bg))
    widget.bind("<Leave>", lambda e: widget.config(bg=normal_bg))

# Windows 11 Acrylic Immersive titlebar configurations
def apply_modern_titlebar(window, bg_color="#0f172a", border_color="#38bdf8"):
    window.update()
    if IS_WINDOWS:
        try:
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(ctypes.c_int(2)), 4
            )
            
            def hex_to_bgr(hex_str):
                hex_str = hex_str.lstrip('#')
                r, g, b = hex_str[0:2], hex_str[2:4], hex_str[4:6]
                return int(f"0x{b}{g}{r}", 16)
            
            DWMWA_CAPTION_COLOR = 35
            cap_color_int = hex_to_bgr(bg_color)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(ctypes.c_int(cap_color_int)), 4
            )
            
            DWMWA_BORDER_COLOR = 34
            border_color_int = hex_to_bgr(border_color)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_BORDER_COLOR, ctypes.byref(ctypes.c_int(border_color_int)), 4
            )
            
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(ctypes.c_int(2)), 4
            )
        except Exception as e:
            print(f"[DEBUG] Fluent titlebar error: {e}")

# Fluent Stepper configuration (With keyboard typing limitations bypassed)
def create_modern_stepper(parent, default_val, increment, update_cb):
    stepper_frame = tk.Frame(parent, bg="#1e293b")
    
    val_var = tk.StringVar(value=str(default_val))
    
    def on_change(*args):
        update_cb()
    
    def step_down():
        try:
            val = int(val_var.get()) - increment
            val_var.set(str(val))
            on_change()
        except Exception:
            pass
            
    def step_up():
        try:
            val = int(val_var.get()) + increment
            val_var.set(str(val))
            on_change()
        except Exception:
            pass
    
    btn_dec = tk.Button(stepper_frame, text="-", font=("Segoe UI", 10, "bold"), bg="#334155", fg="#f8fafc", activebackground="#475569", activeforeground="#f8fafc", bd=0, width=3, height=1, cursor="hand2", command=step_down)
    btn_dec.pack(side="left")
    bind_hover(btn_dec, "#334155", "#475569")
    

    entry_val = tk.Entry(stepper_frame, textvariable=val_var, font=("Segoe UI", 10, "bold"), bg="#0f172a", fg="#f8fafc", insertbackground="#f8fafc", bd=0, width=5, justify="center")
    entry_val.pack(side="left", padx=2, fill="y")
    entry_val.bind("<KeyRelease>", lambda e: on_change())
    
    btn_inc = tk.Button(stepper_frame, text="+", font=("Segoe UI", 10, "bold"), bg="#334155", fg="#f8fafc", activebackground="#475569", activeforeground="#f8fafc", bd=0, width=3, height=1, cursor="hand2", command=step_up)
    btn_inc.pack(side="left")
    bind_hover(btn_inc, "#334155", "#475569")
    
    return stepper_frame, entry_val

# Tkinter Window Configuration
root = tk.Tk()
root.title("Spacedesk Python Server")
root.geometry("580x640")
root.configure(bg="#0f172a")

# Configure modern dark styled Comboboxes
style = ttk.Style()
style.theme_use('clam')
style.configure('TCombobox', 
                fieldbackground='#0f172a', 
                background='#334155', 
                foreground='#f8fafc',
                bordercolor='#475569',
                arrowcolor='#f8fafc',
                relief='flat')
style.map('TCombobox', 
          fieldbackground=[('readonly', '#0f172a')], 
          foreground=[('readonly', '#f8fafc')])

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
            try: os.remove(temp_png)
            except Exception: pass
    except Exception as e:
        print(f"[DEBUG] Error loading titlebar icon: {e}")

# Header titlebar
lbl_header = tk.Label(root, text="SPACEDESK PYTHON TRANSMITTER", font=("Consolas", 14, "bold"), bg="#0f172a", fg="#38bdf8")
lbl_header.pack(pady=12)

canvas_line = tk.Canvas(root, height=1, bg="#38bdf8", highlightthickness=0, bd=0)
canvas_line.pack(fill="x", padx=30, pady=2)

# Card 1: System Status
card_frame = tk.Frame(root, bg="#16223f", highlightthickness=1, highlightbackground="#334155", bd=0)
card_frame.pack(fill="x", padx=25, pady=10)

container_inner = tk.Frame(card_frame, bg="#16223f", padx=15, pady=12)
container_inner.pack(fill="both")

lbl_status_title = tk.Label(container_inner, text="Service Status:", font=("Segoe UI", 9, "bold"), bg="#16223f", fg="#94a3b8")
lbl_status_title.grid(row=0, column=0, sticky="w", pady=3)

status_server = tk.Label(container_inner, text="Active", font=("Segoe UI", 9, "bold"), bg="#16223f", fg="#22c55e")
status_server.grid(row=0, column=1, sticky="w", padx=10, pady=3)

lbl_ip_title = tk.Label(container_inner, text="Client Connection Link:", font=("Segoe UI", 9, "bold"), bg="#16223f", fg="#94a3b8")
lbl_ip_title.grid(row=1, column=0, sticky="nw", pady=3)

ips = get_all_ips()
ip_text = "\n".join([f"http://{ip}:5000" for ip in ips if ip != "127.0.0.1"])
lbl_ips = tk.Label(container_inner, text=ip_text, font=("Consolas", 10, "bold"), bg="#16223f", fg="#38bdf8", justify="left")
lbl_ips.grid(row=1, column=1, sticky="w", padx=10, pady=3)

# Add clipboard copy action directly inside status card
btn_copy_link = tk.Button(container_inner, text="📋 Copy Link", font=("Segoe UI", 8, "bold"), bg="#3b82f6", fg="white", 
                          activebackground="#2563eb", activeforeground="white", bd=0, padx=6, pady=2, cursor="hand2", command=copy_link_to_clipboard)
btn_copy_link.grid(row=2, column=1, sticky="w", padx=10, pady=5)
bind_hover(btn_copy_link, "#3b82f6", "#2563eb")

# Card 2: Configuration settings panel
settings_frame = tk.Frame(root, bg="#1e293b", highlightthickness=1, highlightbackground="#334155", bd=0)
settings_frame.pack(fill="x", padx=25, pady=10)

settings_inner = tk.Frame(settings_frame, bg="#1e293b", padx=15, pady=15)
settings_inner.pack(fill="both")

settings_inner.columnconfigure(0, weight=1)
settings_inner.columnconfigure(1, weight=2)
settings_inner.columnconfigure(2, weight=1)
settings_inner.columnconfigure(3, weight=2)

lbl_proto = tk.Label(settings_inner, text="Protocol:", font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#94a3b8")
lbl_proto.grid(row=0, column=0, sticky="w", pady=8, padx=2)
cb_protocol = ttk.Combobox(settings_inner, values=["MJPEG (Compatible)", "WebRTC (Ultra-Fast)"], state="readonly", width=15)
cb_protocol.set("MJPEG (Compatible)")
cb_protocol.grid(row=0, column=1, sticky="w", pady=8, padx=2)
cb_protocol.bind("<<ComboboxSelected>>", update_globals)

lbl_engine = tk.Label(settings_inner, text="Engine:", font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#94a3b8")
lbl_engine.grid(row=0, column=2, sticky="w", pady=8, padx=5)
cb_engine = ttk.Combobox(settings_inner, values=["Auto (DXcam / MSS)", "MSS (Standard)", "DXcam (Ultra-High FPS)"], state="readonly", width=15)
cb_engine.set("Auto (DXcam / MSS)")
cb_engine.grid(row=0, column=3, sticky="w", pady=8, padx=2)
cb_engine.bind("<<ComboboxSelected>>", update_globals)

lbl_mon = tk.Label(settings_inner, text="Monitor:", font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#94a3b8")
lbl_mon.grid(row=1, column=0, sticky="w", pady=8, padx=2)

monitors_available = populate_monitors()
cb_monitor = ttk.Combobox(settings_inner, values=monitors_available, state="readonly", width=15)
default_monitor_set = False
for mon_item in monitors_available:
    if "Monitor 2" in mon_item:
        cb_monitor.set(mon_item)
        default_monitor_set = True
        break
if not default_monitor_set and len(monitors_available) > 0:
    cb_monitor.set(monitors_available[0])

cb_monitor.grid(row=1, column=1, sticky="w", pady=8, padx=2)
cb_monitor.bind("<<ComboboxSelected>>", update_globals)

lbl_res = tk.Label(settings_inner, text="Resolution:", font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#94a3b8")
lbl_res.grid(row=1, column=2, sticky="w", pady=8, padx=5)
cb_resolution = ttk.Combobox(settings_inner, values=["Native", "3840x2160", "2560x1440", "1920x1080", "1280x720", "1024x768", "800x600"], state="readonly", width=15)
cb_resolution.set("Native")
cb_resolution.grid(row=1, column=3, sticky="w", pady=8, padx=2)
cb_resolution.bind("<<ComboboxSelected>>", update_globals)

# Quality & Max FPS input boxes (Limitations are entirely removed)
lbl_qual = tk.Label(settings_inner, text="Quality (Unbounded):", font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#94a3b8")
lbl_qual.grid(row=2, column=0, sticky="w", pady=8, padx=2)

stepper_quality_frame, entry_quality = create_modern_stepper(settings_inner, 100, 5, update_globals)
stepper_quality_frame.grid(row=2, column=1, sticky="w", pady=8, padx=2)

lbl_fps = tk.Label(settings_inner, text="Max FPS (Unbounded):", font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#94a3b8")
lbl_fps.grid(row=2, column=2, sticky="w", pady=8, padx=5)

stepper_fps_frame, entry_fps = create_modern_stepper(settings_inner, 30, 1, update_globals)
stepper_fps_frame.grid(row=2, column=3, sticky="w", pady=8, padx=2)

# Dynamic Bitrate settings panel (Mbps)
lbl_bitrate = tk.Label(settings_inner, text="Bitrate (Mbps):", font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#94a3b8")
lbl_bitrate.grid(row=3, column=0, sticky="w", pady=8, padx=2)

stepper_bitrate_frame, entry_bitrate = create_modern_stepper(settings_inner, 10, 2, update_globals)
stepper_bitrate_frame.grid(row=3, column=1, sticky="w", pady=8, padx=2)

var_auto_sync = tk.BooleanVar(value=True)
chk_auto_sync = tk.Checkbutton(settings_inner, text="Auto-Sync Client Resolution (Hardware Adaptation)", variable=var_auto_sync, font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#cbd5e1", selectcolor="#0f172a", activebackground="#1e293b", activeforeground="#f8fafc", bd=0, cursor="hand2", command=update_globals)
chk_auto_sync.grid(row=4, column=0, columnspan=4, sticky="w", pady=10, padx=2)

update_globals()

# Bottom Actions Control frame
actions_frame = tk.Frame(root, bg="#0f172a")
actions_frame.pack(pady=15)

btn_toggle = tk.Button(actions_frame, text="Stop Streaming Server", font=("Segoe UI", 10, "bold"), bg="#ef4444", fg="white", 
                       activebackground="#dc2626", activeforeground="white", bd=0, padx=18, pady=8, cursor="hand2", command=toggle_server)
btn_toggle.pack(side="left", padx=12)
bind_hover(btn_toggle, "#ef4444", "#dc2626")

btn_refresh_web = tk.Button(actions_frame, text="🔄 Refresh Client", font=("Segoe UI", 10, "bold"), bg="#0284c7", fg="white", 
                           activebackground="#0369a1", activeforeground="white", bd=0, padx=18, pady=8, cursor="hand2", command=trigger_web_refresh)
btn_refresh_web.pack(side="left", padx=12)
bind_hover(btn_refresh_web, "#0284c7", "#0369a1")

lbl_guide = tk.Label(root, text="Quick Guide:\n1. Press Win+P on your keyboard and choose 'Extend' to project.\n2. Open the URL above in your second device's browser.", 
                     font=("Segoe UI", 9), bg="#0f172a", fg="#64748b", justify="center")
lbl_guide.pack(pady=5)

def on_close_button():
    root.withdraw()

root.protocol("WM_DELETE_WINDOW", on_close_button)

# Apply transparent design window properties
apply_modern_titlebar(root, bg_color="#0f172a", border_color="#38bdf8")

# Launch system tray and main console window loop
setup_system_tray()
root.mainloop()