@echo off
title RapReaper: allow the Vocal Remover program
cd /d "%~dp0"
echo RapReaper never starts a program it merely found on this PC. The installer has already allowed
echo the Vocal Remover that came in this folder (VocalRemover\VocalRemover.exe). To use another copy
echo instead, give its full path here.
echo (In File Explorer: right-click VocalRemover.exe, "Copy as path", then paste it here.)
echo.
set "VRPATH="
set /p "VRPATH=Path of VocalRemover.exe: "
if not defined VRPATH goto done
set "VRPATH=%VRPATH:"=%"
py -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepy
python rapreaper_install.py --allow-vocal-remover "%VRPATH%"
goto done
:usepy
py rapreaper_install.py --allow-vocal-remover "%VRPATH%"
:done
echo.
pause
