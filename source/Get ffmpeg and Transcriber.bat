@echo off
title RapReaper: download ffmpeg and whisper.cpp (one time)
cd /d "%~dp0"
echo This downloads, into THIS folder only:
echo   ffmpeg        (BtbN build from github.com)      -^> tools\ffmpeg
echo   whisper.cpp   (official release from github.com) -^> tools\whisper
echo   speech model  (ggml-base.en.bin, from huggingface.co, checked against its published checksum) -^> kit\models
echo It does not allow anything for RapReaper: you do that afterwards with the two "Allow" files.
echo.
pause
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get_tools_b5130.ps1"
echo.
pause
