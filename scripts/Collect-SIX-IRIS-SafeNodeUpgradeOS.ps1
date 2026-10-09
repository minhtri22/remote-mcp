# SIX/IRIS on-host safety evidence collector (READ ONLY).
# Does not call node, alter files, delete locks, run Git, or restart services.
# Output is partial OS evidence; it does not certify gateway command status
# or authorize a node upgrade.
param(
    [string]$RuntimeDir = "$env:LOCALAPPDATA\RemoteMCP\runtime",
    [int[]]$ProtectedPids = @(9152,24528)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $RuntimeDir -PathType Container)) {
    throw "RUNTIME_DIR_NOT_FOUND"
}
$identityFile = Join-Path $RuntimeDir 'device.json'
$keyFile = Join-Path $RuntimeDir 'device-ed25519.pem'
if (-not (Test-Path -LiteralPath $identityFile -PathType Leaf) -or
    -not (Test-Path -LiteralPath $keyFile -PathType Leaf)) {
    throw "PAIRED_IDENTITY_INCOMPLETE"
}
$device = Get-Content -LiteralPath $identityFile -Raw | ConvertFrom-Json
if (-not $device.device_id -or -not $device.key_fingerprint_sha256 -or
    $null -eq $device.route_generation) {
    throw "PAIRED_IDENTITY_FIELDS_MISSING"
}
# Private key bytes are deliberately NEVER read or printed.
$all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
$index = @{}
foreach ($p in $all) {
    $index[[int]$p.ProcessId] = $p
}
$wanted = @{}
foreach ($p in $all) {
    $c = [string]$p.CommandLine
    if ($c -match 'remotemcp[.]node\s+run' -or
        $c -match 'Watch-RemoteMCP-Node[.]ps1') {
        $wanted[[int]$p.ProcessId] = $true
    }
}
foreach ($id in $ProtectedPids) {
    $wanted[[int]$id] = $true
}
# Collect ancestor chains for exact lineage, including missing-parent markers.
$queue = @($wanted.Keys)
$missing = @()
foreach ($start in $queue) {
    $current = [int]$start
    $seen = @{}
    while ($current -gt 0 -and -not $seen.ContainsKey($current)) {
        $seen[$current] = $true
        if (-not $index.ContainsKey($current)) {
            $missing += $current
            break
        }
        $p = $index[$current]
        $wanted[$current] = $true
        $current = [int]$p.ParentProcessId
    }
}
$rows = @(
    foreach ($rawPid in ($wanted.Keys | Sort-Object)) {
        $id = [int]$rawPid
        if (-not $index.ContainsKey($id)) { continue }
        $p = $index[$id]
        $cmd = [string]$p.CommandLine
        $isNodeOrWatchdog = (
            $cmd -match 'remotemcp[.]node\s+run' -or
            $cmd -match 'Watch-RemoteMCP-Node[.]ps1'
        )
        $isProtected = $ProtectedPids -contains $id
        # Do not expose command-line arguments of arbitrary OS ancestors.
        $safeCmd = if ($isNodeOrWatchdog -or $isProtected) {
            $cmd -replace '(?i)(--(?:token|password|secret|api-key)\s+)\S+', '$1[REDACTED]'
        } else { [string]$p.Name }
        [ordered]@{
            pid = $id
            parent_pid = [int]$p.ParentProcessId
            created_at = if ($p.CreationDate) { $p.CreationDate.ToString('o') } else { $null }
            command_line = $safeCmd
        }
    }
)
$lockFile = Join-Path $RuntimeDir 'node.lock'
$payload = [ordered]@{
    schema = 'remotemcp.six-iris-os-capture.v1'
    captured_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    computer_name = [string]$env:COMPUTERNAME
    node_identity = [ordered]@{
        device_id = [string]$device.device_id
        key_fingerprint_sha256 = [string]$device.key_fingerprint_sha256
        route_generation = [int]$device.route_generation
    }
    runtime_dir = [string]$RuntimeDir
    lock_file_exists = (Test-Path -LiteralPath $lockFile -PathType Leaf)
    os_capture = [ordered]@{
        processes = $rows
        missing_ancestry_pids = @($missing | Sort-Object -Unique)
    }
    signed_gateway_evidence_included = $false
    exact_six_command_journal_included = $false
    cutover_authorized = $false
    mutation_performed = $false
}
$payload | ConvertTo-Json -Depth 8
