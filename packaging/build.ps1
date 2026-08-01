# Build dist\dokey\dokey.exe from a clean virtual environment.
#
# The build venv holds exactly what the executable ships -- dokey and the app
# extras (streamlit, pywebview) plus the two optional readers worth bundling
# (pymupdf for printed-TOC and scan detection, xlrd for legacy .xls). Layout
# converters stay bring-your-own, exactly as they do from source.
#
#   powershell -File packaging\build.ps1 [-Python <path-to-python>]

param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$venv = Join-Path $root ".venv-build"

if (-not (Test-Path $venv)) {
    & $Python -m venv $venv
}
$py = Join-Path $venv "Scripts\python.exe"

& $py -m pip install --upgrade pip wheel | Out-Host
& $py -m pip install $root streamlit pywebview pymupdf xlrd pyinstaller | Out-Host

& $py -m PyInstaller --noconfirm --clean `
    --name dokey `
    --icon (Join-Path $root "dokey\assets\logo.ico") `
    --collect-all streamlit `
    --collect-all webview `
    --collect-submodules dokey `
    --hidden-import fitz `
    --hidden-import xlrd `
    --add-data ((Join-Path $root "dokey\ui_app.py") + ";dokey") `
    --add-data ((Join-Path $root "dokey\assets") + ";dokey\assets") `
    --distpath (Join-Path $root "dist") `
    --workpath (Join-Path $root "build\pyinstaller") `
    --specpath (Join-Path $root "build") `
    (Join-Path $root "packaging\entry.py")

Write-Host "Built: $(Join-Path $root 'dist\dokey\dokey.exe')"
