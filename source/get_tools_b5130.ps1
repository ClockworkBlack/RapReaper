# Downloads ffmpeg (for MP3/M4A copies of exports) and whisper.cpp + a speech model (for vocal_clarity's
# word check) into this RapReaper folder. Run through "Get ffmpeg and Transcriber.bat". Nothing is
# allowed for RapReaper here: you do that yourself afterwards with the two "Allow ... .bat" files.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Join-Path $root 'tools'
$models = Join-Path $root 'kit\models'
$tmp = Join-Path $env:TEMP ('rapreaper_dl_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $tools, $models, $tmp | Out-Null

$ffmpegUrl  = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip'
$whisperUrl = 'https://github.com/ggml-org/whisper.cpp/releases/download/b5130/whisper-bin-x64.zip'
$modelUrl   = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin'
$modelSha   = 'a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002'   # published by Hugging Face for this file

# Every download goes to a temporary name first and is moved into place only once it is complete (and, where a
# published checksum is known, only once it matches). ffmpeg's and whisper.cpp's builds have no fixed checksum that
# could be written here (the ffmpeg build is renewed every day), so for those two the source (github.com) is what
# vouches for them; the Allow step afterwards records the checksum of exactly the file you allow.
function Get-File($url, $dest) {
    Write-Host ("  downloading " + $url)
    $part = $dest + '.part'
    if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part -Force }
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $part
    } catch {
        Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
        throw
    }
    Move-Item -LiteralPath $part -Destination $dest -Force
}
function Sha($path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower() }
# a program folder is filled under a temporary name and renamed when it is complete, so that a copy that broke off
# is never taken for a finished one ("already there") on the next run
function Install-Folder($from, $dest) {
    $staging = $dest + '.part'
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    Copy-Item -Path (Join-Path $from '*') -Destination $staging -Recurse -Force
    if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
    Move-Item -LiteralPath $staging -Destination $dest
}

try {
    Write-Host "`n[1/3] ffmpeg"
    $dest = Join-Path $tools 'ffmpeg'
    if (Test-Path (Join-Path $dest 'ffmpeg.exe')) { Write-Host "  already there" } else {
        $zip = Join-Path $tmp 'ffmpeg.zip'; Get-File $ffmpegUrl $zip
        $x = Join-Path $tmp 'ffmpeg'; Expand-Archive -LiteralPath $zip -DestinationPath $x -Force
        $exe = Get-ChildItem -Path $x -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1
        if (-not $exe) { throw 'ffmpeg.exe was not in the download' }
        $one = Join-Path $tmp 'ffmpeg_only'
        New-Item -ItemType Directory -Force -Path $one | Out-Null
        Copy-Item -LiteralPath $exe.FullName -Destination (Join-Path $one 'ffmpeg.exe') -Force
        Install-Folder $one $dest
    }

    Write-Host "`n[2/3] whisper.cpp"
    $dest = Join-Path $tools 'whisper'
    if (Test-Path (Join-Path $dest 'whisper-cli.exe')) { Write-Host "  already there" } else {
        $zip = Join-Path $tmp 'whisper.zip'; Get-File $whisperUrl $zip
        $x = Join-Path $tmp 'whisper'; Expand-Archive -LiteralPath $zip -DestinationPath $x -Force
        $exe = Get-ChildItem -Path $x -Recurse -Filter 'whisper-cli.exe' | Select-Object -First 1
        if (-not $exe) { $exe = Get-ChildItem -Path $x -Recurse -Filter 'main.exe' | Select-Object -First 1 }
        if (-not $exe) { throw 'no whisper-cli.exe (or main.exe) in the download' }
        Install-Folder $exe.DirectoryName $dest   # the DLLs must sit next to the program
    }

    Write-Host "`n[3/3] speech model (ggml-base.en.bin, about 148 MB)"
    $model = Join-Path $models 'ggml-base.en.bin'
    if ((Test-Path -LiteralPath $model) -and ((Sha $model) -eq $modelSha)) { Write-Host "  already there, checksum correct" } else {
        $dl = Join-Path $tmp 'ggml-base.en.bin'
        Get-File $modelUrl $dl
        if ((Sha $dl) -ne $modelSha) { Remove-Item -LiteralPath $dl -Force; throw 'the model file does not match its published checksum, so it was deleted' }
        if (Test-Path -LiteralPath $model) { Move-Item -LiteralPath $model -Destination ($model + '.damaged') -Force; Write-Host "  the model file that was there was not the published one; moved aside to ggml-base.en.bin.damaged" }
        $part = $model + '.part'
        Copy-Item -LiteralPath $dl -Destination $part -Force
        if ((Sha $part) -ne $modelSha) { Remove-Item -LiteralPath $part -Force; throw 'the model file was damaged while it was copied into kit\models' }
        Move-Item -LiteralPath $part -Destination $model -Force
        Write-Host "  checksum matches the published one"
    }
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

$ff = Join-Path $tools 'ffmpeg\ffmpeg.exe'
$wh = Get-ChildItem -Path (Join-Path $tools 'whisper') -Filter 'whisper-cli.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $wh) { $wh = Get-ChildItem -Path (Join-Path $tools 'whisper') -Filter 'main.exe' | Select-Object -First 1 }
Write-Host "`nDone. Files are in:"
Write-Host ("  " + $ff + "   SHA-256 " + (Sha $ff))
Write-Host ("  " + $wh.FullName + "   SHA-256 " + (Sha $wh.FullName))
Write-Host ("  " + $model)
Write-Host "`nNext (you decide, RapReaper never allows a program by itself):"
Write-Host "  1. Double-click 'Allow ffmpeg.bat' and paste:  $ff"
Write-Host ("  2. Double-click 'Allow Transcriber.bat' and paste:  " + $wh.FullName)
Write-Host "  3. Restart Claude, then ask me to run the RapReaper self-test / 'status'."
