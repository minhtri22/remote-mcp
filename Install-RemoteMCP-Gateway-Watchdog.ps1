param([switch]$StartNow)

$ErrorActionPreference = "Stop"
$Watchdog = (Resolve-Path (Join-Path $PSScriptRoot "Watch-RemoteMCP-Gateway.ps1")).Path
$Startup = [Environment]::GetFolderPath("Startup")
$Vbs = Join-Path $Startup "RemoteMCP-Gateway-Watchdog.vbs"
$body = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$Watchdog""", 0, False
"@
[System.IO.File]::WriteAllText($Vbs,$body,[System.Text.Encoding]::ASCII)
Write-Host "Installed per-user RemoteMCP gateway watchdog startup entry:"
Write-Host $Vbs

if ($StartNow) {
    $existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and $_.CommandLine -like "*Watch-RemoteMCP-Gateway.ps1*"
    }
    if (-not $existing) {
        Start-Process powershell.exe -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$Watchdog) -WindowStyle Hidden | Out-Null
        Write-Host "Gateway watchdog started."
    } else {
        Write-Host "Gateway watchdog is already running."
    }
}
