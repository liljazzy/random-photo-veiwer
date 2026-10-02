import os
import shutil
import subprocess
import sys
import tkinter as tk
import winreg
from tkinter import messagebox

APP_NAME = "Photo Viewer"
APP_VERSION = "1.2.0"
KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PhotoViewer"


def shortcut(path, target, args=""):
    ps = ('$s=(New-Object -ComObject WScript.Shell).CreateShortcut(\'%s\');'
          '$s.TargetPath=\'%s\';$s.WorkingDirectory=\'%s\';$s.Save()'
          % (path, target, os.path.dirname(target)))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   creationflags=0x08000000)


def main():
    root = tk.Tk()
    root.withdraw()
    dest = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "PhotoViewer")
    if not messagebox.askyesno(f"{APP_NAME} Setup",
                               f"Install {APP_NAME} {APP_VERSION} to:\n{dest}\n\n"
                               "Start Menu and Desktop shortcuts will be created."):
        return
    try:
        os.makedirs(dest, exist_ok=True)
        exe = os.path.join(dest, "PhotoViewer.exe")
        src = os.path.join(getattr(sys, "_MEIPASS", "."), "PhotoViewer.exe")
        shutil.copy2(src, exe)

        start = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs")
        shortcut(os.path.join(start, f"{APP_NAME}.lnk"), exe)
        shortcut(os.path.join(os.path.expanduser("~"), "Desktop", f"{APP_NAME}.lnk"), exe)

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            for name, val in {"DisplayName": APP_NAME, "DisplayVersion": APP_VERSION,
                              "Publisher": "Photo Viewer", "InstallLocation": dest,
                              "DisplayIcon": exe,
                              "UninstallString": f'"{exe}" --uninstall'}.items():
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
    except Exception as e:
        messagebox.showerror(f"{APP_NAME} Setup", f"Install failed:\n{e}")
        return
    if messagebox.askyesno(f"{APP_NAME} Setup", "Installed! Launch Photo Viewer now?"):
        subprocess.Popen([exe], cwd=dest)


if __name__ == "__main__":
    main()
