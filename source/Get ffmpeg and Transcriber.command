#!/bin/bash
# RapReaper for macOS: get ffmpeg, whisper.cpp and the speech model (one time, with Homebrew). Double-click this file.
# (If macOS will not open it: System Settings > Privacy & Security > "Open Anyway"; or in Terminal type  bash  and a
# space, drag this file into the window and press Return. Once Install RapReaper.command has run, every RapReaper
# .command file opens by double-click.)

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

main() {
  cd "$(dirname "$0")" || { echo "Could not open the RapReaper folder."; return 1; }
  echo "RapReaper: get ffmpeg, whisper.cpp and the speech model (one time, with Homebrew)"
  echo
  echo "This fetches, with Homebrew (brew.sh), two optional programs and one model file:"
  echo "  ffmpeg       for MP3 and M4A copies of an export        (brew install ffmpeg)"
  echo "  whisper-cpp  the transcriber for the word check         (brew install whisper-cpp)"
  echo "  ggml-base.en.bin  the speech model, from huggingface.co, checked against its published SHA-256 -> kit/models"
  echo "It allows nothing for RapReaper by itself: at the end it offers to run the two Allow steps, and you say yes or no."
  echo
  if ! command -v brew >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/brew ] && [ ! -x /usr/local/bin/brew ]; then
    echo "Homebrew is not installed. Install it first from  https://brew.sh  (one command, shown on that page),"
    echo "then double-click this file again. Or install ffmpeg and whisper.cpp any other way and use the Allow files."
    return 1
  fi
  BREW="$(command -v brew || ls /opt/homebrew/bin/brew /usr/local/bin/brew 2>/dev/null | head -n 1)"
  read -r -p "Go ahead? [y/N] " GO
  case "$GO" in y|Y|yes|YES) ;; *) echo "Nothing done."; return 0 ;; esac
  echo; echo "[1/3] ffmpeg"
  "$BREW" list ffmpeg >/dev/null 2>&1 && echo "  already installed" || "$BREW" install ffmpeg || return 1
  echo; echo "[2/3] whisper.cpp"
  "$BREW" list whisper-cpp >/dev/null 2>&1 && echo "  already installed" || "$BREW" install whisper-cpp || return 1
  echo; echo "[3/3] the speech model"
  mkdir -p kit/models
  MODEL=kit/models/ggml-base.en.bin
  WANT=a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002
  # a model file that is already there counts only when its checksum is the published one (a download that broke
  # off, or another file under the same name, is moved aside and fetched again)
  if [ -f "$MODEL" ] && [ "$(shasum -a 256 "$MODEL" | cut -d' ' -f1)" = "$WANT" ]; then echo "  already there, checksum correct"; else
    if [ -f "$MODEL" ]; then mv -f "$MODEL" "$MODEL.damaged"; echo "  the model file there was not the published one; moved aside to $MODEL.damaged"; fi
    rm -f "$MODEL.part"
    # downloaded under a temporary name and moved into place only when it is complete and its checksum is right
    curl -L --fail --progress-bar -o "$MODEL.part" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin" || { rm -f "$MODEL.part"; return 1; }
    GOT="$(shasum -a 256 "$MODEL.part" | cut -d' ' -f1)"
    if [ "$GOT" != "$WANT" ]; then echo "  the model's checksum ($GOT) is not the published one; the file was removed"; rm -f "$MODEL.part"; return 1; fi
    mv -f "$MODEL.part" "$MODEL"; echo "  checksum verified"
  fi
  FF="$("$BREW" --prefix)/bin/ffmpeg"; WC="$("$BREW" --prefix)/bin/whisper-cli"
  echo; echo "Programs:"; echo "  $FF"; echo "  $WC"
  PY="$(find_python)" || { echo "Python 3.12 or newer was not found; run Install RapReaper.command, then the Allow files."; return 1; }
  read -r -p "Allow RapReaper to use these two programs now (their checksums are recorded)? [y/N] " OK
  case "$OK" in y|Y|yes|YES) "$PY" rapreaper_install.py --allow-program ffmpeg "$FF"; "$PY" rapreaper_install.py --allow-program transcriber "$WC" ;;
                *) echo "Not allowed yet: use Allow ffmpeg.command and Allow Transcriber.command when you want to." ;; esac
}

main "$@"; echo; echo "(You can close this window.)"; exit
