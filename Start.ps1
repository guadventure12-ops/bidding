param(
    [switch]$NoBrowser,
    [ValidateRange(1, 65535)][int]$Port = $(if ($env:BIDDING_PORT) { [int]$env:BIDDING_PORT } else { 8765 })
)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskUrl = "http://127.0.0.1:$Port"
$taskHasher = [Security.Cryptography.SHA256]::Create()
try {
    $taskNormalizedRoot = [IO.Path]::GetFullPath($taskRoot).Replace('/', '\').ToLowerInvariant()
    $taskWorkspaceId = ([BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskNormalizedRoot)))).Replace('-', '').ToLowerInvariant()
} finally { $taskHasher.Dispose() }
function Get-TaskHealth([int]$TimeoutSec) {
    return Invoke-RestMethod -Uri "$taskUrl/api/health" -TimeoutSec $TimeoutSec
}
function Test-TaskHealth($Response) {
    return ($Response.ok -eq $true -and $Response.app_id -eq 'bidding-local' -and $Response.local_only -eq $true -and $Response.workspace_id -eq $taskWorkspaceId -and $Response.process_id -gt 0)
}
$taskHealthy = $false
$taskOtherService = $false
try {
    $taskResponse = Get-TaskHealth -TimeoutSec 2
    $taskHealthy = Test-TaskHealth $taskResponse
    $taskOtherService = -not $taskHealthy
} catch { }
if ($taskOtherService) { throw "Port $Port is used by another service or workspace. This workspace was not started." }
$taskLogs = Join-Path $taskRoot 'data\logs'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
if (-not $taskHealthy) {
    # Reject any listener before importing the app or touching its data.
    $taskProbe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
    try { $taskProbe.Start() }
    catch { throw "Port $Port is used by another service. This workspace was not started." }
    finally { $taskProbe.Stop() }
    $taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) { throw 'The local .venv is missing. Run .\Setup.ps1 first.' }
    $taskPreviousPort = $env:BIDDING_PORT
    try {
        $env:BIDDING_PORT = [string]$Port
        $taskProc = Start-Process -FilePath $taskPython -ArgumentList @(('"' + (Join-Path $taskRoot 'run.py') + '"')) -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLogs "server-$Port.log") -RedirectStandardError (Join-Path $taskLogs "server-$Port-error.log") -PassThru
    } finally { $env:BIDDING_PORT = $taskPreviousPort }
    for ($taskAttempt = 0; $taskAttempt -lt 60; $taskAttempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $taskResponse = Get-TaskHealth -TimeoutSec 1
            $taskHealthy = Test-TaskHealth $taskResponse
            if ($taskHealthy) { break }
        } catch { }
        if ($taskProc.HasExited) { break }
    }
}
if (-not $taskHealthy) { throw "Startup failed. See $taskLogs\server-$Port-error.log" }
# Use the serving process ID, never a child which lost the database lock.
Set-Content -LiteralPath (Join-Path $taskLogs "server-$Port.pid") -Value ([int]$taskResponse.process_id) -Encoding ascii
Write-Output "Bidding workspace is ready: $taskUrl"
if (-not $NoBrowser) { Start-Process $taskUrl }
