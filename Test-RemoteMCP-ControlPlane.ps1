param(
    [int]$TimeoutSec = 5,
    [switch]$ChatGPTTimeoutObserved
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$Base = Join-Path $env:LOCALAPPDATA "RemoteMCP"
$ConfigFile = Join-Path $Base "gateway-config.json"

function Probe-Url {
    param([string]$Name,[string]$Url,[int]$Timeout)

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $Timeout
        $sw.Stop()
        return [ordered]@{
            name = $Name
            url = $Url
            ok = ($r.StatusCode -eq 200)
            http_status = [int]$r.StatusCode
            elapsed_ms = [int]$sw.ElapsedMilliseconds
            error = $null
        }
    } catch {
        $sw.Stop()
        $status = $null
        try {
            if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                $status = [int]$_.Exception.Response.StatusCode
            }
        } catch {}
        return [ordered]@{
            name = $Name
            url = $Url
            ok = $false
            http_status = $status
            elapsed_ms = [int]$sw.ElapsedMilliseconds
            error = $_.Exception.Message
        }
    }
}

if (-not (Test-Path $ConfigFile)) {
    throw "RemoteMCP gateway config not found: $ConfigFile"
}

$cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json
$port = [int]$cfg.port
$publicUrl = ([string]$cfg.public_url).TrimEnd("/")

if (-not $publicUrl) {
    throw "public_url is missing from gateway config"
}

$localMetadata = "http://127.0.0.1:$port/.well-known/oauth-authorization-server"
$publicMetadata = "$publicUrl/.well-known/oauth-authorization-server"

$local = Probe-Url -Name "local_gateway_8099" -Url $localMetadata -Timeout $TimeoutSec
$public = Probe-Url -Name "public_tunnel" -Url $publicMetadata -Timeout $TimeoutSec

$classification = "UNDETERMINED"
$reason = ""

if (-not $local.ok) {
    $classification = "LOCAL_GATEWAY_8099"
    $reason = "Local OAuth metadata did not return HTTP 200. Diagnose the local gateway process/runtime before tunnel or connector."
} elseif (-not $public.ok) {
    $classification = "PUBLIC_TUNNEL_OR_EDGE"
    $reason = "Local gateway is healthy, but the public metadata endpoint is not. The fault is between the local gateway and public edge/tunnel/DNS."
} elseif ($ChatGPTTimeoutObserved) {
    $classification = "CHATGPT_CONNECTOR_PATH"
    $reason = "Local gateway and public metadata are healthy while the ChatGPT RemoteMCP call is timing out."
} else {
    $classification = "LOCAL_AND_PUBLIC_HEALTHY"
    $reason = "Local gateway and public endpoint are healthy. If ChatGPT still times out, the remaining suspect is the ChatGPT connector/OAuth/client path."
}

[ordered]@{
    observed_at = (Get-Date).ToString("o")
    mutation_free = $true
    local_gateway = $local
    public_tunnel = $public
    chatgpt_timeout_observed = [bool]$ChatGPTTimeoutObserved
    classification = $classification
    reason = $reason
} | ConvertTo-Json -Depth 6
