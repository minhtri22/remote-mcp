$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "Start-RemoteMCP-Gateway.ps1"
& $script -Restart
exit $LASTEXITCODE
