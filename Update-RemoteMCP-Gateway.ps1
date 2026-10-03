param(
    [string]$SourceRepo = $PSScriptRoot,
    [string]$Ref = "origin/main",
    [int]$HealthTimeoutSec = 5,
    [switch]$SkipFetch
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Base = Join-Path $env:LOCALAPPDATA "RemoteMCP"
$ConfigFile = Join-Path $Base "gateway-config.json"
if (-not (Test-Path $ConfigFile)) {
    throw "RemoteMCP gateway is not configured: $ConfigFile"
}

$SourceRepo = (Resolve-Path $SourceRepo).Path
if (-not (Test-Path (Join-Path $SourceRepo ".git"))) {
    throw "SourceRepo is not a Git repository: $SourceRepo"
}

if (-not $SkipFetch) {
    & git -C $SourceRepo fetch --prune origin
    if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }
}

$Commit = (& git -C $SourceRepo rev-parse $Ref).Trim()
if ($LASTEXITCODE -ne 0 -or -not $Commit) { throw "Could not resolve ref: $Ref" }
$Short = $Commit.Substring(0,7)

$cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json
$OldSource = [string]$cfg.source_dir
if (-not $OldSource) { throw "gateway-config.json has no source_dir" }

$OldSourceResolved = [IO.Path]::GetFullPath($OldSource)
$ReleaseParent = Split-Path -Parent $OldSourceResolved
if (-not $ReleaseParent) {
    $ReleaseParent = Join-Path $Base "releases"
}
New-Item -ItemType Directory -Force -Path $ReleaseParent | Out-Null

$NewSource = Join-Path $ReleaseParent $Short
if (Test-Path $NewSource) {
    $marker = Join-Path $NewSource ".remotemcp-release.json"
    if (-not (Test-Path $marker)) {
        throw "Target release directory already exists without a RemoteMCP release marker: $NewSource"
    }
    $existing = Get-Content $marker -Raw | ConvertFrom-Json
    if ([string]$existing.commit -ne $Commit) {
        throw "Existing release marker commit does not match requested commit"
    }
} else {
    $Tmp = Join-Path $ReleaseParent (".staging-" + $Short + "-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
    try {
        $Archive = Join-Path $env:TEMP ("remotemcp-" + $Short + ".zip")
        if (Test-Path $Archive) { Remove-Item -Force $Archive }
        & git -C $SourceRepo archive --format=zip --output=$Archive $Commit
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Archive)) {
            throw "git archive failed"
        }
        Expand-Archive -Path $Archive -DestinationPath $Tmp -Force
        Remove-Item -Force $Archive

        if (-not (Test-Path (Join-Path $Tmp "server.py"))) {
            throw "Release snapshot is missing server.py"
        }

        $markerObj = [ordered]@{
            schema = "remotemcp.production-release.v1"
            commit = $Commit
            created_at = (Get-Date).ToString("o")
            source_repo = $SourceRepo
        }
        [IO.File]::WriteAllText(
            (Join-Path $Tmp ".remotemcp-release.json"),
            ($markerObj | ConvertTo-Json -Depth 4),
            (New-Object Text.UTF8Encoding($false))
        )
        Move-Item -Path $Tmp -Destination $NewSource
    } finally {
        if (Test-Path $Tmp) { Remove-Item -Recurse -Force $Tmp }
    }
}

$PythonExe = [string]$cfg.python_exe
if (-not (Test-Path $PythonExe)) { throw "Configured gateway Python is missing: $PythonExe" }

& $PythonExe -m py_compile (Join-Path $NewSource "server.py") (Join-Path $NewSource "oauth_provider.py")
if ($LASTEXITCODE -ne 0) { throw "New release Python syntax preflight failed" }

# Validate that the configured environment can import the gateway dependencies
# before touching the live process.
& $PythonExe -c "import mcp,uvicorn,httpx,cryptography"
if ($LASTEXITCODE -ne 0) { throw "Configured gateway Python dependency preflight failed" }

$BackupFile = Join-Path $Base ("gateway-config.pre-upgrade-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
Copy-Item -Force $ConfigFile $BackupFile

$cfg.source_dir = $NewSource
$cfg | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $ConfigFile

$RestartScript = Join-Path $NewSource "Restart-RemoteMCP-Gateway.ps1"
if (-not (Test-Path $RestartScript)) {
    Copy-Item -Force $BackupFile $ConfigFile
    throw "New release is missing Restart-RemoteMCP-Gateway.ps1"
}

try {
    & $RestartScript
    if ($LASTEXITCODE -ne 0) { throw "Gateway restart returned non-zero" }

    $TestScript = Join-Path $NewSource "Test-RemoteMCP-Gateway.ps1"
    if (Test-Path $TestScript) {
        & $TestScript
        if ($LASTEXITCODE -ne 0) { throw "Post-upgrade local health test failed" }
    }

    $cfgAfter = Get-Content $ConfigFile -Raw | ConvertFrom-Json
    if ([IO.Path]::GetFullPath([string]$cfgAfter.source_dir) -ne [IO.Path]::GetFullPath($NewSource)) {
        throw "Post-upgrade source_dir verification failed"
    }

    Write-Host "REMOTEMCP_GATEWAY_UPGRADE=PASS"
    Write-Host "Commit : $Commit"
    Write-Host "Release: $NewSource"
    Write-Host "Previous source retained: $OldSourceResolved"
    Write-Host "Gateway durable runtime/state/device identities were reused."
    Write-Host "Execution nodes were not restarted."
} catch {
    Write-Warning ("Upgrade failed; rolling gateway source_dir back to: " + $OldSourceResolved)
    Copy-Item -Force $BackupFile $ConfigFile
    $OldRestart = Join-Path $OldSourceResolved "Restart-RemoteMCP-Gateway.ps1"
    if (Test-Path $OldRestart) {
        try { & $OldRestart } catch { Write-Warning ("Rollback restart also failed: " + $_.Exception.Message) }
    }
    throw
}
