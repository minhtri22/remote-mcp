# REMOTEMCP_SILENT_INSTALLER_V2
# Install ONLY per-user hidden Startup persistence, never restart a running node.
param([ValidateSet('Audit','Apply','Verify')][string]$Mode = 'Audit')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

try {
    $bootstrap = Join-Path $PSScriptRoot 'RemoteMCP-Silent-Bootstrap.ps1'
    $self = $MyInvocation.MyCommand.Path
    foreach ($p in @($self,$bootstrap)) {
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {
            throw "PACKAGE_FILE_MISSING: $p"
        }
        if ((Get-Item -LiteralPath $p -ErrorAction Stop).Length -lt 500) {
            throw "PACKAGE_FILE_EMPTY_OR_TRUNCATED: $p"
        }
    }
    $bootstrapText = Get-Content -LiteralPath $bootstrap -Raw
    if (-not $bootstrapText.Contains('REMOTEMCP_SILENT_BOOTSTRAP_V2')) {
        throw 'BOOTSTRAP_CONTENT_MARKER_MISSING'
    }
    $dir = 'D:\WORK\RESEARCH\.remotemcp\machine-1'
    $control = Join-Path $dir 'control'
    $logDir = Join-Path $dir 'logs'
    $runtime = Join-Path $env:LOCALAPPDATA 'RemoteMCP\runtime'
    $cfgFile = Join-Path $env:LOCALAPPDATA 'RemoteMCP\gateway-config.json'
    $deviceId = 'dev_dd73ebfa742f468f2d212bade88c175b'
    $fingerprint = 'b657d5395e393e0957a9ed358bb5a1fe1588fde5d3a44be71295a68d1e73def9'
    $name = "RemoteMCP-Node-Supervisor-$deviceId"
    $startupDir = [Environment]::GetFolderPath('Startup')
    if (-not $startupDir) { throw 'STARTUP_FOLDER_UNAVAILABLE' }
    $legacyCmd = Join-Path $startupDir ($name + '.cmd')
    $newVbs = Join-Path $startupDir ($name + '.vbs')

    $device = Get-Content -LiteralPath (Join-Path $runtime 'device.json') -Raw | ConvertFrom-Json
    $cfg = Get-Content -LiteralPath $cfgFile -Raw | ConvertFrom-Json
    if ($device.device_id -ne $deviceId -or
        $device.key_fingerprint_sha256 -ne $fingerprint -or
        [int]$device.route_generation -ne 1) {
        throw 'DEVICE_IDENTITY_MISMATCH'
    }
    if ($cfg.node_release_gate_enabled -ne $true -or
        $cfg.hold_science_job_dispatch -isnot [bool]) {
        throw 'GATEWAY_RELEASE_GUARD_INVALID'
    }
    $release = [string]$cfg.source_dir
    $sha = [string]$cfg.required_node_release_commit_sha
    $marker = Get-Content -LiteralPath (Join-Path $release '.remotemcp-release.json') -Raw | ConvertFrom-Json
    if ($marker.commit -ne $sha) { throw 'NODE_RELEASE_PIN_MISMATCH' }

    # Correctly escaped VBScript Run string. 0 = hidden; False = no blocking.
    $cmd = 'powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $bootstrap + '"'
    $vbsText = 'Set sh = CreateObject("WScript.Shell")' + [Environment]::NewLine +
        'sh.Run "' + $cmd.Replace('"','""') + '", 0, False' + [Environment]::NewLine
    $expectedBytes = [System.Text.Encoding]::UTF8.GetBytes($vbsText)

    if ($Mode -eq 'Audit') {
        Write-Host 'PACKAGE_NONEMPTY=PASS'
        Write-Host "LEGACY_CMD_EXISTS=$([bool](Test-Path -LiteralPath $legacyCmd))"
        Write-Host "SILENT_VBS_EXISTS=$([bool](Test-Path -LiteralPath $newVbs))"
        Write-Host "EXACT_RELEASE=$sha"
        Write-Host 'REMOTEMCP_SILENT_STARTUP_AUDIT=PASS'
        exit 0
    }

    if ($Mode -eq 'Apply') {
        New-Item -ItemType Directory -Path $control,$logDir -Force | Out-Null
        if (Test-Path -LiteralPath $legacyCmd -PathType Leaf) {
            $old = Get-Content -LiteralPath $legacyCmd -Raw
            if ($old -notmatch 'Watch-RemoteMCP-Node\.ps1' -or
                $old -notmatch [regex]::Escape($runtime) -or
                $old -notmatch [regex]::Escape($release)) {
                throw 'LEGACY_STARTUP_UNRECOGNIZED_NO_MODIFICATION'
            }
        }
        if (Test-Path -LiteralPath $newVbs -PathType Leaf) {
            if ((Get-Content -LiteralPath $newVbs -Raw) -ne $vbsText) {
                throw 'EXISTING_SILENT_STARTUP_DIFFERS_NO_OVERWRITE'
            }
        } else {
            $temp = Join-Path $startupDir ($name + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
            [System.IO.File]::WriteAllBytes($temp,$expectedBytes)
            try { Move-Item -LiteralPath $temp -Destination $newVbs -ErrorAction Stop }
            finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force } }
        }
        # Retire only the exact previous launcher; never kill a running process.
        if (Test-Path -LiteralPath $legacyCmd -PathType Leaf) {
            $backup = Join-Path $control ('startup-legacy-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' +
                [guid]::NewGuid().ToString('N').Substring(0,8) + '.cmd')
            Copy-Item -LiteralPath $legacyCmd -Destination $backup -ErrorAction Stop
            Remove-Item -LiteralPath $legacyCmd -ErrorAction Stop
            Write-Host "LEGACY_CMD_BACKUP=$backup"
        }
        $logLine = [ordered]@{
            utc = [DateTimeOffset]::UtcNow.ToString('o')
            event = 'SILENT_STARTUP_INSTALL'
            release = $sha
            startup = $newVbs
            node_restarted = $false
            gateway_changed = $false
            science_dispatch_released = $false
        } | ConvertTo-Json -Compress
        Add-Content -LiteralPath (Join-Path $logDir 'silent-bootstrap-install.jsonl') -Value $logLine -Encoding UTF8
        Write-Host 'REMOTEMCP_SILENT_STARTUP_APPLY=PASS'
        exit 0
    }

    if ($Mode -eq 'Verify') {
        if (-not (Test-Path -LiteralPath $newVbs -PathType Leaf)) {
            throw 'SILENT_VBS_NOT_INSTALLED'
        }
        if (Test-Path -LiteralPath $legacyCmd) {
            throw 'DUPLICATE_LEGACY_STARTUP_STILL_PRESENT'
        }
        $actualBytes = [System.IO.File]::ReadAllBytes($newVbs)
        if ([Convert]::ToBase64String($actualBytes) -ne
            [Convert]::ToBase64String($expectedBytes)) {
            throw 'SILENT_VBS_CONTENT_MISMATCH'
        }
        Write-Host "SILENT_STARTUP_FILE=$newVbs"
        Write-Host 'STARTUP_REGISTRATION=VERIFIED'
        Write-Host 'SILENT_EXECUTION_AFTER_NEXT_LOGON=NOT_YET_TESTED'
        Write-Host 'SCIENCE_DISPATCH=STILL_QUARANTINED'
        Write-Host 'REMOTEMCP_SILENT_STARTUP_VERIFY=PASS'
        exit 0
    }
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
