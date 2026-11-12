@echo off
title RapReaper: allow a transcriber (whisper.cpp) for vocal_clarity
cd /d "%~dp0"
echo RapReaper never starts a program it merely found on this PC. If you have whisper.cpp
echo (https://github.com/ggerganov/whisper.cpp, the command-line program whisper-cli.exe or main.exe)
echo and want RapReaper to use it to write down the words of a vocal ON THIS PC, give its full
echo path here. Also put a model file (ggml-base.en.bin or ggml-small.en.bin) in kit\models.
echo The words never leave this PC unless you ask the AI to show them.
echo (In File Explorer: right-click the program, "Copy as path", then paste it here.)
echo.
set "WPATH="
set /p "WPATH=Path of whisper-cli.exe (or main.exe): "
if not defined WPATH goto done
set "WPATH=%WPATH:"=%"
py -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepy
python rapreaper_install.py --allow-program transcriber "%WPATH%"
goto done
:usepy
py rapreaper_install.py --allow-program transcriber "%WPATH%"
:done
echo.
pause
