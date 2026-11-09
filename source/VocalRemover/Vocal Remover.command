#!/bin/bash
# Vocal Remover for the Mac: double-click this file. It checks the program files, lets macOS run them
# (they are not signed by a company) and starts the program, whose window opens in your browser.
cd "$(dirname "$0")" || exit 1
# Which Macs this copy is for (written in by the release build): arm64 = Apple chip (M1 and later), x86_64 = Intel.
PACKAGE_CHIP="__PACKAGE_CHIP__"
# Ask the hardware, not this Terminal: a Terminal running under Rosetta says x86_64 on an Apple-chip Mac.
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then MAC_CHIP=arm64; else MAC_CHIP=x86_64; fi
if [ "$PACKAGE_CHIP" = "arm64" ] && [ "$MAC_CHIP" = "x86_64" ]; then
  echo "This copy of the Vocal Remover is for Macs with an Apple chip (M1 and later); this is an Intel Mac."
  echo "Download the RapReaper zip whose name ends in -Mac-Intel.zip and use the Vocal Remover in it."
  read -r -p "Press Enter to close."
  exit 1
fi
if [ "$PACKAGE_CHIP" = "x86_64" ] && [ "$MAC_CHIP" = "arm64" ]; then
  echo "Note: this is the Intel Mac version of the Vocal Remover. It runs on this Mac through Rosetta, more slowly;"
  echo "the RapReaper zip whose name ends in -Mac.zip has the version for Apple chips."
  echo
fi
# The expected checksums are written in by the release build; a changed or damaged file is not started.
PROGRAM_SHA256="__PROGRAM_SHA256__"
LIBRARY_SHA256="__LIBRARY_SHA256__"
check() {
  local want="$2" got
  got=$(shasum -a 256 "$1" | cut -d' ' -f1)
  if [ "$got" != "$want" ]; then
    echo "$1 is not the file that was shipped (checksum $got). Download the Vocal Remover again."
    read -r -p "Press Enter to close."
    exit 1
  fi
}
check VocalRemover "$PROGRAM_SHA256"
check libonnxruntime.dylib "$LIBRARY_SHA256"
chmod +x VocalRemover 2>/dev/null
# Both files were just checked against their checksums, so it is safe to release them from the download
# quarantine; without this macOS refuses to start an unsigned program with "cannot be opened".
xattr -d com.apple.quarantine VocalRemover libonnxruntime.dylib 2>/dev/null
# Switch off ONNX Runtime's usage reporting to Microsoft. The program sets this too, before it loads the
# library; setting it here as well makes sure it is in place from the very start.
export ORT_DISABLE_TELEMETRY=1
./VocalRemover "$@"
echo
read -r -p "Vocal Remover has stopped. Press Enter to close this window."
