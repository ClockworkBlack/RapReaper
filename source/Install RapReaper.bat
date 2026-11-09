@echo off
title RapReaper installer
cd /d "%~dp0"
if not exist rapreaper_install.py goto nofile
echo Looking for Python on this PC ...
echo (the very first time this can take a minute; if Windows offers to fetch Python, let it finish)
echo.
rem  'where python' also finds the Microsoft Store placeholder, so each candidate is really started once.
py -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepy
python -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepython
echo Python is not installed on this PC yet, or Windows cannot find it.
echo.
echo   1. Go to  https://www.python.org/downloads/windows/  and install Python.
echo      If the installer shows a box "Add python.exe to PATH", tick it.
echo   2. Double-click this file again.
goto done
:nofile
echo This file has to be started from the unzipped RapReaper folder.
echo.
echo   1. Right-click the RapReaper zip file and choose "Extract All".
echo   2. Open the extracted RapReaper folder and double-click this file there.
echo.
echo (If the folder is on a network drive, copy it to this PC first.)
goto done
:usepy
py rapreaper_install.py %*
goto done
:usepython
python rapreaper_install.py %*
:done
echo.
pause
