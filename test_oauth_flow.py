# Chạy server ở PUBLIC_URL=http://localhost:8765 (OWNER_PASSWORD=correct-horse-battery) rồi: python test_oauth_flow.py
import base64, hashlib, secrets, json, httpx
from urllib.parse import urlparse, parse_qs
B = "http://localhost:8765"
c = httpx.Client(base_url=B, follow_redirects=False)
def ok(name, cond, extra=""): print(("PASS" if cond else "FAIL"), name, extra)

r = c.get("/.well-known/oauth-authorization-server"); meta = r.json()
ok("AS metadata", r.status_code==200 and "code_challenge_methods_supported" in meta, meta.get("code_challenge_methods_supported"))
r = c.get("/.well-known/oauth-protected-resource/mcp")
ok("PRM metadata", r.status_code==200, r.text[:120])

# không token -> 401 + WWW-Authenticate
r = c.post("/mcp", json={"jsonrpc":"2.0","id":1,"method":"tools/list"}, headers={"Accept":"application/json, text/event-stream"})
ok("no token -> 401", r.status_code==401, r.headers.get("www-authenticate","")[:90])

# DCR: redirect xấu bị từ chối
r = c.post("/register", json={"client_name":"evil","redirect_uris":["https://evil.example/cb"],"token_endpoint_auth_method":"none","grant_types":["authorization_code","refresh_token"],"response_types":["code"]})
ok("DCR evil redirect rejected", r.status_code==400, r.text[:100])

# DCR: hợp lệ
r = c.post("/register", json={"client_name":"Test Client","redirect_uris":["http://localhost:9999/cb"],"token_endpoint_auth_method":"none","grant_types":["authorization_code","refresh_token"],"response_types":["code"]})
ok("DCR ok", r.status_code in (200,201), r.text[:80]); cid = r.json()["client_id"]

ver = secrets.token_urlsafe(48)
chal = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode()
q = dict(response_type="code", client_id=cid, redirect_uri="http://localhost:9999/cb", code_challenge=chal, code_challenge_method="S256", state="xyz", scope="mcp")
r = c.get("/authorize", params=q)
loc = r.headers.get("location",""); ok("authorize -> /login", r.status_code in (302,307) and "/login?req=" in loc, loc[:70])
req = parse_qs(urlparse(loc).query)["req"][0]

r = c.get("/login", params={"req": req}); ok("login page", r.status_code==200 and "Test Client" in r.text)
r = c.post("/login", data={"req":req,"password":"wrong","action":"allow"}); ok("wrong pw -> 401", r.status_code==401)
r = c.post("/login", data={"req":req,"password":"correct-horse-battery","action":"allow"})
loc = r.headers.get("location",""); qs = parse_qs(urlparse(loc).query)
ok("right pw -> redirect w/ code+state", r.status_code==302 and "code" in qs and qs["state"]==["xyz"], loc[:60])
code = qs["code"][0]

# sai PKCE verifier
r = c.post("/token", data=dict(grant_type="authorization_code", code=code, redirect_uri="http://localhost:9999/cb", client_id=cid, code_verifier="x"*50))
ok("bad PKCE rejected", r.status_code==400, r.text[:80])

# code có thể đã bị huỷ sau lần thử sai? thử lại với verifier đúng, nếu invalid thì tạo code mới
def get_code():
    r = c.get("/authorize", params=q); req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
    r = c.post("/login", data={"req":req,"password":"correct-horse-battery","action":"allow"})
    return parse_qs(urlparse(r.headers["location"]).query)["code"][0]
r = c.post("/token", data=dict(grant_type="authorization_code", code=code, redirect_uri="http://localhost:9999/cb", client_id=cid, code_verifier=ver))
if r.status_code != 200: code = get_code(); r = c.post("/token", data=dict(grant_type="authorization_code", code=code, redirect_uri="http://localhost:9999/cb", client_id=cid, code_verifier=ver))
ok("token exchange", r.status_code==200, r.text[:60]); tok = r.json()

r2 = c.post("/token", data=dict(grant_type="authorization_code", code=code, redirect_uri="http://localhost:9999/cb", client_id=cid, code_verifier=ver))
ok("code replay rejected", r2.status_code==400)

H = {"Accept":"application/json, text/event-stream","Content-Type":"application/json","Authorization":"Bearer "+tok["access_token"]}
init = {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}
r = c.post("/mcp", json=init, headers=H); ok("initialize w/ token", r.status_code==200, r.text[:70])
r = c.post("/mcp", json={"jsonrpc":"2.0","id":2,"method":"tools/list"}, headers=H)
names = [t["name"] for t in r.json()["result"]["tools"]]; ok("tools/list", "run_command" in names, names)
r = c.post("/mcp", json={"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"read_file","arguments":{"path":"a.txt"}}}, headers=H)
ok("read_file", "hello" in r.text, r.text[:90])
r = c.post("/mcp", json={"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"read_file","arguments":{"path":"../../etc/passwd"}}}, headers=H)
ok("path traversal blocked", "root:" not in r.text, r.text[:100])
r = c.post("/mcp", json={"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"run_command","arguments":{"command":"rm -rf /"}}}, headers=H)
ok("non-allowlisted cmd blocked", "Từ chối" in r.text, r.text[:90])

# refresh (xoay vòng)
r = c.post("/token", data=dict(grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=cid))
ok("refresh", r.status_code==200); new = r.json()
r = c.post("/token", data=dict(grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=cid))
ok("old refresh rejected (rotation)", r.status_code==400)

# thu hồi
r = c.post("/revoke", data=dict(token=new["access_token"], client_id=cid, client_secret="")); ok("revoke", r.status_code==200)
H["Authorization"]="Bearer "+new["access_token"]
r = c.post("/mcp", json={"jsonrpc":"2.0","id":6,"method":"tools/list"}, headers=H); ok("revoked token -> 401", r.status_code==401)

# khoá brute-force
req = parse_qs(urlparse(c.get("/authorize", params=q).headers["location"]).query)["req"][0]
codes = [c.post("/login", data={"req":req,"password":"bad","action":"allow"}).status_code for _ in range(7)]
ok("lockout after repeated fails", 429 in codes, codes)