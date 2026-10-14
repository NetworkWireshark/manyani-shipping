$ErrorActionPreference = 'Stop'
$taskAppRoot = $PSScriptRoot
$taskPython = 'C:\Users\Administrateur\AppData\Local\Programs\Python\Python312\python.exe'
$taskServerScript = Join-Path $taskAppRoot 'server.py'
$taskLocalLogin = [System.IO.File]::ReadAllBytes((Join-Path $taskAppRoot 'public\login.html'))
$taskHasher = [System.Security.Cryptography.SHA256]::Create()
$taskExpectedHash = [System.BitConverter]::ToString($taskHasher.ComputeHash($taskLocalLogin))
$taskRestartPorts = @(80, 8080)
foreach ($taskPort in $taskRestartPorts) {
    $taskListenerLines = @(netstat -ano -p tcp | Where-Object {
        $taskFields = $_.Trim() -split '\s+'
        $taskFields.Length -ge 5 -and $taskFields[1] -match (':' + $taskPort + '$') -and $taskFields[3] -eq 'LISTENING'
    })
    $taskOwnerIds = @($taskListenerLines | ForEach-Object { [int](($_.Trim() -split '\s+')[4]) } | Select-Object -Unique)
    if ($taskOwnerIds.Count -eq 0) { continue }
    $taskWebClient = New-Object System.Net.WebClient
    try { $taskRemoteLogin = $taskWebClient.DownloadData("http://127.0.0.1:$taskPort/login") }
    finally { $taskWebClient.Dispose() }
    $taskActualHash = [System.BitConverter]::ToString($taskHasher.ComputeHash($taskRemoteLogin))
    if ($taskActualHash -ne $taskExpectedHash) { throw "Port $taskPort does not serve this application. No process stopped." }
    foreach ($taskOwnerId in $taskOwnerIds) {
        $taskOwner = Get-Process -Id $taskOwnerId -ErrorAction Stop
        if ($taskOwner.ProcessName -notmatch '^python(?:w)?(?:3.*)?$') { throw "Port $taskPort owner is not Python. No process stopped." }
        Stop-Process -Id $taskOwnerId -ErrorAction Stop
        Wait-Process -Id $taskOwnerId -Timeout 10 -ErrorAction SilentlyContinue
    }
    $env:PORT = [string]$taskPort
    $env:PORT_STRICT = '1'
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $taskArgument = '"' + $taskServerScript + '"'
    Start-Process -FilePath $taskPython -ArgumentList $taskArgument -WorkingDirectory $taskAppRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $taskAppRoot "data\server-tracking-$taskPort.log") `
        -RedirectStandardError (Join-Path $taskAppRoot "data\server-tracking-$taskPort-error.log") | Out-Null
    Start-Sleep -Seconds 2
    $taskHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$taskPort/api/health" -TimeoutSec 5
    if (-not $taskHealth.ok) { throw "Server health check failed on port $taskPort" }
    Write-Host "Manyani Shipping restarted on port $taskPort"
}
$taskHasher.Dispose()
Write-Host 'Refresh the web application. Android users can tap Actualiser.'
