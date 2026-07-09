"""Deploy verification script.
Pipe this into the running container via:
  Get-Content scripts/deploy-verify.py | wsl sshpass -p 'tukiserver' ssh hp@100.122.35.93 wsl docker exec -i CONTAINER_ID python3

Avoids SSH quoting hell (no pipes, no JSON in command line).
"""

import json
import urllib.request
import urllib.error
import sys

BASE = "http://localhost:8000"
EXIT_CODE = 0


def req(method: str, path: str, data: dict | None = None) -> dict:
    body = json.dumps(data).encode() if data else None
    r = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method=method,
    )
    try:
        resp = urllib.request.urlopen(r, timeout=30)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode()}


def check(label: str, ok: bool, detail: str = ""):
    global EXIT_CODE
    status = "PASS" if ok else "FAIL"
    if not ok:
        EXIT_CODE = 1
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))


print("=== Health checks ===\n")

# 1. API root
print("1. API /docs ...")
resp = req("GET", "/docs")
check("Swagger UI reachable", "html" in str(resp.get("body", resp)))

# 2. Create conversation
print("\n2. Creating conversation...")
conv = req("POST", "/api/conversations/", {"title": "verify-deploy"})
conv_id = conv.get("id")
check("Conversation created", isinstance(conv_id, int), f"id={conv_id}")

# 3. Send message (triggers title gen)
if conv_id:
    print(f"\n3. Sending message to conversation {conv_id}...")
    result = req(
        "POST", "/api/ai/execute",
        {"conversation_id": conv_id, "user_message": "Hello, testing deploy!"},
    )
    check("AI execute accepted", "no response" in str(result))

print("\n=== Done ===")
sys.exit(EXIT_CODE)
