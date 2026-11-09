# Building RapReaper yourself

You don't need to build anything to use RapReaper: the zips on the GitHub page are ready to unzip and install.
This page is for anyone who wants to build those zips from the source, or check that they match it.

RapReaper's own files are under the MIT licence (`LICENSE.txt`). Third-party parts keep their own licences
(`NOTICE.txt`, `VocalRemover/licenses/`).

## What you need

| What | Why |
|---|---|
| **Go 1.27.3** ([go.dev/dl](https://go.dev/dl/)) | Builds the Vocal Remover. Go 1.27 or newer works; 1.27.3 gives files identical byte for byte to the released ones. No C compiler. |
| **Python 3.12 or newer** | Runs the build script and the installer. |
| An internet connection, once | `build/fetch_runtime.py` downloads Microsoft's runtime libraries. |

Nothing else: no Xcode, no Visual Studio, no PyTorch. Windows, macOS and Linux can each build all three zips.

## Build the zips

From the `source` folder of the repository (the folder that holds `rapreaper_install.py`):

```
python3 build/fetch_runtime.py              # Microsoft's libraries into build/vr_runtime, each checked by SHA-256
python3 build/build_release.py --no-pdf     # the three zips and SHA256SUMS.txt into dist/
```

On Windows, type `py` instead of `python3`. The result:

| File | For |
|---|---|
| `dist/RapReaper-<version>-Windows.zip` | Windows 10 or 11, 64-bit |
| `dist/RapReaper-<version>-Mac.zip` | Macs with an Apple chip (M1 and later), macOS 14 or newer |
| `dist/RapReaper-<version>-Mac-Intel.zip` | Intel Macs, macOS 13 or newer |

`--no-pdf` keeps the guide PDFs in `docs/`. Without it, the guides are rebuilt from `docs/*.md` with Playwright
and Chromium (`pip install playwright markdown`, `python3 -m playwright install chromium`).

To check a released zip against the source: build with Go 1.27.3 and compare the `MANIFEST.sha256` inside your
zip with the one inside the released zip. Every line must match (the zip files themselves differ only in their
timestamps).

## The Vocal Remover's model

The model files are not in the repository or the zips. They are large (237 MB for the default one), and their
authors published no licence that allows anyone else to pass the files on. Each person gets the model from its
author instead:

1. Install RapReaper from its zip (Install RapReaper.bat or .command).
2. Double-click **Get Vocal Remover Model** (`.bat` on Windows, `.command` on a Mac). It downloads the model's
   two source files from [unwa's Hugging Face page](https://huggingface.co/pcunwa/Kim-Mel-Band-Roformer-FT),
   `config_kimmel_unwa_ft.yaml` and `kimmel_unwa_ft2.ckpt` (913 MB), and checks each against its published SHA-256.
3. It then runs `rapreaper_install.py --build-model`, which builds `VocalRemover/models/MelRoformer-KimFT2-int8.onnx`
   with `build/build_roformer_onnx.py` (NumPy only, about a minute, about 3 GB of memory) and checks it against the
   model's published checksum. The converter is deterministic, so the file is byte for byte the one RapReaper and
   the Vocal Remover check before loading. The 913 MB download is deleted afterwards.

You can also put those two files into Downloads by hand and run the installer: its step 7 builds the model the same
way. Model pieces (`MelRoformer-KimFT2-int8.onnx.part1of8` ...) from an earlier delivery still work.

The optional BS-Roformer model (the "Two networks" and BS-Roformer modes) cannot yet be rebuilt byte for byte from
its public checkpoint, so it is used only when you already have the file.

## Testing

`DEVELOPMENT.md` describes the test suites (`tests/test_audio.py`, about 3 minutes; `tests/e2e.py`, 1.5 to 2 hours,
which needs Lua 5.4) and the rules that keep RapReaper safe.

## Building on GitHub

`.github/workflows/build.yml` in the repository runs the same two commands on every tag `v*` (and when started by
hand), checks the versions, runs the audio tests, and attaches the three zips and `SHA256SUMS.txt` to the release.
