param(
    [string]$SourceDir = $PSScriptRoot,
    [int]$Port = 8099,
    [string]$PublicUrl = $env:PUBLIC_URL,
    [string]$McpRoot = $env:MCP_ROOT,
    [string]$StateFile = $env:MCP_STATE,
    [string]$RuntimeDir = $env:MCP_RUNTIME_DIR,
    [string]$AllowedRedirectHosts = $env:ALLOWED_REDIRECT_HOSTS,
    [string]$PythonExe = "",
    [Security.SecureString]$OwnerPassword
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Import-RemoteMCPDotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $parts = $trimmed.Split("=",2)
        if ($parts.Count -ne 2) { continue }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (-not $name) { continue }
        if ($value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        )) {
            $value = $value.Substring(1,$value.Length-2)
        }
        if (-not [Environment]::GetEnvironmentVariable($name,"Process")) {
            [Environment]::SetEnvironmentVariable($name,$value,"Process")
        }
    }
}

Import-RemoteMCPDotEnv (Join-Path $PSScriptRoot ".env")

if (-not $PublicUrl) { $PublicUrl = $env:PUBLIC_URL }
if (-not $McpRoot) { $McpRoot = $env:MCP_ROOT }
if (-not $StateFile) { $StateFile = $env:MCP_STATE }
if (-not $RuntimeDir) { $RuntimeDir = $env:MCP_RUNTIME_DIR }
if (-not $AllowedRedirectHosts) { $AllowedRedirectHosts = $env:ALLOWED_REDIRECT_HOSTS }

$Base = Join-Path $env:LOCALAPPDATA "RemoteMCP"
$ConfigFile = Join-Path $Base "gateway-config.json"
$SecretFile = Join-Path $Base "gateway-owner-password.txt"
$GatewayVenv = Join-Path $Base "gateway-venv"
New-Item -ItemType Directory -Force -Path $Base | Out-Null

function Test-Python311 {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path $Path)) { return $false }
    try {
        & $Path -c "import sys; assert sys.version_info >= (3,11)" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Test-GatewayPython {
    param([string]$Path)
    if (-not (Test-Python311 -Path $Path)) { return $false }
    try {
        & $Path -c "import mcp,uvicorn,httpx,cryptography" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-ExistingGatewayPython {
    param([string]$Requested,[string]$Source)
    $candidates = @()
    $venvPython = Join-Path $GatewayVenv "Scripts\python.exe"
    if (Test-Path $venvPython) { $candidates += $venvPython }
    if ($Requested) { $candidates += $Requested }
    $repoVenv = Join-Path $Source ".venv\Scripts\python.exe"
    if (Test-Path $repoVenv) { $candidates += $repoVenv }
    $azureVenv = Join-Path $env:LOCALAPPDATA "Programs\azure-cli-venv\Scripts\python.exe"
    if (Test-Path $azureVenv) { $candidates += $azureVenv }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    foreach ($p in $candidates | Select-Object -Unique) {
        if (Test-GatewayPython -Path $p) { return (Resolve-Path $p).Path }
    }
    return $null
}

function Find-BootstrapPython {
    param([string]$Requested)
    $candidates = @()
    if ($Requested) { $candidates += $Requested }
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
        if (Test-Python311 -Path $p) { return (Resolve-Path $p).Path }
    }
    return $null
}

$SourceDir = (Resolve-Path $SourceDir).Path
if (-not (Test-Path (Join-Path $SourceDir "server.py"))) {
    throw "RemoteMCP server.py not found under: $SourceDir"
}

if (-not $PublicUrl) { $PublicUrl = Read-Host "RemoteMCP PUBLIC_URL (https://...)" }
if (-not $PublicUrl.StartsWith("https://")) { throw "PUBLIC_URL must be HTTPS." }

if (-not $McpRoot) { $McpRoot = (Join-Path $env:USERPROFILE "agent-workspace") }
if (-not $StateFile) { $StateFile = (Join-Path $Base "oauth-state.json") }
if (-not $RuntimeDir) { $RuntimeDir = (Join-Path $Base "gateway-runtime") }
if (-not $AllowedRedirectHosts) { $AllowedRedirectHosts = "claude.ai,claude.com,chatgpt.com,localhost,127.0.0.1" }

$GatewayPython = Find-ExistingGatewayPython -Requested $PythonExe -Source $SourceDir
if (-not $GatewayPython) {
    $BootstrapPython = Find-BootstrapPython -Requested $PythonExe
    if (-not $BootstrapPython) {
        throw "Python 3.11+ was not found. Install Python 3.11 or newer, then run this script again."
    }

    $GatewayPython = Join-Path $GatewayVenv "Scripts\python.exe"
    if (-not (Test-Path $GatewayPython)) {
        Write-Host "Creating dedicated RemoteMCP gateway virtual environment..."
        & $BootstrapPython -m venv $GatewayVenv
        if ($LASTEXITCODE -ne 0) { throw "Could not create RemoteMCP gateway virtual environment." }
    }

    Write-Host "Installing RemoteMCP gateway dependencies..."
    & $GatewayPython -m pip install --disable-pip-version-check --quiet --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip in the RemoteMCP gateway virtual environment." }
    & $GatewayPython -m pip install --disable-pip-version-check --quiet "mcp[cli]<2" "uvicorn" "httpx==0.28.1" "cryptography==46.0.6"
    if ($LASTEXITCODE -ne 0) { throw "Could not install RemoteMCP gateway dependencies." }

    if (-not (Test-GatewayPython -Path $GatewayPython)) {
        throw "RemoteMCP gateway dependencies were installed but the gateway Python preflight still failed."
    }
}

if ($null -eq $OwnerPassword) {
    if ($env:OWNER_PASSWORD) {
        $OwnerPassword = ConvertTo-SecureString $env:OWNER_PASSWORD -AsPlainText -Force
    } else {
        $OwnerPassword = Read-Host "RemoteMCP owner password" -AsSecureString
    }
}

$plain = [System.Net.NetworkCredential]::new("",$OwnerPassword).Password
if ($plain.Length -lt 12) { throw "OWNER_PASSWORD must be at least 12 characters." }
$plain = $null

$cfg = [ordered]@{
    schema_version = 2
    source_dir = $SourceDir
    python_exe = $GatewayPython
    port = $Port
    public_url = $PublicUrl.TrimEnd("/")
    mcp_root = [System.IO.Path]::GetFullPath($McpRoot)
    state_file = [System.IO.Path]::GetFullPath($StateFile)
    runtime_dir = [System.IO.Path]::GetFullPath($RuntimeDir)
    allowed_redirect_hosts = $AllowedRedirectHosts
}
$cfg | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $ConfigFile
$encryptedSecret = $OwnerPassword | ConvertFrom-SecureString
[System.IO.File]::WriteAllText($SecretFile,$encryptedSecret,[System.Text.Encoding]::ASCII)
$encryptedSecret = $null

Write-Host "RemoteMCP gateway recovery configuration saved."
Write-Host "Config : $ConfigFile"
Write-Host "Python : $GatewayPython"
Write-Host "Secret : $SecretFile (DPAPI-encrypted for the current Windows user)"
Write-Host "No OAuth tokens, durable runtime state, project registry, or device identities were changed."
