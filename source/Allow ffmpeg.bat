@echo off
title RapReaper: allow ffmpeg (MP3 and M4A copies of an export)
cd /d "%~dp0"
echo RapReaper never starts a program it merely found on this PC. If you have ffmpeg installed
echo (https://ffmpeg.org) and want RapReaper to use it for MP3 and M4A copies of your exports,
echo give the full path of ffmpeg.exe here.
echo (In File Explorer: right-click ffmpeg.exe, "Copy as path", then paste it here.)
echo.
set "FFPATH="
set /p "FFPATH=Path of ffmpeg.exe: "
if not defined FFPATH goto done
set "FFPATH=%FFPATH:"=%"
py -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepy
python rapreaper_install.py --allow-program ffmpeg "%FFPATH%"
goto done
:usepy
py rapreaper_install.py --allow-program ffmpeg "%FFPATH%"
:done
echo.
pause
