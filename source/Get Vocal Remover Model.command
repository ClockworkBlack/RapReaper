#!/bin/bash
# RapReaper for macOS: get the Vocal Remover's model (one time). Double-click this file.
# It downloads the model's two public source files (the Kim FT2 Mel-Band Roformer, by unwa, on huggingface.co) into
# VocalRemover/models, checks each against its published SHA-256, then builds the model with the installer
# (build/build_roformer_onnx.py, NumPy only) and checks the result against the model's published checksum.

find_python() {
  for candidate in /Library/Frameworks/Python.framework/Versions/3.{19,18,17,16,15,14,13,12}/bin/python3 \
                   /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
                   /opt/homebrew/bin/python3 /usr/local/bin/python3 /opt/local/bin/python3; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
      echo "$candidate"; return 0
    fi
  done
  for candidate in $(type -a -p python3 2>/dev/null); do
    case "$candidate" in /usr/bin/python3) continue ;; esac
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then echo "$candidate"; return 0; fi
  done
  return 1
}

get() {   # name, published SHA-256, size for the message
  local dest="VocalRemover/models/$1" got
  if [ -f "$dest" ] && [ "$(shasum -a 256 "$dest" | cut -d' ' -f1)" = "$2" ]; then echo "  already here, checksum correct"; return 0; fi
  rm -f "$dest.part"
  echo "  downloading $1 ($3)"
  curl -L --fail --progress-bar -o "$dest.part" "https://huggingface.co/pcunwa/Kim-Mel-Band-Roformer-FT/resolve/main/$1" \
    || { rm -f "$dest.part"; echo "  the download failed"; return 1; }
  got="$(shasum -a 256 "$dest.part" | cut -d' ' -f1)"
  if [ "$got" != "$2" ]; then rm -f "$dest.part"; echo "  $1 does not match its published checksum ($got); it was deleted"; return 1; fi
  mv -f "$dest.part" "$dest"; echo "  checksum matches the published one"
}

main() {
  cd "$(dirname "$0")" || { echo "Could not open the RapReaper folder."; return 1; }
  echo "RapReaper: get the Vocal Remover's model (one time)"
  echo
  echo "This downloads, into VocalRemover/models only:"
  echo "  config_kimmel_unwa_ft.yaml and kimmel_unwa_ft2.ckpt (913 MB), the Kim FT2 Mel-Band Roformer by unwa,"
  echo "  from huggingface.co, each checked against its published checksum,"
  echo "and then builds the Vocal Remover's model from them (MelRoformer-KimFT2-int8.onnx, 237 MB), checked against"
  echo "its published checksum. The 913 MB download is deleted afterwards."
  echo "Run Install RapReaper.command first (the build needs the NumPy it installs)."
  echo
  local target=VocalRemover/models/MelRoformer-KimFT2-int8.onnx
  if [ -f "$target" ] && [ "$(shasum -a 256 "$target" | cut -d' ' -f1)" = "f81d9265cf57b66808db38d72ae136f3d74f3888f642e085478288f774b5a0d4" ]; then
    echo "The model is already in VocalRemover/models, checksum correct. Nothing to do."; return 0
  fi
  read -r -p "Go ahead? [y/N] " GO
  case "$GO" in y|Y|yes|YES) ;; *) echo "Nothing done."; return 0 ;; esac
  mkdir -p VocalRemover/models
  echo; echo "[1/3] config_kimmel_unwa_ft.yaml"
  get config_kimmel_unwa_ft.yaml 0942cb7da8e86ac1be840cc7b9647831c11ffe00327a0b239df256031e6c8355 "2 KB" || return 1
  echo; echo "[2/3] kimmel_unwa_ft2.ckpt"
  get kimmel_unwa_ft2.ckpt 5ed7b9e4c2eebbec7a7e5e8113058f7b68ba5e6048db8eaccfbbeb884c7884c0 "913 MB" || return 1
  echo; echo "[3/3] building the model (about a minute)"
  PY="$(find_python)" || { echo "Python 3.12 or newer was not found; run Install RapReaper.command first."; return 1; }
  "$PY" rapreaper_install.py --build-model
}

main "$@"; echo; echo "(You can close this window.)"; exit
