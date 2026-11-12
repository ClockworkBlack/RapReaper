@echo off
title RapReaper Local AI (keep this window open while you use it)
cd /d "%~dp0"
if not exist rapreaper_install.py goto nofile
py -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepy
python -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepython
echo Python is not installed on this PC yet, or Windows cannot find it: run "Install RapReaper.bat" first.
goto done
:nofile
echo This file has to be started from the unzipped RapReaper folder.
goto done
:usepy
py rapreaper_install.py --local-ai %*
goto done
:usepython
python rapreaper_install.py --local-ai %*
:done
echo.
pause
