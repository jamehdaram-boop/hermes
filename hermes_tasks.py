#!/usr/bin/env python3
"""hermes_tasks - structured task/event memory and the single
enforcement chokepoint (run_gated) that every future Hermes action must
be called through.

This is groundwork for the "real agent" phase: today Hermes has no
browser/email/registration actions yet (those need a separate decision
- see README "Next phase" section), so nothing calls run_gated() for
real traffic yet. What exists now is the contract those future actions
must follow, fully built and tested with simulated actions:

    from hermes_tasks import run_gated

    result = run_gated(
        action_name="delete_file",              # short machine name
        description="حذف فایل قدیمی گزارش",     # human-readable, what/why
        fn=lambda: really_delete_the_file(...),  # only called if ALLOWED
        amount=None, where=None, why=None,        # financial disclosure fields
    )

run_gated() ALWAYS logs to hermes_tasks.log (request, status, result,
error, duration_ms - never secrets). If hermes_policy_gate blocks the
action, `fn` is never called, a pending-approval record is logged to
hermes_pending_approvals.log instead, and the returned TaskResult has
status="blocked" with a ready-to-send Telegram notice in the user's
language. There is no function in this module that approves a pending
item - clearing one requires the user's own fresh instruction elsewhere.
"""
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_i18n import t, DEFAULT_LOCALE
from hermes_policy_gate import evaluate as gate_evaluate, format_blocked_notice

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
TASK_LOG_FILE = LOG_DIR / "hermes_tasks.log"
PENDING_LOG_FILE = LOG_DIR / "hermes_pending_approvals.log"


class TaskResult:
    __slots__ = ("task_id", "status", "result", "error", "notice", "category")

    def __init__(self, task_id, status, result=None, error=None, notice=None, category=None):
        self.task_id = task_id
        self.status = status  # "success" | "failed" | "blocked"
        self.result = result
        self.error = error
        self.notice = notice  # ready-to-send Telegram text, only set when blocked
        self.category = category  # "DESTRUCTIVE" | "FINANCIAL", only set when blocked


def _append_jsonl(path, event):
    event = dict(event)
    event["ts"] = datetime.now(timezone.utc).isoformat()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def log_task(request, status, result=None, error=None, duration_ms=None, task_id=None):
    task_id = task_id or uuid.uuid4().hex
    _append_jsonl(TASK_LOG_FILE, {
        "task_id": task_id,
        "request": request,
        "status": status,
        "result": result,
        "error": error,
        "duration_ms": duration_ms,
    })
    return task_id


def log_pending_approval(action_name, category, description, amount=None, where=None, why=None):
    approval_id = uuid.uuid4().hex
    _append_jsonl(PENDING_LOG_FILE, {
        "approval_id": approval_id,
        "action_name": action_name,
        "category": category,
        "description": description,
        "amount": amount,
        "where": where,
        "why": why,
        "status": "pending",
    })
    return approval_id


def run_gated(action_name, description, fn, args=None, amount=None, where=None, why=None,
              locale=DEFAULT_LOCALE):
    """The single required chokepoint for any action beyond today's
    fixed whitelisted VPS actions. Evaluates the policy gate first;
    `fn` is invoked only if the gate allows it. Always logs a task
    record; blocked actions also log a pending-approval record and are
    never retried automatically."""
    decision = gate_evaluate(action_name, description, args)

    if not decision.allowed:
        approval_id = log_pending_approval(
            action_name, decision.category, description, amount=amount, where=where, why=why,
        )
        task_id = log_task(
            request={"action": action_name, "description": description},
            status="blocked",
            result={"category": decision.category, "matched_pattern": decision.matched_pattern,
                    "approval_id": approval_id},
        )
        notice = format_blocked_notice(
            action_name, description, decision.category, locale=locale,
            approval_id=approval_id, amount=amount, where=where, why=why,
        )
        return TaskResult(task_id, "blocked", notice=notice, category=decision.category)

    task_id = uuid.uuid4().hex
    start = time.monotonic()
    try:
        result = fn()
    except Exception as e:
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        log_task(
            request={"action": action_name, "description": description}, status="failed",
            error=str(e), duration_ms=duration_ms, task_id=task_id,
        )
        return TaskResult(task_id, "failed", error=str(e))

    duration_ms = round((time.monotonic() - start) * 1000, 1)
    log_task(
        request={"action": action_name, "description": description}, status="success",
        result=result if isinstance(result, (dict, list, str, int, float, bool, type(None))) else str(result),
        duration_ms=duration_ms, task_id=task_id,
    )
    return TaskResult(task_id, "success", result=result)


# ---- simulated, non-destructive, non-financial self-tests ----
# These exercise run_gated() end-to-end without ever touching a real
# file, account, or payment - see README "Phase 7 tests".

def _simulate_delete():
    def _would_delete():
        raise AssertionError("run_gated must never call fn for a blocked action")
    return run_gated(
        action_name="delete_file",
        description="حذف فایل آزمایشی /tmp/example-report.txt (شبیه‌سازی - هرگز واقعی نیست)",
        fn=_would_delete,
    )


def _simulate_payment():
    def _would_pay():
        raise AssertionError("run_gated must never call fn for a blocked action")
    return run_gated(
        action_name="buy_subscription",
        description="خرید اشتراک آزمایشی از یک سایت نمونه (شبیه‌سازی - هرگز واقعی نیست)",
        fn=_would_pay,
        amount="99000 تومان (نمونه)",
        where="example-shop.test (سایت آزمایشی - واقعی نیست)",
        why="تست Policy Gate مالی",
    )


def _simulate_normal():
    return run_gated(
        action_name="check_status",
        description="بررسی وضعیت سیستم (عملیات عادی، غیرمخرب و غیرمالی)",
        fn=lambda: {"ok": True, "note": "simulated normal task"},
    )


if __name__ == "__main__":
    if "--simulate-delete" in sys.argv:
        r = _simulate_delete()
        print("status:", r.status, "category:", r.category)
        print(r.notice)
    elif "--simulate-payment" in sys.argv:
        r = _simulate_payment()
        print("status:", r.status, "category:", r.category)
        print(r.notice)
    elif "--simulate-normal" in sys.argv:
        r = _simulate_normal()
        print("status:", r.status, "result:", r.result)
    else:
        print("usage: hermes_tasks.py --simulate-delete | --simulate-payment | --simulate-normal")
