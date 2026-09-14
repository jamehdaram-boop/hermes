#!/usr/bin/env python3
"""Hermes - VPS operational/execution layer for AI Home.

Phase 1: read-only status reporting.
Phase 2: a small, whitelisted action layer - both the set of actions AND,
for the one write action, the set of targets it may act on are fixed
allowlists checked in code. There is no arbitrary shell/command execution:
every action is a specific Python function with fixed subprocess args.
Phase 3: a standard result envelope (see RESULT CONTRACT below) and an
audit_id on every request/response so an external caller (n8n, a future
coordinator) and the local audit log can be cross-referenced exactly.
No third-party dependencies (stdlib only).

RESULT CONTRACT - every response from POST /task has this exact shape:
    {
        "success": bool,
        "action": str,
        "target": str | null,      # args["name"] when the action has one
        "timestamp": ISO8601 str,  # when the request was received
        "duration_ms": float,      # time spent running the action itself
        "result": object | null,   # the action's own return value
        "error": str | null,       # set only when success is false
        "audit_id": str,           # matches the "audit_id" field logged
                                    # for this same call in logs/hermes.log
    }
This applies uniformly whether the request succeeded, the action itself
failed (e.g. disallowed target), or the request was rejected before any
action ran (bad auth, unknown action, unknown parameter) - every case
gets the same shape and its own audit_id, so nothing falls outside the
audit trail.

ACTION REGISTRY - adding a future action means adding one entry to
ACTION_SPECS: a plain Python function with a fixed, explicit
`allowed_args` set and a `write` flag. There is no generic "run this
command" action and none is planned - every action is individually
reviewed code, not a passthrough to the shell. A write action that needs
a target allowlist follows the restart_container pattern: its own
separate, narrow *_ALLOWED_* list, never reusing the read-only
KNOWN_CONTAINERS list.
"""
import hmac
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / "hermes.env"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
HEARTBEAT_FILE = BASE_DIR / "heartbeat.json"
LOG_FILE = LOG_DIR / "hermes.log"

# Read-only lookups (container_status) may target any of these.
KNOWN_CONTAINERS = [
    "apiserver", "apiserver-pricedb", "n8n", "wireguard",
    "sftp", "guard-honeypot", "postgres",
]

# The ONE write action (restart_container) may only ever target containers
# in this separate, much narrower list. Deliberately does not reuse
# KNOWN_CONTAINERS above - no production/security container is ever
# eligible for restart via Hermes. hermes-test-target is a dedicated,
# isolated nginx:alpine container created solely for exercising this action.
RESTART_ALLOWED_CONTAINERS = ["hermes-test-target"]


def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENV = load_env()
TOKEN = ENV.get("HERMES_TOKEN", "")
PORT = int(ENV.get("HERMES_PORT", "8787"))
BIND_ADDRS = [a.strip() for a in ENV.get("HERMES_BIND_ADDRS", "127.0.0.1").split(",") if a.strip()]


def log_event(event):
    event = dict(event)
    event["ts"] = datetime.now(timezone.utc).isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


def write_heartbeat():
    HEARTBEAT_FILE.write_text(json.dumps({
        "last_beat": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
    }))


def heartbeat_loop():
    while True:
        write_heartbeat()
        time.sleep(60)


def get_uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def get_system_status():
    load_avg = os.getloadavg()
    mem = {}
    with open("/proc/meminfo") as f:
        for line in f:
            parts = line.split()
            mem[parts[0].rstrip(":")] = int(parts[1])
    mem_total = mem.get("MemTotal", 0)
    mem_avail = mem.get("MemAvailable", 0)
    mem_used_pct = round((1 - mem_avail / mem_total) * 100, 1) if mem_total else None
    disk = shutil.disk_usage("/")
    return {
        "load_avg": load_avg,
        "mem_used_pct": mem_used_pct,
        "mem_total_mb": round(mem_total / 1024, 1),
        "mem_available_mb": round(mem_avail / 1024, 1),
        "disk_used_pct": round(disk.used / disk.total * 100, 1),
        "disk_free_gb": round(disk.free / (1024 ** 3), 1),
        "uptime_seconds": get_uptime(),
        "containers": docker_ps(),
    }


def docker_ps(_args=None):
    try:
        out = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.Image}}"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        containers = []
        for line in out.stdout.strip().splitlines():
            if not line:
                continue
            name, status, image = line.split("|", 2)
            containers.append({"name": name, "status": status, "image": image})
        return containers
    except Exception as e:
        return {"error": str(e)}


def docker_container_status(args):
    name = args.get("name", "")
    if name not in KNOWN_CONTAINERS:
        return {"error": "unknown or non-whitelisted container", "known": KNOWN_CONTAINERS}
    try:
        out = subprocess.run(
            ["docker", "inspect", name],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return {"error": out.stderr.strip()}
        state = json.loads(out.stdout)[0].get("State", {})
        return {
            "name": name,
            "state": state.get("Status", "unknown"),
            "health": (state.get("Health") or {}).get("Status", "n/a"),
        }
    except Exception as e:
        return {"error": str(e)}


def restart_container(args):
    """The only write action. Target must be in RESTART_ALLOWED_CONTAINERS
    (never a production/security container) and the caller must pass
    confirm=true, as a deliberate extra guard against an accidental call."""
    name = args.get("name", "")
    if name not in RESTART_ALLOWED_CONTAINERS:
        return {"error": "target not allowed", "allowed": RESTART_ALLOWED_CONTAINERS}
    if args.get("confirm") is not True:
        return {"error": "confirm must be true to restart a container"}
    try:
        out = subprocess.run(
            ["docker", "restart", "--time", "10", name],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return {"error": out.stderr.strip(), "name": name}
        return {"name": name, "restarted": True}
    except subprocess.TimeoutExpired:
        return {"error": "restart timed out", "name": name}
    except Exception as e:
        return {"error": str(e), "name": name}


# Each action declares the exact set of arg keys it accepts; anything else
# in the request is rejected before the action function ever runs. To add
# a future action: write its function, then add one line here.
ACTION_SPECS = {
    "system_status": {"fn": lambda args: get_system_status(), "allowed_args": set(), "write": False},
    "list_containers": {"fn": docker_ps, "allowed_args": set(), "write": False},
    "container_status": {"fn": docker_container_status, "allowed_args": {"name"}, "write": False},
    "restart_container": {"fn": restart_container, "allowed_args": {"name", "confirm"}, "write": True},
}


def is_success(result):
    return not (isinstance(result, dict) and "error" in result)


def error_of(result):
    if isinstance(result, dict) and "error" in result:
        return result["error"]
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "Hermes/1.0"

    def _send_json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not TOKEN:
            return False
        auth = self.headers.get("Authorization", "")
        return hmac.compare_digest(auth, f"Bearer {TOKEN}")

    def _envelope(self, *, success, action, target, started_iso, duration_ms, result, error, audit_id):
        return {
            "success": success,
            "action": action,
            "target": target,
            "timestamp": started_iso,
            "duration_ms": duration_ms,
            "result": result,
            "error": error,
            "audit_id": audit_id,
        }

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "service": "hermes"})
            return
        if self.path == "/status":
            if not self._authorized():
                self._send_json(401, {"error": "unauthorized"})
                return
            self._send_json(200, get_system_status())
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/task":
            self._send_json(404, {"error": "not found"})
            return
        client_ip = self.client_address[0]
        audit_id = uuid.uuid4().hex
        started_iso = datetime.now(timezone.utc).isoformat()

        if not self._authorized():
            log_event({"event": "task_rejected", "reason": "unauthorized", "client": client_ip, "audit_id": audit_id})
            self._send_json(401, self._envelope(
                success=False, action=None, target=None, started_iso=started_iso,
                duration_ms=0.0, result=None, error="unauthorized", audit_id=audit_id,
            ))
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            log_event({"event": "task_rejected", "reason": "invalid_json", "client": client_ip, "audit_id": audit_id})
            self._send_json(400, self._envelope(
                success=False, action=None, target=None, started_iso=started_iso,
                duration_ms=0.0, result=None, error="invalid json", audit_id=audit_id,
            ))
            return

        action = data.get("action")
        args = data.get("args", {}) or {}
        target = args.get("name") if isinstance(args, dict) else None
        spec = ACTION_SPECS.get(action)

        if not spec:
            log_event({"event": "task_rejected", "reason": "unknown_action", "action": action, "client": client_ip, "audit_id": audit_id})
            self._send_json(400, self._envelope(
                success=False, action=action, target=target, started_iso=started_iso,
                duration_ms=0.0, result=None,
                error=f"unknown action: {action} (available: {sorted(ACTION_SPECS)})", audit_id=audit_id,
            ))
            return

        if not isinstance(args, dict) or not set(args.keys()) <= spec["allowed_args"]:
            log_event({
                "event": "task_rejected", "reason": "unknown_param", "action": action,
                "args_keys": sorted(args.keys()) if isinstance(args, dict) else str(type(args)),
                "client": client_ip, "audit_id": audit_id,
            })
            self._send_json(400, self._envelope(
                success=False, action=action, target=target, started_iso=started_iso,
                duration_ms=0.0, result=None,
                error=f"unexpected parameter (allowed: {sorted(spec['allowed_args'])})", audit_id=audit_id,
            ))
            return

        start = time.monotonic()
        try:
            result = spec["fn"](args)
        except Exception as e:
            result = {"error": str(e)}
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        success = is_success(result)

        log_event({
            "event": "task_executed",
            "action": action,
            "target": target,
            "args": args,
            "client": client_ip,
            "write": spec["write"],
            "success": success,
            "duration_ms": duration_ms,
            "audit_id": audit_id,
        })
        self._send_json(200 if success else 500, self._envelope(
            success=success, action=action, target=target, started_iso=started_iso,
            duration_ms=duration_ms, result=result, error=error_of(result), audit_id=audit_id,
        ))

    def log_message(self, fmt, *args):
        pass


def main():
    if not TOKEN:
        raise SystemExit("HERMES_TOKEN not set in hermes.env - refusing to start without auth")
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    write_heartbeat()
    log_event({"event": "startup", "port": PORT, "bind_addrs": BIND_ADDRS})
    servers = [ThreadingHTTPServer((addr, PORT), Handler) for addr in BIND_ADDRS]
    for srv in servers[1:]:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
