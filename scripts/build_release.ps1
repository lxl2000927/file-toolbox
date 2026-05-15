$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$Version = "1.1.2.0"

python -m pip install --upgrade pip
python -m pip install --upgrade pyinstaller

if (Test-Path "dist\\PDF Split") { Remove-Item "dist\\PDF Split" -Recurse -Force }
if (Test-Path "dist\\PDF Split.exe") { Remove-Item "dist\\PDF Split.exe" -Force }
if (Test-Path "dist\\PDF Split_${Version}.exe") { Remove-Item "dist\\PDF Split_${Version}.exe" -Force }

pyinstaller --noconfirm --clean packaging/pdf_split.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

pyinstaller --noconfirm --clean packaging/pdf_split_onefile.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path "dist\\PDF Split.exe") {
    Move-Item -Force "dist\\PDF Split.exe" "dist\\PDF Split_${Version}.exe"
}

Write-Host ""
Write-Host "Build finished."
Write-Host "Portable: dist\\PDF Split\\PDF Split.exe (copy the whole dist\\PDF Split folder)"
Write-Host "Single EXE: dist\\PDF Split_${Version}.exe"

