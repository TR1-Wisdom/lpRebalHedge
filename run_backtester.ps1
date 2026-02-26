param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$fileName = "LP_Delta Neutral_EWMA_Backtester.html"
$filePath = Join-Path $scriptDir $fileName

if (-not (Test-Path $filePath)) {
    throw "File not found: $filePath"
}

$urlFile = [System.Uri]::EscapeDataString($fileName)
$url = "http://localhost:$Port/$urlFile"

Write-Host "Starting local server at http://localhost:$Port ..."
Start-Process -FilePath "python" -ArgumentList @("-m", "http.server", "$Port") -WorkingDirectory $scriptDir

Start-Sleep -Seconds 2

Write-Host "Opening $url"
Start-Process $url

Write-Host "Done. Close the Python server process when finished."
