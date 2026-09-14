#!/usr/bin/env python3
"""hermes_i18n - centralized user-facing text for Hermes.

Persian (fa) is the default locale for every user-facing message Hermes
sends (Telegram alerts, daily reports). English and Japanese catalogs
exist so a caller can explicitly request either one and so this module
is easy to extend later (new locale = one new dict entry).

This module only holds *display* text - labels, titles, short phrases.
Technical values (timestamps, event_id, percentages, container names,
commit hashes, JSON keys, severity codes used internally for state
tracking) are never stored here and must be passed through unchanged
by the caller; only `severity_label()` maps a severity code to a
human-readable word for display, the underlying code itself is never
translated in state/log data.

hermes.py's own API error strings (e.g. "unauthorized", "target not
allowed") are NOT translated here on purpose - they are part of the
Result Contract asserted on by hermes_security_test.py and consumed by
n8n. Presentation-layer translation of those happens where the reply is
actually built for a human (the n8n Telegram Bridge's Format Reply
nodes), not in this module.
"""

DEFAULT_LOCALE = "fa"

SEVERITY_LABELS = {
    "fa": {"WARNING": "هشدار", "CRITICAL": "بحرانی"},
    "en": {"WARNING": "WARNING", "CRITICAL": "CRITICAL"},
    "ja": {"WARNING": "警告", "CRITICAL": "重大"},
}

MESSAGES = {
    "fa": {
        "monitor_alert_title": "\U0001F6A8 هشدار Hermes Monitor",
        "monitor_test_title": "\U0001F9EA هشدار آزمایشی Hermes Monitor (TEST - واقعی نیست)",
        "monitor_recovery_title": "✅ رفع مشکل Hermes Monitor",
        "monitor_test_recovery_title": "\U0001F9EA رفع مشکل آزمایشی Hermes Monitor (TEST)",
        "label_type": "نوع",
        "label_target": "هدف",
        "label_severity": "شدت",
        "label_observed": "مقدار مشاهده‌شده",
        "label_time": "زمان",
        "label_event_id": "شناسه رویداد",
        "hermes_unreachable": "Hermes در دسترس نیست",
        "report_title": "\U0001F4CB گزارش روزانه Hermes",
        "report_uptime": "مدت روشن بودن",
        "report_load": "بار پردازنده (Load)",
        "report_ram": "RAM مصرف‌شده",
        "report_disk": "دیسک مصرف‌شده",
        "report_containers_healthy": "کانتینرهای سالم",
        "report_containers_problem": "کانتینرهای دارای مشکل",
        "report_actions": "عملیات Hermes (۲۴ ساعت اخیر)",
        "report_actions_executed": "اجراشده",
        "report_actions_failed": "ناموفق/ردشده",
        "report_monitor_alerts": "هشدارهای Monitor (۲۴ ساعت اخیر)",
        "report_guard_alerts": "هشدارهای امنیتی Guard AI (۲۴ ساعت اخیر)",
        "report_overall": "وضعیت کلی",
        "overall_ok": "سالم",
        "overall_attention": "نیازمند توجه",
        "report_failed_title": "\U0001F6A8 تولید گزارش روزانه Hermes ناموفق بود",
        "report_failed_reason": "دلیل",
        "day": "روز",
        "hour": "ساعت",
    },
    "en": {
        "monitor_alert_title": "\U0001F6A8 Hermes Monitor Alert",
        "monitor_test_title": "\U0001F9EA Hermes Monitor TEST Alert (not real)",
        "monitor_recovery_title": "✅ Hermes Monitor Recovery",
        "monitor_test_recovery_title": "\U0001F9EA Hermes Monitor TEST Recovery",
        "label_type": "type",
        "label_target": "target",
        "label_severity": "severity",
        "label_observed": "observed",
        "label_time": "time",
        "label_event_id": "event_id",
        "hermes_unreachable": "Hermes is unreachable",
        "report_title": "\U0001F4CB Hermes Daily Report",
        "report_uptime": "uptime",
        "report_load": "load avg",
        "report_ram": "RAM used",
        "report_disk": "disk used",
        "report_containers_healthy": "containers healthy",
        "report_containers_problem": "containers with problems",
        "report_actions": "Hermes actions (24h)",
        "report_actions_executed": "executed",
        "report_actions_failed": "failed/rejected",
        "report_monitor_alerts": "Hermes monitor alerts (24h)",
        "report_guard_alerts": "Guard AI security alerts (24h)",
        "report_overall": "overall status",
        "overall_ok": "OK",
        "overall_attention": "ATTENTION",
        "report_failed_title": "\U0001F6A8 Hermes Daily Report FAILED to generate",
        "report_failed_reason": "reason",
        "day": "d",
        "hour": "h",
    },
    "ja": {
        "monitor_alert_title": "\U0001F6A8 Hermes モニターアラート",
        "monitor_test_title": "\U0001F9EA Hermes モニター テストアラート(実際の問題ではありません)",
        "monitor_recovery_title": "✅ Hermes モニター復旧",
        "monitor_test_recovery_title": "\U0001F9EA Hermes モニター テスト復旧",
        "label_type": "種類",
        "label_target": "対象",
        "label_severity": "重大度",
        "label_observed": "観測値",
        "label_time": "時刻",
        "label_event_id": "イベントID",
        "hermes_unreachable": "Hermes に到達できません",
        "report_title": "\U0001F4CB Hermes 日次レポート",
        "report_uptime": "稼働時間",
        "report_load": "負荷平均",
        "report_ram": "RAM使用率",
        "report_disk": "ディスク使用率",
        "report_containers_healthy": "正常なコンテナ",
        "report_containers_problem": "問題のあるコンテナ",
        "report_actions": "Hermesの操作(過去24時間)",
        "report_actions_executed": "実行済み",
        "report_actions_failed": "失敗/拒否",
        "report_monitor_alerts": "モニターアラート(過去24時間)",
        "report_guard_alerts": "Guard AIセキュリティアラート(過去24時間)",
        "report_overall": "総合状態",
        "overall_ok": "正常",
        "overall_attention": "要確認",
        "report_failed_title": "\U0001F6A8 Hermes 日次レポートの生成に失敗しました",
        "report_failed_reason": "理由",
        "day": "日",
        "hour": "時間",
    },
}


def t(key, locale=DEFAULT_LOCALE):
    cat = MESSAGES.get(locale, MESSAGES[DEFAULT_LOCALE])
    return cat.get(key, MESSAGES[DEFAULT_LOCALE].get(key, key))


def severity_label(severity, locale=DEFAULT_LOCALE):
    table = SEVERITY_LABELS.get(locale, SEVERITY_LABELS[DEFAULT_LOCALE])
    return table.get(severity, severity)
