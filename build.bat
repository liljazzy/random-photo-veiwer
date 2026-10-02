@echo off
rem Builds dist\PhotoViewer.exe, dist\PhotoViewer.scr and dist\PhotoViewerSetup.exe
rem Needs: python, pillow, pyinstaller.  (icon.ico is drawn by make_icon.py)
cd /d "%~dp0"
python -m PyInstaller --noconfirm --onefile --windowed --icon icon.ico --add-data "icon.ico;." --name PhotoViewer photo_viewer.py || exit /b 1
copy /y dist\PhotoViewer.exe dist\PhotoViewer.scr >nul
python -m PyInstaller --noconfirm --onefile --windowed --icon icon.ico --add-data "icon.ico;." --add-data "dist\PhotoViewer.exe;." --name PhotoViewerSetup installer.py || exit /b 1
echo Done.
