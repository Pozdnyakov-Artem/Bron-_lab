param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv .venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv .venv
    } else {
        $bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
        if (-not (Test-Path -LiteralPath $bundledPython)) { throw 'Install Python 3.11 or newer, then run this script again.' }
        & $bundledPython -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create Python environment.' }
}
& './.venv/Scripts/python.exe' -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& './.venv/Scripts/python.exe' -m bronylab validate --config config.yaml
if ($LASTEXITCODE -ne 0) { throw 'Configuration is invalid.' }
Write-Host "Open http://127.0.0.1:$Port in your browser. Stop: Ctrl+C"
& './.venv/Scripts/python.exe' -m bronylab serve --config config.yaml --port $Port
