from __future__ import annotations

import os


DEFAULT_SOURCE_URL = "https://github.com/minhtri22/remote-mcp/archive/refs/heads/main.zip"


def _ps_single(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def render_windows_join_script(
    *,
    public_origin: str,
    device_name: str,
    pairing_bundle: str,
    source_url: str | None = None,
) -> str:
    """Return a one-file Windows bootstrap for an outbound RemoteMCP node.

    The generated script is self-contained from the operator's point of view:
    it discovers/installs Python, creates an isolated venv, obtains node source
    when necessary, consumes the one-time pairing bundle, installs an
    auto-start entry, starts the node headlessly, and prints local status.

    The pairing bundle is intentionally embedded in the generated artifact.
    It is short-lived and single-use; after successful pairing it has no
    authority. The node private key is generated on the joining machine and
    never leaves that machine.
    """
    origin = public_origin.rstrip("/")
    url = source_url or os.environ.get("REMOTEMCP_NODE_SOURCE_URL") or DEFAULT_SOURCE_URL

    return f"""# RemoteMCP one-file node join
# Generated for one device. Treat this file as sensitive until first successful run.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$GatewayUrl = {_ps_single(origin)}
$DeviceName = {_ps_single(device_name)}
$PairingBundle = {_ps_single(pairing_bundle)}
$SourceUrl = {_ps_single(url)}

$Base = Join-Path $env:LOCALAPPDATA "RemoteMCP"
$Runtime = Join-Path $Base "runtime"
$Workspace = Join-Path $env:USERPROFILE "RemoteMCP-Workspace"
$Source = Join-Path $Base "source"
$Venv = Join-Path $Base "node-venv"
$PairFile = Join-Path $Base "pairing.txt"
$LogFile = Join-Path $Base "node.log"

New-Item -ItemType Directory -Force -Path $Base,$Runtime,$Workspace | Out-Null

function Find-RemoteMCPPython {{
    $candidates = @()
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {{ $candidates += $cmd.Source }}
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) {{
        try {{
            $p = & $cmd.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) {{ $candidates += $p.Trim() }}
        }} catch {{}}
    }}
    $known = Join-Path $env:LOCALAPPDATA "Programs/Python/Python312/python.exe"
    if (Test-Path $known) {{ $candidates += $known }}
    foreach ($p in $candidates | Select-Object -Unique) {{
        try {{
            & $p -c "import sys; assert sys.version_info >= (3,11)"
            if ($LASTEXITCODE -eq 0) {{ return $p }}
        }} catch {{}}
    }}
    return $null
}}

$BootstrapPython = Find-RemoteMCPPython
if (-not $BootstrapPython) {{
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {{
        throw "Python 3.11+ is required and winget is unavailable."
    }}
    Write-Host "Installing Python 3.12..."
    & $winget.Source install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {{ throw "Python installation failed." }}
    $BootstrapPython = Find-RemoteMCPPython
    if (-not $BootstrapPython) {{ throw "Python was installed but could not be located." }}
}}

if (-not (Test-Path (Join-Path $Venv "Scripts/python.exe"))) {{
    & $BootstrapPython -m venv $Venv
    if ($LASTEXITCODE -ne 0) {{ throw "Could not create RemoteMCP node venv." }}
}}
$NodePython = Join-Path $Venv "Scripts/python.exe"
& $NodePython -m pip install --disable-pip-version-check --quiet --upgrade pip
& $NodePython -m pip install --disable-pip-version-check --quiet "httpx==0.28.1" "cryptography==46.0.6"
if ($LASTEXITCODE -ne 0) {{ throw "RemoteMCP node dependency installation failed." }}

$LocalSource = $null
foreach ($candidate in @($PSScriptRoot,(Get-Location).Path,$Source)) {{
    if ($candidate -and (Test-Path (Join-Path $candidate "remotemcp/node/__main__.py"))) {{
        $LocalSource = (Resolve-Path $candidate).Path
        break
    }}
}}

if (-not $LocalSource) {{
    Write-Host "Downloading RemoteMCP node source..."
    $Zip = Join-Path $Base "source.zip"
    $Extract = Join-Path $Base "source-extract"
    Remove-Item $Zip -Force -ErrorAction SilentlyContinue
    Remove-Item $Extract -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $Source -Recurse -Force -ErrorAction SilentlyContinue
    Invoke-WebRequest -UseBasicParsing -Uri $SourceUrl -OutFile $Zip
    Expand-Archive -Path $Zip -DestinationPath $Extract -Force
    $root = Get-ChildItem -Path $Extract -Directory | Select-Object -First 1
    if (-not $root -or -not (Test-Path (Join-Path $root.FullName "remotemcp/node/__main__.py"))) {{
        throw "Downloaded RemoteMCP source archive is invalid."
    }}
    Move-Item -Path $root.FullName -Destination $Source
    Remove-Item $Extract -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $Zip -Force -ErrorAction SilentlyContinue
    $LocalSource = $Source
}}

Set-Location $LocalSource

$DeviceJson = Join-Path $Runtime "device.json"
if (-not (Test-Path $DeviceJson)) {{
    [System.IO.File]::WriteAllText($PairFile,$PairingBundle,[System.Text.UTF8Encoding]::new($false))
    try {{
        & $NodePython -m remotemcp.node pair --url $GatewayUrl --code-file $PairFile --name $DeviceName --root $Workspace --runtime-dir $Runtime
        if ($LASTEXITCODE -ne 0) {{ throw "RemoteMCP pairing failed." }}
    }} finally {{
        Remove-Item $PairFile -Force -ErrorAction SilentlyContinue
        $PairingBundle = $null
    }}
}} else {{
    Write-Host "Existing RemoteMCP node identity found; keeping the existing device identity."
}}

& $NodePython -m remotemcp.node doctor --runtime-dir $Runtime
if ($LASTEXITCODE -ne 0) {{ throw "RemoteMCP node doctor failed." }}

$Runner = Join-Path $Base "run-node.cmd"
$RunnerBody = @"
@echo off
cd /d "$LocalSource"
"$NodePython" -m remotemcp.node run --runtime-dir "$Runtime" >> "$LogFile" 2>&1
"@
[System.IO.File]::WriteAllText($Runner,$RunnerBody,[System.Text.Encoding]::ASCII)

$Startup = [Environment]::GetFolderPath("Startup")
$Vbs = Join-Path $Startup "RemoteMCP-Node.vbs"
$VbsBody = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run Chr(34) & "$Runner" & Chr(34), 0, False
"@
[System.IO.File]::WriteAllText($Vbs,$VbsBody,[System.Text.Encoding]::ASCII)

$already = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {{ $_.CommandLine -like "*remotemcp.node*run*--runtime-dir*$Runtime*" }}
if (-not $already) {{
    Start-Process -FilePath $NodePython -ArgumentList @("-m","remotemcp.node","run","--runtime-dir",$Runtime) -WorkingDirectory $LocalSource -WindowStyle Hidden | Out-Null
}}

Start-Sleep -Seconds 2
$status = & $NodePython -m remotemcp.node status --runtime-dir $Runtime
Write-Host ""
Write-Host "RemoteMCP connected"
Write-Host "Device : $env:COMPUTERNAME"
Write-Host $status
Write-Host "Startup: $Vbs"
"""
