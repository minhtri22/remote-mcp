param(
    [string]$RuntimeDir = "",
    [string]$RootDir = "",
    [string]$SourceDir = $PSScriptRoot,
    [string]$VenvDir = (Join-Path $env:LOCALAPPDATA "RemoteMCP\node-venv"),
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Base = Join-Path $env:LOCALAPPDATA "RemoteMCP"
$RuntimePointer = Join-Path $Base "active-runtime.txt"

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

function Test-RemoteMCPPairedRuntime {
    param([string]$Path)
    if (-not $Path) { return $false }
    return (
        (Test-Path (Join-Path $Path "device.json")) -and
        (Test-Path (Join-Path $Path "device-ed25519.pem"))
    )
}

function Resolve-RemoteMCPRuntime {
    param([string]$Requested)

    if ($Requested) {
        if (-not (Test-RemoteMCPPairedRuntime -Path $Requested)) {
            throw "The requested runtime does not contain a paired RemoteMCP device identity: $Requested"
        }
        return (Resolve-Path $Requested).Path
    }

    $candidates = @()

    if (Test-Path $RuntimePointer) {
        try {
            $remembered = (Get-Content $RuntimePointer -Raw).Trim()
            if (Test-RemoteMCPPairedRuntime -Path $remembered) {
                $candidates += (Resolve-Path $remembered).Path
            }
        } catch {}
    }

    $defaultRuntime = Join-Path $Base "runtime"
    if (Test-RemoteMCPPairedRuntime -Path $defaultRuntime) {
        $candidates += (Resolve-Path $defaultRuntime).Path
    }

    foreach ($parent in @(Get-ChildItem $env:USERPROFILE -Directory -Filter "RemoteMCP*" -ErrorAction SilentlyContinue)) {
        $candidate = Join-Path $parent.FullName "runtime"
        if (Test-RemoteMCPPairedRuntime -Path $candidate) {
            $candidates += (Resolve-Path $candidate).Path
        }
    }

    $candidates = @($candidates | Select-Object -Unique)
    if ($candidates.Count -eq 1) { return $candidates[0] }

    if ($candidates.Count -eq 0) {
        throw "No paired RemoteMCP runtime identity was found. Run the one-time join/pair flow first. This starter never creates a new device identity."
    }

    $choices = ($candidates | ForEach-Object { "  - $_" }) -join [Environment]::NewLine
    throw "Multiple paired RemoteMCP runtimes were found. Re-run with -RuntimeDir and choose exactly one:`n$choices"
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

New-Item -ItemType Directory -Force -Path $Base | Out-Null
$RuntimeDir = Resolve-RemoteMCPRuntime -Requested $RuntimeDir
[System.IO.File]::WriteAllText($RuntimePointer,$RuntimeDir,[System.Text.UTF8Encoding]::new($false))

# Resolve the project/worktree execution root explicitly. Runtime identity may remain
# under LOCALAPPDATA, but research/project/worktree storage must not use the OS drive.
$DeviceFile = Join-Path $RuntimeDir "device.json"
$StoredDevice = Get-Content $DeviceFile -Raw | ConvertFrom-Json
if (-not $RootDir) { $RootDir = $env:REMOTEMCP_NODE_ROOT }
if (-not $RootDir) { $RootDir = [string]$StoredDevice.root }
if (-not $RootDir) {
    throw "RemoteMCP node root is missing. Pass -RootDir or set REMOTEMCP_NODE_ROOT."
}
if (-not [System.IO.Path]::IsPathFullyQualified($RootDir)) {
    throw "RemoteMCP node root must be an absolute path."
}
$RootDir = [System.IO.Path]::GetFullPath($RootDir)
$RootDrive = [System.IO.Path]::GetPathRoot($RootDir)
$OsDrive = [System.IO.Path]::GetPathRoot($env:SystemRoot)
if ($RootDrive -and $OsDrive -and ($RootDrive.TrimEnd('\') -ieq $OsDrive.TrimEnd('\'))) {
    throw "OS_DRIVE_RESEARCH_ROOT_FORBIDDEN: project/worktree root cannot be on the Windows OS drive. Use a data/research drive such as D:\WORK\RESEARCH."
}
New-Item -ItemType Directory -Force -Path $RootDir | Out-Null
Write-Host "Node root: $RootDir"

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

if ($existing.Count -gt 0) {
    $escapedRoot = [Regex]::Escape($RootDir)
    $rootMismatch = @($existing | Where-Object {
        -not $_.CommandLine -or
        $_.CommandLine -notmatch "--root" -or
        $_.CommandLine -notmatch $escapedRoot
    }).Count -gt 0
    if ($rootMismatch -and -not $Restart) {
        throw "NODE_ROOT_MISMATCH_RESTART_REQUIRED: existing node is not running with RootDir '$RootDir'. Re-run with -Restart to apply the approved non-OS research root."
    }
}

if ($Restart -and $existing.Count -gt 0) {
    Write-Host "Restarting existing RemoteMCP node..."
    foreach ($p in $existing) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
    $existing = @()
}

if ($existing.Count -eq 0) {
    $logFile = Join-Path $Base "node.log"
    $errFile = Join-Path $Base "node-error.log"
    Write-Host "Starting RemoteMCP node..."
    Start-Process -FilePath $NodePython -ArgumentList @("-m","remotemcp.node","run","--runtime-dir",$RuntimeDir,"--root",$RootDir) -WorkingDirectory $SourceDir -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError $errFile | Out-Null
    Start-Sleep -Seconds 2
} else {
    Write-Host "RemoteMCP node is already running."
}

Push-Location $SourceDir
try {
    & $NodePython -m remotemcp.node status --runtime-dir $RuntimeDir
    if ($LASTEXITCODE -ne 0) { throw "RemoteMCP node status failed." }
    & $NodePython -m remotemcp.node doctor --runtime-dir $RuntimeDir --root $RootDir
    if ($LASTEXITCODE -ne 0) { throw "RemoteMCP node doctor failed." }
} finally {
    Pop-Location
}
