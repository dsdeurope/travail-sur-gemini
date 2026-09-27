$ErrorActionPreference='Stop'
$runtime=Join-Path (Split-Path $PSScriptRoot -Parent) 'work\google-runtime'
$python=Join-Path $runtime 'google-cloud-sdk\platform\bundledpython\python.exe'
$ready=Join-Path $runtime 'installation-complete.txt'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $ready)) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Gemini-Comptes.ps1') -Action Installer
    if ($LASTEXITCODE -ne 0) { throw 'La preparation de Google a echoue.' }
}
$info=Join-Path $runtime 'browser-session.json'
if (Test-Path -LiteralPath $info) {
    try {
        $session=Get-Content -LiteralPath $info -Raw | ConvertFrom-Json
        if ($session.url -match '^http://127\.0\.0\.1:8765/\?session=[A-Za-z0-9_-]+$') {
            $response=Invoke-WebRequest -Uri $session.url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { Start-Process $session.url; exit }
        }
    } catch { }
}
$server=Join-Path $PSScriptRoot 'gemini-web\server.py'
Start-Process -FilePath $python -ArgumentList @(('"'+$server+'"')) -WindowStyle Hidden
