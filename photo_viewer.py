import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.request
from tkinter import filedialog, messagebox

from PIL import Image, ImageOps, ImageTk

APP_NAME = "Photo Viewer"
APP_VERSION = "1.0.0"
# Point this at a JSON file you host: {"version": "1.1.0", "url": "https://.../PhotoViewer.exe"}
UPDATE_URL = "https://raw.githubusercontent.com/liljazzy/random-photo-veiwer/main/version.json"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PhotoViewer"

EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Random Photo Viewer {APP_VERSION}")
        self.geometry("1000x700")
        self.configure(bg="#111")
        self.files = []
        self.history = []
        self.pos = -1
        self.auto_id = None
        self.tk_img = None
        self.current = None

        self.canvas = tk.Label(self, bg="#111", fg="#888", cursor="hand2",
                               text="Open a folder to start (Ctrl+O)")
        self.canvas.pack(fill="both", expand=True)

        bar = tk.Frame(self, bg="#1b1b1b")
        bar.pack(fill="x")
        for text, cmd in [("Open folder", self.open_folder), ("◀ Back", self.prev),
                          ("Random ▶", self.next), ("Auto: off", self.toggle_auto),
                          ("Fullscreen", self.toggle_full),
                          ("Check for updates", lambda: self.check_updates(True))]:
            b = tk.Button(bar, text=text, command=cmd, bg="#2d2d2d", fg="#eee",
                          relief="flat", padx=12, pady=6)
            b.pack(side="left", padx=6, pady=8)
            if text.startswith("Auto"):
                self.auto_btn = b
        self.info = tk.Label(bar, bg="#1b1b1b", fg="#999")
        self.info.pack(side="left", padx=10)

        self.bind("<Right>", lambda e: self.next())
        self.bind("<space>", lambda e: self.next())
        self.bind("<Left>", lambda e: self.prev())
        self.bind("<F11>", lambda e: self.toggle_full())
        self.bind("f", lambda e: self.toggle_full())
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))
        self.bind("<Control-o>", lambda e: self.open_folder())
        self.canvas.bind("<Configure>", lambda e: self.render())
        # Click the photo: left = next random photo, right = go back.
        self.canvas.bind("<Button-1>", lambda e: self.next())
        self.canvas.bind("<Button-3>", lambda e: self.prev())

        default = os.path.join(os.path.expanduser("~"), "Pictures")
        if os.path.isdir(default):
            self.load(default)
        if UPDATE_URL and getattr(sys, "frozen", False):
            self.after(1500, lambda: self.check_updates(False))

    def load(self, folder):
        self.files = [os.path.join(r, f) for r, _, fs in os.walk(folder)
                      for f in fs if os.path.splitext(f)[1].lower() in EXTS]
        self.history, self.pos, self.current = [], -1, None
        if self.files:
            self.next()
        else:
            self.canvas.configure(image="", text="No images found in that folder")
            self.info.configure(text="")

    def open_folder(self):
        folder = filedialog.askdirectory(title="Choose a photo folder")
        if folder:
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
        self.render()

    def prev(self):
        if self.pos > 0:
            self.pos -= 1
            self.current = self.history[self.pos]
            self.render()

    def render(self):
        if not self.current:
            return
        try:
            img = ImageOps.exif_transpose(Image.open(self.current)).convert("RGB")
        except Exception:
            bad = self.current
            self.files = [f for f in self.files if f != bad]
            self.history = [h for h in self.history if h != bad]
            self.pos = len(self.history) - 1
            self.current = None
            if self.files:
                self.next()
            return
        w, h = max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 50)
        img.thumbnail((w, h), Image.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(img)
        self.canvas.configure(image=self.tk_img, text="")
        self.info.configure(text=f"{os.path.basename(self.current)}  ({self.pos + 1}/{len(self.history)})")

    def toggle_auto(self):
        if self.auto_id:
            self.after_cancel(self.auto_id)
            self.auto_id = None
            self.auto_btn.configure(text="Auto: off")
        else:
            self.auto_btn.configure(text="Auto: 4s")
            self.tick()

    def tick(self):
        self.next()
        self.auto_id = self.after(4000, self.tick)

    def toggle_full(self):
        self.attributes("-fullscreen", not self.attributes("-fullscreen"))

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
