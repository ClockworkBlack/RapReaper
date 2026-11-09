# Downloads the two public files the Vocal Remover's model is built from (the Kim FT2 Mel-Band Roformer, by unwa, on
# huggingface.co) into VocalRemover\models, checks each against its published SHA-256, then has the installer build
# the model (build\build_roformer_onnx.py, NumPy only) and check the result against the model's published checksum.
# Run through "Get Vocal Remover Model.bat".
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$models = Join-Path $root 'VocalRemover\models'
New-Item -ItemType Directory -Force -Path $models | Out-Null

$base = 'https://huggingface.co/pcunwa/Kim-Mel-Band-Roformer-FT/resolve/main/'
$files = @(
    @{ Name = 'config_kimmel_unwa_ft.yaml'; Sha = '0942cb7da8e86ac1be840cc7b9647831c11ffe00327a0b239df256031e6c8355'; Size = '2 KB' },
    @{ Name = 'kimmel_unwa_ft2.ckpt';       Sha = '5ed7b9e4c2eebbec7a7e5e8113058f7b68ba5e6048db8eaccfbbeb884c7884c0'; Size = '913 MB' }
)
function Sha($path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower() }

$target = Join-Path $models 'MelRoformer-KimFT2-int8.onnx'
if ((Test-Path -LiteralPath $target) -and ((Sha $target) -eq 'f81d9265cf57b66808db38d72ae136f3d74f3888f642e085478288f774b5a0d4')) {
    Write-Host "The model is already in VocalRemover\models, checksum correct. Nothing to do."
    exit 0
}
$i = 0
foreach ($f in $files) {
    $i++
    $dest = Join-Path $models $f.Name
    Write-Host ("`n[{0}/3] {1} ({2})" -f $i, $f.Name, $f.Size)
    if ((Test-Path -LiteralPath $dest) -and ((Sha $dest) -eq $f.Sha)) { Write-Host "  already here, checksum correct"; continue }
    $part = $dest + '.part'
    if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part -Force }
    Write-Host ("  downloading " + $base + $f.Name)
    # curl.exe (in Windows 10 and 11) shows progress and is much faster than Invoke-WebRequest for a large file
    $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (Test-Path -LiteralPath $curl) {
        & $curl -L --fail --progress-bar -o $part ($base + $f.Name)
        if ($LASTEXITCODE -ne 0) { Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue; throw "the download failed" }
    } else {
        Invoke-WebRequest -UseBasicParsing -Uri ($base + $f.Name) -OutFile $part
    }
    $got = Sha $part
    if ($got -ne $f.Sha) { Remove-Item -LiteralPath $part -Force; throw ("{0} does not match its published checksum (got {1}); it was deleted" -f $f.Name, $got) }
    Move-Item -LiteralPath $part -Destination $dest -Force
    Write-Host "  checksum matches the published one"
}
Write-Host "`n[3/3] building the model (about a minute)"
