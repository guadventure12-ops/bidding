param([switch]$OCR, [switch]$Dev)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskVenvPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskVenvPython -PathType Leaf)) {
    $taskLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($taskLauncher) {
        & $taskLauncher.Source -3.12 -m venv (Join-Path $taskRoot '.venv')
    } else {
        $taskPython = (Get-Command python -ErrorAction Stop).Source
        & $taskPython -c "import sys; assert sys.version_info[:2] == (3,12), 'Install Python 3.12 first.'"
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 and add it to PATH.' }
        & $taskPython -m venv (Join-Path $taskRoot '.venv')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv with Python 3.12.' }
}
& $taskVenvPython -c "import sys; assert sys.version_info[:2] == (3,12), 'This release is tested with Python 3.12.'"
if ($LASTEXITCODE -ne 0) { throw 'The existing .venv must use Python 3.12; no files were deleted.' }
& $taskVenvPython -m pip install -r (Join-Path $taskRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Core dependency installation failed.' }
if ($OCR) {
    & $taskVenvPython -m pip install -r (Join-Path $taskRoot 'requirements-ocr.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Optional OCR dependency installation failed.' }
}
if ($Dev) {
    & $taskVenvPython -m pip install -r (Join-Path $taskRoot 'requirements-dev.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Development dependency installation failed.' }
}
& $taskVenvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Installed dependencies have incompatible requirements.' }
Write-Output 'Installation complete. Run .\Start.ps1 to open the local workbench.'
