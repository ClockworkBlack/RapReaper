#!/bin/bash
# RapReaper for macOS: start the Local AI. Double-click this file and keep its window open while you use the Local AI.
# It checks llama.cpp's files and the AI model that Get Local AI downloaded, starts llama-server on this Mac only
# (127.0.0.1) with RapReaper's tools, and opens its chat window in your browser. Close the window to stop it.

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
  if [ ! -f rapreaper_install.py ]; then echo "This file has to be started from the unzipped RapReaper folder."; return 1; fi
  PY="$(find_python)" || { echo "Python 3.12 or newer was not found; run Install RapReaper.command first."; return 1; }
  "$PY" rapreaper_install.py --local-ai "$@"
}

main "$@"; echo; echo "(You can close this window.)"; exit
