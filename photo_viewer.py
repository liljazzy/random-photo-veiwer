import json
import queue
import math
import os
import re
import random
import ctypes
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
import tkinter as tk
import urllib.request
from tkinter import filedialog, messagebox, simpledialog

from PIL import Image, ImageOps, ImageTk

APP_NAME = "Photo Viewer"
APP_VERSION = "1.4.0"
# Point this at a JSON file you host: {"version": "1.1.0", "url": "https://.../PhotoViewer.exe"}
UPDATE_URL = "https://raw.githubusercontent.com/liljazzy/random-photo-veiwer/main/version.json"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PhotoViewer"

BOUNCE_LEVELS = {"Off": 0.0, "Low": 0.5, "Normal": 0.85, "High": 1.0, "Super": 1.2}

SIZE_LEVELS = (40, 50, 67, 80, 100, 130, 160)  # percent of the full fit-to-screen size
DEFAULT_SIZE = 67

EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


def place_window(win, x, y):
    # Win32 move: Tk ignores position changes on borderless windows.
    try:
        hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), 2)
        # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
        ctypes.windll.user32.SetWindowPos(hwnd, 0, round(x), round(y), 0, 0, 0x0001 | 0x0004 | 0x0010 | 0x4000)
    except Exception:
        win.geometry(f"+{round(x)}+{round(y)}")


class Body:
    """A window that can be dragged and thrown, bouncing off the screen edges and other windows."""

    def __init__(self, app, win, blocked=lambda: False):
        self.app, self.win, self.blocked = app, win, blocked
        self.pos = [float(win.winfo_x()), float(win.winfo_y())]
        self.vel = [0.0, 0.0]
        self.held = False
        self.placed = None
        self.samples = deque(maxlen=8)
        self.pull = (0.0, 0.0)
        self.rmoved = False
        self._sz = None       # size measured once per physics frame
        self.known = None     # size just set by us, trusted until Tk catches up
        self.known_until = 0.0

    def measure(self):
        return max(self.win.winfo_width(), 1), max(self.win.winfo_height(), 1)

    def size(self):
        if self._sz:
            return self._sz
        if self.known and time.perf_counter() < self.known_until:
            return self.known
        return self.measure()

    def set_known(self, w, h):
        self.known, self.known_until = (w, h), time.perf_counter() + 0.2

    def place(self):
        key = (round(self.pos[0]), round(self.pos[1]))
        if key != self.placed:
            self.placed = key
            place_window(self.win, *self.pos)

    def bind(self, widget, on_click, on_right_click):
        """Left-click = on_click, right-click = on_right_click, right-drag or Alt+drag = move/throw."""
        widget.bind("<Button-1>", lambda e: None if e.state & 0x20000 else on_click())
        widget.bind("<Alt-Button-1>", self.start_drag)
        widget.bind("<Alt-B1-Motion>", self.drag)
        widget.bind("<ButtonRelease-1>", self.end_drag)
        widget.bind("<ButtonPress-3>", self._rpress)
        widget.bind("<B3-Motion>", self._rdrag)
        widget.bind("<ButtonRelease-3>", lambda e: self._rrelease(e, on_right_click))

    def _rpress(self, e):
        self.start_drag(e)
        self.rmoved = False
        self.rstart = (e.x_root, e.y_root)

    def _rdrag(self, e):
        if abs(e.x_root - self.rstart[0]) + abs(e.y_root - self.rstart[1]) > 4:
            self.rmoved = True
        if self.rmoved:
            self.drag(e)

    def _rrelease(self, e, on_right_click):
        if self.rmoved:
            self.end_drag()
        else:
            self.held = False
            on_right_click()

    def start_drag(self, e):
        if self.blocked():
            return
        self.vel = [0.0, 0.0]
        self.dx, self.dy = e.x_root - self.pos[0], e.y_root - self.pos[1]
        self.samples = deque(maxlen=8)
        self.held = True
        self.pull = (0.0, 0.0)
        mx, my, mw, mh = self.app.monitor_rect()
        w, h = self.size()
        self.wall = (mx, my, mx + mw - w, my + mh - h)

    def drag(self, e):
        if self.blocked() or not self.held:
            return
        self.samples.append((time.perf_counter(), e.x_root, e.y_root))
        x, y = e.x_root - self.dx, e.y_root - self.dy
        l, t, r, b = self.wall
        cx, cy = min(max(x, l), r), min(max(y, t), b)
        # The window stops at the screen edge; the cursor carrying on past it is the bowstring.
        self.pull = (x - cx, y - cy)
        self.pos = [cx, cy]
        self.place()
        if len(self.app.live_bodies()) > 1:
            self.app.start_physics()  # a held window shoves the others around

    def drag_velocity(self):
        now = time.perf_counter()
        recent = [q for q in self.samples if now - q[0] < 0.1]
        if len(recent) < 2 or recent[-1][0] == recent[0][0]:
            return 0.0, 0.0
        dt = recent[-1][0] - recent[0][0]
        vx, vy = (recent[-1][1] - recent[0][1]) / dt, (recent[-1][2] - recent[0][2]) / dt
        speed = (vx * vx + vy * vy) ** 0.5
        if speed > 5000:
            vx, vy = vx * 5000 / speed, vy * 5000 / speed
        return vx, vy

    def end_drag(self, e=None):
        """Let go of a drag: fling the window if the cursor was still moving."""
        if not self.held:
            return
        self.held = False
        px, py = self.pull
        self.pull = (0.0, 0.0)
        if (px * px + py * py) ** 0.5 > 15:
            # Released while pulled against a wall: slingshot away from it (double force).
            vx, vy = -px * 28, -py * 28
            speed = (vx * vx + vy * vy) ** 0.5
            if speed > 10000:
                vx, vy = vx * 10000 / speed, vy * 10000 / speed
            self.vel = [vx, vy]
        else:
            vx, vy = self.drag_velocity()
            if (vx * vx + vy * vy) ** 0.5 >= 150:
                self.vel = [vx, vy]
        self.app.start_physics()


def calc_fit_size(iw, ih, sw, sh, pct):
    """Window size for a photo of this shape on a screen of sw x sh, at pct% of the full fit."""
    fit = min(sw * 0.9 / iw, sh * 0.9 / ih)
    scale = max(min(fit, 1), min(fit, 500 / max(iw, ih)))  # upscale small photos a bit
    scale = min(scale * pct / 100, fit)  # base size setting, never bigger than the screen
    return max(int(iw * scale), 200), max(int(ih * scale), 150)


class Preloader(threading.Thread):
    """Decodes and resizes random photos in the background so swapping one in is instant."""

    def __init__(self, files, sw, sh, pct):
        super().__init__(daemon=True)
        self.files, self.sw, self.sh, self.pct = files, sw, sh, pct
        self.q = queue.Queue(maxsize=8)
        self.running = True

    def run(self):
        while self.running:
            try:
                path = random.choice(self.files)
                img = Image.open(path)
                iw, ih = img.size
                if img.getexif().get(274, 1) in (5, 6, 7, 8):  # rotated photo: width and height swap
                    iw, ih = ih, iw
                nw, nh = calc_fit_size(iw, ih, self.sw, self.sh, self.pct)
                try:
                    img.draft("RGB", (nw, nh))  # JPEGs decode at reduced size
                except Exception:
                    pass
                img = ImageOps.exif_transpose(img).convert("RGB").resize((nw, nh), Image.BICUBIC)
                item = (path, img, (nw, nh))
            except Exception:
                time.sleep(0.05)
                continue
            while self.running:
                try:
                    self.q.put(item, timeout=0.2)
                    break
                except queue.Full:
                    pass

    def get(self):
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None

    def stop(self):
        self.running = False


SAVER_DEFAULTS = {"windows": 5, "size": 30, "speed": 350, "trigger": "collision", "secs": 4}


def settings_path():
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PhotoViewer", "settings.json")


def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(**values):
    data = {**load_settings(), **values}
    try:
        os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def saver_config():
    cfg = dict(SAVER_DEFAULTS)
    saved = load_settings().get("saver")
    if isinstance(saved, dict):
        for k, default in SAVER_DEFAULTS.items():
            if isinstance(saved.get(k), type(default)) or (k != "trigger" and isinstance(saved.get(k), (int, float))):
                cfg[k] = saved[k]
    cfg["trigger"] = cfg["trigger"] if cfg["trigger"] in ("collision", "timer") else "collision"
    return cfg


def build_saver_settings(win, on_preview=None):
    """Fill `win` (a window or the root) with the screensaver settings; changes save as you make them."""
    cfg = saver_config()
    bg, fg = "#1b1b1b", "#eee"
    win.title("Screensaver settings")
    win.configure(bg=bg)
    frame = tk.Frame(win, bg=bg, padx=18, pady=14)
    frame.pack(fill="both", expand=True)
    vars_ = {"windows": tk.IntVar(value=cfg["windows"]), "size": tk.IntVar(value=cfg["size"]),
             "speed": tk.IntVar(value=cfg["speed"]), "secs": tk.IntVar(value=cfg["secs"]),
             "trigger": tk.StringVar(value=cfg["trigger"])}

    def save(*_):
        save_settings(saver={k: v.get() for k, v in vars_.items()})

    def slider(row, label, key, lo, hi, unit):
        tk.Label(frame, text=label, bg=bg, fg=fg, anchor="w").grid(row=row, column=0, sticky="w", pady=6)
        sc = tk.Scale(frame, from_=lo, to=hi, orient="horizontal", variable=vars_[key], length=240,
                      bg=bg, fg=fg, troughcolor="#333", highlightthickness=0, showvalue=True)
        sc.grid(row=row, column=1, padx=10)
        sc.bind("<ButtonRelease-1>", save)
        sc.bind("<KeyRelease>", save)
        tk.Label(frame, text=unit, bg=bg, fg="#999").grid(row=row, column=2, sticky="w")

    slider(0, "Photo windows", "windows", 2, 10, "")
    slider(1, "Window size", "size", 10, 60, "% of full")
    slider(2, "Speed", "speed", 100, 1500, "px / second")
    tk.Label(frame, text="Change a photo", bg=bg, fg=fg, anchor="w").grid(row=3, column=0, sticky="nw", pady=(10, 0))
    box = tk.Frame(frame, bg=bg)
    box.grid(row=3, column=1, columnspan=2, sticky="w", pady=(8, 0))
    for value, text in (("collision", "when a window hits another"), ("timer", "on a timer")):
        tk.Radiobutton(box, text=text, value=value, variable=vars_["trigger"], command=save, bg=bg, fg=fg,
                       selectcolor="#333", activebackground=bg, activeforeground=fg).pack(anchor="w")
    slider(4, "Timer interval", "secs", 1, 30, "seconds")
    row = tk.Frame(frame, bg=bg)
    row.grid(row=5, column=0, columnspan=3, pady=(16, 0), sticky="e")
    if on_preview:
        tk.Button(row, text="Preview", bg="#3b82f6", fg="white", relief="flat", padx=14, pady=5,
                  command=lambda: (save(), on_preview())).pack(side="left", padx=6)
    tk.Button(row, text="Close", bg="#2d2d2d", fg=fg, relief="flat", padx=14, pady=5,
              command=lambda: (save(), win.destroy())).pack(side="left")
    win.protocol("WM_DELETE_WINDOW", lambda: (save(), win.destroy()))
    return win


class MenuBar(tk.Frame):
    """The bottom menu: floats over a photo window and scales its buttons to the window's width."""

    def __init__(self, owner, app, body, specs, closer, grip=False):
        super().__init__(owner, bg="#1b1b1b")
        self.owner, self.app = owner, app
        self.shown = False
        self.btns = []  # (button, full-label function, compact label)
        for full, short, cmd in specs:
            b = tk.Button(self, bg="#2d2d2d", fg="#eee", relief="flat")
            b.configure(command=lambda b=b, cmd=cmd: self._press(b, cmd))
            b.pack(side="left")
            self.btns.append((b, full if callable(full) else (lambda t=full: t), short))
        self.info = tk.Label(self, bg="#1b1b1b", fg="#999")
        self.grip = None
        if grip:
            self.grip = tk.Label(self, text="◢", bg="#1b1b1b", fg="#777", cursor="size_nw_se", padx=8)
            self.grip.pack(side="right")
            self.grip.bind("<Button-1>", app.start_resize)
            self.grip.bind("<B1-Motion>", app.do_resize)
        self.close_btn = tk.Button(self, text="✕", command=closer, bg="#2d2d2d", fg="#eee", relief="flat")
        self.close_btn.pack(side="right")
        for w in (self, self.info):  # drag the menu to move the window
            w.bind("<Button-1>", body.start_drag)
            w.bind("<B1-Motion>", body.drag)
            w.bind("<ButtonRelease-1>", body.end_drag)

    def _press(self, button, cmd):
        self.app.anchor_btn, self.app.anchor_menu = button, self  # popup menus open at this button
        cmd()

    def style(self, size, compact):
        font = ("Segoe UI", size)
        for b, full, short in self.btns:
            b.configure(text=short if compact else full(), font=font,
                        padx=size // 2 + 1 if compact else size, pady=size // 2)
            b.pack_configure(padx=max(size // 2, 2), pady=size // 2 + 2)
        self.close_btn.configure(font=font, padx=size, pady=size // 2)
        self.close_btn.pack_configure(padx=max(size // 2, 2))
        if self.grip:
            self.grip.configure(font=font)
        if compact:
            self.info.pack_forget()
        else:
            self.info.configure(font=("Segoe UI", max(size - 1, 6)))
            self.info.pack(side="left", padx=10, after=self.btns[-1][0])

    def scale(self, width=None):
        """Size the text and buttons so everything fits the window's width."""
        w = width or self.owner.winfo_width()
        base = max(7, min(16, round(w / 90)))
        for compact in (False, True):
            for size in range(base, 5 if compact else 7, -1):  # full labels never below 8pt
                self.style(size, compact)
                self.update_idletasks()
                if self.winfo_reqwidth() <= w:
                    return

    def set_shown(self, on):
        if on and not self.shown:
            self.scale()
            self.place(relx=0, rely=1, anchor="sw", relwidth=1)
            self.lift()
            self.shown = True
        elif not on and self.shown:
            self.place_forget()
            self.shown = False


class PhotoWindow(tk.Toplevel):
    """An extra borderless photo window that collides with the others."""

    def __init__(self, app, path, x, y):
        super().__init__(app)
        self.app = app
        self.overrideredirect(True)
        self.configure(bg="#111")
        self.label = tk.Label(self, bg="#111", bd=0, cursor="hand2")
        self.label.pack(fill="both", expand=True)
        self.path, self.history, self.hpos = path, [path], 0
        self.body = Body(app, self, blocked=lambda: app.saver)
        self.menu = MenuBar(self, app, self.body, [
            ("Folder", "📂", app.open_folder), ("◀ Back", "◀", self.prev_photo),
            ("Random ▶", "▶", self.next_photo),
            (app.auto_label, "Auto", app.auto_menu), (app.bounce_label, "⤴", app.bounce_menu),
            (app.size_label, "⤢", app.size_menu), ("＋ Add", "＋", app.add_image)],
            closer=lambda: app.remove_extra(self))
        self.body.bind(self.label, self.next_photo, self.prev_photo)
        self.label.bind("<Button-2>", lambda e: self.reveal())
        self.show(path, x, y)

    def show(self, path, x=None, y=None):
        base = Image.open(path)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        try:
            base.draft("RGB", (sw, sh))
        except Exception:
            pass
        base = ImageOps.exif_transpose(base).convert("RGB")
        nw, nh = self.app.fit_size(*base.size)
        if x is None:  # keep the same centre when the shape changes
            ow, oh = self.body.size()
            x, y = self.body.pos[0] + ow / 2 - nw / 2, self.body.pos[1] + oh / 2 - nh / 2
        mx, my, mw, mh = self.app.monitor_rect()
        x, y = min(max(x, mx), mx + mw - nw), min(max(y, my), my + mh - nh)
        self.tk_img = ImageTk.PhotoImage(base.resize((nw, nh), Image.BICUBIC))
        self.label.configure(image=self.tk_img)
        self.geometry(f"{nw}x{nh}+{round(x)}+{round(y)}")
        self.update_idletasks()
        self.path = path
        self.menu.info.configure(text=os.path.basename(path))
        self.menu.scale(nw)
        self.body.pos = [float(x), float(y)]
        self.body.placed = None
        self.body.place()

    def show_prepared(self, item):
        path, img, (nw, nh) = item
        ow, oh = self.body.size()
        mx, my, mw, mh = self.app.monitor_rect()
        x = min(max(self.body.pos[0] + ow / 2 - nw / 2, mx), mx + mw - nw)
        y = min(max(self.body.pos[1] + oh / 2 - nh / 2, my), my + mh - nh)
        self.tk_img = ImageTk.PhotoImage(img)
        self.label.configure(image=self.tk_img)
        self.geometry(f"{nw}x{nh}")
        self.path = path
        self.history.append(path)
        self.hpos = len(self.history) - 1
        self.menu.info.configure(text=os.path.basename(path))
        self.body.pos, self.body.placed = [float(x), float(y)], None
        self.body.set_known(nw, nh)
        self.body.place()

    def refit(self):
        try:
            self.show(self.path)
        except Exception:
            pass

    def next_photo(self):
        if self.hpos < len(self.history) - 1:
            self.hpos += 1
        elif self.app.files:
            self.history.append(random.choice(self.app.files))
            self.hpos = len(self.history) - 1
        self._show_current()

    def prev_photo(self):
        if self.hpos > 0:
            self.hpos -= 1
            self._show_current()

    def _show_current(self):
        try:
            self.show(self.history[self.hpos])
        except Exception:
            pass

    def reveal(self):
        if os.path.exists(self.path):
            subprocess.Popen(f'explorer /select,"{os.path.normpath(self.path)}"')


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Random Photo Viewer {APP_VERSION}")
        self.geometry("1000x700")
        self.overrideredirect(True)  # borderless
        self.configure(bg="#111")
        self.files = []
        self.history = []
        self.pos = -1
        self.auto_id = None
        self.tk_img = None
        self.is_full = False
        self._cache = None
        self._last_key = None
        self.current = None

        self.canvas = tk.Label(self, bg="#111", fg="#888", cursor="hand2",
                               text="Open a folder to start (Ctrl+O)")
        self.canvas.pack(fill="both", expand=True)

        self.auto_on = False
        self.auto_secs = 4
        saved = self.read_settings()
        self.size_pct = saved.get("size") if isinstance(saved.get("size"), (int, float)) and 10 <= saved["size"] <= 300 else DEFAULT_SIZE
        saved_bounce = saved.get("bounce")
        self.bounce_name = saved_bounce if saved_bounce in BOUNCE_LEVELS else "Normal"
        self.menu_open = False
        self.anchor_btn = self.anchor_menu = None
        self.extras = []  # extra PhotoWindows
        self.saver = False
        self.preloader = None
        self.backdrop = None
        self._contacts = set()
        self._touching = set()
        self._last_change = {}
        self._phys_running = False
        self._phys_id = None
        self._fine_timer = False
        self.update_idletasks()
        self.body = Body(self, self, blocked=lambda: self.is_full or self.saver)
        # Bottom menu floats over the photo and only shows when the cursor is near it.
        self.menu = MenuBar(self, self, self.body, [
            ("Folder", "📂", self.open_folder), ("◀ Back", "◀", self.prev),
            ("Random ▶", "▶", self.next),
            (self.auto_label, "Auto", self.auto_menu), (self.bounce_label, "⤴", self.bounce_menu),
            (self.size_label, "⤢", self.size_menu), ("＋ Add", "＋", self.add_image),
            ("🌙 Saver", "🌙", self.start_screensaver), ("⚙ Setup", "⚙", self.saver_settings),
            ("Fullscreen", "⛶", self.toggle_full),
            ("Updates", "⟳", lambda: self.check_updates(True))],
            closer=self.destroy, grip=True)
        self.info = self.menu.info

        self.bind("<Right>", lambda e: self.next())
        self.bind("<space>", lambda e: self.next())
        self.bind("<Left>", lambda e: self.prev())
        self.bind("<F11>", lambda e: self.toggle_full())
        self.bind("f", lambda e: self.toggle_full())
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-o>", lambda e: self.open_folder())
        self.bind("q", lambda e: self.destroy())
        self.bind("s", lambda e: self.start_screensaver())
        # Re-render once the window has settled; the label's own size lags behind and can
        # still be the old (larger) one mid-resize, which left the photo stuck enlarged.
        self._rid = None
        self.bind("<Configure>", self.on_configure)
        # Click the photo: left = next random photo, right = go back; right-drag / Alt+drag throws.
        self.body.bind(self.canvas, self.next, self.prev)
        # Middle-click: show this photo in File Explorer.
        self.canvas.bind("<Button-2>", lambda e: self.reveal())

        self.after(50, self.show_in_taskbar)
        self.after(300, self.sync_body)
        self.after(100, self.poll_hover)
        self.focus_force()

        saved = self.read_settings().get("folder")
        default = os.path.join(os.path.expanduser("~"), "Pictures")
        for start in (saved, default):  # last chosen folder first
            if start and os.path.isdir(start):
                self.load(start)
                break
        if UPDATE_URL and getattr(sys, "frozen", False):
            self.after(1500, lambda: self.check_updates(False))

    def show_in_taskbar(self):
        # Borderless windows normally vanish from the taskbar; flip the style bits back.
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, (style & ~0x80) | 0x40000)
            self.withdraw()
            self.after(10, self.deiconify)
        except Exception:
            pass

    def poll_hover(self):
        if self.saver:
            return self.after(100, self.poll_hover)
        px, py = self.winfo_pointerxy()
        for win, menu in [(self, self.menu)] + [(ex, ex.menu) for ex in self.extras]:
            x, y, w, h = win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()
            zone = (menu.winfo_height() + 30) if menu.shown else 70
            inside = (x <= px < x + w and y + h - zone <= py < y + h) or (
                self.menu_open and menu is self.anchor_menu)
            menu.set_shown(inside)
        self.after(100, self.poll_hover)

    def sync_body(self):
        # The window manager picks the real start position; learn it once mapped.
        if not self.body.held and not self._phys_running:
            self.body.pos = [float(self.winfo_x()), float(self.winfo_y())]

    def live_bodies(self):
        return ([] if self.is_full else [self.body]) + [ex.body for ex in self.extras]

    def stop_throw(self):
        self.body.vel = [0.0, 0.0]

    def start_physics(self):
        if self._phys_running:
            return
        self._phys_running = True
        self._screen = self.monitor_rect()
        self._tlast = time.perf_counter()
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)  # Windows timers default to ~15ms steps
            self._fine_timer = True
        except Exception:
            self._fine_timer = False
        self.physics_step()

    def stop_physics(self):
        self._phys_running = False
        if self._phys_id:
            self.after_cancel(self._phys_id)
        self._phys_id = None
        if self._fine_timer:
            ctypes.windll.winmm.timeEndPeriod(1)
            self._fine_timer = False

    def physics_step(self):
        self._phys_id = None
        if not self._phys_running:
            return
        bodies = self.live_bodies()
        for bd in bodies:
            bd._sz = bd.size()
        try:
            self._physics_step()
        finally:
            for bd in bodies:
                bd._sz = None

    def _physics_step(self):
        """One frame: slide, bounce off screen edges, bounce windows off each other."""
        now = time.perf_counter()
        dt = min(now - self._tlast, 0.05)
        self._tlast = now
        e = 1.0 if self.saver else BOUNCE_LEVELS[self.bounce_name]
        sx, sy, sw, sh = self._screen
        bodies = self.live_bodies()
        # Sub-step so fast windows can't skip over each other between frames.
        fastest = max([(bd.vel[0] ** 2 + bd.vel[1] ** 2) ** 0.5 for bd in bodies if not bd.held] + [0.0])
        steps = max(1, math.ceil(fastest * dt / 60))
        sub = dt / steps
        damp = 1.0 if self.saver else 0.5 ** sub  # speed halves every second (never in the screensaver)
        overlapping = False
        self._touching = set()
        if self.saver:  # keep everything gliding: no window may crawl to a stop
            for bd in bodies:
                sp = (bd.vel[0] ** 2 + bd.vel[1] ** 2) ** 0.5
                floor = self.saver_cfg["speed"] * 0.5
                if sp < floor:
                    ang = random.uniform(0, 6.2832) if sp == 0 else math.atan2(bd.vel[1], bd.vel[0])
                    bd.vel = [floor * math.cos(ang), floor * math.sin(ang)]
        for _ in range(steps):
            for bd in bodies:
                if bd.held:
                    continue
                w, h = bd.size()
                for i, (lo, hi) in enumerate(((sx, sx + sw - w), (sy, sy + sh - h))):
                    bd.pos[i] += bd.vel[i] * sub
                    if bd.pos[i] < lo:
                        bd.pos[i], bd.vel[i] = lo, abs(bd.vel[i]) * e
                    elif bd.pos[i] > hi:
                        bd.pos[i], bd.vel[i] = hi, -abs(bd.vel[i]) * e
                bd.vel = [max(min(v * damp, 10000.0), -10000.0) for v in bd.vel]
            overlapping = self.collide(bodies, e) or overlapping
        busy = overlapping or self.saver
        if self.saver:
            fresh = self._touching - self._contacts  # pairs that only just touched
            if fresh and self.saver_cfg["trigger"] == "collision":
                wins = {w for pair in fresh for w in pair}
                self.after(0, lambda: self._saver_collided(wins))
        self._contacts = self._touching
        for bd in bodies:
            if bd.held:
                continue
            bd.place()
            if (bd.vel[0] ** 2 + bd.vel[1] ** 2) ** 0.5 >= 25:
                busy = True
        if not busy:
            for bd in bodies:
                if not bd.held:
                    bd.vel = [0.0, 0.0]
            return self.stop_physics()
        try:
            ctypes.windll.dwmapi.DwmFlush()  # wait for the next screen refresh: even frame pacing
        except Exception:
            pass
        self._phys_id = self.after(1, self.physics_step)

    def collide(self, bodies, e):
        """Push overlapping windows apart and bounce them off each other (heavier = bigger area)."""
        sx, sy, sw, sh = self._screen
        hit = False
        for i in range(len(bodies)):
            for j in range(i + 1, len(bodies)):
                a, b = bodies[i], bodies[j]
                aw, ah = a.size()
                bw, bh = b.size()
                ox = min(a.pos[0] + aw, b.pos[0] + bw) - max(a.pos[0], b.pos[0])
                oy = min(a.pos[1] + ah, b.pos[1] + bh) - max(a.pos[1], b.pos[1])
                if ox <= 0 or oy <= 0:
                    continue
                hit = True
                self._touching.add((a.win, b.win))
                ax = 0 if ox < oy else 1  # separate along the shallower overlap
                pen = ox if ax == 0 else oy
                ca = a.pos[ax] + (aw if ax == 0 else ah) / 2
                cb = b.pos[ax] + (bw if ax == 0 else bh) / 2
                n = 1 if cb >= ca else -1  # from a towards b
                # A window being dragged pushes along its direction of travel (so a fast drag
                # can't hop over the other window).
                if a.held and abs(a.drag_velocity()[ax]) > 100:
                    n = 1 if a.drag_velocity()[ax] > 0 else -1
                elif b.held and abs(b.drag_velocity()[ax]) > 100:
                    n = -1 if b.drag_velocity()[ax] > 0 else 1
                ima = 0.0 if a.held else 1.0 / (aw * ah)
                imb = 0.0 if b.held else 1.0 / (bw * bh)
                total = ima + imb
                if total == 0:
                    continue
                a.pos[ax] -= n * pen * ima / total
                b.pos[ax] += n * pen * imb / total
                va = a.drag_velocity()[ax] if a.held else a.vel[ax]
                vb = b.drag_velocity()[ax] if b.held else b.vel[ax]
                vn = (vb - va) * n
                if vn < 0:  # moving towards each other
                    jmp = -(1 + e) * vn / total
                    if not a.held:
                        a.vel[ax] -= n * jmp * ima
                    if not b.held:
                        b.vel[ax] += n * jmp * imb
                    if ax == 0:  # side-on hit: glance off diagonally, up or down by where it struck
                        # Which window ran into the other? (the one closing in faster)
                        hitter, other, hh = (a, b, bh) if va * n >= -vb * n else (b, a, ah)
                        yc = (max(a.pos[1], b.pos[1]) + min(a.pos[1] + ah, b.pos[1] + bh)) / 2  # contact height
                        up = -1 if yc < other.pos[1] + hh / 2 else 1  # top half of the other -> up
                        for bd, away, vdir in ((a, -n, up if hitter is a else -up), (b, n, up if hitter is b else -up)):
                            sp = (bd.vel[0] ** 2 + bd.vel[1] ** 2) ** 0.5
                            if not bd.held and sp > 1:
                                bd.vel = [away * sp / 1.4142, vdir * sp / 1.4142]
        if hit:  # a shove must never push a window off the screen
            for bd in bodies:
                if not bd.held:
                    w, h = bd.size()
                    bd.pos[0] = min(max(bd.pos[0], sx), sx + sw - w)
                    bd.pos[1] = min(max(bd.pos[1], sy), sy + sh - h)
        return hit

    # ---- screensaver ----
    def saver_settings(self):
        win = tk.Toplevel(self)
        build_saver_settings(win, on_preview=lambda: (win.destroy(), self.after(300, self.start_screensaver)))

    def start_screensaver(self, quit_on_exit=False):
        if self.saver:
            return
        if not self.files:
            messagebox.showinfo(APP_NAME, "Choose a photo folder first.")
            return self.open_folder()
        if self.is_full:
            self.toggle_full()
        cfg = self.saver_cfg = saver_config()
        self.saver, self._quit_on_exit = True, quit_on_exit
        self.preloader = Preloader(self.files, self.winfo_screenwidth(), self.winfo_screenheight(), cfg["size"])
        self.preloader.start()
        self._contacts, self._touching = set(), set()
        w0, h0 = self.winfo_width(), self.winfo_height()
        self._saved_state = ((self.body.pos[0] + w0 / 2, self.body.pos[1] + h0 / 2), self.geometry(), len(self.extras))
        self.stop_throw()
        # Smaller windows so five of them have room to glide without constantly colliding.
        self._saved_size = self.size_pct
        self.size_pct = cfg["size"]
        self.render(fit=True)
        for ex in self.extras:
            ex.refit()
        mx, my, mw, mh = self.monitor_rect()
        # Black backdrop over the whole screen; the photo windows float on top of it.
        self.backdrop = tk.Toplevel(self, bg="black", cursor="none")
        self.backdrop.overrideredirect(True)
        self.backdrop.attributes("-topmost", True)
        self.backdrop.geometry(f"{mw}x{mh}+{mx}+{my}")
        self.backdrop.update_idletasks()
        place_window(self.backdrop, mx, my)
        for ex in self.extras:
            ex.menu.set_shown(False)
        self.menu.set_shown(False)
        while len(self.extras) < cfg["windows"] - 1:
            self.add_image(start=False)
        wins = [self] + self.extras
        for w in wins:
            w.attributes("-topmost", True)
            w.lift()
        self.canvas.configure(cursor="none")
        for ex in self.extras:
            ex.label.configure(cursor="none")
        for bd in self.live_bodies():
            angle = random.uniform(0, 6.2832)
            speed = cfg["speed"] * random.uniform(0.75, 1.25)
            bd.vel = [speed * math.cos(angle), speed * math.sin(angle)]
        self._saver_t0 = time.perf_counter()
        self._saver_ptr = self.winfo_pointerxy()
        self.bind_all("<Key>", self._saver_event)
        self.bind_all("<ButtonPress>", self._saver_event)
        self.focus_force()
        self.raise_windows()
        self.start_physics()
        self.after(150, self.raise_windows)  # windows finish mapping a moment later
        self.after(60, self._saver_watch)
        if cfg["trigger"] == "timer":
            self.after(int(cfg["secs"] * 1000), self._saver_shuffle)

    def raise_windows(self):
        """Put the photo windows above the black backdrop (topmost band, without stealing focus)."""
        if not self.saver:
            return
        try:
            for w in [self] + self.extras:
                hwnd = ctypes.windll.user32.GetAncestor(w.winfo_id(), 2)
                # HWND_TOPMOST; SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010)
        except Exception:
            pass

    def _saver_event(self, e=None):
        if self.saver and time.perf_counter() - self._saver_t0 > 0.5:
            self.stop_screensaver()

    def _saver_watch(self):
        if not self.saver:
            return
        px, py = self.winfo_pointerxy()
        ox, oy = self._saver_ptr
        if time.perf_counter() - self._saver_t0 > 0.7 and abs(px - ox) + abs(py - oy) > 12:
            return self.stop_screensaver()
        self.after(60, self._saver_watch)

    def _saver_shuffle(self):
        """Every couple of seconds, swap the photo in one random window."""
        if not self.saver:
            return
        self.change_photo(random.choice([self] + self.extras))
        self.after(int(self.saver_cfg["secs"] * 1000), self._saver_shuffle)

    def change_photo(self, w):
        item = self.preloader.get() if self.preloader else None
        if w is self:
            self.apply_prepared(item) if item else self.next()
        else:
            w.show_prepared(item) if item else w.next_photo()

    def _saver_collided(self, wins):
        """A window hit another one: both get a new photo (one per frame, to keep motion smooth)."""
        now = time.perf_counter()
        todo = [w for w in wins if now - self._last_change.get(w, 0) >= 0.4]
        for i, w in enumerate(todo):
            self._last_change[w] = now
            self.after(i * 12, lambda w=w: self.saver and self.change_photo(w))

    def stop_screensaver(self):
        if not self.saver:
            return
        self.saver = False
        if self.preloader:
            self.preloader.stop()
            self.preloader = None
        self.unbind_all("<Key>")
        self.unbind_all("<ButtonPress>")
        self.stop_physics()
        for bd in self.live_bodies():
            bd.vel = [0.0, 0.0]
        if self.backdrop:
            self.backdrop.destroy()
            self.backdrop = None
        pos, geo, n_extras = self._saved_state
        for ex in self.extras[n_extras:]:  # remove only the windows the saver added
            ex.destroy()
        del self.extras[n_extras:]
        for w in [self] + self.extras:
            w.attributes("-topmost", False)
        self.canvas.configure(cursor="hand2")
        self.size_pct = self._saved_size
        for ex in self.extras:
            ex.label.configure(cursor="hand2")
            ex.refit()
        if self._quit_on_exit:
            return self.destroy()
        # Back to where the window was (same centre), then fit it to the photo showing now.
        self.body.pos = [pos[0] - self.winfo_width() / 2, pos[1] - self.winfo_height() / 2]
        self.body.placed = None
        self.body.place()
        self.render(fit=True)

    def add_image(self, start=True):
        if not self.files:
            return self.open_folder()
        if len(self.extras) >= 12:
            return
        mx, my, mw, mh = self.monitor_rect()
        path = random.choice(self.files)
        try:
            ex = PhotoWindow(self, path, mx + random.random() * mw * 0.6, my + random.random() * mh * 0.6)
        except Exception:
            return
        self.extras.append(ex)
        angle = random.uniform(0, 6.2832)
        speed = random.uniform(500, 1000)
        ex.body.vel = [speed * math.cos(angle), speed * math.sin(angle)]
        if start:
            self.start_physics()

    def remove_extra(self, ex):
        if ex in self.extras:
            self.extras.remove(ex)
        ex.destroy()

    def start_resize(self, e):
        if self.is_full:
            return
        self._r = (self.winfo_width(), self.winfo_height(), e.x_root, e.y_root)

    def do_resize(self, e):
        if self.is_full:
            return
        w, h, x0, y0 = self._r
        self.geometry(f"{max(400, w + e.x_root - x0)}x{max(300, h + e.y_root - y0)}")

    def load(self, folder):
        self.files = [os.path.join(r, f) for r, _, fs in os.walk(folder)
                      for f in fs if os.path.splitext(f)[1].lower() in EXTS]
        self.history, self.pos, self.current = [], -1, None
        if self.files:
            self.next()
        else:
            self.canvas.configure(image="", text="No images found in that folder")
            self.info.configure(text="")

    def read_settings(self):
        return load_settings()

    def write_settings(self, **values):
        save_settings(**values)

    def open_folder(self):
        saved = self.read_settings().get("folder")
        folder = filedialog.askdirectory(title="Choose a photo folder",
                                         initialdir=saved if saved and os.path.isdir(saved) else None)
        if folder:
            self.write_settings(folder=os.path.normpath(folder))
            self.load(folder)

    def next(self):
        if not self.files:
            return self.open_folder()
        if self.pos < len(self.history) - 1:
            self.pos += 1
        else:
            self.history.append(random.choice(self.files))
            self.pos = len(self.history) - 1
        self.current = self.history[self.pos]
        self.render(fit=True)

    def prev(self):
        if self.pos > 0:
            self.pos -= 1
            self.current = self.history[self.pos]
            self.render(fit=True)

    def fit_size(self, iw, ih):
        """Window size for a photo of this shape, from the base size setting."""
        return calc_fit_size(iw, ih, self.winfo_screenwidth(), self.winfo_screenheight(), self.size_pct)

    def fit_window(self, iw, ih, size=None):
        """Resize the window to the photo's shape (centered on its current spot)."""
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        nw, nh = size or self.fit_size(iw, ih)
        cx, cy = self.body.pos[0] + self.winfo_width() // 2, self.body.pos[1] + self.winfo_height() // 2
        x = round(min(max(cx - nw // 2, 0), max(sw - nw, 0)))
        y = round(min(max(cy - nh // 2, 0), max(sh - nh, 0)))
        self.geometry(f"{nw}x{nh}+{x}+{y}")
        if not size:
            self.update_idletasks()
        place_window(self, x, y)
        self.body.pos, self.body.placed = [float(x), float(y)], None
        self.body.set_known(nw, nh)
        return nw, nh

    def apply_prepared(self, item):
        """Swap in a photo that was already decoded and resized in the background."""
        path, img, (nw, nh) = item
        self.history.append(path)
        self.pos = len(self.history) - 1
        self.current = path
        self.fit_window(0, 0, size=(nw, nh))
        self.tk_img = ImageTk.PhotoImage(img)
        self.canvas.configure(image=self.tk_img, text="")
        self._last_key = (path, nw, nh)

    def load_image(self, path):
        """Decode once and keep a screen-sized copy; re-rendering then stays fast."""
        if self._cache and self._cache[0] == path:
            return self._cache[1], self._cache[2]
        img = Image.open(path)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        try:
            img.draft("RGB", (sw, sh))  # JPEGs decode at reduced size
        except Exception:
            pass
        img = ImageOps.exif_transpose(img).convert("RGB")
        orig = img.size
        if img.width > sw or img.height > sh:
            img.thumbnail((sw, sh), Image.BILINEAR)
        self._cache = (path, img, orig)
        return img, orig

    def render(self, fit=False):
        if not self.current:
            return
        try:
            base, orig = self.load_image(self.current)
        except Exception:
            bad = self.current
            self.files = [f for f in self.files if f != bad]
            self.history = [h for h in self.history if h != bad]
            self.pos = len(self.history) - 1
            self.current = None
            self._cache = None
            if self.files:
                self.next()
            return
        if fit and not self.is_full:
            w, h = self.fit_window(*orig)
        else:
            w, h = max(self.winfo_width(), 50), max(self.winfo_height(), 50)
        scale = min(w / base.width, h / base.height)
        nw, nh = max(round(base.width * scale), 1), max(round(base.height * scale), 1)
        # Keep the window exactly the photo's shape so no bars are left around it.
        if not self.is_full and (abs(w - nw) > 3 or abs(h - nh) > 3):
            self.geometry(f"{nw}x{nh}")
        key = (self.current, nw, nh)
        self.info.configure(text=f"{os.path.basename(self.current)}  ({self.pos + 1}/{len(self.history)})")
        if key == self._last_key:
            return
        self._last_key = key
        img = base if (nw, nh) == base.size else base.resize((nw, nh), Image.BICUBIC)
        self.tk_img = ImageTk.PhotoImage(img)
        self.canvas.configure(image=self.tk_img, text="")

    def reveal(self):
        if self.current and os.path.exists(self.current):
            subprocess.Popen(f'explorer /select,"{os.path.normpath(self.current)}"')

    def auto_label(self):
        return f"Auto: {self.auto_secs}s" if self.auto_on else "Auto: off"

    def bounce_label(self):
        return f"Bounce: {self.bounce_name}"

    def size_label(self):
        return f"Size: {self.size_pct:g}%"

    def refresh_bars(self):
        self.scale_bar()
        for ex in self.extras:
            ex.menu.scale()

    def auto_menu(self):
        m = tk.Menu(self, tearoff=0)
        if self.auto_on:
            m.add_command(label="Stop", command=self.stop_auto)
            m.add_separator()
        for n in (1, 2, 3, 5, 10, 15, 30, 60):
            m.add_command(label=f"Every {n} s" + ("  ✓" if self.auto_on and n == self.auto_secs else ""),
                          command=lambda n=n: self.start_auto(n))
        m.add_command(label="Custom…", command=self.custom_auto)
        b = self.anchor_btn
        self.menu_open = True  # keep the bottom bar up while the menu is showing
        try:
            m.tk_popup(b.winfo_rootx(), b.winfo_rooty())
        finally:
            m.grab_release()
            self.menu_open = False

    def bounce_menu(self):
        m = tk.Menu(self, tearoff=0)
        for name, value in BOUNCE_LEVELS.items():
            tick = "  ✓" if name == self.bounce_name else ""
            m.add_command(label=f"{name}  ({round(value * 100)}% speed kept){tick}",
                          command=lambda n=name: self.set_bounce(n))
        self.menu_open = True
        try:
            btn = self.anchor_btn
            m.tk_popup(btn.winfo_rootx(), btn.winfo_rooty())
        finally:
            m.grab_release()
            self.menu_open = False

    def set_bounce(self, name):
        self.bounce_name = name
        self.write_settings(bounce=name)
        self.refresh_bars()

    def size_menu(self):
        m = tk.Menu(self, tearoff=0)
        for pct in SIZE_LEVELS:
            note = "  (default)" if pct == DEFAULT_SIZE else ""
            tick = "  ✓" if pct == self.size_pct else ""
            m.add_command(label=f"{pct}%{note}{tick}", command=lambda p=pct: self.set_size(p))
        m.add_command(label="Custom…", command=self.custom_size)
        btn = self.anchor_btn
        self.menu_open = True
        try:
            m.tk_popup(btn.winfo_rootx(), btn.winfo_rooty())
        finally:
            m.grab_release()
            self.menu_open = False

    def custom_size(self):
        n = simpledialog.askinteger(APP_NAME, "Base size (% of full fit-to-screen):",
                                    initialvalue=int(self.size_pct), minvalue=10, maxvalue=300, parent=self)
        if n:
            self.set_size(n)

    def set_size(self, pct):
        self.size_pct = pct
        self.write_settings(size=pct)
        self.refresh_bars()
        if not self.is_full:
            self.render(fit=True)
        for ex in self.extras:
            ex.refit()

    def custom_auto(self):
        n = simpledialog.askfloat(APP_NAME, "Seconds between photos:", initialvalue=self.auto_secs,
                                  minvalue=0.2, maxvalue=3600, parent=self)
        if n:
            self.start_auto(int(n) if n == int(n) else n)

    def start_auto(self, secs):
        self.stop_auto()
        self.auto_secs, self.auto_on = secs, True
        self.refresh_bars()
        self.auto_id = self.after(int(secs * 1000), self.tick)

    def stop_auto(self):
        if self.auto_id:
            self.after_cancel(self.auto_id)
        self.auto_id, self.auto_on = None, False
        self.refresh_bars()

    def tick(self):
        self.next()
        for ex in self.extras:
            ex.next_photo()
        self.auto_id = self.after(int(self.auto_secs * 1000), self.tick)

    def scale_bar(self):
        self.menu.scale()

    def on_configure(self, e):
        if e.widget is not self or self._phys_running or self.body.held:
            return
        if self._rid:
            self.after_cancel(self._rid)
        self._rid = self.after(40, self.settle)

    def settle(self):
        self.scale_bar()
        self.render()

    def monitor_rect(self):
        """Bounds (x, y, w, h) of the monitor this window is mostly on."""
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.winfo_id())
            mon = user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)  # nearest monitor

            class MI(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
            mi = MI()
            mi.cbSize = ctypes.sizeof(MI)
            user32.GetMonitorInfoW(wintypes.HANDLE(mon), ctypes.byref(mi))
            r = mi.rcMonitor
            return r.left, r.top, r.right - r.left, r.bottom - r.top
        except Exception:
            return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def toggle_full(self):
        # The native fullscreen flag is ignored on borderless windows, so do it by hand.
        self.stop_throw()
        if self.is_full:
            self.is_full = False
            self.attributes("-topmost", False)
            self.update_idletasks()
            w, h, x, y = (int(v) for v in re.match(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", self._normal_geo).groups())
            self.geometry(self._normal_geo)
            self.update_idletasks()
            try:
                hwnd = ctypes.windll.user32.GetAncestor(self.winfo_id(), 2)
                ctypes.windll.user32.SetWindowPos(hwnd, -2, x, y, w, h, 0x0040)
            except Exception:
                pass
            self.body.pos, self.body.placed = [float(x), float(y)], None
        else:
            self._normal_geo = self.geometry()
            self.is_full = True
            x, y, w, h = self.monitor_rect()
            self.attributes("-topmost", True)
            self.update_idletasks()
            # Tk keeps the old position on borderless windows, so place it with Win32 directly.
            try:
                hwnd = ctypes.windll.user32.GetAncestor(self.winfo_id(), 2)
                if not ctypes.windll.user32.SetWindowPos(hwnd, -1, x, y, w, h, 0x0040):
                    raise OSError("SetWindowPos failed")
            except Exception:
                self.geometry(f"{w}x{h}+{x}+{y}")
        # Leaving fullscreen: refit the window to whatever photo is showing now.
        self.after(60, lambda: self.render(fit=not self.is_full))

    # ---- updater ----
    def check_updates(self, manual):
        if not UPDATE_URL:
            if manual:
                messagebox.showinfo(APP_NAME, "No update server is configured in this build.")
            return

        def work():
            try:
                with urllib.request.urlopen(UPDATE_URL, timeout=10) as r:
                    data = json.load(r)
                self.after(0, lambda: self.offer_update(data, manual))
            except Exception as e:
                if manual:
                    self.after(0, lambda: messagebox.showerror(APP_NAME, f"Update check failed:\n{e}"))
        threading.Thread(target=work, daemon=True).start()

    def offer_update(self, data, manual):
        def ver(v):
            return tuple(int(x) for x in str(v).split("."))
        if ver(data["version"]) <= ver(APP_VERSION):
            if manual:
                messagebox.showinfo(APP_NAME, f"You're up to date (v{APP_VERSION}).")
            return
        if not messagebox.askyesno(APP_NAME, f"Version {data['version']} is available "
                                   f"(you have {APP_VERSION}). Update now?"):
            return
        if not getattr(sys, "frozen", False):
            messagebox.showinfo(APP_NAME, "Updating only works in the installed program.")
            return
        try:
            exe = sys.executable
            new = exe + ".new"
            urllib.request.urlretrieve(data["url"], new)
            bat = os.path.join(tempfile.gettempdir(), "pv_update.bat")
            with open(bat, "w") as f:
                f.write('@echo off\r\nping 127.0.0.1 -n 3 >nul\r\n'
                        f'move /y "{new}" "{exe}"\r\nstart "" "{exe}"\r\ndel "%~f0"\r\n')
            subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000)
            self.destroy()
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Update failed:\n{e}")


def uninstall():
    import winreg
    if not messagebox.askyesno(APP_NAME, "Uninstall Photo Viewer?"):
        return
    start = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                         "Start Menu", "Programs")
    for lnk in (os.path.join(start, "Photo Viewer.lnk"),
                os.path.join(os.path.expanduser("~"), "Desktop", "Photo Viewer.lnk")):
        if os.path.exists(lnk):
            os.remove(lnk)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
    d = os.path.dirname(sys.executable)
    # Delete the install folder after this process exits.
    subprocess.Popen(["cmd", "/c", f'ping 127.0.0.1 -n 3 >nul & rmdir /s /q "{d}"'],
                     creationflags=0x08000000)
    messagebox.showinfo(APP_NAME, "Photo Viewer was uninstalled.")


if __name__ == "__main__":
    flags = [a.lower().lstrip("/-")[:1] for a in sys.argv[1:]]
    if "--uninstall" in sys.argv:
        tk.Tk().withdraw()
        uninstall()
    elif "p" in flags:  # screensaver preview pane: nothing to show
        pass
    elif "c" in flags:  # screensaver settings dialog (Windows' "Settings" button)
        root = tk.Tk()
        build_saver_settings(root)
        root.mainloop()
    else:
        app = App()
        if "s" in flags:  # run as a Windows screensaver: start at once, quit when touched
            app.after(400, lambda: app.start_screensaver(quit_on_exit=True))
        app.mainloop()
