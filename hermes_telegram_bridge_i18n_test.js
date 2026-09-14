// Standalone offline test of the PREPARED (not yet deployed) n8n Telegram
// Bridge logic. This does NOT touch the running n8n workflow, DB, or API -
// it just re-runs the exact same JS as plain functions with fake input, to
// verify the language-switch and Persian-default logic before deployment.

const ALLOWED_CHAT_IDS = ['7461341721'];
const RATE_LIMIT_MAX = 10;
const RATE_LIMIT_WINDOW_MS = 60000;
const DEFAULT_LANG = 'fa';
const LANG_HINTS = {
  en: 'en', english: 'en',
  ja: 'ja', jp: 'ja', japanese: 'ja',
  fa: 'fa', farsi: 'fa', persian: 'fa'
};

function securityGate(text, chatId, rateLimitState) {
  const allowed = ALLOWED_CHAT_IDS.includes(chatId);
  function reject(reason, lang) {
    return { ok: false, reason, chat_id: allowed ? chatId : null, lang: lang || DEFAULT_LANG };
  }
  if (!chatId || !allowed) return reject('chat_not_allowed', DEFAULT_LANG);

  const now = Date.now();
  const rl = rateLimitState.value || { windowStart: now, count: 0 };
  if (now - rl.windowStart > RATE_LIMIT_WINDOW_MS) { rl.windowStart = now; rl.count = 0; }
  rl.count += 1;
  rateLimitState.value = rl;
  if (rl.count > RATE_LIMIT_MAX) return reject('rate_limited', DEFAULT_LANG);

  const stripped = text.indexOf('/') === 0 ? text.slice(1) : text;
  let parts = stripped.trim().split(' ').filter(function (p) { return p.length > 0; });

  let lang = DEFAULT_LANG;
  if (parts.length > 0) {
    const lastHint = LANG_HINTS[parts[parts.length - 1].toLowerCase()];
    if (lastHint) {
      lang = lastHint;
      parts = parts.slice(0, -1);
    }
  }

  const cmd = (parts[0] || '').toLowerCase();
  let action = null, args = {};

  if (cmd === 'status') {
    action = 'system_status';
  } else if (cmd === 'containers') {
    action = 'list_containers';
  } else if (cmd === 'container' && parts[1]) {
    action = 'container_status';
    args = { name: parts[1] };
  } else {
    return reject('unknown_command', lang);
  }

  return { ok: true, chat_id: chatId, action, args, lang };
}

const L = {
  fa: {
    ok: '✅', warn: '⚠️', title: 'Hermes',
    action: 'عملیات', target: 'هدف', duration: 'مدت', auditId: 'شناسه رهگیری (audit_id)', error: 'خطا', none: '—',
    statusTitle: 'وضعیت سیستم', load: 'بار پردازنده (Load)', mem: 'RAM مصرف‌شده', memAvail: 'در دسترس',
    disk: 'دیسک مصرف‌شده', diskFree: 'فضای آزاد', uptime: 'مدت روشن بودن', containersLbl: 'کانتینرها',
    containersTitle: 'لیست کانتینرها', containerTitle: 'وضعیت کانتینر', state: 'وضعیت', health: 'سلامت',
    errUnauthorized: 'احراز هویت ناموفق بود.', errUnknownContainer: 'این کانتینر شناخته‌شده یا مجاز نیست.',
    errTargetNotAllowed: 'این هدف برای این عملیات مجاز نیست.', errGeneric: 'اجرای عملیات با خطا مواجه شد',
    day: 'روز', hour: 'ساعت',
  },
  en: {
    ok: '✅', warn: '⚠️', title: 'Hermes',
    action: 'action', target: 'target', duration: 'duration', auditId: 'audit_id', error: 'error', none: '—',
    statusTitle: 'System status', load: 'Load average', mem: 'RAM used', memAvail: 'available',
    disk: 'Disk used', diskFree: 'free', uptime: 'Uptime', containersLbl: 'Containers',
    containersTitle: 'Container list', containerTitle: 'Container status', state: 'state', health: 'health',
    errUnauthorized: 'Authentication failed.', errUnknownContainer: 'This container is unknown or not whitelisted.',
    errTargetNotAllowed: 'This target is not allowed for this action.', errGeneric: 'The operation failed',
    day: 'd', hour: 'h',
  },
  ja: {
    ok: '✅', warn: '⚠️', title: 'Hermes',
    action: 'アクション', target: '対象', duration: '所要時間', auditId: '監査ID', error: 'エラー', none: '—',
    statusTitle: 'システム状態', load: '負荷平均 (Load)', mem: 'RAM使用率', memAvail: '空き',
    disk: 'ディスク使用率', diskFree: '空き容量', uptime: '稼働時間', containersLbl: 'コンテナ',
    containersTitle: 'コンテナ一覧', containerTitle: 'コンテナ状態', state: '状態', health: 'ヘルス',
    errUnauthorized: '認証に失敗しました。', errUnknownContainer: 'このコンテナは不明または許可されていません。',
    errTargetNotAllowed: 'この対象はこの操作では許可されていません。', errGeneric: '操作が失敗しました',
    day: '日', hour: '時間',
  },
};

function fmtUptime(sec, t) {
  if (typeof sec !== 'number') return String(sec);
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  return d + ' ' + t.day + ' ' + h + ' ' + t.hour;
}
function fmtContainerLine(c) { return '- ' + c.name + ': ' + c.status; }
function errText(err, t) {
  if (err === 'unauthorized') return t.errUnauthorized;
  if (err === 'unknown or non-whitelisted container') return t.errUnknownContainer;
  if (err === 'target not allowed') return t.errTargetNotAllowed;
  return t.errGeneric + ': ' + err;
}

function formatSuccess(r, gateResult) {
  const lang = (gateResult || {}).lang || 'fa';
  const t = L[lang] || L.fa;
  let body;
  if (!r.success) {
    body = t.warn + ' ' + t.title + '\n' + errText(r.error, t) +
      '\n' + t.action + ': ' + (r.action || t.none) +
      '\n' + t.auditId + ': ' + r.audit_id;
  } else if (r.action === 'system_status' && r.result) {
    const s = r.result;
    const load = Array.isArray(s.load_avg) ? s.load_avg.map(function(x){return x.toFixed(2);}).join(', ') : t.none;
    const lines = [
      t.ok + ' Hermes — ' + t.statusTitle,
      t.load + ': ' + load,
      t.mem + ': ' + s.mem_used_pct + '% (' + t.memAvail + ': ' + s.mem_available_mb + ' MB / ' + s.mem_total_mb + ' MB)',
      t.disk + ': ' + s.disk_used_pct + '% (' + t.diskFree + ': ' + s.disk_free_gb + ' GB)',
      t.uptime + ': ' + fmtUptime(s.uptime_seconds, t),
      t.containersLbl + ' (' + (s.containers || []).length + '):',
    ].concat((s.containers || []).map(fmtContainerLine));
    lines.push(t.duration + ': ' + r.duration_ms + 'ms');
    lines.push(t.auditId + ': ' + r.audit_id);
    body = lines.join('\n');
  } else if (r.action === 'list_containers' && Array.isArray(r.result)) {
    const lines = [t.ok + ' Hermes — ' + t.containersTitle + ' (' + r.result.length + ')']
      .concat(r.result.map(fmtContainerLine));
    lines.push(t.duration + ': ' + r.duration_ms + 'ms');
    lines.push(t.auditId + ': ' + r.audit_id);
    body = lines.join('\n');
  } else if (r.action === 'container_status' && r.result) {
    const c = r.result;
    body = [
      t.ok + ' Hermes — ' + t.containerTitle + ': ' + c.name,
      t.state + ': ' + c.state,
      t.health + ': ' + c.health,
      t.duration + ': ' + r.duration_ms + 'ms',
      t.auditId + ': ' + r.audit_id,
    ].join('\n');
  } else {
    body = t.ok + ' ' + t.title +
      '\n' + t.action + ': ' + r.action +
      '\n' + t.target + ': ' + (r.target || t.none) +
      '\n' + t.duration + ': ' + r.duration_ms + 'ms' +
      '\n' + t.auditId + ': ' + r.audit_id +
      '\n\n' + JSON.stringify(r.result).slice(0, 500);
  }
  return body;
}

const M = {
  fa: { rate_limited: '⛔ تعداد درخواست‌ها زیاد است، کمی صبر کنید.',
        unknown_command: '❓ دستور ناشناخته است. دستورهای مجاز: /status، /containers، /container <name>',
        fallback: '⛔ ' },
  en: { rate_limited: '⛔ Too many requests, please wait.',
        unknown_command: '❓ Unknown command. Allowed: /status /containers /container <name>',
        fallback: '⛔ ' },
  ja: { rate_limited: '⛔ リクエストが多すぎます。しばらくお待ちください。',
        unknown_command: '❓ 不明なコマンドです。使用可能: /status /containers /container <name>',
        fallback: '⛔ ' },
};
function formatReject(r) {
  if (!r.chat_id) return null;
  const lang = r.lang || 'fa';
  const m = M[lang] || M.fa;
  return m[r.reason] || (m.fallback + r.reason);
}

// ---- Test cases ----
console.log('=== TEST 1: plain /status, no language specified -> must be Persian ===');
let gate = securityGate('/status', '7461341721', {});
console.log('gate result:', JSON.stringify(gate));
let hermesResult = {
  success: true, action: 'system_status', target: null, duration_ms: 56.4,
  result: {
    load_avg: [0.08, 0.04, 0.01], mem_used_pct: 35.0, mem_total_mb: 3904.1, mem_available_mb: 2539.3,
    disk_used_pct: 7.2, disk_free_gb: 133.9, uptime_seconds: 2936651.28,
    containers: [{name:'n8n', status:'Up 18 hours', image:'x'}, {name:'apiserver', status:'Up 2 weeks (healthy)', image:'x'}],
  },
  error: null, audit_id: '5b5d644476984302a6c292f7a58b81cc',
};
console.log(formatSuccess(hermesResult, gate));

console.log('\n=== TEST 2: /status en -> must switch to English for this reply only ===');
gate = securityGate('/status en', '7461341721', {});
console.log('gate result:', JSON.stringify(gate));
console.log(formatSuccess(hermesResult, gate));

console.log('\n=== TEST 3: unknown command, no language -> Persian help text ===');
gate = securityGate('/foobar', '7461341721', {});
console.log('gate result:', JSON.stringify(gate));
console.log(formatReject(gate));

console.log('\n=== TEST 4: unknown command with japanese hint -> Japanese help text ===');
gate = securityGate('/foobar ja', '7461341721', {});
console.log('gate result:', JSON.stringify(gate));
console.log(formatReject(gate));

console.log('\n=== TEST 5: /container n8n -> Persian container_status ===');
gate = securityGate('/container n8n', '7461341721', {});
console.log('gate result:', JSON.stringify(gate));
let containerResult = { success: true, action: 'container_status', target: 'n8n', duration_ms: 12.3,
  result: { name: 'n8n', state: 'running', health: 'n/a' }, error: null, audit_id: 'abc123' };
console.log(formatSuccess(containerResult, gate));

console.log('\n=== TEST 6: error path (unauthorized) -> Persian error sentence, technical fields intact ===');
let errResult = { success: false, action: null, target: null, duration_ms: 0.0, result: null, error: 'unauthorized', audit_id: 'err-audit-1' };
console.log(formatSuccess(errResult, { lang: 'fa' }));

console.log('\n=== TEST 7: chat not allowed -> must produce NO message (existing security behavior preserved) ===');
gate = securityGate('/status', '999999999', {});
console.log('gate result:', JSON.stringify(gate));
console.log('reject formatted ->', formatReject(gate));

console.log('\n=== TEST 8: rate limiting still works (11th message in window rejected) ===');
const rl = {};
let lastGate;
for (let i = 0; i < 11; i++) {
  lastGate = securityGate('/status', '7461341721', rl);
}
console.log('11th call result:', JSON.stringify(lastGate));
