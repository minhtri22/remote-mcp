param(
    [string]$SourceRepo = $PSScriptRoot,
    [string]$Ref = "origin/main",
    [int]$HealthTimeoutSec = 5,
    [switch]$SkipFetch,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Base = Join-Path $env:LOCALAPPDATA "RemoteMCP"
$ConfigFile = Join-Path $Base "gateway-config.json"
$LiveLogFile = Join-Path $Base "gateway.log"
$LiveErrFile = Join-Path $Base "gateway-error.log"

if (-not (Test-Path $ConfigFile)) {
    throw "RemoteMCP gateway is not configured: $ConfigFile"
}

function Get-FreeLoopbackPort {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback,0)
    try {
        $listener.Start()
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    } finally {
        try { $listener.Stop() } catch {}
    }
}

function Invoke-IsolatedGatewayReleaseProbe {
    param(
        [string]$PythonExe,
        [string]$SourceDir,
        [string]$ReleaseShort,
        [int]$TimeoutSec
    )

    $ProbeBase = Join-Path $env:TEMP ("remotemcp-gateway-probe-" + [guid]::NewGuid().ToString("N"))
    $ProbeRoot = Join-Path $ProbeBase "workspace"
    $ProbeRuntime = Join-Path $ProbeBase "runtime"
    $ProbeState = Join-Path $ProbeBase "oauth-state.json"
    $ProbeOut = Join-Path $ProbeBase "stdout.log"
    $ProbeErr = Join-Path $ProbeBase "stderr.log"
    New-Item -ItemType Directory -Force -Path $ProbeRoot,$ProbeRuntime | Out-Null

    $Port = Get-FreeLoopbackPort
    $Names = @(
        "PUBLIC_URL","OWNER_PASSWORD","MCP_ROOT","MCP_STATE","MCP_RUNTIME_DIR",
        "ALLOWED_REDIRECT_HOSTS","PORT"
    )
    $Saved = @{}
    foreach ($Name in $Names) {
        $Saved[$Name] = [Environment]::GetEnvironmentVariable($Name,"Process")
    }

    $Process = $null
    try {
        [Environment]::SetEnvironmentVariable("PUBLIC_URL","https://diagnostic.invalid","Process")
        [Environment]::SetEnvironmentVariable("OWNER_PASSWORD","diagnostic-password-only","Process")
        [Environment]::SetEnvironmentVariable("MCP_ROOT",$ProbeRoot,"Process")
        [Environment]::SetEnvironmentVariable("MCP_STATE",$ProbeState,"Process")
        [Environment]::SetEnvironmentVariable("MCP_RUNTIME_DIR",$ProbeRuntime,"Process")
        [Environment]::SetEnvironmentVariable("ALLOWED_REDIRECT_HOSTS","chatgpt.com,localhost,127.0.0.1","Process")
        [Environment]::SetEnvironmentVariable("PORT",[string]$Port,"Process")

        $Process = Start-Process -FilePath $PythonExe -ArgumentList @("server.py") -WorkingDirectory $SourceDir -WindowStyle Hidden -RedirectStandardOutput $ProbeOut -RedirectStandardError $ProbeErr -PassThru

        $Deadline = (Get-Date).AddSeconds([Math]::Max(15,$TimeoutSec * 4))
        $Healthy = $false
        do {
            Start-Sleep -Milliseconds 500
            if ($Process.HasExited) { break }
            try {
                $r = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/.well-known/oauth-authorization-server" -f $Port) -TimeoutSec $TimeoutSec
                $Healthy = ($r.StatusCode -eq 200)
            } catch {}
        } while (-not $Healthy -and (Get-Date) -lt $Deadline)

        if (-not $Healthy) {
            $Stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
            $PreservedOut = Join-Path $Base ("gateway-upgrade-probe-failed-" + $ReleaseShort + "-" + $Stamp + ".stdout.log")
            $PreservedErr = Join-Path $Base ("gateway-upgrade-probe-failed-" + $ReleaseShort + "-" + $Stamp + ".stderr.log")
            if (Test-Path $ProbeOut) { Copy-Item -Force $ProbeOut $PreservedOut }
            if (Test-Path $ProbeErr) { Copy-Item -Force $ProbeErr $PreservedErr }
            $ExitText = if ($Process.HasExited) { [string]$Process.ExitCode } else { "still-running-unhealthy" }
            throw ("Isolated gateway release startup probe failed; exit=" + $ExitText + "; stdout=" + $PreservedOut + "; stderr=" + $PreservedErr)
        }

        Write-Host "REMOTEMCP_GATEWAY_RELEASE_PROBE=PASS"
        Write-Host ("Probe commit short : {0}" -f $ReleaseShort)
        Write-Host ("Probe loopback port: {0}" -f $Port)
    } finally {
        if ($Process -and -not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
        foreach ($Name in $Names) {
            [Environment]::SetEnvironmentVariable($Name,$Saved[$Name],"Process")
        }
        if (Test-Path $ProbeBase) {
            Remove-Item -Recurse -Force $ProbeBase -ErrorAction SilentlyContinue
        }
    }
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
        [IO.File]::WriteAllText((Join-Path $Tmp ".remotemcp-release.json"),($markerObj | ConvertTo-Json -Depth 4),(New-Object Text.UTF8Encoding($false)))
        Move-Item -Path $Tmp -Destination $NewSource
    } finally {
        if (Test-Path $Tmp) { Remove-Item -Recurse -Force $Tmp }
    }
}

$PythonExe = [string]$cfg.python_exe
if (-not (Test-Path $PythonExe)) { throw "Configured gateway Python is missing: $PythonExe" }

& $PythonExe -m py_compile (Join-Path $NewSource "server.py") (Join-Path $NewSource "oauth_provider.py")
if ($LASTEXITCODE -ne 0) { throw "New release Python syntax preflight failed" }

& $PythonExe -c "import mcp,uvicorn,httpx,cryptography"
if ($LASTEXITCODE -ne 0) { throw "Configured gateway Python dependency preflight failed" }

Invoke-IsolatedGatewayReleaseProbe -PythonExe $PythonExe -SourceDir $NewSource -ReleaseShort $Short -TimeoutSec $HealthTimeoutSec

if ($PreflightOnly) {
    Write-Host "REMOTEMCP_GATEWAY_UPDATE_PREFLIGHT_ONLY=PASS"
    Write-Host "Commit : $Commit"
    Write-Host "Release: $NewSource"
    Write-Host "Live gateway configuration and process were not changed."
    exit 0
}

$BackupFile = Join-Path $Base ("gateway-config.pre-upgrade-" + (Get-Date -Format "yyyyMMdd-HHmmss-fff") + ".json")
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
    $UpgradeFailure = $_
    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $SavedLiveOut = Join-Path $Base ("gateway-upgrade-failed-" + $Short + "-" + $Stamp + ".stdout.log")
    $SavedLiveErr = Join-Path $Base ("gateway-upgrade-failed-" + $Short + "-" + $Stamp + ".stderr.log")

    if (Test-Path $LiveLogFile) { Copy-Item -Force $LiveLogFile $SavedLiveOut }
    if (Test-Path $LiveErrFile) { Copy-Item -Force $LiveErrFile $SavedLiveErr }

    Write-Warning ("Upgrade failed; preserved stdout: " + $SavedLiveOut)
    Write-Warning ("Upgrade failed; preserved stderr: " + $SavedLiveErr)
    Write-Warning ("Rolling gateway source_dir back to: " + $OldSourceResolved)

    Copy-Item -Force $BackupFile $ConfigFile

    $RollbackStarter = Join-Path $NewSource "Start-RemoteMCP-Gateway.ps1"
    $RollbackFailure = $null
    if (Test-Path $RollbackStarter) {
        try {
            & $RollbackStarter -Restart -HealthTimeoutSec $HealthTimeoutSec
        } catch {
            $RollbackFailure = $_.Exception.Message
            Write-Warning ("Rollback restart also failed: " + $RollbackFailure)
        }
    } else {
        $RollbackFailure = "candidate release is missing Start-RemoteMCP-Gateway.ps1"
        Write-Warning ("Rollback restart also failed: " + $RollbackFailure)
    }

    if ($RollbackFailure) {
        throw ("Gateway upgrade failed: " + $UpgradeFailure.Exception.Message + "; rollback restart failed: " + $RollbackFailure + "; preserved stderr: " + $SavedLiveErr)
    }

    throw $UpgradeFailure
}
