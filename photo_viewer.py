import json
import os
import re
import random
import ctypes
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.request
from tkinter import filedialog, messagebox, simpledialog

from PIL import Image, ImageOps, ImageTk

APP_NAME = "Photo Viewer"
APP_VERSION = "1.1.0"
# Point this at a JSON file you host: {"version": "1.1.0", "url": "https://.../PhotoViewer.exe"}
UPDATE_URL = "https://raw.githubusercontent.com/liljazzy/random-photo-veiwer/main/version.json"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PhotoViewer"

EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


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

        # Bottom menu floats over the photo and only shows when the cursor is near it.
        bar = tk.Frame(self, bg="#1b1b1b")
        self.bar = bar
        self.bar_shown = False
        self.auto_on = False
        self.auto_secs = 4
        self.menu_open = False
        self.btns = []  # (button, full-label function, compact label)
        for full, short, cmd in [
                ("Open folder", "📂", self.open_folder), ("◀ Back", "◀", self.prev),
                ("Random ▶", "▶", self.next),
                (lambda: f"Auto: {self.auto_secs}s" if self.auto_on else "Auto: off", "Auto", self.auto_menu),
                ("Fullscreen", "⛶", self.toggle_full),
                ("Check for updates", "⟳", lambda: self.check_updates(True))]:
            b = tk.Button(bar, command=cmd, bg="#2d2d2d", fg="#eee", relief="flat")
            b.pack(side="left")
            self.btns.append((b, full if callable(full) else (lambda t=full: t), short))
            if short == "Auto":
                self.auto_btn = b
        self.info = tk.Label(bar, bg="#1b1b1b", fg="#999")

        grip = tk.Label(bar, text="◢", bg="#1b1b1b", fg="#777", cursor="size_nw_se", padx=8)
        grip.pack(side="right")
        grip.bind("<Button-1>", self.start_resize)
        grip.bind("<B1-Motion>", self.do_resize)
        self.close_btn = tk.Button(bar, text="✕", command=self.destroy, bg="#2d2d2d", fg="#eee",
                                   relief="flat")
        self.close_btn.pack(side="right")
        self.grip = grip
        for w in (bar, self.info):  # drag the menu to move the window
            w.bind("<Button-1>", self.start_move)
            w.bind("<B1-Motion>", self.do_move)

        self.bind("<Right>", lambda e: self.next())
        self.bind("<space>", lambda e: self.next())
        self.bind("<Left>", lambda e: self.prev())
        self.bind("<F11>", lambda e: self.toggle_full())
        self.bind("f", lambda e: self.toggle_full())
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-o>", lambda e: self.open_folder())
        self.bind("q", lambda e: self.destroy())
        # Re-render once the window has settled; the label's own size lags behind and can
        # still be the old (larger) one mid-resize, which left the photo stuck enlarged.
        self._rid = None
        self.bind("<Configure>", self.on_configure)
        # Click the photo: left = next random photo, right = go back.
        self.canvas.bind("<Button-1>", lambda e: None if e.state & 0x20000 else self.next())
        # Right-drag moves the window; a right-click without dragging goes back.
        self.canvas.bind("<ButtonPress-3>", self.rpress)
        self.canvas.bind("<B3-Motion>", self.rdrag)
        self.canvas.bind("<ButtonRelease-3>", self.rrelease)
        # Middle-click: show this photo in File Explorer.
        self.canvas.bind("<Button-2>", lambda e: self.reveal())
        # Alt+drag the photo to move the window.
        self.canvas.bind("<Alt-Button-1>", self.start_move)
        self.canvas.bind("<Alt-B1-Motion>", self.do_move)

        self.after(50, self.show_in_taskbar)
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
        px, py = self.winfo_pointerxy()
        x, y, w, h = self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height()
        zone = (self.bar.winfo_height() + 30) if self.bar_shown else 70
        inside = (x <= px < x + w and y + h - zone <= py < y + h) or self.menu_open
        if inside and not self.bar_shown:
            self.bar.place(relx=0, rely=1, anchor="sw", relwidth=1)
            self.bar.lift()
            self.bar_shown = True
        elif not inside and self.bar_shown:
            self.bar.place_forget()
            self.bar_shown = False
        self.after(100, self.poll_hover)

    def start_move(self, e):
        if self.is_full:
            return
        self._dx, self._dy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()

    def do_move(self, e):
        if self.is_full:
            return
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

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

    @staticmethod
    def settings_path():
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                            "PhotoViewer", "settings.json")

    def read_settings(self):
        try:
            with open(self.settings_path(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def write_settings(self, **values):
        data = {**self.read_settings(), **values}
        try:
            os.makedirs(os.path.dirname(self.settings_path()), exist_ok=True)
            with open(self.settings_path(), "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

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

    def rpress(self, e):
        self.start_move(e)
        self._rmoved = False
        self._rstart = (e.x_root, e.y_root)

    def rdrag(self, e):
        if abs(e.x_root - self._rstart[0]) + abs(e.y_root - self._rstart[1]) > 4:
            self._rmoved = True
        if self._rmoved:
            self.do_move(e)

    def rrelease(self, e):
        if not self._rmoved:
            self.prev()

    def fit_window(self, iw, ih):
        """Resize the window to the photo's shape (centered on its current spot)."""
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        fit = min(sw * 0.9 / iw, sh * 0.9 / ih)
        scale = max(min(fit, 1), min(fit, 500 / max(iw, ih)))  # upscale small photos a bit
        scale *= 2 / 3  # base size is one third smaller
        nw, nh = max(int(iw * scale), 200), max(int(ih * scale), 150)
        cx, cy = self.winfo_x() + self.winfo_width() // 2, self.winfo_y() + self.winfo_height() // 2
        x = min(max(cx - nw // 2, 0), max(sw - nw, 0))
        y = min(max(cy - nh // 2, 0), max(sh - nh, 0))
        self.geometry(f"{nw}x{nh}+{x}+{y}")
        return nw, nh

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

    def auto_menu(self):
        m = tk.Menu(self, tearoff=0)
        if self.auto_on:
            m.add_command(label="Stop", command=self.stop_auto)
            m.add_separator()
        for n in (1, 2, 3, 5, 10, 15, 30, 60):
            m.add_command(label=f"Every {n} s" + ("  ✓" if self.auto_on and n == self.auto_secs else ""),
                          command=lambda n=n: self.start_auto(n))
        m.add_command(label="Custom…", command=self.custom_auto)
        b = self.auto_btn
        self.menu_open = True  # keep the bottom bar up while the menu is showing
        try:
            m.tk_popup(b.winfo_rootx(), b.winfo_rooty())
        finally:
            m.grab_release()
            self.menu_open = False

    def custom_auto(self):
        n = simpledialog.askfloat(APP_NAME, "Seconds between photos:", initialvalue=self.auto_secs,
                                  minvalue=0.2, maxvalue=3600, parent=self)
        if n:
            self.start_auto(int(n) if n == int(n) else n)

    def start_auto(self, secs):
        self.stop_auto()
        self.auto_secs, self.auto_on = secs, True
        self.scale_bar()
        self.auto_id = self.after(int(secs * 1000), self.tick)

    def stop_auto(self):
        if self.auto_id:
            self.after_cancel(self.auto_id)
        self.auto_id, self.auto_on = None, False
        self.scale_bar()

    def tick(self):
        self.next()
        self.auto_id = self.after(int(self.auto_secs * 1000), self.tick)

    def style_bar(self, size, compact):
        font = ("Segoe UI", size)
        for b, full, short in self.btns:
            b.configure(text=short if compact else full(), font=font, padx=size, pady=size // 2)
            b.pack_configure(padx=max(size // 2, 2), pady=size // 2 + 2)
        self.close_btn.configure(font=font, padx=size, pady=size // 2)
        self.close_btn.pack_configure(padx=max(size // 2, 2))
        self.grip.configure(font=font)
        if compact:
            self.info.pack_forget()
        else:
            self.info.configure(font=("Segoe UI", max(size - 1, 6)))
            self.info.pack(side="left", padx=10, after=self.btns[-1][0])

    def scale_bar(self):
        """Size the menu's text and buttons so everything fits the window's width."""
        w = self.winfo_width()
        base = max(7, min(16, round(w / 90)))
        for compact in (False, True):
            for size in range(base, 5 if compact else 7, -1):  # full labels never below 8pt
                self.style_bar(size, compact)
                self.bar.update_idletasks()
                if self.bar.winfo_reqwidth() <= w:
                    return

    def on_configure(self, e):
        if e.widget is not self:
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
    if "--uninstall" in sys.argv:
        tk.Tk().withdraw()
        uninstall()
    else:
        App().mainloop()
