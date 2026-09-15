#!/usr/bin/env python3
"""hermes_ai_brain - Tier 1 "AI Brain" (experimental, test-only).

Analyzes a free-form user request (Persian by default) and produces a
structured Plan: intent, a step-by-step breakdown, which tool
category(ies) the request would need (vps_status / browser / gmail /
none), and whether it looks destructive or financial. This module is
NOT wired into the live Telegram Bridge - it is a standalone,
offline-testable component. Wiring it into the real Telegram flow is a
separate decision for later.

Real inference: Groq Free (OpenAI-compatible chat completions),
model `qwen/qwen3.6-27b` (Groq's current recommended Qwen model -
strong multilingual/Persian tokenization, JSON mode + tool-calling
support, not on Groq's deprecated-model list as of this writing).
Used ONLY if HERMES_GROQ_API_KEY is present in hermes.env. This module
never creates, requests, or stores a new key - if the key is absent,
analyze() runs in SIMULATED mode: a deterministic, rule-based
classifier that exercises the exact same Plan schema, so the *routing
and safety logic* can be fully tested without any cost or a credential
that doesn't exist yet. Every Plan carries `"simulated": true/false` so
a caller (or a human reading the log) always knows which path produced
it.

SAFETY - the one rule that matters most in this module: the AI Brain
(real or simulated) only ever *proposes* an intent/plan/tools. It does
NOT decide whether an action is allowed. That decision is made by a
separate, independent call to hermes_policy_gate.evaluate() against
the raw request text - never against the model's own self-reported
risk assessment. This means a compromised or adversarial model output
that claims "this is safe" cannot bypass the gate: the gate re-checks
the actual text itself, every time. See analyze()'s docstring and
run_test_suite()'s adversarial test for a concrete demonstration.
"""
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_policy_gate import evaluate as gate_evaluate

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / "hermes.env"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
BRAIN_LOG_FILE = LOG_DIR / "hermes_ai_brain.log"

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL_DEFAULT = "qwen/qwen3.6-27b"


def _load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


_ENV = _load_env()
GROQ_API_KEY = _ENV.get("HERMES_GROQ_API_KEY", "")  # never created/requested by this module


def groq_configured():
    return bool(GROQ_API_KEY)


SYSTEM_PROMPT = (
    "You are a request analyzer for a VPS operations assistant. Given a "
    "user's message (often Persian), respond with ONLY a JSON object, no "
    "other text, matching exactly this schema:\n"
    '{"intent": "short_snake_case_label", "summary_fa": "one line Persian '
    'summary of what the user wants", "steps": ["step 1", "step 2", ...], '
    '"required_tools": ["vps_status" | "browser" | "gmail", ...], '
    '"risk_hint": "none" | "destructive" | "financial"}\n'
    "required_tools should be an empty list if no tool is needed. "
    "risk_hint is only your own best guess and is NOT the final safety "
    "decision - a separate system independently checks every request "
    "for destructive/financial content regardless of what you answer here."
)


def _call_groq(user_message, model=GROQ_MODEL_DEFAULT, timeout=20):
    """Real Groq API call. Returns (plan_dict, error) - error is None on
    success. Never called unless groq_configured() is True; this
    function itself never creates or stores a key."""
    if not GROQ_API_KEY:
        return None, "groq_not_configured"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        GROQ_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        plan = json.loads(content)
        return plan, None
    except urllib.error.HTTPError as e:
        return None, f"groq_http_error_{e.code}"
    except Exception as e:
        return None, f"groq_call_failed: {type(e).__name__}"


# ---- simulated (offline, deterministic) fallback ----
# Bilingual keyword heuristics, used ONLY when no Groq key is configured.
# Intentionally simple and auditable - this is a test double for the
# routing/tool-selection logic, not a claim of real language
# understanding. It never makes the risk decision (see analyze()).

_BROWSER_HINTS = [
    "سایت", "وب‌سایت", "وبسایت", "ثبت‌نام", "ثبت نام", "فرم", "کلیک",
    "لینک", "مرورگر", "browser", "website", "sign up", "signup",
    "register", "form", "click",
]
_GMAIL_HINTS = [
    "ایمیل", "جیمیل", "gmail", "email", "inbox", "صندوق پستی",
    "کد تأیید", "پیام تأیید", "confirmation email", "verification code",
]
_STATUS_HINTS = [
    "وضعیت", "سرور", "کانتینر", "status", "container", "vps", "سیستم",
]
_STEP_SPLIT_PATTERN = re.compile(
    r"(?:\.|،|,|\bبعد\b|\bسپس\b|\bthen\b|\bafter that\b)", re.IGNORECASE
)


def _simulate_analysis(message):
    text = message.lower()
    tools = []
    if any(h in text for h in _BROWSER_HINTS):
        tools.append("browser")
    if any(h in text for h in _GMAIL_HINTS):
        tools.append("gmail")
    if any(h in text for h in _STATUS_HINTS) and not tools:
        tools.append("vps_status")

    raw_steps = [s.strip() for s in _STEP_SPLIT_PATTERN.split(message) if s.strip()]
    steps = raw_steps if len(raw_steps) > 1 else [message.strip()]

    if "gmail" in tools:
        intent = "check_email"
    elif "browser" in tools:
        intent = "web_task"
    elif "vps_status" in tools:
        intent = "status_check"
    else:
        intent = "general_request"

    return {
        "intent": intent,
        "summary_fa": message.strip()[:120],
        "steps": steps,
        "required_tools": tools,
        "risk_hint": "none",  # simulated brain never claims risk - the gate decides
    }, None


def _log(event):
    event = dict(event)
    from datetime import datetime, timezone
    event["ts"] = datetime.now(timezone.utc).isoformat()
    with open(BRAIN_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def analyze(message, force_model_risk_hint=None):
    """The single entry point. Returns a Plan dict:
      intent, summary_fa, steps, required_tools, simulated, model,
      model_risk_hint (whatever the brain itself guessed - informational
      only), policy_gate (the ACTUAL, independent, non-bypassable
      decision from hermes_policy_gate.evaluate()).

    force_model_risk_hint exists only for the adversarial test in
    run_test_suite() - it lets a test simulate a model that lies about
    risk_hint, to prove the real policy_gate decision is unaffected by
    whatever the brain claims.
    """
    if groq_configured():
        plan, error = _call_groq(message)
        simulated = False
        model = GROQ_MODEL_DEFAULT
    else:
        plan, error = None, "groq_not_configured"
        simulated = True
        model = None

    if plan is None:
        sim_plan, _ = _simulate_analysis(message)
        plan = sim_plan
        simulated = True
        model = None

    if force_model_risk_hint is not None:
        plan["risk_hint"] = force_model_risk_hint

    # The authoritative, independent safety decision - evaluated against
    # the raw request text and the brain's own proposed intent/steps,
    # NEVER against plan["risk_hint"]. This is what actually gates
    # execution; nothing above this line is trusted for that purpose.
    description = plan.get("summary_fa") or message
    gate_decision = gate_evaluate(
        action_name=plan.get("intent", "unknown"),
        description=f"{message} | {description} | steps: {plan.get('steps')}",
        args={"required_tools": plan.get("required_tools", [])},
    )

    result = {
        "intent": plan.get("intent"),
        "summary_fa": plan.get("summary_fa"),
        "steps": plan.get("steps", []),
        "required_tools": plan.get("required_tools", []),
        "model_risk_hint": plan.get("risk_hint", "none"),
        "simulated": simulated,
        "model": model,
        "policy_gate": {
            "allowed": gate_decision.allowed,
            "category": gate_decision.category,
            "matched_pattern": gate_decision.matched_pattern,
        },
        "groq_error": error if simulated and groq_configured() else None,
    }

    if gate_decision.allowed is False and plan.get("risk_hint") == "none":
        # The brain thought this was safe (or didn't say), but the
        # independent gate disagrees - exactly the scenario that proves
        # the gate cannot be talked out of blocking something. Worth its
        # own log line for audit visibility.
        _log({"event": "policy_gate_override", "message": message, "result": result})
    else:
        _log({"event": "analyze", "message": message, "result": result})

    return result


# ---- test suite (task requirement: simple fa, multi-step fa, browser
# detection, gmail detection, delete->approval, payment->approval, plus
# an adversarial non-bypass proof) ----

def run_test_suite():
    cases = [
        ("simple_persian", "سلام، وضعیت سرور و کانتینرها را برایم بگو", {
            "expect_tools_any": ["vps_status"], "expect_blocked": False,
        }),
        ("multistep_persian", "اول وضعیت سرور رو چک کن، بعد کانتینرهای مشکل‌دار رو مشخص کن، و در پایان یک گزارش خلاصه بده", {
            "expect_min_steps": 2, "expect_blocked": False,
        }),
        ("needs_browser", "برو سایت example.com و یک ثبت‌نام رایگان انجام بده", {
            "expect_tools_any": ["browser"], "expect_blocked": False,
        }),
        ("needs_gmail", "ایمیل تأیید ثبت‌نام رو از جیمیل من پیدا کن و کد داخلش رو بهم بگو", {
            "expect_tools_any": ["gmail"], "expect_blocked": False,
        }),
        ("delete_request", "فایل گزارش قدیمی روی سرور رو پاک کن", {
            "expect_blocked": True, "expect_category": "DESTRUCTIVE",
        }),
        ("payment_request", "لطفاً اشتراک پرمیوم این سایت رو برام بخر", {
            "expect_blocked": True, "expect_category": "FINANCIAL",
        }),
    ]

    passed = 0
    failed = 0
    print(f"AI Brain mode: {'SIMULATED (no Groq key configured)' if not groq_configured() else 'REAL Groq call'}")
    print()
    for name, message, expect in cases:
        r = analyze(message)
        ok = True
        reasons = []

        if "expect_tools_any" in expect:
            if not any(t in r["required_tools"] for t in expect["expect_tools_any"]):
                ok = False
                reasons.append(f"expected one of {expect['expect_tools_any']} in required_tools, got {r['required_tools']}")
        if "expect_min_steps" in expect:
            if len(r["steps"]) < expect["expect_min_steps"]:
                ok = False
                reasons.append(f"expected >= {expect['expect_min_steps']} steps, got {len(r['steps'])}")
        if "expect_blocked" in expect:
            actually_blocked = r["policy_gate"]["allowed"] is False
            if actually_blocked != expect["expect_blocked"]:
                ok = False
                reasons.append(f"expected blocked={expect['expect_blocked']}, got {actually_blocked}")
        if "expect_category" in expect:
            if r["policy_gate"]["category"] != expect["expect_category"]:
                ok = False
                reasons.append(f"expected category={expect['expect_category']}, got {r['policy_gate']['category']}")

        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}: intent={r['intent']} tools={r['required_tools']} "
              f"steps={len(r['steps'])} blocked={r['policy_gate']['allowed'] is False} "
              f"category={r['policy_gate']['category']}")
        if not ok:
            for reason in reasons:
                print(f"         -> {reason}")
        passed += ok
        failed += not ok

    # Adversarial non-bypass proof: force the brain to CLAIM risk_hint
    # "none" for a delete request, and confirm the independent gate
    # still blocks it - proving the gate ignores the brain's own
    # self-assessment entirely.
    print()
    print("[ADVERSARIAL] forcing model_risk_hint='none' on a delete request:")
    r = analyze("این پوشه رو کامل حذف کن", force_model_risk_hint="none")
    adversarial_ok = r["policy_gate"]["allowed"] is False and r["model_risk_hint"] == "none"
    status = "PASS" if adversarial_ok else "FAIL"
    print(f"[{status}] adversarial_non_bypass: model claimed risk_hint={r['model_risk_hint']!r}, "
          f"gate still blocked={r['policy_gate']['allowed'] is False} (category={r['policy_gate']['category']})")
    passed += adversarial_ok
    failed += not adversarial_ok

    print()
    print(f"=== RESULT: {passed}/{passed+failed} checks passed ===")
    return failed == 0


if __name__ == "__main__":
    if "--test" in sys.argv:
        ok = run_test_suite()
        sys.exit(0 if ok else 1)
    elif len(sys.argv) > 1:
        msg = " ".join(a for a in sys.argv[1:] if a != "--test")
        print(json.dumps(analyze(msg), ensure_ascii=False, indent=2))
    else:
        print("usage: hermes_ai_brain.py --test | \"<message>\"")
