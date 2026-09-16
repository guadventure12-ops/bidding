param([ValidateRange(1, 65535)][int]$Port = $(if ($env:BIDDING_PORT) { [int]$env:BIDDING_PORT } else { 8765 }))
$ErrorActionPreference = 'Stop'
$taskPidPath = Join-Path $PSScriptRoot "data\logs\server-$Port.pid"
if (-not (Test-Path -LiteralPath $taskPidPath)) {
    Write-Output 'No launcher process record for this port. Nothing was stopped.'
    exit 0
}
$taskServerId = [int](Get-Content -LiteralPath $taskPidPath -Raw)
$taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$taskServerId" -ErrorAction SilentlyContinue
$taskExpected = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'run.py'))
if ($taskProcess -and $taskProcess.CommandLine -and $taskProcess.CommandLine.Contains(('"' + $taskExpected + '"'))) {
    Stop-Process -Id $taskServerId
    Remove-Item -LiteralPath $taskPidPath
    Write-Output 'Bidding workspace stopped. Data is retained.'
} elseif (-not $taskProcess) {
    Remove-Item -LiteralPath $taskPidPath
    Write-Output 'The recorded process has already exited. Data is retained.'
} else {
    throw 'The recorded process does not belong to this workspace. Nothing was stopped.'
}
