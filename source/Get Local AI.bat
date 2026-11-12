@echo off
title RapReaper: get the Local AI (one time)
cd /d "%~dp0"
echo RapReaper's Local AI: an open AI model that runs on this PC and drives RapReaper.
echo Nothing you say to it leaves this PC, and there is no account or subscription.
echo.
echo This downloads, into LocalAI only:
echo   llama.cpp's llama-server, build b12250 (38 MB, MIT licence), from github.com/ggml-org/llama.cpp
echo   one AI model, Qwen 3.5 by the Qwen team (Apache 2.0 licence), from huggingface.co:
echo     4B (2.7 GB) on most PCs, or 9B (5.7 GB) when this PC has 16 GB of memory or more and you choose it
echo each checked against its published checksum. Run "Install RapReaper.bat" first.
echo.
pause
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get_local_ai.ps1" %*
if errorlevel 1 goto done
set MODEL=
if exist "LocalAI\models\chosen-model.txt" set /p MODEL=<"LocalAI\models\chosen-model.txt"
py -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepy
python -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepython
echo Python is not installed on this PC yet, or Windows cannot find it: run "Install RapReaper.bat" first.
goto done
:usepython
python rapreaper_install.py --local-ai-check %MODEL%
goto done
:usepy
py rapreaper_install.py --local-ai-check %MODEL%
:done
echo.
pause
