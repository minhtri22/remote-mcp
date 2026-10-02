"""V2-0 public no-credential smoke test.

Usage:
    PUBLIC_URL=https://mcp.example.com python scripts/v2_0_public_smoke.py
    python scripts/v2_0_public_smoke.py https://mcp.example.com

Checks only public metadata and the unauthenticated 401 contract.
It never requests or handles the owner password.
"""
import os
import sys

# Required because the frozen run_command baseline strips Windows system vars.
os.environ.setdefault("SystemRoot", r"C:\Windows")
os.environ.setdefault("WINDIR", r"C:\Windows")

import json
import urllib.error
import urllib.request


def fetch(url: str):
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "curl/8.10.1", "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def main():
    if len(sys.argv) > 2:
        raise SystemExit("usage: python scripts/v2_0_public_smoke.py [base-url]")
    raw = sys.argv[1] if len(sys.argv) == 2 else os.environ.get("PUBLIC_URL", "")
    if not raw:
        raise SystemExit("set PUBLIC_URL or pass <base-url>")
    base = raw.rstrip("/")

    status, _, body = fetch(base + "/.well-known/oauth-authorization-server")
    assert status == 200, (status, body[:200])
    metadata = json.loads(body)
    assert metadata.get("authorization_endpoint")
    assert metadata.get("token_endpoint")

    status, _, body = fetch(base + "/.well-known/oauth-protected-resource/mcp")
    assert status == 200, (status, body[:200])

    status, headers, body = fetch(base + "/mcp")
    assert status == 401, (status, body[:200])
    challenge = (
        headers.get("WWW-Authenticate")
        or headers.get("Www-Authenticate")
        or headers.get("www-authenticate")
        or ""
    )
    assert "Bearer" in challenge
    assert "resource_metadata=" in challenge

    print("PASS public OAuth metadata")
    print("PASS protected-resource metadata")
    print("PASS /mcp unauthenticated -> 401 Bearer challenge")


if __name__ == "__main__":
    main()