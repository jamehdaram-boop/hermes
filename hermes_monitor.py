#!/usr/bin/env python3
"""Hermes Monitor - lightweight VPS health monitoring + Telegram alerting.

Reuses Hermes's own GET /status (does not re-implement system reading -
single source of truth stays in hermes.py). Runs as a systemd --user
timer, stdlib only, zero cost.

Alerts are sent DIRECTLY to the Hermes Telegram Bot's API, deliberately
bypassing n8n: an alert about "n8n is down" must not depend on n8n being
up to be delivered. This mirrors Guard AI's own established pattern
(guard-ai/telegram_config.json) of a standalone script owning its own
direct Telegram send path.

Deduplication: an alert key only re-fires when it's new, when its
severity changes, or after COOLDOWN_SECONDS if still open. A recovery
message fires once when a previously-open problem clears.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / "hermes.env"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
STATE_DIR = BASE_DIR / "state"
STATE_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "monitor_state.json"
LAST_STATUS_FILE = STATE_DIR / "last_status.json"
HEARTBEAT_FILE = BASE_DIR / "monitor-heartbeat.json"
LOG_FILE = LOG_DIR / "hermes_monitor.log"

HERMES_URL = "http://127.0.0.1:8787"

# Thresholds - calibrated against the real baseline observed before
# enabling this (load <0.1, RAM ~35-48%, disk ~7%), with headroom.
CPU_LOAD_WARN_RATIO = 0.85   # load_avg[0] / nproc
CPU_LOAD_CRIT_RATIO = 1.5
MEM_WARN_PCT = 85
MEM_CRIT_PCT = 95
DISK_WARN_PCT = 85
DISK_CRIT_PCT = 95

# Only these production containers are health-checked here - matches
# Hermes's own KNOWN_CONTAINERS read-allowlist exactly.
KNOWN_CONTAINERS = ["apiserver", "apiserver-pricedb", "n8n", "wireguard", "sftp", "guard-honeypot", "postgres"]

COOLDOWN_SECONDS = 3600  # re-remind about a still-open problem at most once/hour


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
HERMES_TOKEN = ENV.get("HERMES_TOKEN", "")
TG_BOT_TOKEN = ENV.get("HERMES_TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = ENV.get("HERMES_TELEGRAM_CHAT_ID", "")


def log_event(event):
    event = dict(event)
    event["ts"] = datetime.now(timezone.utc).isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


def write_heartbeat(extra=None):
    payload = {"last_check": datetime.now(timezone.utc).isoformat()}
    if extra:
        payload.update(extra)
    HEARTBEAT_FILE.write_text(json.dumps(payload))


def load_json(p, default):
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return default
    return default


def save_json(p, obj):
    p.write_text(json.dumps(obj, indent=2))


def call_hermes_status():
    req = urllib.request.Request(
        HERMES_URL + "/status",
        headers={"Authorization": f"Bearer {HERMES_TOKEN}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def check_hermes_self():
    try:
        req = urllib.request.Request(HERMES_URL + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def send_telegram(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False, "telegram not configured"
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TG_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
            return bool(body.get("ok")), body
    except Exception as e:
        return False, str(e)


def nproc():
    try:
        return max(1, os.cpu_count() or 1)
    except Exception:
        return 1


def evaluate(status):
    problems = []
    cores = nproc()
    load1 = (status.get("load_avg") or [0])[0]
    ratio = load1 / cores if cores else 0
    if ratio >= CPU_LOAD_CRIT_RATIO:
        problems.append({"key": "cpu_load", "severity": "CRITICAL", "value": load1, "detail": f"load_avg[0]={load1} ({ratio:.2f}x {cores} cores)"})
    elif ratio >= CPU_LOAD_WARN_RATIO:
        problems.append({"key": "cpu_load", "severity": "WARNING", "value": load1, "detail": f"load_avg[0]={load1} ({ratio:.2f}x {cores} cores)"})

    mem_pct = status.get("mem_used_pct")
    if mem_pct is not None:
        if mem_pct >= MEM_CRIT_PCT:
            problems.append({"key": "mem", "severity": "CRITICAL", "value": mem_pct, "detail": f"RAM used {mem_pct}%"})
        elif mem_pct >= MEM_WARN_PCT:
            problems.append({"key": "mem", "severity": "WARNING", "value": mem_pct, "detail": f"RAM used {mem_pct}%"})

    disk_pct = status.get("disk_used_pct")
    if disk_pct is not None:
        if disk_pct >= DISK_CRIT_PCT:
            problems.append({"key": "disk", "severity": "CRITICAL", "value": disk_pct, "detail": f"Disk used {disk_pct}%"})
        elif disk_pct >= DISK_WARN_PCT:
            problems.append({"key": "disk", "severity": "WARNING", "value": disk_pct, "detail": f"Disk used {disk_pct}%"})

    containers = {c["name"]: c for c in status.get("containers", [])}
    for name in KNOWN_CONTAINERS:
        c = containers.get(name)
        if not c:
            problems.append({"key": f"container:{name}", "severity": "CRITICAL", "value": "missing", "detail": f"{name} not found in docker ps"})
            continue
        st = c.get("status", "")
        if not st.startswith("Up"):
            problems.append({"key": f"container:{name}", "severity": "CRITICAL", "value": st, "detail": f"{name} status: {st}"})
        elif "unhealthy" in st.lower():
            problems.append({"key": f"container:{name}", "severity": "CRITICAL", "value": st, "detail": f"{name} status: {st}"})

    return problems


def format_alert(p, event_id):
    return (
        "\U0001F6A8 Hermes Monitor Alert\n"
        f"type: {p['key']}\n"
        f"target: {p['key']}\n"
        f"severity: {p['severity']}\n"
        f"observed: {p['detail']}\n"
        f"time: {datetime.now(timezone.utc).isoformat()}\n"
        f"event_id: {event_id}"
    )


def format_recovery(key, event_id):
    return (
        "✅ Hermes Monitor Recovery\n"
        f"type: {key}\n"
        f"target: {key}\n"
        f"time: {datetime.now(timezone.utc).isoformat()}\n"
        f"event_id: {event_id}"
    )


def run(dry_run=False):
    state = load_json(STATE_FILE, {"alerts": {}})
    alerts_state = state.setdefault("alerts", {})
    now = time.time()

    if not check_hermes_self():
        event_id = uuid.uuid4().hex
        log_event({"event": "check_failed", "reason": "hermes_unreachable", "event_id": event_id})
        if not dry_run:
            send_telegram(
                "\U0001F6A8 Hermes Monitor Alert\ntype: hermes_unreachable\nseverity: CRITICAL\n"
                f"time: {datetime.now(timezone.utc).isoformat()}\nevent_id: {event_id}"
            )
        write_heartbeat({"hermes_reachable": False})
        return

    try:
        status = call_hermes_status()
    except Exception as e:
        event_id = uuid.uuid4().hex
        log_event({"event": "check_failed", "reason": str(e), "event_id": event_id})
        write_heartbeat({"hermes_reachable": False})
        return

    save_json(LAST_STATUS_FILE, status)
    problems = evaluate(status)
    problem_keys = {p["key"] for p in problems}

    sent = 0
    for p in problems:
        key = p["key"]
        prev = alerts_state.get(key)
        should_alert = (
            prev is None
            or prev.get("severity") != p["severity"]
            or (now - prev.get("last_alert_epoch", 0)) > COOLDOWN_SECONDS
        )
        if should_alert:
            event_id = uuid.uuid4().hex
            text = format_alert(p, event_id)
            ok = True if dry_run else send_telegram(text)[0]
            log_event({
                "event": "alert", "key": key, "severity": p["severity"], "value": p["value"],
                "detail": p["detail"], "telegram_sent": ok, "event_id": event_id, "dry_run": dry_run,
            })
            if ok:
                sent += 1
            alerts_state[key] = {"severity": p["severity"], "last_alert_epoch": now, "open": True}
        else:
            alerts_state[key]["severity"] = p["severity"]
            alerts_state[key]["open"] = True

    for key in list(alerts_state.keys()):
        if alerts_state[key].get("open") and key not in problem_keys:
            event_id = uuid.uuid4().hex
            text = format_recovery(key, event_id)
            ok = True if dry_run else send_telegram(text)[0]
            log_event({"event": "recovery", "key": key, "telegram_sent": ok, "event_id": event_id, "dry_run": dry_run})
            del alerts_state[key]

    save_json(STATE_FILE, state)
    write_heartbeat({"hermes_reachable": True, "problems_open": len(problems), "alerts_sent_this_run": sent})
    log_event({"event": "check_complete", "problems_open": len(problems), "alerts_sent": sent, "dry_run": dry_run})


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
