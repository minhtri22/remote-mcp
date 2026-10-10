param(
    [string]$RuntimeDir = "",
    [string]$RootDir = "",
    [string[]]$LegacyRootDirs = @(),
    [string]$SourceDir = $PSScriptRoot,
    [string]$VenvDir = "",
    [string]$LogDir = "",
    [string]$TempDir = "",
    [string]$CacheDir = "",
    [string]$ControlDir = "",
    [string]$DeclaredLongLivedProcessPatterns = "",
    [switch]$ZeroC,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Base = Join-Path $env:LOCALAPPDATA "RemoteMCP"
$LegacyDefaultVenvDir = (Join-Path $env:LOCALAPPDATA "RemoteMCP\node-venv")

function Assert-NonOsPath {
    param([string]$Path,[string]$Label)
    if (-not $Path) { throw "$Label is required." }
    if (
        -not [System.IO.Path]::IsPathRooted($Path) -or
        $Path -match '^[A-Za-z]:[^\\/]'
    ) {
        throw "$Label must be an absolute path."
    }
    $full = [System.IO.Path]::GetFullPath($Path)
    $drive = [System.IO.Path]::GetPathRoot($full)
    $osDrive = [System.IO.Path]::GetPathRoot($env:SystemRoot)
    if ($drive -and $osDrive -and ($drive.TrimEnd('\') -ieq $osDrive.TrimEnd('\'))) {
        throw "OS_DRIVE_REMOTEMCP_INFRA_FORBIDDEN: $Label cannot be on the Windows OS drive."
    }
    return $full
}

if ($ZeroC) {
    if (-not $ControlDir) { throw "ZeroC requires -ControlDir on a non-OS drive." }
    $ControlDir = Assert-NonOsPath -Path $ControlDir -Label "ControlDir"
    New-Item -ItemType Directory -Force -Path $ControlDir | Out-Null
    $RuntimePointer = Join-Path $ControlDir "active-runtime.txt"
} else {
    $RuntimePointer = Join-Path $Base "active-runtime.txt"
}

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

if (-not $ZeroC) {
    New-Item -ItemType Directory -Force -Path $Base | Out-Null
}
$RuntimeDir = Resolve-RemoteMCPRuntime -Requested $RuntimeDir
if ($ZeroC) {
    $RuntimeDir = Assert-NonOsPath -Path $RuntimeDir -Label "RuntimeDir"
}
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
# Windows PowerShell 5.1 runs on .NET Framework with an older Path API.
# Use the compatible rooted check and explicitly reject drive-relative
# forms such as "D:folder".
if (
    -not [System.IO.Path]::IsPathRooted($RootDir) -or
    $RootDir -match '^[A-Za-z]:[^\\/]'
) {
    throw "RemoteMCP node root must be an absolute path."
}
$RootDir = [System.IO.Path]::GetFullPath($RootDir)
$RootDrive = [System.IO.Path]::GetPathRoot($RootDir)
$OsDrive = [System.IO.Path]::GetPathRoot($env:SystemRoot)
if ($RootDrive -and $OsDrive -and ($RootDrive.TrimEnd('\') -ieq $OsDrive.TrimEnd('\'))) {
    throw "OS_DRIVE_RESEARCH_ROOT_FORBIDDEN: project/worktree root cannot be on the Windows OS drive. Use an approved non-OS data/research root."
}
New-Item -ItemType Directory -Force -Path $RootDir | Out-Null
Write-Host "Node root: $RootDir"

$ResolvedLegacyRootDirs = @()
foreach ($legacy in @($LegacyRootDirs)) {
    if (-not $legacy) { continue }
    if (
        -not [System.IO.Path]::IsPathRooted($legacy) -or
        $legacy -match '^[A-Za-z]:[^\\/]'
    ) {
        throw "LegacyRootDirs entries must be absolute paths."
    }
    $fullLegacy = [System.IO.Path]::GetFullPath($legacy)
    if (-not (Test-Path -LiteralPath $fullLegacy -PathType Container)) {
        throw "LEGACY_ROOT_NOT_FOUND: $fullLegacy"
    }
    if ($fullLegacy.TrimEnd('\') -ieq $RootDir.TrimEnd('\')) {
        continue
    }
    if (-not ($ResolvedLegacyRootDirs | Where-Object { $_.TrimEnd('\') -ieq $fullLegacy.TrimEnd('\') })) {
        $ResolvedLegacyRootDirs += $fullLegacy
    }
}
foreach ($legacy in $ResolvedLegacyRootDirs) {
    Write-Host "Legacy compatibility root: $legacy"
}

if (-not $VenvDir) {
    $VenvDir = if ($env:REMOTEMCP_NODE_VENV) { $env:REMOTEMCP_NODE_VENV } else { $LegacyDefaultVenvDir }
}
if (-not $LogDir) {
    $LogDir = if ($env:REMOTEMCP_LOG_DIR) { $env:REMOTEMCP_LOG_DIR } else { $Base }
}
if (-not $TempDir) {
    $TempDir = if ($env:REMOTEMCP_TEMP_DIR) { $env:REMOTEMCP_TEMP_DIR } else { [System.IO.Path]::GetTempPath() }
}
if (-not $CacheDir) {
    $CacheDir = if ($env:REMOTEMCP_CACHE_DIR) { $env:REMOTEMCP_CACHE_DIR } else { Join-Path $Base "cache" }
}
if (-not $ControlDir) {
    $ControlDir = if ($env:REMOTEMCP_CONTROL_DIR) { $env:REMOTEMCP_CONTROL_DIR } else { $Base }
}

if ($ZeroC) {
    $VenvDir = Assert-NonOsPath -Path $VenvDir -Label "VenvDir"
    $LogDir = Assert-NonOsPath -Path $LogDir -Label "LogDir"
    $TempDir = Assert-NonOsPath -Path $TempDir -Label "TempDir"
    $CacheDir = Assert-NonOsPath -Path $CacheDir -Label "CacheDir"
    $ControlDir = Assert-NonOsPath -Path $ControlDir -Label "ControlDir"
}

foreach ($p in @($VenvDir,$LogDir,$TempDir,$CacheDir,$ControlDir)) {
    New-Item -ItemType Directory -Force -Path $p | Out-Null
}

# Child processes inherit these explicit non-OS infrastructure locations.
$env:REMOTEMCP_INFRA_ROOT = (Split-Path $RuntimeDir -Parent)
$env:REMOTEMCP_NODE_VENV = $VenvDir
$env:REMOTEMCP_LOG_DIR = $LogDir
$env:REMOTEMCP_TEMP_DIR = $TempDir
$env:REMOTEMCP_CACHE_DIR = $CacheDir
$env:REMOTEMCP_CONTROL_DIR = $ControlDir
$env:REMOTEMCP_ZERO_C = if ($ZeroC) { "1" } else { "0" }
$env:REMOTEMCP_LONG_LIVED_PROCESS_PATTERNS = $DeclaredLongLivedProcessPatterns
$env:TEMP = $TempDir
$env:TMP = $TempDir
$env:XDG_CACHE_HOME = $CacheDir
$env:PIP_CACHE_DIR = (Join-Path $CacheDir "pip")
$env:PYTHONPYCACHEPREFIX = (Join-Path $CacheDir "pycache")
$env:HF_HOME = (Join-Path $CacheDir "huggingface")
$env:TORCH_HOME = (Join-Path $CacheDir "torch")
foreach ($p in @($env:PIP_CACHE_DIR,$env:PYTHONPYCACHEPREFIX,$env:HF_HOME,$env:TORCH_HOME)) {
    New-Item -ItemType Directory -Force -Path $p | Out-Null
}

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
    $legacyMismatch = $false
    foreach ($legacy in $ResolvedLegacyRootDirs) {
        $escapedLegacy = [Regex]::Escape($legacy)
        if (@($existing | Where-Object {
            -not $_.CommandLine -or
            $_.CommandLine -notmatch "--legacy-root" -or
            $_.CommandLine -notmatch $escapedLegacy
        }).Count -gt 0) {
            $legacyMismatch = $true
            break
        }
    }
    if (($rootMismatch -or $legacyMismatch) -and -not $Restart) {
        throw "NODE_ROOT_MISMATCH_RESTART_REQUIRED: existing node does not match canonical/legacy root configuration. Re-run with -Restart after protected-process audit."
    }
}

if ($Restart -and $existing.Count -gt 0) {
    Write-Host "Restarting existing RemoteMCP node..."
    foreach ($p in $existing) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
    $existing = @()
}

if ($existing.Count -eq 0) {
    $logFile = Join-Path $LogDir "node.log"
    $errFile = Join-Path $LogDir "node-error.log"
    # Preserve previous stderr/stdout before Start-Process truncates log paths.
    # Never truncate or overwrite previous startup evidence across watchdog relaunches.
    $archiveStamp = (Get-Date -Format "yyyyMMdd-HHmmss-fff") + "-" + ([guid]::NewGuid().ToString("N").Substring(0,8))
    foreach ($oldLog in @($logFile,$errFile)) {
        if (Test-Path -LiteralPath $oldLog -PathType Leaf) {
            $info = Get-Item -LiteralPath $oldLog -ErrorAction Stop
            if ($info.Length -gt 0) {
                $archive = $oldLog + "." + $archiveStamp + ".previous"
                Move-Item -LiteralPath $oldLog -Destination $archive -ErrorAction Stop
            }
        }
    }
    Write-Host "Starting RemoteMCP node..."
    $NodeArgs = @("-m","remotemcp.node","run","--runtime-dir",$RuntimeDir,"--root",$RootDir)
    foreach ($legacy in $ResolvedLegacyRootDirs) {
        $NodeArgs += @("--legacy-root",$legacy)
    }
    Start-Process -FilePath $NodePython -ArgumentList $NodeArgs -WorkingDirectory $SourceDir -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError $errFile | Out-Null
    Start-Sleep -Seconds 2
} else {
    Write-Host "RemoteMCP node is already running."
}

Push-Location $SourceDir
try {
    & $NodePython -m remotemcp.node status --runtime-dir $RuntimeDir
    if ($LASTEXITCODE -ne 0) { throw "RemoteMCP node status failed." }
    $DoctorArgs = @("-m","remotemcp.node","doctor","--runtime-dir",$RuntimeDir,"--root",$RootDir)
    foreach ($legacy in $ResolvedLegacyRootDirs) {
        $DoctorArgs += @("--legacy-root",$legacy)
    }
    & $NodePython @DoctorArgs
    if ($LASTEXITCODE -ne 0) { throw "RemoteMCP node doctor failed." }
} finally {
    Pop-Location
}
