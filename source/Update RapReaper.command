#!/bin/bash
# RapReaper for macOS: update from a newer RapReaper zip in Downloads. Double-click this file.
#
# If macOS says the file "cannot be opened" (it came from the internet): open System Settings > Privacy & Security,
# scroll down to the message about this file and click "Open Anyway", then double-click it again. Or open Terminal,
# type   bash   followed by a space, drag this file into the Terminal window and press Return. The installer
# releases the other double-click files in this folder once it has run, so this is only needed once.

main() {
  cd "$(dirname "$0")" || { echo "Could not open the RapReaper folder."; return 1; }
  echo "RapReaper: update from a newer RapReaper zip in Downloads"
  echo
  if [ ! -f rapreaper_install.py ]; then
    echo "This file has to be started from the unzipped RapReaper folder."
    echo
    echo "  1. Double-click the RapReaper zip file (macOS unzips it next to the zip)."
    echo "  2. Move the RapReaper folder into your home folder (the one with your name, next to Desktop and Documents)."
    echo "  3. Open that folder and double-click this file there."
    return 1
  fi
  echo "Looking for Python on this Mac ..."
  PY=""
  # Python from python.org installs here (newest first), then Homebrew's. Apple's own /usr/bin/python3 is only a
  # stub that offers to install the developer tools, and is too old in any case, so it is not started.
  for candidate in /Library/Frameworks/Python.framework/Versions/3.{19,18,17,16,15,14,13,12}/bin/python3 \
                   /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
                   /opt/homebrew/bin/python3 /usr/local/bin/python3 /opt/local/bin/python3; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
      PY="$candidate"
      break
    fi
  done
  if [ -z "$PY" ]; then
    for candidate in $(type -a -p python3 2>/dev/null); do
      case "$candidate" in /usr/bin/python3) continue ;; esac
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
        PY="$candidate"
        break
      fi
    done
  fi
  if [ -z "$PY" ]; then
    echo "Python 3.12 or newer is not installed on this Mac yet."
    echo
    echo "  1. Go to  https://www.python.org/downloads/macos/  and download the 'macOS 64-bit universal2 installer'"
    echo "     of the current Python 3. Open it and click through it (the standard choices are right)."
    echo "  2. Double-click this file again."
    return 1
  fi
  echo "Using $PY"
  echo
  "$PY" rapreaper_install.py --update "$@"
}

main "$@"; echo; echo "(You can close this window.)"; exit
