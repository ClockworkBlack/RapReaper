@echo off
title RapReaper: get the Vocal Remover's model (one time)
cd /d "%~dp0"
echo This downloads, into VocalRemover\models only:
echo   config_kimmel_unwa_ft.yaml and kimmel_unwa_ft2.ckpt (913 MB), the Kim FT2 Mel-Band Roformer by unwa,
echo   from huggingface.co, each checked against its published checksum,
echo and then builds the Vocal Remover's model from them (MelRoformer-KimFT2-int8.onnx, 237 MB), checked against
echo its published checksum. The 913 MB download is deleted afterwards.
echo Run "Install RapReaper.bat" first (the build needs the NumPy it installs).
echo.
pause
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get_model_kimft2.ps1"
if errorlevel 1 goto done
py -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepy
python rapreaper_install.py --build-model
goto done
:usepy
py rapreaper_install.py --build-model
:done
echo.
pause
