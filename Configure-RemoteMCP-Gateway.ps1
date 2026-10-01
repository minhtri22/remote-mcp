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

$Base = Join-Path $env:LOCALAPPDATA "RemoteMCP"
$ConfigFile = Join-Path $Base "gateway-config.json"
$SecretFile = Join-Path $Base "gateway-owner-password.txt"
New-Item -ItemType Directory -Force -Path $Base | Out-Null

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

if (-not $PythonExe) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "Python executable not found. Pass -PythonExe explicitly."
}

& $PythonExe -c "import mcp,uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "The selected Python does not contain the RemoteMCP gateway dependencies (mcp, uvicorn)."
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
    schema_version = 1
    source_dir = $SourceDir
    python_exe = $PythonExe
    port = $Port
    public_url = $PublicUrl.TrimEnd("/")
    mcp_root = [System.IO.Path]::GetFullPath($McpRoot)
    state_file = [System.IO.Path]::GetFullPath($StateFile)
    runtime_dir = [System.IO.Path]::GetFullPath($RuntimeDir)
    allowed_redirect_hosts = $AllowedRedirectHosts
}
$cfg | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $ConfigFile
$OwnerPassword | ConvertFrom-SecureString | Set-Content -Encoding ASCII $SecretFile

Write-Host "RemoteMCP gateway recovery configuration saved."
Write-Host "Config : $ConfigFile"
Write-Host "Secret : $SecretFile (DPAPI-encrypted for the current Windows user)"
Write-Host "No OAuth tokens or device identities were changed."
