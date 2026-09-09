$env:IDF_TOOLS_PATH = "D:\gongju\Espressif"
$env:IDF_PATH = "D:\gongju\Espressif\frameworks\esp-idf-v5.4.4"
Set-Location D:\aijiaohu\week1\firmware\esp32_imu

Write-Output "=== load export.ps1 ==="
. "$env:IDF_PATH\export.ps1"
if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) { Write-Output "export failed: $LASTEXITCODE"; exit 1 }

Write-Output "=== set-target esp32s3 ==="
idf.py set-target esp32s3
if ($LASTEXITCODE -ne 0) { Write-Output "set-target failed"; exit 1 }

Write-Output "=== build ==="
idf.py build
Write-Output "BUILD_EXITCODE=$LASTEXITCODE"
