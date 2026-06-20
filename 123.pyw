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

# وارد کردن ملزومات سیستم ترای (System Tray)
import pystray
from pystray import MenuItem as item

# وارد کردن فریم‌ورک فلاسک و ملزومات آن
from flask import Flask, Response, request, jsonify, render_template_string

# غیرفعال کردن لاگ‌های متوالی Flask در ترمینال جهت بهینه‌سازی سرعت
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)
server_active = True
tray_icon = None

# تعریف آدرس‌های فایل لوگو و آیکون
LOGO_PNG = r"C:\Users\sina hamidi\Desktop\New folder (3)\Image.png"
LOGO_ICO = r"C:\Users\sina hamidi\Desktop\New folder (3)\Image.ico"

# ارتقای الگوریتم مقیاس‌گذاری به LANCZOS (باکیفیت‌ترین فیلتر حفظ جزئیات متون و تصاویر)
try:
    RESAMPLE_METHOD = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE_METHOD = Image.LANCZOS

# ساختارهای ویندوزی برای استخراج نشانگر واقعی ماوس بدون نیاز به نصب کتابخانه اضافی
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

IS_WINDOWS = sys.platform.startswith('win')
user32 = ctypes.windll.user32 if IS_WINDOWS else None
gdi32 = ctypes.windll.gdi32 if IS_WINDOWS else None

# حل مشکل لوگوی تسکبار در ویندوز (تثبیت اختصاصی آیکون فرآیند پایتون)
if IS_WINDOWS:
    try:
        myappid = 'spacedesk.python.server.v1'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

# پیدا کردن لیست آی‌پی‌های فعال سیستم
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

# تابع کپچر مانیتور با اعمال زنده رزولوشن، فریم‌ریت و نشانگر ماوس واقعی سیستم
def gen_frames(monitor_idx, target_res, quality, fps):
    delay = 1.0 / fps
    with mss.MSS() as sct:
        if monitor_idx >= len(sct.monitors):
            monitor_idx = 1
            
        monitor = sct.monitors[monitor_idx]
        
        while server_active:
            start_time = time.time()
            sct_img = sct.grab(monitor)
            
            # تبدیل به فرمت تصویر خام
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            
            # ---- ترسیم هوشمند و واقعی ماوس اصلی سیستم با متد Real Background ----
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
            # -------------------------------------------
            
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

@app.route('/')
def index():
    html_template = """
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Spacedesk Web Receiver</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            .glass {
                background: rgba(15, 23, 42, 0.85);
                backdrop-filter: blur(16px);
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            .hide-cursor { cursor: none; }
        </style>
    </head>
    <body class="bg-slate-950 text-slate-100 font-sans overflow-hidden select-none h-screen w-screen flex flex-col justify-between">
        <div class="relative w-full h-full flex items-center justify-center bg-black overflow-hidden" id="video-container">
            <img id="screen-stream" class="w-full h-full object-fill transition-all" alt="Screen Stream">
            <div id="loading" class="absolute inset-0 flex flex-col items-center justify-center bg-slate-950 z-10 transition-opacity duration-300">
                <div class="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-blue-500 mb-4"></div>
                <p class="text-lg text-slate-300">در حال متصل شدن به فرستنده...</p>
            </div>
        </div>

        <div id="control-bar" class="absolute bottom-6 left-1/2 -translate-x-1/2 glass px-6 py-4 rounded-2xl flex flex-wrap items-center gap-6 shadow-2xl transition-all duration-300 z-20">
            <div class="flex flex-col gap-1">
                <label class="text-xs text-slate-400 font-medium">نمایشگر خروجی</label>
                <select id="monitor-select" class="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500">
                </select>
            </div>

            <div class="flex flex-col gap-1">
                <label class="text-xs text-slate-400 font-medium">رزولوشن استریم</label>
                <select id="res-select" class="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500">
                    <option value="Native" selected>کیفیت اصلی (بالا)</option>
                    <option value="3840x2160">3840x2160 (4K UltraHD)</option>
                    <option value="2560x1440">2560x1440 (2K WQHD)</option>
                    <option value="1920x1080">1920x1080 (FullHD)</option>
                    <option value="1280x720">1280x720 (HD - بهینه)</option>
                    <option value="1024x768">1024x768 (مربع)</option>
                    <option value="800x600">800x600 (حداکثر سرعت)</option>
                </select>
            </div>

            <div class="flex flex-col gap-1">
                <label class="text-xs text-slate-400 font-medium">تناسب تصویر (Fit)</label>
                <select id="scale-select" class="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500">
                    <option value="fill" selected>کشش تمام‌صفحه (Fill Stretch)</option>
                    <option value="contain">حفظ ابعاد اصلی (اصلی)</option>
                </select>
            </div>

            <div class="flex flex-col gap-1">
                <div class="flex justify-between text-xs text-slate-400 font-medium">
                    <span>کیفیت فشرده‌سازی</span>
                    <span id="quality-val">100%</span>
                </div>
                <input type="range" id="quality-slider" min="10" max="100" value="100" class="w-24 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500">
            </div>

            <div class="flex flex-col gap-1">
                <div class="flex justify-between text-xs text-slate-400 font-medium">
                    <span>حداکثر فریم (FPS)</span>
                    <span id="fps-val">30</span>
                </div>
                <input type="range" id="fps-slider" min="5" max="60" value="30" class="w-24 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500">
            </div>

            <button id="fullscreen-btn" class="bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold px-4 py-2 rounded-xl transition-all">
                تمام صفحه
            </button>
        </div>

        <script>
            const streamImg = document.getElementById('screen-stream');
            const loading = document.getElementById('loading');
            const monitorSelect = document.getElementById('monitor-select');
            const resSelect = document.getElementById('res-select');
            const scaleSelect = document.getElementById('scale-select');
            const qualitySlider = document.getElementById('quality-slider');
            const qualityVal = document.getElementById('quality-val');
            const fpsSlider = document.getElementById('fps-slider');
            const fpsVal = document.getElementById('fps-val');
            const fullscreenBtn = document.getElementById('fullscreen-btn');
            const videoContainer = document.getElementById('video-container');
            const controlBar = document.getElementById('control-bar');

            let activeMonitor = 2; 
            let activeRes = "Native"; 
            let activeQuality = 100;  
            let activeFPS = 30;       

            async function fetchMonitors() {
                try {
                    const res = await fetch('/api/monitors');
                    const list = await res.json();
                    monitorSelect.innerHTML = '';
                    list.forEach(m => {
                        const opt = document.createElement('option');
                        opt.value = m.id;
                        opt.textContent = `نمایشگر ${m.id} (${m.width}x${m.height})`;
                        if (m.id === 2) { opt.selected = true; }
                        monitorSelect.appendChild(opt);
                    });
                    
                    const monitor2Exists = list.some(m => m.id === 2);
                    if (monitor2Exists) {
                        activeMonitor = 2;
                    } else if (list.length > 0) {
                        activeMonitor = list[0].id;
                    }
                    updateStream();
                } catch (e) { console.error(e); }
            }

            function updateStream() {
                loading.classList.remove('hidden');
                loading.style.opacity = '1';
                streamImg.onload = () => {
                    loading.style.opacity = '0';
                    setTimeout(() => loading.classList.add('hidden'), 300);
                };
                const url = `/video_feed?monitor=${activeMonitor}&res=${activeRes}&quality=${activeQuality}&fps=${activeFPS}&_t=${Date.now()}`;
                streamImg.src = url;
            }

            monitorSelect.addEventListener('change', (e) => { activeMonitor = e.target.value; updateStream(); });
            resSelect.addEventListener('change', (e) => { activeRes = e.target.value; updateStream(); });
            scaleSelect.addEventListener('change', (e) => {
                if(e.target.value === 'fill') {
                    streamImg.className = 'w-full h-full object-fill';
                } else {
                    streamImg.className = 'w-full h-full object-contain';
                }
            });
            qualitySlider.addEventListener('input', (e) => { activeQuality = e.target.value; qualityVal.textContent = activeQuality + '%'; });
            qualitySlider.addEventListener('change', () => updateStream());
            fpsSlider.addEventListener('input', (e) => { activeFPS = e.target.value; fpsVal.textContent = activeFPS; });
            fpsSlider.addEventListener('change', () => updateStream());

            fullscreenBtn.addEventListener('click', () => {
                if (!document.fullscreenElement) { videoContainer.requestFullscreen(); } else { document.exitFullscreen(); }
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

# استارت زدن فلاسک در ترد مجزا
def start_flask():
    app.run(host='0.0.0.0', port=5000, threaded=True, debug=False)

flask_thread = threading.Thread(target=start_flask, daemon=True)
flask_thread.start()

# --- توابع رابط کاربری دسکتاپ (GUI Callbacks) ---
def install_virtual_display():
    status_label_install.config(text="در حال نصب درایور... لطفاً منتظر بمانید", fg="#eab308")
    root.update()
    
    def run_installer():
        try:
            cmd = ["winget", "install", "--id=VirtualDrivers.Virtual-Display-Driver", "-e", "--accept-source-agreements", "--accept-package-agreements"]
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=True)
            stdout, stderr = process.communicate()
            if process.returncode == 0:
                root.after(0, lambda: messagebox.showinfo("موفقیت", "درایور با موفقیت نصب شد!\nاکنون کلیدهای Win + P را بزنید و حالت Extend را فعال کنید."))
                root.after(0, lambda: status_label_install.config(text="درایور نصب شده است.", fg="#22c55e"))
            else:
                root.after(0, lambda: messagebox.showerror("خطا", f"خطا در نصب:\n{stderr or stdout}"))
                root.after(0, lambda: status_label_install.config(text="خطا در نصب درایور مانیتور مجازی", fg="#ef4444"))
        except Exception as e:
            root.after(0, lambda: messagebox.showerror("خطا", f"دستور اجرا نشد: {e}"))
            root.after(0, lambda: status_label_install.config(text="خطا در اجرای فرآیند نصب", fg="#ef4444"))
            
    threading.Thread(target=run_installer, daemon=True).start()

def toggle_server():
    global server_active
    server_active = not server_active
    if server_active:
        btn_toggle.config(text="خاموش کردن سرور استریم", bg="#ef4444", activebackground="#dc2626")
        status_server.config(text="فعال (Active)", fg="#22c55e")
    else:
        btn_toggle.config(text="روشن کردن سرور استریم", bg="#22c55e", activebackground="#16a34a")
        status_server.config(text="غیرفعال (Stopped)", fg="#ef4444")

# لوپ اجرای بومی سیستم ترای
def pc_run_loop():
    tray_icon.run()

# راه‌اندازی سیستم ترای
def setup_system_tray():
    global tray_icon
    
    def show_window(icon, item):
        root.after(0, root.deiconify) # نمایش پنجره برنامه
        
    def quit_app(icon, item):
        global server_active
        server_active = False
        icon.stop() # بستن آیکون ترای
        root.after(0, root.destroy) # تخریب پنجره Tkinter
        
        # برای خروج بی صدا و آنی از سیستم چندرشته‌ای بدون ثبت خطا در ماژول ctypes
        os._exit(0)

    try:
        tray_img = Image.open(LOGO_PNG)
    except Exception:
        tray_img = Image.new("RGB", (32, 32), "#0f172a")

    menu = (
        item('نمایش برنامه (Show)', show_window, default=True),
        item('خروج کامل (Exit)', quit_app)
    )
    
    tray_icon = pystray.Icon("spacedesk_python", tray_img, "Spacedesk Python Server", menu)
    
    tray_thread = threading.Thread(target=pc_run_loop, daemon=True)
    tray_thread.start()


# ساخت پنجره اصلی GUI
root = tk.Tk()
root.title("Spacedesk Python Server")
root.geometry("540x540")
root.configure(bg="#0f172a")

# اعمال ماوس واقعی ویندوز به عنوان آیکون برنامه و لود کردن آن
if IS_WINDOWS:
    try:
        img_icon = tk.PhotoImage(file=LOGO_PNG)
        root.iconphoto(False, img_icon)
    except Exception as e:
        print(f"Error loading Titlebar Icon: {e}")

# هدر برنامه
lbl_header = tk.Label(root, text="کنسول مدیریتی مانیتور دوم (پایتون)", font=("Segoe UI", 14, "bold"), bg="#0f172a", fg="#f8fafc")
lbl_header.pack(pady=15)

# کارت اطلاعات سرور
card_frame = tk.Frame(root, bg="#1e293b", bd=0, padx=15, pady=15)
card_frame.pack(fill="x", padx=20, pady=5)

lbl_status_title = tk.Label(card_frame, text="وضعیت سرویس محلی:", font=("Segoe UI", 10), bg="#1e293b", fg="#94a3b8")
lbl_status_title.grid(row=0, column=0, sticky="w", pady=5)

status_server = tk.Label(card_frame, text="فعال (Active)", font=("Segoe UI", 10, "bold"), bg="#1e293b", fg="#22c55e")
status_server.grid(row=0, column=1, sticky="w", padx=10, pady=5)

lbl_ip_title = tk.Label(card_frame, text="آدرس اتصال لپ‌تاپ دوم (کلاینت):", font=("Segoe UI", 10), bg="#1e293b", fg="#94a3b8")
lbl_ip_title.grid(row=1, column=0, sticky="nw", pady=5)

# نمایش تمامی کارت‌های شبکه فعال سیستم
ips = get_all_ips()
ip_text = "\n".join([f"http://{ip}:5000" for ip in ips if ip != "127.0.0.1"])
lbl_ips = tk.Label(card_frame, text=ip_text, font=("Consolas", 11, "bold"), bg="#1e293b", fg="#38bdf8", justify="left")
lbl_ips.grid(row=1, column=1, sticky="w", padx=10, pady=5)

# دکمه خاموش/روشن کردن استریم
btn_toggle = tk.Button(root, text="خاموش کردن سرور استریم", font=("Segoe UI", 10, "bold"), bg="#ef4444", fg="white", 
                       activebackground="#dc2626", activeforeground="white", bd=0, padx=20, pady=8, cursor="hand2", command=toggle_server)
btn_toggle.pack(pady=15)

# بخش نصب مانیتور مجازی
card_driver_frame = tk.Frame(root, bg="#1e293b", bd=0, padx=15, pady=15)
card_driver_frame.pack(fill="x", padx=20, pady=5)

lbl_driver_info = tk.Label(card_driver_frame, text="سیستم شما مانیتور دوم واقعی ندارد? ", font=("Segoe UI", 11, "bold"), bg="#1e293b", fg="#f8fafc")
lbl_driver_info.pack(anchor="w", pady=2)

lbl_driver_desc = tk.Label(card_driver_frame, text="جهت ایجاد مانیتور دوم مجازی به صورت نرم‌افزاری، روی دکمه زیر کلیک کنید تا درایور سیستمی ویندوز نصب شود.", 
                           font=("Segoe UI", 9), bg="#1e293b", fg="#94a3b8", wraplength=460, justify="right")
lbl_driver_desc.pack(anchor="w", pady=5)

btn_install = tk.Button(card_driver_frame, text="نصب خودکار مانیتور مجازی (ویندوز ۱۰/۱۱)", font=("Segoe UI", 9, "bold"), bg="#2563eb", fg="white", 
                        activebackground="#1d4ed8", activeforeground="white", bd=0, padx=15, pady=6, cursor="hand2", command=install_virtual_display)
btn_install.pack(pady=5)

status_label_install = tk.Label(card_driver_frame, text="آماده برای نصب درایور", font=("Segoe UI", 9), bg="#1e293b", fg="#94a3b8")
status_label_install.pack(pady=2)

# راهنمای سریع استفاده
lbl_guide = tk.Label(root, text="راهنمای سریع:\n۱. نصب درایور مانیتور مجازی -> ۲. فشردن Win+P و فعال کردن Extend\n۳. وارد کردن آدرس آبی رنگ بالا در مرورگر لپ‌تاپ دوم", 
                     font=("Segoe UI", 9), bg="#0f172a", fg="#64748b", justify="center")
lbl_guide.pack(pady=20)

# هندل کردن دکمه بستن ویندوز (X) به عنوان مخفی شدن به سمت سیستم ترای
def on_close_button():
    root.withdraw() # مخفی کردن پنجره (بدون بستن فرآیند پایتون)

root.protocol("WM_DELETE_WINDOW", on_close_button)

# اجرای نهایی سیستم ترای و منوی گرافیکی
setup_system_tray()
root.mainloop()