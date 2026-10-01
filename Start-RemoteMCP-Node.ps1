param(
    [string]$RuntimeDir = (Join-Path $env:LOCALAPPDATA "RemoteMCP\runtime"),
    [string]$SourceDir = $PSScriptRoot,
    [string]$VenvDir = (Join-Path $env:LOCALAPPDATA "RemoteMCP\node-venv"),
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Find-RemoteMCPPython {
    $candidates = @()
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }

    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) {
        try {
            $p = & $cmd.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) { $candidates += $p.Trim() }
        } catch {}
    }

    $known = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $known) { $candidates += $known }

    foreach ($p in $candidates | Select-Object -Unique) {
        try {
            & $p -c "import sys; assert sys.version_info >= (3,11)" 2>$null
            if ($LASTEXITCODE -eq 0) { return $p }
        } catch {}
    }
    return $null
}

function Get-RemoteMCPNodeProcess {
    param([string]$Runtime)
    $escaped = [Regex]::Escape($Runtime)
    return Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "remotemcp\.node" -and
        $_.CommandLine -match "--runtime-dir" -and
        $_.CommandLine -match $escaped
    }
}

$SourceDir = (Resolve-Path $SourceDir).Path
if (-not (Test-Path (Join-Path $SourceDir "remotemcp\node\__main__.py"))) {
    throw "RemoteMCP source not found under: $SourceDir"
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $VenvDir -Parent) | Out-Null

$BootstrapPython = Find-RemoteMCPPython
if (-not $BootstrapPython) {
    throw "Python 3.11+ was not found. Install Python 3.11 or newer, then run this script again."
}

$NodePython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $NodePython)) {
    Write-Host "Creating RemoteMCP node virtual environment..."
    & $BootstrapPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "Could not create RemoteMCP virtual environment." }
}

$depsOk = $false
try {
    & $NodePython -c "import httpx, cryptography" 2>$null
    $depsOk = ($LASTEXITCODE -eq 0)
} catch {}

if (-not $depsOk) {
    Write-Host "Installing RemoteMCP node dependencies..."
    & $NodePython -m pip install --disable-pip-version-check --quiet "httpx==0.28.1" "cryptography==46.0.6"
    if ($LASTEXITCODE -ne 0) { throw "Could not install RemoteMCP node dependencies." }
}

$existing = @(Get-RemoteMCPNodeProcess -Runtime $RuntimeDir)

if ($Restart -and $existing.Count -gt 0) {
    Write-Host "Restarting existing RemoteMCP node..."
    foreach ($p in $existing) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
    $existing = @()
}

if ($existing.Count -eq 0) {
    $logDir = Join-Path $env:LOCALAPPDATA "RemoteMCP"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $logFile = Join-Path $logDir "node.log"
    $errFile = Join-Path $logDir "node-error.log"
    Write-Host "Starting RemoteMCP node..."
    Start-Process -FilePath $NodePython -ArgumentList @("-m","remotemcp.node","run","--runtime-dir",$RuntimeDir) -WorkingDirectory $SourceDir -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError $errFile | Out-Null
    Start-Sleep -Seconds 2
} else {
    Write-Host "RemoteMCP node is already running."
}

Push-Location $SourceDir
try {
    & $NodePython -m remotemcp.node status --runtime-dir $RuntimeDir
    if ($LASTEXITCODE -ne 0) { throw "RemoteMCP node status failed." }
} finally {
    Pop-Location
}
