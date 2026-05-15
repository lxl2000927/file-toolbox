$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

python -m pip install --upgrade pip
python -m pip install --upgrade pyinstaller

if (Test-Path "dist\\PDF Split") { Remove-Item "dist\\PDF Split" -Recurse -Force }
if (Test-Path "dist\\PDF Split.exe") { Remove-Item "dist\\PDF Split.exe" -Force }

pyinstaller --noconfirm --clean packaging/pdf_split.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path "dist\\PDF Split.exe") { Remove-Item "dist\\PDF Split.exe" -Force }

Write-Host ""
Write-Host "Build finished."
Write-Host "Output: dist\\PDF Split\\PDF Split.exe (copy the whole dist\\PDF Split folder)"
