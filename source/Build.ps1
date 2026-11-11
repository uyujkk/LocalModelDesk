param([string] $Python = 'python', [string] $Output = (Join-Path $PSScriptRoot '..\dist'))
$ErrorActionPreference = 'Stop'
$Python = (Get-Command $Python -CommandType Application -ErrorAction Stop).Source
$work = Join-Path $PSScriptRoot '..\build'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $work 'cache'
$env:PYTHONIOENCODING = 'utf-8'
New-Item -ItemType Directory -Path $work -Force | Out-Null
$buildPath = $env:Path
try {
    # Avoid collecting unrelated native DLLs from tools on the developer machine's PATH.
    $env:Path = (Split-Path $Python -Parent) + ';' + (Join-Path $env:WINDIR 'System32') + ';' + $env:WINDIR
    & $Python -m PyInstaller --clean --noconfirm --onedir --windowed --noupx --name LocalModelDesk `
    --icon (Join-Path $PSScriptRoot 'assets\QwenDesk.ico') `
    --manifest (Join-Path $PSScriptRoot 'app.manifest') `
    --add-data ((Join-Path $PSScriptRoot 'assets') + ';assets') `
    --distpath $Output --workpath $work --specpath $work `
    (Join-Path $PSScriptRoot 'app.py')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
}
finally { $env:Path = $buildPath }
Write-Host "Built: $(Join-Path $Output 'LocalModelDesk\LocalModelDesk.exe')"
