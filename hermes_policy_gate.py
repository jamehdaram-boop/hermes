#!/usr/bin/env python3
"""hermes_policy_gate - the two absolute, non-bypassable locks for Hermes.

Every current and future Hermes action that goes beyond today's fixed,
already-whitelisted VPS actions (browser/web actions, site
registration, email actions, or any other capability added later) MUST
be classified through this module's `require_gate()` before it is
allowed to run for real. This module has no "approve" function and no
override flag reachable from any automated caller - a match here can
only be resolved by a human giving Hermes a fresh, separate, explicit
instruction outside of whatever prompt/site/script triggered the
match. Nothing in Hermes may skip this: not a system prompt, not
website content, not a config value, not a Telegram command.

Two absolute lock categories, matched by keyword/pattern against the
action name plus a short human-readable description every caller must
supply:

  DESTRUCTIVE - deleting, erasing, wiping, resetting, or otherwise
  irreversibly destroying a file, folder, email, account, site data,
  Docker resource/container/volume, or similar.

  FINANCIAL - a purchase, payment, paid order, paid subscription, paid
  renewal, funds transfer, checkout, or any other action that moves
  money or creates a financial commitment.

A match means the action is blocked before it ever runs - `run_gated()`
never calls the supplied function at all when the gate blocks. The
caller gets back a `GateDecision` (or, via `run_gated`, a blocked
`TaskResult`) describing exactly why, which the caller can then surface
to the user (see `format_blocked_notice()` for the Telegram-ready
Persian/English/Japanese text) and log as a pending approval - see
`hermes_tasks.py` for the logging side of this.
"""
import re

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_i18n import t, DEFAULT_LOCALE

# Bilingual (English + Persian) keyword/phrase patterns. Matched
# case-insensitively as substrings against "<action_name> <description>
# <args values>" - deliberately broad/over-inclusive: a false positive
# here just means Hermes asks first, which is the safe direction to
# fail in. A future caller that legitimately needs a word like "delete"
# in a NON-destructive sense (e.g. "check whether the delete button is
# visible") should rephrase its description rather than expect the gate
# to guess intent.
DESTRUCTIVE_PATTERNS = [
    "delete", "remove", "erase", "destroy", "purge", "wipe", "drop table",
    "truncate", "format disk", "reset", "uninstall", "unregister",
    "deactivate account", "close account", "rm -rf", "docker rm",
    "docker volume rm", "docker system prune", "factory reset",
    "حذف", "پاک", "از بین بردن", "نابود", "ریست",
    "بازنشانی", "غیرفعال‌سازی حساب", "بستن حساب",
]

FINANCIAL_PATTERNS = [
    "pay", "payment", "purchase", "buy", "order now", "checkout",
    "subscribe", "subscription", "renew", "renewal", "invoice",
    "transfer funds", "wire transfer", "charge card", "billing",
    "credit card", "add funds", "top up", "pay now",
    "پرداخت", "خرید", "سفارش پولی", "تمدید", "اشتراک", "انتقال وجه",
    "کارت اعتباری", "شارژ حساب", "صورت‌حساب",
]


class PolicyGateBlocked(Exception):
    """Raised by require_gate() when an action is blocked. Callers that
    want a structured result instead of an exception should use
    run_gated() rather than catching this directly."""

    def __init__(self, category, matched_pattern, action_name, description):
        self.category = category
        self.matched_pattern = matched_pattern
        self.action_name = action_name
        self.description = description
        super().__init__(
            f"blocked by policy gate: category={category} action={action_name!r} "
            f"matched={matched_pattern!r}"
        )


class GateDecision:
    __slots__ = ("allowed", "category", "matched_pattern")

    def __init__(self, allowed, category=None, matched_pattern=None):
        self.allowed = allowed
        self.category = category
        self.matched_pattern = matched_pattern


def _combined_text(action_name, description, args=None):
    parts = [str(action_name or ""), str(description or "")]
    if args:
        parts.extend(str(v) for v in args.values())
    return " ".join(parts).lower()


def evaluate(action_name, description, args=None):
    """Read-only classification - never raises, never logs. Prefer
    require_gate()/run_gated() in real call sites; this is exposed for
    tests and for callers that want to pre-check without committing."""
    text = _combined_text(action_name, description, args)
    for pattern in DESTRUCTIVE_PATTERNS:
        if pattern in text:
            return GateDecision(False, "DESTRUCTIVE", pattern)
    for pattern in FINANCIAL_PATTERNS:
        if pattern in text:
            return GateDecision(False, "FINANCIAL", pattern)
    return GateDecision(True)


def require_gate(action_name, description, args=None):
    """Raises PolicyGateBlocked if the action is classified as
    destructive or financial. Returns None (silently) if allowed."""
    decision = evaluate(action_name, description, args)
    if not decision.allowed:
        raise PolicyGateBlocked(decision.category, decision.matched_pattern, action_name, description)


def format_blocked_notice(action_name, description, category, locale=DEFAULT_LOCALE,
                           approval_id=None, amount=None, where=None, why=None):
    """Telegram-ready explanation of a blocked action. For FINANCIAL,
    includes what/amount/where/why per the required pre-payment
    disclosure; for DESTRUCTIVE, a simpler what/description notice."""
    cat_label = t("gate_category_financial" if category == "FINANCIAL" else "gate_category_destructive", locale)
    lines = [
        t("gate_blocked_title", locale),
        f"{t('gate_label_action', locale)}: {action_name}",
        f"{t('gate_label_description', locale)}: {description}",
        f"{cat_label}",
    ]
    if category == "FINANCIAL":
        if amount:
            lines.append(f"{t('gate_label_amount', locale)}: {amount}")
        if where:
            lines.append(f"{t('gate_label_where', locale)}: {where}")
        if why:
            lines.append(f"{t('gate_label_why', locale)}: {why}")
    if approval_id:
        lines.append(f"{t('gate_label_approval_id', locale)}: {approval_id}")
    lines.append(t("gate_footer", locale))
    return "\n".join(lines)
