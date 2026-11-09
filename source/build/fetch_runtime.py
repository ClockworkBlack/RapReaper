#!/usr/bin/env python3
"""Fill build/vr_runtime with the Microsoft libraries the release build packs next to the Vocal Remover.

BUILD TOOL, for building RapReaper from its GitHub source (not shipped in the zips). Downloads Microsoft's own
packages, checks each package's SHA-256, takes out the library files, checks each file's SHA-256 against the
checksum the Vocal Remover itself checks (VocalRemover/source/engine.go), and writes them under the names
build_release.py expects. Files already there with the right checksum are kept. Only the standard library is used.

    python3 build/fetch_runtime.py

What comes from where (all unchanged files):
    ONNX Runtime 1.30.0, Windows x64 and macOS arm64      github.com/microsoft/onnxruntime/releases
    ONNX Runtime 1.23.2, macOS x86_64 (Intel Macs)         the same releases page (the last Intel Mac build)
    ONNX Runtime 1.24.4 with DirectML (Windows graphics)   nuget.org, Microsoft.ML.OnnxRuntime.DirectML
    DirectML 1.15.4 (Windows graphics)                     nuget.org, Microsoft.AI.DirectML
    Visual C++ runtime 14.40.33728 (Windows)               Microsoft.VCLibs.140.00.UWPDesktop appx in
                                                           DesktopAppInstaller_Dependencies.zip of winget-cli v1.29.380
"""
import hashlib
import io
import os
import sys
import tarfile
import urllib.request
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RUNTIME = os.path.join(ROOT, "build", "vr_runtime")
CACHE = os.path.join(ROOT, "build", "downloads")

ORT = "https://github.com/microsoft/onnxruntime/releases/download/"
NUGET = "https://api.nuget.org/v3-flatcontainer/"
VCLIBS = "x64/Microsoft.VCLibs.140.00.UWPDesktop_14.0.33728.0_x64.appx"

# package: (url, SHA-256 of the package, [(member inside it, file in build/vr_runtime, SHA-256 of that file)])
PACKAGES = [
    (ORT + "v1.30.0/onnxruntime-win-x64-1.30.0.zip",
     "c6ba983baf5681af108599675d2a89c2d145512d02de28aed0bff177cd0ba949",
     [("onnxruntime-win-x64-1.30.0/lib/onnxruntime.dll", "onnxruntime-win-1.30.0.dll",
       "7e39e2bdbba836d98071ef28620735ba36a47c554cf794585269aecc50fab0da")]),
    (ORT + "v1.30.0/onnxruntime-osx-arm64-1.30.0.tgz",
     "6ebb5062a934537c352937821f9fe9718e7de1a2db1122a93dd363ffd53a7012",
     [("onnxruntime-osx-arm64-1.30.0/lib/libonnxruntime.1.30.0.dylib", "libonnxruntime-osx-arm64-1.30.0.dylib",
       "bcc9110f9d638a119de2db7afb3ba9a1da8085f0cb3401e1c48ae1caf450b6fa")]),
    (ORT + "v1.23.2/onnxruntime-osx-x86_64-1.23.2.tgz",
     "d10359e16347b57d9959f7e80a225a5b4a66ed7d7e007274a15cae86836485a6",
     [("onnxruntime-osx-x86_64-1.23.2/lib/libonnxruntime.1.23.2.dylib", "libonnxruntime-osx-x86_64-1.23.2.dylib",
       "8c9c78de65ea3786f987c0d980e9c1b13a3a5fbc6b3e2965ba05b450e6e4c054")]),
    (NUGET + "microsoft.ml.onnxruntime.directml/1.24.4/microsoft.ml.onnxruntime.directml.1.24.4.nupkg",
     "57e9f11b73437bef7a309496135d4c1f96b1a8e9ddba60013fa27bfc1d788681",
     [("runtimes/win-x64/native/onnxruntime.dll", "onnxruntime-directml-1.24.4.dll",
       "e7eedec6a6f26dc39dc948276a75ef6d2bee3fff944d874ceed0bbd3b97bff40")]),
    (NUGET + "microsoft.ai.directml/1.15.4/microsoft.ai.directml.1.15.4.nupkg",
     "4e7cb7ddce8cf837a7a75dc029209b520ca0101470fcdf275c1f49736a3615b9",
     [("bin/x64-win/DirectML.dll", "DirectML-1.15.4.dll",
       "9c9e6d822561c6c41b90e6994b3e8857cf1d66dbfb1e0c4c799c7c89b4e92da1")]),
    ("https://github.com/microsoft/winget-cli/releases/download/v1.29.380/DesktopAppInstaller_Dependencies.zip",
     "ba875afe9d190f61218985ac0292a99d1db710bf93e13c68944ca9d89f0d82d1",
     [(VCLIBS + "!msvcp140.dll", "msvc/msvcp140.dll", "fd96b594b1f418852893de4cb960f74b43d6905d9966c5532263ca619c9dafc7"),
      (VCLIBS + "!msvcp140_1.dll", "msvc/msvcp140_1.dll", "58c7208525b5dcc34ef0f6482c436dc594040f53f8bb923559bcef636419fc7c"),
      (VCLIBS + "!vcruntime140.dll", "msvc/vcruntime140.dll", "c4ae88c9eb48d63b23560f2e5babbdbf129e3787127688d5383415bbc7347b59"),
      (VCLIBS + "!vcruntime140_1.dll", "msvc/vcruntime140_1.dll", "63b9a905cfb0c822d403e2a511ffa4132b1433285530b3cc062f41e8e0a274ac")]),
]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(url, want):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, url.rsplit("/", 1)[1])
    if os.path.isfile(path) and sha256_file(path) == want:
        return path
    print("  downloading %s" % url, flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "RapReaper-build"})
    with urllib.request.urlopen(req, timeout=120) as r, open(path + ".part", "wb") as out:
        for block in iter(lambda: r.read(1 << 20), b""):
            out.write(block)
    got = sha256_file(path + ".part")
    if got != want:
        os.remove(path + ".part")
        sys.exit("ERROR: %s does not match its checksum (got %s, want %s)" % (url, got, want))
    os.replace(path + ".part", path)
    return path


def member(archive, name):
    if "!" in name:                                    # a file inside an archive inside the archive
        outer, inner = name.split("!", 1)
        with zipfile.ZipFile(archive) as z:
            return member(io.BytesIO(z.read(outer)), inner)
    if isinstance(archive, str) and archive.endswith(".tgz"):
        with tarfile.open(archive, "r:gz") as t:
            for m in t.getmembers():
                if m.name.lstrip("./") == name and m.isfile():
                    return t.extractfile(m).read()
        sys.exit("ERROR: %s is not in %s" % (name, archive))
    with zipfile.ZipFile(archive) as z:
        return z.read(name)


def main():
    done = 0
    for url, want, files in PACKAGES:
        todo = [f for f in files if not (os.path.isfile(os.path.join(RUNTIME, f[1]))
                                         and sha256_file(os.path.join(RUNTIME, f[1])) == f[2])]
        if not todo:
            done += len(files)
            continue
        archive = fetch(url, want)
        for name, target, file_sha in todo:
            data = member(archive, name)
            if sha256(data) != file_sha:
                sys.exit("ERROR: %s in %s is not the expected file" % (name, url))
            dest = os.path.join(RUNTIME, target)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            if dest.endswith(".dylib"):
                os.chmod(dest, 0o755)
            print("  put    build/vr_runtime/%s" % target)
            done += 1
    print("build/vr_runtime: %d library files, every one checked" % done)


if __name__ == "__main__":
    main()
