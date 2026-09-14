#!/usr/bin/env python3
"""Hermes Daily Report - once-daily VPS summary sent via Telegram.

Runs as a systemd --user timer (not an n8n workflow), for the same
resilience reason as hermes_monitor.py: sending direct via the Telegram
Bot API means the report still goes out even if n8n itself is down or
mid-restart. Reads only existing logs (hermes.log, hermes_monitor.log,
Guard AI's own alert logs) - read-only, no Guard AI files touched.
"""
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / "hermes.env"
LOG_DIR = BASE_DIR / "logs"
REPORT_LOG_FILE = LOG_DIR / "hermes_daily_report.log"
HERMES_LOG_FILE = LOG_DIR / "hermes.log"
MONITOR_LOG_FILE = LOG_DIR / "hermes_monitor.log"

GUARD_LOG_DIR = Path("/home/claude-admin/guard-ai/logs")
GUARD_ALERT_LOGS = [GUARD_LOG_DIR / "defense-alerts.log", GUARD_LOG_DIR / "incident-alerts.log"]

sys.path.insert(0, str(BASE_DIR))
import hermes_monitor as hm  # reuses load_env, call_hermes_status, send_telegram, nproc
from hermes_i18n import t


def parse_ts(raw):
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def count_jsonl_last_24h(path, ts_field="timestamp"):
    if not path.exists():
        return 0, 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    total = 0
    matched = 0
    try:
        with open(path, "r", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ts = parse_ts(obj.get(ts_field, ""))
                if ts and ts >= cutoff:
                    matched += 1
    except Exception:
        pass
    return matched, total


def hermes_actions_last_24h():
    if not HERMES_LOG_FILE.exists():
        return {"executed": 0, "failed": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    executed = 0
    failed = 0
    with open(HERMES_LOG_FILE, "r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts = parse_ts(obj.get("timestamp") or obj.get("ts") or "")
            if not ts or ts < cutoff:
                continue
            event = obj.get("event", "")
            if event == "task_executed":
                executed += 1
                if obj.get("success") is False:
                    failed += 1
            elif event == "task_rejected":
                failed += 1
    return {"executed": executed, "failed": failed}


def monitor_alerts_last_24h():
    if not MONITOR_LOG_FILE.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    n = 0
    with open(MONITOR_LOG_FILE, "r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("event") != "alert":
                continue
            ts = parse_ts(obj.get("ts", ""))
            if ts and ts >= cutoff:
                n += 1
    return n


def guard_alerts_last_24h():
    total = 0
    for path in GUARD_ALERT_LOGS:
        matched, _ = count_jsonl_last_24h(path, ts_field="timestamp")
        total += matched
    return total


def build_report():
    status = hm.call_hermes_status()
    uptime_s = status.get("uptime_seconds", 0)
    uptime_days = int(uptime_s // 86400)
    uptime_hours = int((uptime_s % 86400) // 3600)
    load1 = (status.get("load_avg") or [0])[0]
    mem_pct = status.get("mem_used_pct")
    disk_pct = status.get("disk_used_pct")
    containers = status.get("containers", [])
    healthy = sum(1 for c in containers if str(c.get("status", "")).startswith("Up"))
    problem = [c["name"] for c in containers if not str(c.get("status", "")).startswith("Up")]

    actions = hermes_actions_last_24h()
    monitor_alerts = monitor_alerts_last_24h()
    guard_alerts = guard_alerts_last_24h()

    overall_ok = not (problem or actions["failed"] > 0 or monitor_alerts > 0)
    overall_raw = "OK" if overall_ok else "ATTENTION"  # kept English in the audit log/summary dict
    overall_display = t("overall_ok") if overall_ok else t("overall_attention")

    lines = [
        t("report_title"),
        datetime.now(timezone.utc).strftime("%Y-%m-%d") + " (UTC)",
        "",
        f"{t('report_uptime')}: {uptime_days} {t('day')} {uptime_hours} {t('hour')}",
        f"{t('report_load')}: {load1}",
        f"{t('report_ram')}: {mem_pct}%",
        f"{t('report_disk')}: {disk_pct}%",
        "",
        f"{t('report_containers_healthy')}: {healthy}/{len(containers)}",
    ]
    if problem:
        lines.append(f"{t('report_containers_problem')}: {', '.join(problem)}")
    lines += [
        "",
        f"{t('report_actions')}: {actions['executed']} {t('report_actions_executed')}, "
        f"{actions['failed']} {t('report_actions_failed')}",
        f"{t('report_monitor_alerts')}: {monitor_alerts}",
        f"{t('report_guard_alerts')}: {guard_alerts}",
        "",
        f"{t('report_overall')}: {overall_display}",
    ]
    return "\n".join(lines), {
        "uptime_days": uptime_days, "load1": load1, "mem_pct": mem_pct, "disk_pct": disk_pct,
        "healthy": healthy, "total_containers": len(containers), "problem_containers": problem,
        "actions_executed": actions["executed"], "actions_failed": actions["failed"],
        "monitor_alerts": monitor_alerts, "guard_alerts": guard_alerts, "overall": overall_raw,
    }


def log_event(event):
    event = dict(event)
    event["ts"] = datetime.now(timezone.utc).isoformat()
    with open(REPORT_LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


def run(dry_run=False):
    event_id = uuid.uuid4().hex
    try:
        text, summary = build_report()
    except Exception as e:
        log_event({"event": "report_failed", "reason": str(e), "event_id": event_id})
        if not dry_run:
            hm.send_telegram(
                f"{t('report_failed_title')}\n"
                f"{t('report_failed_reason')}: {e}\n{t('label_event_id')}: {event_id}"
            )
        return

    ok = True if dry_run else hm.send_telegram(text)[0]
    log_event({"event": "report_sent", "telegram_sent": ok, "event_id": event_id, "dry_run": dry_run, **summary})
    if dry_run:
        print(text)
        print("---")
        print("(dry-run: not sent)")


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
