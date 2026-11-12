# RapReaper's Local AI, one time: downloads llama.cpp's llama-server (build b12250 of github.com/ggml-org/llama.cpp, MIT
# licence) and one open AI model (Qwen 3.5 by the Qwen team, Apache 2.0 licence; the GGUF file by unsloth on
# huggingface.co), each checked against its published SHA-256, into LocalAI\runtime and LocalAI\models. The installer then
# checks every file again (--local-ai-check). Nothing else is downloaded or changed. Run through "Get Local AI.bat".
param([string]$Choice = '')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtime = Join-Path $root 'LocalAI\runtime'
$models = Join-Path $root 'LocalAI\models'
New-Item -ItemType Directory -Force -Path $runtime, $models | Out-Null

$build = 'b12250'
$zip = @{ Name = "llama-$build-bin-win-vulkan-x64.zip"; Size = '38 MB';
          Sha = '00ba40348fa2c5f60c605c00f67a670725e48781977c4a365de5c0ec8aa13845';
          Url = "https://github.com/ggml-org/llama.cpp/releases/download/$build/llama-$build-bin-win-vulkan-x64.zip" }
$small = @{ Name = 'Qwen3.5-4B-Q4_K_M.gguf'; Label = 'Qwen 3.5 4B'; Size = '2.7 GB'; Bytes = 2740937888;
            Sha = '00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4';
            Url = 'https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q4_K_M.gguf' }
$large = @{ Name = 'Qwen3.5-9B-Q4_K_M.gguf'; Label = 'Qwen 3.5 9B'; Size = '5.7 GB'; Bytes = 5680522464;
            Sha = '03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8';
            Url = 'https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/3885219b6810b007914f3a7950a8d1b469d598a5/Qwen3.5-9B-Q4_K_M.gguf' }

function Sha($path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower() }

function Fetch($f, $dest) {
    if ((Test-Path -LiteralPath $dest) -and ((Sha $dest) -eq $f.Sha)) { Write-Host "  already here, checksum correct"; return }
    $part = $dest + '.part'
    if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part -Force }
    Write-Host ("  downloading " + $f.Url)
    # curl.exe (in Windows 10 and 11) shows progress and is much faster than Invoke-WebRequest for a large file
    $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (Test-Path -LiteralPath $curl) {
        & $curl -L --fail --progress-bar -o $part $f.Url
        if ($LASTEXITCODE -ne 0) { Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue; throw "the download failed" }
    } else {
        Invoke-WebRequest -UseBasicParsing -Uri $f.Url -OutFile $part
    }
    Write-Host "  checking it ..."
    $got = Sha $part
    if ($got -ne $f.Sha) { Remove-Item -LiteralPath $part -Force; throw ("{0} does not match its published checksum (got {1}); it was deleted" -f $f.Name, $got) }
    Move-Item -LiteralPath $part -Destination $dest -Force
    Write-Host "  checksum matches the published one"
}

$gb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
Write-Host ("This PC has about {0} GB of memory." -f $gb)
if ($gb -lt 7) {
    Write-Host "That is less than the 8 GB the smaller model needs to run well. It may still work, slowly, with nothing else open."
}
$model = $small
if ($Choice -eq 'large') { $model = $large }
elseif ($Choice -ne 'small' -and $gb -ge 15) {
    Write-Host ""
    Write-Host "Which AI model?"
    Write-Host ("  1  {0} ({1}): quicker; good with the RapReaper tools. Best without a strong graphics card." -f $small.Label, $small.Size)
    Write-Host ("  2  {0} ({1}): better answers; wants 16 GB of memory and is quick only with a good graphics card." -f $large.Label, $large.Size)
    $answer = Read-Host "Type 1 or 2 and press Enter (Enter alone takes 1)"
    if ($answer.Trim() -eq '2') { $model = $large }
}
$drive = (Get-Item -LiteralPath $root).PSDrive
if ($drive -and $drive.Free -and ($drive.Free -lt ($model.Bytes + 400MB))) {
    throw ("{0} needs about {1} free on this drive; only {2:N1} GB are free" -f $model.Label, $model.Size, ($drive.Free / 1GB))
}

Write-Host ("`n[1/3] llama.cpp {0} ({1})" -f $build, $zip.Size)
$zipPath = Join-Path $runtime $zip.Name
Fetch $zip $zipPath
Write-Host "`n[2/3] unpacking llama.cpp into LocalAI\runtime"
Get-ChildItem -LiteralPath $runtime -Force | Where-Object { $_.Name -ne '.keep' -and $_.Name -ne $zip.Name } | Remove-Item -Recurse -Force
Expand-Archive -LiteralPath $zipPath -DestinationPath $runtime -Force
Remove-Item -LiteralPath $zipPath -Force
Write-Host "  done"
Write-Host ("`n[3/3] {0} ({1}): the AI model" -f $model.Label, $model.Size)
Fetch $model (Join-Path $models $model.Name)
Set-Content -LiteralPath (Join-Path $root 'LocalAI\models\chosen-model.txt') -Value $model.Name -Encoding ASCII
