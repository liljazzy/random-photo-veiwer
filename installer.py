import glob
import os
import shutil
import subprocess
import sys
import time
import tkinter as tk
import winreg
from tkinter import messagebox

from photo_viewer import APP_NAME, APP_VERSION, UNINSTALL_KEY, known_folder

CSIDL_PROGRAMS, CSIDL_DESKTOP = 0x02, 0x10


def make_shortcut(link, target):
    """Create a .lnk via PowerShell (paths are passed single-quoted, with ' doubled)."""
    q = lambda p: p.replace("'", "''")
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
          "$s.TargetPath='%s';$s.WorkingDirectory='%s';$s.IconLocation='%s,0';$s.Save()"
          % (q(link), q(target), q(os.path.dirname(target)), q(target)))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   capture_output=True, creationflags=0x08000000)


def install(dest, shortcut_dirs, key, src_exe, version=APP_VERSION):
    """Copy the program into `dest`, add shortcuts and an Installed-apps entry.
    Returns a list of non-fatal warnings (e.g. a shortcut that couldn't be made)."""
    warnings = []
    os.makedirs(dest, exist_ok=True)
    exe = os.path.join(dest, "PhotoViewer.exe")

    # Tidy up files left by earlier upgrades, then move a running copy out of the way:
    # Windows lets you rename a running program, just not overwrite it.
    for stale in glob.glob(exe + ".old*"):
        try:
            os.remove(stale)
        except OSError:
            pass
    if os.path.exists(exe):
        try:
            os.replace(exe, exe + f".old{int(time.time())}")
        except OSError as err:
            raise OSError(f"Can't replace the installed copy. Close Photo Viewer and try again.\n({err})")
    shutil.copy2(src_exe, exe)

    for folder in shortcut_dirs:
        try:
            os.makedirs(folder, exist_ok=True)
            make_shortcut(os.path.join(folder, f"{APP_NAME}.lnk"), exe)
        except Exception as err:  # a missing shortcut shouldn't undo a good install
            detail = getattr(err, "stderr", b"")
            detail = detail.decode(errors="replace").strip() if isinstance(detail, bytes) else ""
            warnings.append(f"Couldn't create a shortcut in {folder}\n{detail or err}")

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        for name, val in {"DisplayName": APP_NAME, "DisplayVersion": version,
                          "Publisher": "Photo Viewer", "InstallLocation": dest,
                          "DisplayIcon": exe,
                          "UninstallString": f'"{exe}" --uninstall'}.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
    return warnings


def main():
    root = tk.Tk()
    root.withdraw()
    dest = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "PhotoViewer")
    shortcut_dirs = [d for d in (known_folder(CSIDL_PROGRAMS), known_folder(CSIDL_DESKTOP)) if d]
    if not messagebox.askyesno(f"{APP_NAME} Setup",
                               f"Install {APP_NAME} {APP_VERSION} to:\n{dest}\n\n"
                               "Start Menu and Desktop shortcuts will be created."):
        return
    src = os.path.join(getattr(sys, "_MEIPASS", "."), "PhotoViewer.exe")
    try:
        warnings = install(dest, shortcut_dirs, UNINSTALL_KEY, src)
    except Exception as e:
        messagebox.showerror(f"{APP_NAME} Setup", f"Install failed:\n{e}")
        return
    if warnings:
        messagebox.showwarning(f"{APP_NAME} Setup", "Installed, but:\n\n" + "\n\n".join(warnings))
    if messagebox.askyesno(f"{APP_NAME} Setup", "Installed! Launch Photo Viewer now?"):
        subprocess.Popen([os.path.join(dest, "PhotoViewer.exe")], cwd=dest)


if __name__ == "__main__":
    main()
