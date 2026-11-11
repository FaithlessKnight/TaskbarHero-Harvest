param([switch]$Diagnose)
$ErrorActionPreference = 'Stop'
$candidates = @(
    (Join-Path $PSScriptRoot 'runtime\python.exe'),
    "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
)
$runtime = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (!$runtime) {
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*\WindowsApps\*') { $runtime = $command.Source }
}
if (!$runtime) { throw 'Python 3.10+ with tkinter is required.' }
$entryScript = if ($Diagnose) { 'process_diagnostics.py' } else { 'app.py' }
& $runtime -X utf8 (Join-Path $PSScriptRoot $entryScript)
exit $LASTEXITCODE
