#!/usr/bin/env python3
"""Hermes Security Test Mode.

An on-demand (not a systemd service) adversarial test suite against
Hermes's OWN security controls: auth, action/target allowlists, parameter
validation, and audit logging. Scope, deliberately narrow per project
rules:

  - Every request in here targets ONLY `hermes-test-target` (the isolated
    nginx:alpine container from Phase 2) or a deliberately-disallowed
    NAME STRING that never reaches a shell (there is no shell/exec
    anywhere in Hermes - subprocess is always called with a fixed argv
    list, never shell=True, so injection-flavored strings can only ever
    fail the allowlist check, never execute).
  - Never targets n8n, apiserver, apiserver-pricedb, postgres, wireguard,
    sftp, guard-honeypot, the public internet, or any third party.
  - Does not touch Guard AI in any way - this measures Hermes's own
    control surface only (per explicit instruction: "فقط عملکرد خود
    Hermes (audit log/allowlist) را بسنجیم، نه Guard AI").
  - Reads HERMES_TOKEN from hermes.env itself (this script runs on the
    VPS) - the token is never printed, never put in a command line
    argument, never written anywhere by this script.

Run manually: python3 hermes_security_test.py
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / "hermes.env"
LOG_FILE = BASE_DIR / "logs" / "hermes.log"
BASE_URL = "http://127.0.0.1:8787"

REAL_SERVICES = ["n8n", "apiserver", "apiserver-pricedb", "postgres", "wireguard", "sftp", "guard-honeypot"]
TEST_TARGET = "hermes-test-target"


def load_token():
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("HERMES_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("HERMES_TOKEN not found in hermes.env")


TOKEN = load_token()


def call(method, path, body=None, auth=True, bad_auth=None):
    url = BASE_URL + path
    headers = {"Content-Type": "application/json"}
    if bad_auth is not None:
        headers["Authorization"] = bad_auth
    elif auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode())
        except Exception:
            payload = {}
        return e.code, payload
    except Exception as e:
        return None, {"error": f"request_failed: {e}"}


RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail and not condition else ""))


def run():
    print("=== Hermes Security Test Mode ===")
    print(f"Target sandbox container: {TEST_TARGET}")
    print(f"Protected real services (must never be reachable via restart_container): {REAL_SERVICES}")
    print()

    # --- Auth ---
    code, _ = call("GET", "/status", auth=False)
    check("auth: /status with no Authorization header -> 401", code == 401, f"got {code}")

    code, _ = call("POST", "/task", body={"action": "system_status"}, bad_auth="Bearer wrong-token-0000")
    check("auth: /task with wrong token -> 401", code == 401, f"got {code}")

    code, _ = call("POST", "/task", body={"action": "system_status"}, bad_auth="Bearer")
    check("auth: /task with malformed Authorization header -> 401", code == 401, f"got {code}")

    code, body = call("POST", "/task", body={"action": "system_status"})
    check("auth: /task with correct token -> 200 (sanity baseline)", code == 200 and body.get("success") is True, f"got {code} {body}")

    # --- Read allowlist (container_status) ---
    code, body = call("POST", "/task", body={"action": "container_status", "args": {"name": "n8n"}})
    check("read-allowlist: known production name (n8n) -> allowed", code == 200 and body.get("success") is True, f"got {code} {body}")

    for bad_name, label in [
        (TEST_TARGET, "hermes-test-target is NOT in the read allowlist either"),
        ("root; rm -rf /", "shell-injection-flavored string"),
        ("../../../etc/passwd", "path-traversal-flavored string"),
        ("", "empty name"),
        ("x" * 5000, "oversized string (5000 chars)"),
    ]:
        code, body = call("POST", "/task", body={"action": "container_status", "args": {"name": bad_name}})
        ok = code == 500 and body.get("result", {}).get("error") == "unknown or non-whitelisted container"
        check(f"read-allowlist: rejects [{label}]", ok, f"got {code} {body}")

    # --- Write allowlist (restart_container) - the critical section ---
    for real_name in REAL_SERVICES:
        code, body = call("POST", "/task", body={"action": "restart_container", "args": {"name": real_name, "confirm": True}})
        ok = code == 500 and body.get("result", {}).get("error") == "target not allowed"
        check(f"write-allowlist: restart_container REJECTS real service '{real_name}' even with confirm=true", ok, f"got {code} {body}")

    code, body = call("POST", "/task", body={"action": "restart_container", "args": {"name": "postgres; docker restart n8n", "confirm": True}})
    ok = code == 500 and body.get("result", {}).get("error") == "target not allowed"
    check("write-allowlist: rejects injection-flavored compound name", ok, f"got {code} {body}")

    code, body = call("POST", "/task", body={"action": "restart_container", "args": {"name": TEST_TARGET, "confirm": False}})
    ok = code == 500 and "confirm" in body.get("result", {}).get("error", "")
    check("write-allowlist: allowed target but confirm=false -> rejected", ok, f"got {code} {body}")

    code, body = call("POST", "/task", body={"action": "restart_container", "args": {"name": TEST_TARGET, "confirm": "true"}})
    ok = code == 500 and "confirm" in body.get("result", {}).get("error", "")
    check("write-allowlist: confirm as string \"true\" (type confusion) -> rejected", ok, f"got {code} {body}")

    code, body = call("POST", "/task", body={"action": "restart_container", "args": {"name": TEST_TARGET, "confirm": True, "extra": "x"}})
    ok = code == 400 and "unexpected parameter" in (body.get("error") or "")
    check("write-allowlist: extra unknown parameter -> rejected before execution", ok, f"got {code} {body}")

    # --- Malformed requests ---
    url = BASE_URL + "/task"
    req = urllib.request.Request(url, data=b"{not valid json", headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        check("malformed: invalid JSON body -> 400", False, "did not raise")
    except urllib.error.HTTPError as e:
        check("malformed: invalid JSON body -> 400", e.code == 400, f"got {e.code}")

    code, body = call("POST", "/task", body={})
    check("malformed: missing action -> 400 unknown action", code == 400, f"got {code} {body}")

    code, body = call("POST", "/task", body={"action": "system_status; ls"})
    check("malformed: bogus action name -> 400 unknown action", code == 400, f"got {code} {body}")

    code, body = call("POST", "/task", body={"action": "system_status", "args": "not-a-dict"})
    check("malformed: args is a string, not an object -> 400", code == 400, f"got {code} {body}")

    # --- Burst of legitimate read-only calls against the sandbox (health, not a DoS test) ---
    burst_ok = True
    for _ in range(20):
        code, body = call("POST", "/task", body={"action": "list_containers"})
        if code != 200:
            burst_ok = False
    check("stability: 20 rapid legitimate read-only calls all succeed, Hermes stays up", burst_ok)

    # --- The one real, low-risk write against the allowed sandbox target ---
    code, body = call("POST", "/task", body={"action": "restart_container", "args": {"name": TEST_TARGET, "confirm": True}})
    ok = code == 200 and body.get("result", {}).get("restarted") is True
    check("write-allowlist: valid restart of the allowed sandbox target succeeds", ok, f"got {code} {body}")

    # --- Health check after everything ---
    code, body = call("GET", "/health", auth=False)
    check("health: Hermes /health still responds after full test run", code == 200, f"got {code} {body}")

    print()
    print("=== Audit log verification ===")
    time.sleep(0.3)  # let the last log_event() flush
    log_text = LOG_FILE.read_text()
    total_calls = len(RESULTS)
    # Every /task attempt above (auth failures excluded - those never reach /task's
    # body parser except the wrong-token one, which IS still logged as task_rejected)
    task_rejected_count = log_text.count('"event": "task_rejected"')
    task_executed_count = log_text.count('"event": "task_executed"')
    check("audit: hermes.log contains task_rejected entries", task_rejected_count > 0, f"count={task_rejected_count}")
    check("audit: hermes.log contains task_executed entries", task_executed_count > 0, f"count={task_executed_count}")
    check("audit: HERMES_TOKEN value never appears in hermes.log", TOKEN not in log_text)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print()
    print(f"=== RESULT: {passed}/{total} checks passed ===")
    if passed != total:
        print("FAILED CHECKS:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}: {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run())
