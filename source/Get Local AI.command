#!/bin/bash
# RapReaper for macOS: get the Local AI (one time). Double-click this file.
# It downloads llama.cpp's llama-server (build b12250 of github.com/ggml-org/llama.cpp, MIT licence) and one open AI model
# (Qwen 3.5 by the Qwen team, Apache 2.0 licence; the GGUF file by unsloth on huggingface.co), each checked against its
# published SHA-256, into LocalAI/runtime and LocalAI/models. The installer then checks every file again
# (--local-ai-check). Nothing else is downloaded or changed.

BUILD=b12250
SMALL_NAME=Qwen3.5-4B-Q4_K_M.gguf; SMALL_LABEL="Qwen 3.5 4B"; SMALL_SIZE="2.7 GB"; SMALL_BYTES=2740937888
SMALL_SHA=00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4
SMALL_URL=https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q4_K_M.gguf
LARGE_NAME=Qwen3.5-9B-Q4_K_M.gguf; LARGE_LABEL="Qwen 3.5 9B"; LARGE_SIZE="5.7 GB"; LARGE_BYTES=5680522464
LARGE_SHA=03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8
LARGE_URL=https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/3885219b6810b007914f3a7950a8d1b469d598a5/Qwen3.5-9B-Q4_K_M.gguf

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

sha() { shasum -a 256 "$1" | cut -d' ' -f1; }

fetch() {   # url, destination, published SHA-256
  local url="$1" dest="$2" want="$3" got
  if [ -f "$dest" ] && [ "$(sha "$dest")" = "$want" ]; then echo "  already here, checksum correct"; return 0; fi
  rm -f "$dest.part"
  echo "  downloading $url"
  curl -L --fail --progress-bar -o "$dest.part" "$url" || { rm -f "$dest.part"; echo "  the download failed"; return 1; }
  echo "  checking it ..."
  got="$(sha "$dest.part")"
  if [ "$got" != "$want" ]; then rm -f "$dest.part"; echo "  $(basename "$dest") does not match its published checksum ($got); it was deleted"; return 1; fi
  mv -f "$dest.part" "$dest"; echo "  checksum matches the published one"
}

main() {
  cd "$(dirname "$0")" || { echo "Could not open the RapReaper folder."; return 1; }
  echo "RapReaper's Local AI: an open AI model that runs on this Mac and drives RapReaper."
  echo "Nothing you say to it leaves this Mac, and there is no account or subscription."
  echo
  local arm=0 asset sum gb choice="${1:-}"
  [ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ] && arm=1
  if [ "$arm" = 1 ]; then
    asset="llama-$BUILD-bin-macos-arm64.tar.gz"; sum=119dd527e2c2218b934a8900d96fb9874f3c90e659d0d80727737784b45ab9e9
  else
    asset="llama-$BUILD-bin-macos-x64.tar.gz"; sum=7b12d26ec4b0505f56690db49ad34a0ddc42020e5c3d8ceb8f9585a83ce4b47b
  fi
  gb=$(( $(/usr/sbin/sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))
  echo "This downloads, into LocalAI only:"
  echo "  llama.cpp's llama-server, build $BUILD (about 15 MB, MIT licence), from github.com/ggml-org/llama.cpp"
  echo "  one AI model, Qwen 3.5 by the Qwen team (Apache 2.0 licence), from huggingface.co"
  echo "each checked against its published checksum. Run Install RapReaper.command first."
  echo
  echo "This Mac has about $gb GB of memory$([ "$arm" = 1 ] && echo " and an Apple chip" || echo " and an Intel processor")."
  local name="$SMALL_NAME" label="$SMALL_LABEL" size="$SMALL_SIZE" bytes=$SMALL_BYTES want=$SMALL_SHA url="$SMALL_URL"
  if [ "$choice" = large ] || { [ "$choice" != small ] && [ "$arm" = 1 ] && [ "$gb" -ge 15 ]; }; then
    name="$LARGE_NAME"; label="$LARGE_LABEL"; size="$LARGE_SIZE"; bytes=$LARGE_BYTES; want=$LARGE_SHA; url="$LARGE_URL"
  fi
  if [ -z "$choice" ] && [ "$gb" -ge 15 ]; then
    echo "Which AI model?"
    echo "  1  $SMALL_LABEL ($SMALL_SIZE): quicker; good with the RapReaper tools."
    echo "  2  $LARGE_LABEL ($LARGE_SIZE): better answers; wants 16 GB of memory$([ "$arm" = 1 ] || echo ", and is slow on an Intel Mac")."
    read -r -p "Type 1 or 2 and press Return (Return alone takes $([ "$name" = "$LARGE_NAME" ] && echo 2 || echo 1)) " ANSWER
    case "$ANSWER" in
      1) name="$SMALL_NAME"; label="$SMALL_LABEL"; size="$SMALL_SIZE"; bytes=$SMALL_BYTES; want=$SMALL_SHA; url="$SMALL_URL" ;;
      2) name="$LARGE_NAME"; label="$LARGE_LABEL"; size="$LARGE_SIZE"; bytes=$LARGE_BYTES; want=$LARGE_SHA; url="$LARGE_URL" ;;
    esac
  elif [ "$gb" -lt 7 ] && [ "$gb" -gt 0 ]; then
    echo "That is less than the 8 GB the smaller model needs to run well. It may still work, slowly, with nothing else open."
  fi
  read -r -p "Download llama.cpp and $label ($size) now? [y/N] " GO
  case "$GO" in y|Y|yes|YES) ;; *) echo "Nothing done."; return 0 ;; esac
  local free_kb; free_kb=$(df -Pk . | awk 'NR==2 {print $4}')
  if [ -n "$free_kb" ] && [ $(( free_kb * 1024 )) -lt $(( bytes + 419430400 )) ]; then
    echo "$label needs about $size free on this disk; there is less. Make room and try again."; return 1
  fi
  mkdir -p LocalAI/runtime LocalAI/models
  echo; echo "[1/3] llama.cpp $BUILD"
  fetch "https://github.com/ggml-org/llama.cpp/releases/download/$BUILD/$asset" "LocalAI/runtime/$asset" "$sum" || return 1
  echo; echo "[2/3] unpacking llama.cpp into LocalAI/runtime"
  local tmp="LocalAI/runtime/.unpack"
  rm -rf "$tmp"; mkdir -p "$tmp"
  tar -xzf "LocalAI/runtime/$asset" -C "$tmp" || { echo "  could not unpack it"; rm -rf "$tmp"; return 1; }
  find LocalAI/runtime -mindepth 1 -maxdepth 1 ! -name .keep ! -name .unpack ! -name "$asset" -exec rm -rf {} +
  mv "$tmp/llama-$BUILD"/* LocalAI/runtime/ && rm -rf "$tmp" "LocalAI/runtime/$asset" || { echo "  could not unpack it"; return 1; }
  echo "  done"
  echo; echo "[3/3] $label ($size): the AI model"
  fetch "$url" "LocalAI/models/$name" "$want" || return 1
  echo
  PY="$(find_python)" || { echo "Python 3.12 or newer was not found; run Install RapReaper.command first."; return 1; }
  "$PY" rapreaper_install.py --local-ai-check "$name"
}

main "$@"; echo; echo "(You can close this window.)"; exit
