#!/bin/bash
# RapReaper for macOS: allow the transcriber (whisper.cpp, for the word check). Double-click this file.
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
  echo "RapReaper: allow the transcriber (whisper.cpp, for the word check)"
  echo
  echo "RapReaper never starts a program it merely found on this Mac. If you have whisper.cpp installed and want"
  echo "RapReaper to use it (writing down the words of a vocal, on this Mac, for vocal_clarity), give the full path of the program here."
  echo "(With Homebrew it is usually  /opt/homebrew/bin/whisper-cli . In Finder: right-click the file, hold Option, 'Copy ... as Pathname'.)"
  echo
  read -r -p "Path of whisper-cli: " PPATH
  PPATH="${PPATH%\"}"; PPATH="${PPATH#\"}"
  [ -z "$PPATH" ] && { echo "Nothing given; nothing changed."; return 0; }
  PY="$(find_python)" || { echo "Python 3.12 or newer was not found; run Install RapReaper.command first."; return 1; }
  "$PY" rapreaper_install.py --allow-program transcriber "$PPATH"
}

main "$@"; echo; echo "(You can close this window.)"; exit
