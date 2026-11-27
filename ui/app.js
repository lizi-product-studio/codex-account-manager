'use strict';

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

let state = null;
let settings = {
  close_to_tray: true,
  autostart: false,
  launch_after_switch: true,
  confirm_before_switch: true,
  notify_enabled: true,
  auto_check_update: true,
  sort_mode: 'custom',
  privacy_mode: false,
  theme: 'auto',
  break_enabled: true,
  break_minutes: 10,
  break_goal: 8,
};
let busy = false;
let sortMode = localStorage.getItem('sortMode') || 'custom';   // custom | remaining
let privacyMode = false;
let themeMode = 'auto';                                        // auto | light | dark
let lastRenderSig = '';
let waitTimer = null;
let dismissedUpdateSha = null;

const PLAN_NAMES = {
  free: 'Free', plus: 'Plus', pro: 'Pro', prolite: 'Pro Lite', team: 'Team',
  business: 'Business', enterprise: 'Enterprise', edu: 'Edu', 'api-key': 'API Key',
};
const planName = (p) => (p ? PLAN_NAMES[p] || p : '未知套餐');

// ------------------------------------------------------------------ 工具函数
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function maskEmail(email) {
  const s = String(email || '');
  if (!privacyMode || !s) return s;
  const at = s.indexOf('@');
  if (at <= 0) return s.slice(0, 2) + '***';
  const local = s.slice(0, at);
  const domain = s.slice(at + 1);
  const keep = local.length <= 2 ? local.slice(0, 1) : local.slice(0, 2);
  return `${keep}***@${domain}`;
}
async function copyEmail(rawEmail) {
  if (!rawEmail) return;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(rawEmail);
    } else {
      const ta = document.createElement('textarea');
      ta.value = rawEmail;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    toast(`已复制邮箱：${maskEmail(rawEmail)}`);
  } catch (e) {
    toast('复制失败', true);
  }
}
function applyTheme() {
  const sysDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const isDark = themeMode === 'dark' || (themeMode === 'auto' && sysDark);
  document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
  const btn = $('btnTheme');
  if (btn) {
    const label = themeMode === 'auto' ? '◐ 自动' : themeMode === 'dark' ? '☾ 深色' : '☀ 浅色';
    btn.textContent = label;
  }
}
function updatePrivacyBtn() {
  const btn = $('btnPrivacy');
  if (!btn) return;
  btn.classList.toggle('on', !!privacyMode);
  btn.textContent = privacyMode ? '👁 已打码' : '👁 隐私';
}
const nowSec = () => Date.now() / 1000;
const pad = (n) => String(n).padStart(2, '0');

function fmtDate(ts, withTime = true) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  const s = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  return withTime ? `${s} ${pad(d.getHours())}:${pad(d.getMinutes())}` : s;
}
function fmtShort(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (d.toDateString() === today.toDateString()) return `今天 ${hm}`;
  const t = new Date(today); t.setDate(t.getDate() + 1);
  if (d.toDateString() === t.toDateString()) return `明天 ${hm}`;
  return `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}
function fmtDuration(sec) {
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (d > 0) return `${d}天${h}小时`;
  if (h > 0) return `${h}小时${m}分`;
  if (m > 0) return `${m}分${pad(s)}秒`;
  return `${s}秒`;
}
function fmtAgo(ts) {
  if (!ts) return '-';
  const s = nowSec() - ts;
  if (s < 60) return '刚刚';
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ 提示 / 弹窗
let toastTimer = null;
function toast(msg, isErr = false) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast' + (isErr ? ' err' : '');
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isErr ? 5000 : 2800);
}

/** 通用弹窗。buttons: [{id, text, kind}]，返回 Promise<{id, value}>；Esc/点遮罩 返回 {id:'cancel'} */
function dialog({ title, body = '', input = null, buttons }) {
  return new Promise((resolve) => {
    $('mTitle').textContent = title;
    $('mBody').textContent = body;
    $('mBody').hidden = !body;
    const inp = $('mInput');
    inp.hidden = input === null;
    if (input !== null) { inp.value = input.value || ''; inp.placeholder = input.placeholder || ''; }
    const box = $('mActions');
    box.innerHTML = '';
    const mask = $('modal');
    const close = (id) => {
      mask.hidden = true;
      mask.onclick = null;
      document.removeEventListener('keydown', onKey, true);
      resolve({ id, value: inp.value.trim() });
    };
    mask.onclick = (e) => { if (e.target === mask) close('cancel'); };
    buttons.forEach((b) => {
      const el = document.createElement('button');
      el.className = 'btn ' + (b.kind || '');
      el.textContent = b.text;
      el.onclick = () => close(b.id);
      box.appendChild(el);
    });
    const primary = buttons.find((b) => /primary|solid/.test(b.kind || ''));
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); close('cancel'); }
      if (e.key === 'Enter' && primary) { e.preventDefault(); close(primary.id); }
    }
    document.addEventListener('keydown', onKey, true);
    mask.hidden = false;
    setTimeout(() => (input !== null ? (inp.focus(), inp.select()) : box.lastChild && box.lastChild.focus()), 30);
  });
}
const confirmBox = async (title, body, okText = '确定', kind = 'primary') =>
  (await dialog({ title, body, buttons: [{ id: 'cancel', text: '取消' }, { id: 'ok', text: okText, kind }] })).id === 'ok';
const promptBox = async (title, body, value = '', placeholder = '') => {
  const r = await dialog({ title, body, input: { value, placeholder }, buttons: [{ id: 'cancel', text: '取消' }, { id: 'ok', text: '确定', kind: 'primary' }] });
  return r.id === 'ok' ? r.value : null;
};

// 忙碌遮罩 + 分步进度
function setBusy(on, text = '处理中…', steps = null) {
  busy = on;
  $('busyText').textContent = text;
  $('busySteps').innerHTML = '';
  if (steps) setSteps(steps, 0);
  $('busy').hidden = !on;
}
function setSteps(steps, current) {
  $('busySteps').innerHTML = steps.map((s, i) =>
    i < current ? `<div class="done">✓ ${esc(s)}</div>`
      : i === current ? `<div class="doing">› ${esc(s)}…</div>`
        : `<div>· ${esc(s)}</div>`).join('');
}

async function call(name, ...args) {
  const r = await api()[name](...args);
  if (r && r.ok === false && r.error) throw new Error(r.error);
  return r;
}

// ------------------------------------------------------------------ 等待新登录（自动唤起保存）
function stopWaitLogin() {
  if (waitTimer) { clearInterval(waitTimer); waitTimer = null; }
  $('waitBanner').hidden = true;
}

function startWaitLogin() {
  stopWaitLogin();
  const wb = $('waitBanner');
  wb.innerHTML = `<span class="pulse"></span><span>正在等待你在 Codex 中登录新账号… 登录完成后会自动弹出保存窗口。</span>
    <span class="spacer"></span><button class="btn sm" id="btnCancelWait">取消等待</button>`;
  wb.hidden = false;
  $('btnCancelWait').onclick = stopWaitLogin;
  waitTimer = setInterval(async () => {
    if (busy || !$('modal').hidden) return;
    await refresh();
    const c = state && state.current;
    if (c && c.valid && !c.saved_id) {
      stopWaitLogin();
      try { await call('bring_to_front'); } catch (e) { /* ignore */ }
      await sleep(180);
      if ($('modal').hidden) saveCurrent();
    }
  }, 2000);
}

// ------------------------------------------------------------------ 数据
async function refresh(silent = true, forceRender = false, syncLive = false) {
  if (busy) return;
  const manualSync = !silent && syncLive;
  const btn = manualSync ? $('btnRefresh') : null;
  let liveRes = null;
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⟳ 刷新中…';
  }
  try {
    if (manualSync) {
      try { liveRes = await api().refresh_live(true); } catch (e) { /* 离线时自动回退本地日志 */ }
    } else if (syncLive) {
      const curFresh = state && state.current && state.current.status && state.current.status.freshness;
      const needForce = !!(curFresh && curFresh !== 'live');
      api().refresh_live(needForce).then((lr) => {
        if (lr && lr.updated && !busy) refresh(true, true, false);
      }).catch(() => {});
    }
    const r = await call('get_state');
    state = r.state;
    render(forceRender);
    if (!silent) {
      if (syncLive && liveRes) {
        if (liveRes.ok && liveRes.updated) toast('实时额度已同步');
        else if (liveRes.ok) toast('已是最新实时额度');
        else toast('已刷新本地记录（' + (liveRes.error || 'Codex 实时同步不可用') + '）');
      } else if (syncLive) {
        toast('已刷新本地记录（Codex 实时同步不可用）');
      } else {
        toast('已刷新');
      }
    }
  } catch (e) {
    toast('读取失败：' + e.message, true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '⟳ 刷新';
    }
  }
}
async function loadSettings() {
  try {
    settings = (await call('get_settings')).settings;
  } catch (e) { /* 用默认值 */ }
  $('chkLaunch').checked = !!settings.launch_after_switch;
  if (settings.sort_mode === 'remaining' || settings.sort_mode === 'custom') {
    sortMode = settings.sort_mode;
    localStorage.setItem('sortMode', sortMode);
  } else {
    sortMode = localStorage.getItem('sortMode') || 'custom';
  }
  privacyMode = !!settings.privacy_mode;
  themeMode = ['auto', 'light', 'dark'].includes(settings.theme) ? settings.theme : 'auto';
  updatePrivacyBtn();
  applyTheme();
}
async function setSetting(key, value) {
  try {
    settings = (await call('set_setting', key, value)).settings;
  } catch (e) { toast('设置失败：' + e.message, true); }
  $('chkLaunch').checked = !!settings.launch_after_switch;
}

// ------------------------------------------------------------------ 状态文案（总览/卡片共用）
const LEVEL_TEXT = { ok: '充足', low: '偏紧', out: '已用尽', none: '无数据' };

function freshTag(st) {
  if (!st || st.freshness === 'none') return '<span class="fresh none">—</span>';
  if (st.freshness === 'live') return `<span class="fresh live" title="数据更新于 ${fmtDate(st.updated)}">实时</span>`;
  if (st.freshness === 'reset') return `<span class="fresh reset" title="上次数据 ${fmtDate(st.updated)}，所有额度窗口都已过重置时间">应已重置</span>`;
  return `<span class="fresh stale" title="上次已知数据：${fmtDate(st.updated)}" data-ago="${st.updated}">${fmtAgo(st.updated)}</span>`;
}

function resetText(st) {
  if (!st || st.level === 'none') return '<span>暂无记录</span>';
  if (st.freshness === 'reset') {
    return st.reset_at ? `<span>已于 ${fmtShort(st.reset_at)} 重置</span>` : '<span>已过重置时间</span>';
  }
  if (st.level === 'out' && st.recover_at) {
    return `<b data-reset="${st.recover_at}">${fmtDuration(st.recover_at - nowSec())}</b> 后恢复 · ${fmtShort(st.recover_at)}`;
  }
  if (st.next_reset) {
    return `${esc(st.bottleneck || '')}额度 · <b data-reset="${st.next_reset}">${fmtDuration(st.next_reset - nowSec())}</b> 后重置`;
  }
  return '<span>重置时间未知</span>';
}

function effectiveRemaining(st) {
  if (!st || st.level === 'none') return -1;
  if (st.freshness === 'reset') return 100;
  return typeof st.remaining === 'number' ? st.remaining : -1;
}

function pickRecommendation() {
  if (!state || !state.accounts || !state.accounts.length) return null;
  const curAcc = state.accounts.find((a) => a.is_current);
  const curSt = (curAcc && curAcc.status) || (state.current && state.current.status) || {};
  const curRem = effectiveRemaining(curSt);

  // 1. 当前号已用尽或剩余 <= 15%，推荐切到最优备用号
  if (curRem >= 0 && (curRem <= 15 || curSt.level === 'out')) {
    const cands = state.accounts
      .filter((a) => a.valid && !a.is_current && effectiveRemaining(a.status) >= 30 && effectiveRemaining(a.status) > curRem + 15)
      .slice();
    if (cands.length) {
      cands.sort((a, b) => {
        const ra = effectiveRemaining(a.status);
        const rb = effectiveRemaining(b.status);
        if (Math.abs(rb - ra) > 5) return rb - ra;
        const na = (a.status && a.status.next_reset) || Infinity;
        const nb = (b.status && b.status.next_reset) || Infinity;
        return na - nb;
      });
      const best = cands[0];
      const bSt = best.status || {};
      const bDesc = bSt.freshness === 'reset' ? '已重置 · 预计 100%' : `剩余 ${effectiveRemaining(bSt)}%`;
      return {
        kind: 'switch',
        curName: (curAcc && curAcc.name) || (state.current && maskEmail(state.current.email)) || '当前账号',
        curRem,
        curOut: curSt.level === 'out' || curRem <= 0,
        target: best,
        targetDesc: bDesc,
      };
    }
  }

  // 2. 所有有效账号都已用尽，汇总最早恢复时间
  const validAccs = state.accounts.filter((a) => a.valid && a.status && a.status.level !== 'none');
  if (validAccs.length > 0 && validAccs.every((a) => a.status.level === 'out' && a.status.freshness !== 'reset')) {
    const withRec = validAccs.filter((a) => a.status.recover_at && a.status.recover_at > nowSec());
    if (withRec.length) {
      withRec.sort((a, b) => a.status.recover_at - b.status.recover_at);
      return { kind: 'all_out', earliest: withRec[0], recoverAt: withRec[0].status.recover_at };
    }
  }
  return null;
}

// ------------------------------------------------------------------ 渲染：排序（当前使用永远优先排最前）
function orderedAccounts() {
  const accs = state.accounts.slice();
  accs.sort((a, b) => (b.is_current ? 1 : 0) - (a.is_current ? 1 : 0));
  return accs;
}

// ------------------------------------------------------------------ 渲染：总览
function overviewRows() {
  const rows = orderedAccounts().map((a) => ({ ...a, saved: true }));
  const c = state.current;
  if (c.valid && !c.saved_id) {
    rows.unshift({ id: null, name: c.email.split('@')[0], email: c.email, is_current: true, saved: false, valid: true, status: c.status });
  }
  if (sortMode === 'remaining') {
    const score = (r) => (r.status && r.status.level !== 'none' ? (r.status.freshness === 'reset' ? 100 : r.status.remaining) : -1);
    rows.sort((x, y) => score(y) - score(x));
  }
  return rows;
}

function renderOverview() {
  const rows = overviewRows();
  const el = $('overview');
  const head = `<div class="ov-head">
      <span class="ov-title">额度总览</span>
      <div class="seg">
        <button data-sort="custom" class="${sortMode === 'custom' ? 'on' : ''}">自定义顺序</button>
        <button data-sort="remaining" class="${sortMode === 'remaining' ? 'on' : ''}">剩余最多</button>
      </div>
      <span class="spacer"></span>
      <div class="legend"><span><i style="background:var(--green)"></i>充足</span><span><i style="background:var(--amber)"></i>偏紧</span>
        <span><i style="background:var(--red)"></i>用尽</span><span><i style="border:2px solid var(--grey);width:6px;height:6px"></i>无数据</span></div>
    </div>`;
  if (!rows.length) {
    el.innerHTML = head + '<div class="muted" style="padding:14px 6px">当前未登录，也还没有保存账号。请先在 Codex 中登录。</div>';
    return;
  }
  el.innerHTML = head + rows.map((r) => {
    const st = r.status || { level: 'none' };
    const known = st.level !== 'none';
    const rem = known ? (st.freshness === 'reset' ? 100 : st.remaining) : null;
    const stale = st.freshness === 'stale';
    const isOut = known && st.level === 'out';
    const fillCls = rem !== null && rem <= 10 ? 'red' : st.level === 'low' ? 'amber' : '';
    const bar = known
      ? (st.freshness === 'reset' ? '<div class="bar reset"></div>'
        : isOut ? '<div class="bar out"></div>'
          : `<div class="bar"><div class="fill ${fillCls}" style="width:${rem}%"></div></div>`)
      : '<div class="bar unknown"></div>';
    const tag = r.is_current ? `<span class="tag ${r.saved ? 'ok' : 'warn'}">${r.saved ? '当前' : '当前·未保存'}</span>` : '';
    const rawPlan = r.plan || (r.usage && r.usage.plan);
    const planTag = rawPlan ? `<span class="tag plan-sm">${esc(planName(rawPlan))}</span>` : '';
    let act;
    if (!r.saved) act = '<button class="btn sm primary" data-act="save">保存</button>';
    else if (r.is_current) act = '<span class="using">● 使用中</span>';
    else act = `<button class="btn sm" data-act="switch" ${r.valid ? '' : 'disabled'}>切换</button>`;
    const remText = !known ? '—' : st.freshness === 'reset' ? '≈100%' : isOut ? '已用尽' : `${rem}%`;
    return `<div class="ov-row ${r.is_current ? 'current' : ''} ${stale ? 'stale' : ''}" data-id="${esc(r.id || '')}">
      <span class="dot ${known ? st.level : 'none'}" title="${LEVEL_TEXT[st.level] || ''}"></span>
      <span class="ov-name" title="${esc(maskEmail(r.email))}">${esc(r.name)}${planTag}${tag}</span>
      ${bar}
      <span class="ov-rem ${known ? (isOut ? 'out' : '') : 'none'}">${remText}</span>
      <span class="ov-reset">${resetText(st)}</span>
      ${freshTag(st)}
      <span class="ov-act">${act}</span>
    </div>`;
  }).join('');
}

// ------------------------------------------------------------------ 渲染：卡片
function renderWindow(w, stale, forcedOut = false) {
  const reset = w.resets_at;
  if (reset && reset <= nowSec()) {
    return `<div class="win">
      <div class="win-top"><span class="win-label">${esc(w.label)}额度</span>
        <span class="win-pct">应已重置<span class="rest">预计剩余 100%</span></span></div>
      <div class="bar reset"></div>
      <div class="win-foot">上次记录已用 ${w.used.toFixed(0)}%，已于 ${fmtShort(reset)} 重置</div></div>`;
  }
  const used = Math.min(100, Math.max(0, w.used));
  const left = forcedOut ? 0 : 100 - used;
  const isOut = forcedOut || left <= 0.5;
  const cls = left <= 10 ? 'red' : left < 30 ? 'amber' : '';
  return `<div class="win">
    <div class="win-top"><span class="win-label">${esc(w.label)}额度</span>
      <span class="win-pct ${isOut ? 'out' : ''}">${isOut ? '已用尽' : `剩余 ${left.toFixed(0)}%`}<span class="rest">${isOut ? '剩余 0%' : `已用 ${used.toFixed(0)}%`}${stale ? '（旧数据）' : ''}</span></span></div>
    ${isOut ? '<div class="bar out"></div>' : `<div class="bar"><div class="fill ${cls}" style="width:${left}%"></div></div>`}
    <div class="win-foot">${reset
      ? `<b data-reset="${reset}">${fmtDuration(reset - nowSec())}</b> 后重置 · ${fmtShort(reset)}`
      : '重置时间未知'}</div></div>`;
}

function renderUsage(usage, isCurrent, stale) {
  if (!usage || !usage.limits.length) {
    return '<div class="no-usage">暂无额度记录</div>';
  }
  const multi = usage.limits.length > 1;
  return usage.limits.map((l) => {
    const head = multi ? `<div class="limit-title">${esc(l.id)}</div>` : '';
    const c = l.credits || {};
    const credits = c.unlimited ? '<div class="credits">Credits：无限</div>'
      : (c.has ? `<div class="credits">Credits 余额：${esc(c.balance)}</div>` : '');
    let bottleneckWin = null;
    if (l.reached) {
      const active = l.windows.filter((w) => !(w.resets_at && w.resets_at <= nowSec()));
      if (active.length) {
        bottleneckWin = active.reduce((a, b) => (b.used > a.used ? b : a), active[0]);
      }
    }
    return head + l.windows.map((w) => renderWindow(w, stale, w === bottleneckWin)).join('') + credits;
  }).join('');
}

function renderCard(a, rawIdx, total) {
  const usage = a.usage;
  const st = a.status || {};
  const stale = st.freshness === 'stale';
  const subLeft = a.sub_until ? a.sub_until - nowSec() : null;
  let subHtml = '';
  if (a.sub_until) {
    if (subLeft > 0) {
      const subWarn = subLeft <= 3 * 86400;
      subHtml = `<span class="${subWarn ? 'warn' : ''}">订阅至 ${fmtDate(a.sub_until, false)}${subWarn ? '（快到期）' : ''}</span>`;
    } else if (a.sub_state === 'stale') {
      subHtml = `<span title="该日期来自上次刷新凭证时的本地记录；若已续费，切换后会自动刷新">上次记录订阅至 ${fmtDate(a.sub_until, false)}（待刷新）</span>`;
    }
  }
  const meta = [
    subHtml,
    a.last_refresh ? `<span title="${fmtDate(a.last_refresh)}">凭证刷新于 <span data-ago="${a.last_refresh}">${fmtAgo(a.last_refresh)}</span></span>` : '',
  ].filter(Boolean).join('');
  let src = '本地日志';
  if (usage) {
    const label = st.freshness === 'live' ? '实时' : st.freshness === 'reset' ? '上次已知（已过重置时间）' : '上次已知';
    src = `${label} · 数据更新于 <span data-ago="${usage.updated}" title="${fmtDate(usage.updated)}">${fmtAgo(usage.updated)}</span>`;
  }
  return `<div class="card ${a.is_current ? 'current' : ''} ${stale ? 'stale' : ''}" data-id="${esc(a.id)}">
    <div class="card-head">
      <span class="dot ${st.level || 'none'}"></span>
      <span class="card-name" title="${esc(a.name)}">${esc(a.name)}</span>
      ${a.is_current ? '<span class="tag ok">当前使用</span>' : ''}
      ${a.valid ? '' : '<span class="tag warn">存档无效</span>'}
      <div class="card-menu-wrap">
        <button class="btn-more" data-menu="toggle" title="更多操作">⋯</button>
        <div class="dropdown" hidden>
          <button data-act="rename">重命名</button>
          <button data-act="note">编辑备注</button>
          <div class="sep"></div>
          <button data-act="up" ${rawIdx === 0 ? 'disabled' : ''}>上移排序</button>
          <button data-act="down" ${rawIdx === total - 1 ? 'disabled' : ''}>下移排序</button>
          <div class="sep"></div>
          <button class="danger" data-act="delete">删除账号</button>
        </div>
      </div>
    </div>
    <div class="card-sub"><span class="copy-email" data-copy-email="${esc(a.email)}" title="点击复制邮箱">${esc(maskEmail(a.email))}</span><span class="tag">${esc(planName(a.plan || (usage && usage.plan)))}</span></div>
    ${meta ? `<div class="meta">${meta}</div>` : ''}
    ${a.note ? `<div class="card-note">${esc(a.note)}</div>` : ''}
    <div class="usage">${renderUsage(usage, a.is_current, stale)}</div>
    <div class="card-foot"><span class="src">${src}</span>
      ${a.is_current ? '<span class="using">● 正在使用</span>'
        : `<button class="btn primary sm" data-act="switch" ${a.valid ? '' : 'disabled'}>切换到此账号</button>`}
    </div></div>`;
}

function closeMenus() {
  document.querySelectorAll('.card-menu-wrap.open').forEach((w) => {
    w.classList.remove('open');
    const dd = w.querySelector('.dropdown');
    if (dd) dd.hidden = true;
  });
}

function render(force = false) {
  if (!state) return;
  renderBreaks();
  if (!force && document.querySelector('.card-menu-wrap.open')) return;
  const sig = JSON.stringify([
    state.codex_running, sortMode, privacyMode, themeMode, state.snapshots,
    state.current, state.accounts, state.update, dismissedUpdateSha,
  ]);
  if (!force && sig === lastRenderSig) return;
  lastRenderSig = sig;

  const st = $('codexStatus');
  if (state.codex_running === null) { st.textContent = 'Codex 状态检测中'; st.className = 'pill'; }
  else { st.textContent = state.codex_running ? 'Codex 运行中' : 'Codex 未运行'; st.className = 'pill' + (state.codex_running ? ' on' : ''); }

  const banner = $('banner');
  if (state.current.valid && !state.current.saved_id) {
    banner.innerHTML = `<span>检测到当前登录的 <b>${esc(maskEmail(state.current.email))}</b> 还没有保存。保存后，以后切回这个号就不用再登录了。</span>
      <span class="spacer"></span><button class="btn primary sm" id="bannerSave">立即保存</button>`;
    banner.hidden = false;
    $('bannerSave').onclick = saveCurrent;
  } else {
    banner.hidden = true;
  }

  const rec = pickRecommendation();
  const rb = $('recBanner');
  if (rec && rec.kind === 'switch') {
    const curTxt = rec.curOut ? '额度已用尽' : `额度仅剩 ${rec.curRem}%`;
    rb.className = 'banner rec';
    rb.innerHTML = `<span>当前「<b>${esc(rec.curName)}</b>」${esc(curTxt)}，推荐切换到「<b>${esc(rec.target.name)}</b>」（${esc(rec.targetDesc)}）</span>
      <span class="spacer"></span>
      <button class="btn primary sm" id="btnRecSwitch">一键切换</button>`;
    rb.hidden = false;
    $('btnRecSwitch').onclick = () => switchTo(rec.target);
  } else if (rec && rec.kind === 'all_out') {
    rb.className = 'banner rec warn';
    rb.innerHTML = `<span>所有账号当前均已用尽 · 最早恢复：「<b>${esc(rec.earliest.name)}</b>」将于 <b data-reset="${rec.recoverAt}">${fmtDuration(rec.recoverAt - nowSec())}</b> 后（${fmtShort(rec.recoverAt)}）恢复</span>`;
    rb.hidden = false;
  } else {
    rb.hidden = true;
  }

  const u = state.update || {};
  const ub = $('updateBanner');
  if (u.supported && u.has_update && u.remote_sha && u.remote_sha !== dismissedUpdateSha) {
    const commits = u.commits || [];
    const rawLatest = commits[0] || '';
    const latest = rawLatest.includes(' ')
      ? rawLatest.slice(rawLatest.indexOf(' ') + 1)
      : (rawLatest || u.remote_short || '');
    const more = u.behind > 1 ? `（共 ${u.behind} 项更新）` : '';
    ub.innerHTML = `<span>发现新版本：<b>${esc(latest)}</b>${esc(more)}</span>
      <span class="spacer"></span>
      <button class="btn primary sm" id="btnBannerUpdate">立即更新并重启</button>
      <button class="btn sm" id="btnBannerDismiss">暂不</button>`;
    ub.hidden = false;
    $('btnBannerUpdate').onclick = () => runUpdate(false);
    $('btnBannerDismiss').onclick = () => { dismissedUpdateSha = u.remote_sha; render(true); };
  } else {
    ub.hidden = true;
  }

  $('settingsDot').hidden = !(u.supported && u.has_update);
  if (!$('settings').hidden) renderUpdateBox();

  renderOverview();

  const list = $('list');
  const rawIdxById = new Map(state.accounts.map((a, i) => [a.id, i]));
  const accs = orderedAccounts();
  list.innerHTML = accs.length
    ? accs.map((a) => renderCard(a, rawIdxById.get(a.id), accs.length)).join('')
    : `<div class="empty"><b>还没有保存任何账号</b><br><br>
       先保存正在用的账号，再用“登录新账号”添加其他账号。<br>每个账号只需要登录一次，之后都能一键切换。</div>`;
  const ver = state.version || u.app_version || '1.7.2';
  $('footInfo').textContent = `v${ver} · ${accs.length} 个账号 · ${state.snapshots} 条额度记录 · 自动刷新`;
}

// 每秒更新倒计时 / “x 分钟前”；有倒计时到点就强制重新拉取渲染
function tick() {
  if (!state) return;
  tickBreaks();
  let expired = false;
  document.querySelectorAll('[data-reset]').forEach((el) => {
    const left = Number(el.dataset.reset) - nowSec();
    if (left <= 0) expired = true;
    else el.textContent = fmtDuration(left);
  });
  document.querySelectorAll('[data-ago]').forEach((el) => (el.textContent = fmtAgo(Number(el.dataset.ago))));
  if (expired) refresh(true, true);
}

// ------------------------------------------------------------------ 操作
const findAcc = (id) => state && state.accounts.find((a) => a.id === id);

async function saveCurrent() {
  const c = state && state.current;
  if (!c || !c.valid) { toast('当前 Codex 没有登录任何账号', true); return false; }
  let name = '';
  if (!c.saved_id) {
    name = await promptBox('保存当前登录', `给这个账号起个名字：\n${c.email}（${planName(c.plan)}）`,
      c.email.split('@')[0], '例如：工作号 / 个人号');
    if (name === null) return false;
  }
  try {
    const r = await call('save_current', name, '');
    stopWaitLogin();
    toast(r.message || '已保存');
    await refresh(true, true);
    return true;
  } catch (e) { toast(e.message, true); return false; }
}

/** 切换/登出前，确保当前登录不丢。返回 {go, force} */
async function ensureSaved() {
  const c = state.current;
  if (!c.valid || c.saved_id) return { go: true, force: false };
  const r = await dialog({
    title: '当前登录还没保存',
    body: `${c.email} 还没有保存。\n不保存的话，之后要用这个账号得重新登录。`,
    buttons: [{ id: 'cancel', text: '取消' }, { id: 'skip', text: '不保存，继续' }, { id: 'save', text: '先保存', kind: 'primary' }],
  });
  if (r.id === 'cancel') return { go: false };
  if (r.id === 'save') return { go: await saveCurrent(), force: false };
  return { go: true, force: true };
}

async function switchTo(a) {
  stopWaitLogin();
  if (!state) await refresh();
  a = findAcc(a.id) || a;
  if (a.is_current) { toast(`现在登录的就是「${a.name}」`); return; }
  const g = await ensureSaved();
  if (!g.go) return;
  if (state.codex_running && settings.confirm_before_switch !== false) {
    const ans = await dialog({
      title: '切换账号',
      body: `切换到：${a.name}（${maskEmail(a.email)}）\n\n需要先关闭 Codex，正在进行的任务会被中断。`,
      buttons: [
        { id: 'cancel', text: '取消' },
        { id: 'never', text: '切换（不再提示）' },
        { id: 'ok', text: '关闭并切换', kind: 'primary' },
      ],
    });
    if (ans.id === 'cancel') return;
    if (ans.id === 'never') setSetting('confirm_before_switch', false);
  }
  const launch = !!settings.launch_after_switch;
  const steps = ['关闭 Codex 并写入新凭证'].concat(launch ? ['启动 Codex'] : []);
  setBusy(true, `正在切换到「${a.name}」`, steps);
  try {
    const r = await call('switch_account', a.id, launch, g.force);
    if (r.need_save) throw new Error('请先保存当前登录');
    setSteps(steps, steps.length);
    if (r.state) {
      state = r.state;
      render(true);
    }
    setBusy(false);
    toast(r.message || (`已切换到「${a.name}」` + (launch ? '，Codex 正在启动' : '')));
    [2200, 4500, 7500, 11000].forEach((ms) => setTimeout(() => refresh(true, true), ms));
  } catch (e) {
    setBusy(false);
    toast(e.message, true);
    await refresh(true, true);
  }
}
window.switchById = async (id) => {
  if (!state) await refresh();
  const a = findAcc(id);
  if (a) switchTo(a);
};

async function addNew() {
  await refresh();
  const g = await ensureSaved();
  if (!g.go) return;
  const ok = await confirmBox('登录新账号',
    '将关闭 Codex 并收起当前登录（不会作废，随时能切回来），然后重新打开 Codex。\n\n在 Codex 中完成登录后，本窗口会自动弹出来提醒你起名保存。\n\n提示：不要在 Codex 里点“退出登录”，那会让已保存的凭证失效。', '继续');
  if (!ok) return;
  setBusy(true, '正在准备登录新账号…');
  try {
    await call('add_new', !!settings.launch_after_switch, g.force);
    setBusy(false);
    startWaitLogin();
    toast('请在打开的 Codex 里登录新账号');
  } catch (e) {
    setBusy(false);
    toast(e.message, true);
  }
  await refresh(true, true);
}

async function onAction(act, a) {
  try {
    if (act === 'switch') return switchTo(a);
    if (act === 'save') return saveCurrent();
    if (act === 'up' || act === 'down') { await call('move', a.id, act === 'up' ? -1 : 1); }
    if (act === 'rename') {
      const name = await promptBox('重命名', '', a.name);
      if (name === null || name === a.name) return;
      toast((await call('rename', a.id, name)).message);
    }
    if (act === 'note') {
      const note = await promptBox('备注', '例如：公司号、7号续费、只用来跑大任务…', a.note || '');
      if (note === null) return;
      toast((await call('set_note', a.id, note)).message);
    }
    if (act === 'delete') {
      const extra = a.is_current ? '\n（这是当前登录的账号：删除存档不会退出 Codex 登录。）' : '';
      if (!(await confirmBox('删除账号存档', `确定删除「${a.name}」（${a.email}）？\n文件会移到 _backup 目录，可以找回。${extra}`, '删除', 'danger solid'))) return;
      toast((await call('delete', a.id)).message);
    }
    await refresh(true, true);
  } catch (e) { toast(e.message, true); }
}

function scrollToCard(id) {
  const card = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
  if (!card) return;
  card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  card.classList.remove('flash');
  void card.offsetWidth;
  card.classList.add('flash');
}

// ------------------------------------------------------------------ 更新
function renderUpdateBox() {
  const u = (state && state.update) || {};
  const ver = (state && state.version) || u.app_version || '1.7.2';
  $('updateVer').textContent = u.local_short ? `v${ver} (${u.local_short})` : `v${ver}`;
  const btnCheck = $('btnCheckUpdate');
  const btnApply = $('btnApplyUpdate');
  const desc = $('updateDesc');
  const commitsEl = $('updateCommits');

  if (!u.supported) {
    desc.textContent = u.reason || '当前环境不支持 Git 自动更新';
    btnCheck.hidden = true;
    btnApply.hidden = true;
    commitsEl.hidden = true;
    return;
  }

  btnCheck.hidden = false;
  btnCheck.disabled = !!u.checking;
  if (u.checking) {
    desc.textContent = '正在检查 GitHub 仓库更新…';
    btnApply.hidden = true;
    commitsEl.hidden = true;
    return;
  }
  if (u.error) {
    desc.textContent = u.error + (u.checked_at ? `（${fmtAgo(u.checked_at)}检查）` : '');
    btnApply.hidden = !u.has_update;
    commitsEl.hidden = true;
    return;
  }
  if (u.has_update) {
    desc.textContent = `发现 ${u.behind || 1} 项新更新（远端 ${u.remote_short || ''}）`;
    btnApply.hidden = false;
    const commits = u.commits || [];
    if (commits.length) {
      commitsEl.innerHTML = commits.map((c) => `<div>· ${esc(c)}</div>`).join('');
      commitsEl.hidden = false;
    } else {
      commitsEl.hidden = true;
    }
    return;
  }
  btnApply.hidden = true;
  commitsEl.hidden = true;
  if (u.checked_at) {
    desc.textContent = `已是最新（${fmtAgo(u.checked_at)}检查${u.dirty ? ' · 本地有未提交改动' : ''}）`;
  } else {
    desc.textContent = `当前分支 ${u.branch || 'main'} · 点击右侧检查更新`;
  }
}

async function checkUpdateNow() {
  const btn = $('btnCheckUpdate');
  if (btn) btn.disabled = true;
  $('updateDesc').textContent = '正在检查 GitHub 仓库更新…';
  try {
    const r = await api().check_update(true);
    if (r && r.status && state) state.update = r.status;
    if (r && r.ok === false && r.error) {
      toast(r.error, true);
    } else {
      const st = (r && r.status) || {};
      toast(st.has_update ? `发现 ${st.behind || 1} 项新更新` : '当前已是最新版本');
    }
    await refresh(true, true);
  } catch (e) {
    toast('检查更新失败：' + e.message, true);
  } finally {
    if (btn) btn.disabled = false;
    renderUpdateBox();
  }
}

async function runUpdate(forceReset = false) {
  if (busy) return;
  closeSettings();
  setBusy(true, forceReset ? '正在暂存本地改动并同步最新代码…' : '正在从 GitHub 拉取最新代码…');
  try {
    const r = await api().apply_update(forceReset);
    if (r && r.status && state) state.update = r.status;
    if (r && r.need_force) {
      setBusy(false);
      const ok = await confirmBox(
        '本地存在未同步修改',
        (r.error || '无法直接快进合并。') + '\n\n是否先自动暂存（git stash）本地修改，再同步到最新版本？',
        '暂存并同步',
        'primary'
      );
      if (ok) return runUpdate(true);
      return;
    }
    if (!r || r.ok === false) {
      setBusy(false);
      toast((r && r.error) || '更新失败', true);
      await refresh(true, true);
      return;
    }
    if (!r.updated) {
      setBusy(false);
      toast(r.message || '当前已是最新版本');
      await refresh(true, true);
      return;
    }
    $('busyText').textContent = r.message || '更新完成，正在重启…';
  } catch (e) {
    setBusy(false);
    toast('更新失败：' + e.message, true);
  }
}

// ------------------------------------------------------------------ 放空小站
let breakBusy = false;
let breakPanelSig = '';
const fmtClock = (sec) => {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
};
const fmtDur = (sec) => {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec}秒`;
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}分钟`;
  return `${Math.floor(m / 60)}小时${m % 60 ? `${m % 60}分` : ''}`;
};
const fmtHM = (ts) => {
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const breakRange = (b) => {
  const m = (b && b.target_minutes) || 10;
  return m > 5 ? `5–${m}` : `${m}`;
};
const tagLabel = (b, id) => {
  const t = ((b && b.tags) || []).find((x) => x.id === id);
  return t ? t.label : '';
};

function renderBreaks() {
  const b = state && state.breaks;
  const pill = $('breakPill');
  const bn = $('breakBanner');
  if (!$('breakPanel').hidden) renderBreakPanel();
  if (!b || !b.enabled) {
    pill.hidden = true;
    bn.hidden = true;
    bn.dataset.sig = '';
    return;
  }
  const now = nowSec();
  const act = b.active;
  const over = !!(act && now - act.start >= b.target_minutes * 60);
  if (act) {
    pill.className = 'pill break-pill ' + (over ? 'over' : 'active');
    pill.innerHTML = `☕ 放空中 <b data-brk-elapsed="${act.start}">${fmtClock(now - act.start)}</b> · 点我已回来`;
    pill.title = '回来了就点一下，记录这次放空';
  } else {
    pill.className = 'pill break-pill';
    pill.innerHTML = `☕ 去放空 · 今日 ${b.today.count} 次`;
    pill.title = `今日放空 ${b.today.count} 次，共 ${fmtDur(b.today.total_sec)}\n点击打开放空小站`;
  }
  pill.hidden = false;

  const sig = JSON.stringify([act && act.start, act && act.tag, over]);
  if (bn.dataset.sig === sig) return;
  bn.dataset.sig = sig;
  if (act) {
    const tg = tagLabel(b, act.tag);
    const el = `<b data-brk-elapsed="${act.start}">${fmtClock(now - act.start)}</b>`;
    bn.className = 'banner brk ' + (over ? 'over' : 'active');
    bn.innerHTML = `<span>☕ ${over ? `已经放空 ${el} 啦，超过 ${b.target_minutes} 分钟了` : `正在放空 ${el}`}${tg ? ` · ${esc(tg)}` : ''}${over ? '' : ` · 建议 ${breakRange(b)} 分钟`}</span>
      <span class="spacer"></span>
      <button class="btn primary sm" data-brk="end">✅ 已回来</button>
      <button class="btn sm" data-brk="open">放空小站</button>
      <button class="btn sm" data-brk="cancel">不算这次</button>`;
    bn.hidden = false;
  } else {
    bn.hidden = true;
    bn.innerHTML = '';
  }
}

function syncBreak3DState() {
  if (!window.Break3D) return;
  const b = state && state.breaks;
  const stageEl = $('bpStage');
  if (!b) return;
  const now = nowSec();
  const act = b.active;
  const targetSec = Math.max(60, (b.target_minutes || 10) * 60);
  const elapsed = act ? Math.max(0, now - act.start) : 0;
  const over = !!(act && elapsed >= targetSec);
  const mode = act ? (over ? 'over' : 'active') : 'idle';
  if (stageEl) {
    stageEl.className = 'bp-stage' + (mode === 'active' ? ' on' : mode === 'over' ? ' over' : '');
  }
  window.Break3D.sync(mode, act ? elapsed / targetSec : 0);
  const curModel = window.Break3D.getModel();
  document.querySelectorAll('#bpModelBar [data-model]').forEach((btn) => {
    btn.classList.toggle('on', btn.dataset.model === curModel);
  });
}

function tickBreaks() {
  const b = state && state.breaks;
  if (!b) return;
  const now = nowSec();
  document.querySelectorAll('[data-brk-elapsed]').forEach((el) => {
    el.textContent = fmtClock(now - Number(el.dataset.brkElapsed));
  });
  document.querySelectorAll('[data-brk-since]').forEach((el) => {
    el.textContent = fmtDur(now - Number(el.dataset.brkSince));
  });
  if (!$('breakPanel').hidden) syncBreak3DState();
  if (b.enabled && b.active && !$('breakBanner').classList.contains('over') && now - b.active.start >= b.target_minutes * 60) {
    renderBreaks();
  }
}

function renderBreakPanel(force = false) {
  const b = state && state.breaks;
  const box = $('breakPanelBody');
  const heroBox = $('bpHeroInfo');
  if (!b) { box.innerHTML = '<div class="muted">正在读取…</div>'; return; }
  syncBreak3DState();
  const sig = JSON.stringify(b);
  if (!force && sig === breakPanelSig) return;
  breakPanelSig = sig;
  const now = nowSec();
  const t = b.today;
  const act = b.active;
  const lv = b.level;
  const nextLv = lv.next_at ? ` · 再放空 ${lv.next_at - b.all.count} 次升级` : '';
  $('bpLevel').innerHTML = `<b>Lv.${lv.lv} ${esc(lv.title)}</b> · 累计 ${b.all.count} 次 / ${fmtDur(b.all.total_sec)}${nextLv}`
    + (b.streak ? ` · 🔥 连续 ${b.streak} 天` : '');

  const chips = (b.tags || []).map((x) => `<button class="chip" data-bp="start" data-tag="${esc(x.id)}">${esc(x.label)}</button>`).join('');
  heroBox.innerHTML = act
    ? `<div class="bp-hero-top">
        <div class="bp-hero-l">
          <div class="bp-cap"><span class="bp-breath-dot"></span>正在放空${act.tag ? ' · ' + esc(tagLabel(b, act.tag)) : ''}</div>
          <div class="bp-timer" data-brk-elapsed="${act.start}">${fmtClock(now - act.start)}</div>
          <div class="bp-sub">${fmtHM(act.start)} 出发 · 建议 ${breakRange(b)} 分钟 · 戳一戳左边 3D 球解压</div>
        </div>
        <div class="bp-hero-r">
          <button class="btn primary bp-big" data-bp="end">✅ 已回来</button>
          <button class="btn sm ghost" data-bp="cancel">不算这次</button>
        </div>
      </div>`
    : `<div class="bp-hero-top">
        <div class="bp-hero-l">
          <div class="bp-cap">${b.since_last ? '距离上次放空' : '今天还没放空过'}</div>
          <div class="bp-timer">${b.since_last ? `<span data-brk-since="${b.since_last}">${fmtDur(now - b.since_last)}</span>` : '准备出发'}</div>
          <div class="bp-sub">想走就走，回来点「已回来」就会记下这次放空</div>
        </div>
        <div class="bp-hero-r"><button class="btn primary bp-big" data-bp="start">☕ 去放空</button></div>
      </div>
      <div class="bp-chips"><span class="muted small">选个理由出发：</span>${chips}</div>`;

  const goalPct = Math.min(100, Math.round((t.count / Math.max(1, b.goal)) * 100));
  const stats = `<div class="bp-stats">
      <div class="bp-stat" data-tilt="9"><div class="k">今日次数</div><div class="v">${t.count}<small> / 目标 ${b.goal}</small></div>
        <div class="bar"><div class="fill ${t.count >= b.goal ? '' : 'amber'}" style="width:${goalPct}%"></div></div></div>
      <div class="bp-stat" data-tilt="9"><div class="k">今日总时长</div><div class="v">${t.count ? fmtDur(t.total_sec) : '—'}</div></div>
      <div class="bp-stat" data-tilt="9"><div class="k">平均每次</div><div class="v">${t.count ? fmtDur(t.avg_sec) : '—'}</div></div>
      <div class="bp-stat" data-tilt="9"><div class="k">最长一次</div><div class="v">${t.count ? fmtDur(t.longest_sec) : '—'}</div></div>
    </div>`;

  const recs = t.records.length
    ? t.records.map((r) => `<div class="bp-rec">
        <span class="tm">${fmtHM(r.start)} – ${fmtHM(r.end)}</span>
        <span class="du">${fmtDur(r.end - r.start)}</span>
        <span class="tg">${esc(tagLabel(b, r.tag) || '—')}</span>
        ${r.auto ? '<span class="tag warn">自动结束</span>' : ''}
        <button class="bp-del" data-bp="delete" data-start="${r.start}" title="删除这条记录">×</button>
      </div>`).join('')
    : '<div class="muted small bp-empty">今天还没有放空记录，点上面的「去放空」开始吧</div>';

  const maxMin = Math.max(10, ...b.week.map((d) => d.total_sec / 60));
  const week = b.week.map((d, idx) => {
    const pct = d.total_sec > 0 ? Math.max(6, Math.round((d.total_sec / 60 / maxMin) * 100)) : 0;
    return `<div class="bp-col ${d.today ? 'today' : ''}" title="${d.date} · ${d.count} 次 · ${fmtDur(d.total_sec)}">
      <div class="n">${d.count || ''}</div>
      <div class="bp-barv">${pct ? `<div class="bp-prism" style="height:${pct}%;--d:${idx * 55}ms"></div>` : ''}</div>
      <div class="l">${d.label}</div>
    </div>`;
  }).join('');
  const got = b.badges.filter((x) => x.got).length;
  const badges = b.badges.map((x) => `<div class="bp-badge ${x.got ? 'got' : ''}" ${x.got ? 'data-tilt="12" data-badge-spin="1"' : ''} title="${esc(x.desc)}${x.got ? '（已解锁 · 点击翻转炫耀）' : '（未解锁）'}"><span class="i">${x.icon}</span><span>${esc(x.name)}</span></div>`).join('');

  box.innerHTML = stats
    + `<div class="bp-sec"><div class="bp-h">今日记录 <span class="muted small">${t.count} 次</span></div><div class="bp-recs">${recs}</div></div>
      <div class="bp-cols2">
        <div class="bp-sec"><div class="bp-h">最近 7 天 <span class="muted small">3D 光柱 = 放空时长，数字 = 次数</span></div><div class="bp-week">${week}</div></div>
        <div class="bp-sec"><div class="bp-h">成就 <span class="muted small">${got}/${b.badges.length}</span></div><div class="bp-badges">${badges}</div></div>
      </div>`;
  $('bpMinutes').value = String(b.target_minutes);
  $('bpGoal').value = String(b.goal);
  if (window.Break3D) window.Break3D.bindTilts(box);
}

function openBreakPanel() {
  $('breakPanel').hidden = false;
  renderBreakPanel(true);
  if (window.Break3D) {
    window.Break3D.start();
    syncBreak3DState();
    window.Break3D.pulse(0.7, false);
  }
}
function closeBreakPanel() {
  $('breakPanel').hidden = true;
  if (window.Break3D) window.Break3D.stop();
}

async function breakAction(action, arg = null) {
  if (breakBusy) return;
  if (action === 'delete' && !(await confirmBox('删除记录', '确定删除这条放空记录吗？', '删除', 'danger solid'))) return;
  breakBusy = true;
  const prev = state && state.breaks && state.breaks.active;
  try {
    const r = await call('break_action', action, arg);
    if (state && r.breaks) state.breaks = r.breaks;
    if (action === 'start') {
      toast('出发放空 ☕ 回来记得点「已回来」');
      if (window.Break3D && !$('breakPanel').hidden) window.Break3D.pulse(1.3, '出发放空 ☕');
    } else if (action === 'end') {
      const dur = prev ? Math.max(0, Math.round(nowSec() - prev.start)) : 0;
      if (prev && dur < 20) toast(`这次放空 ${fmtDur(dur)}（不足 20 秒，未计入记录）`);
      else {
        toast(prev ? `欢迎回来！这次放空 ${fmtDur(dur)}，今天第 ${r.breaks.today.count} 次` : '欢迎回来！');
        if (window.Break3D && !$('breakPanel').hidden) window.Break3D.celebrate(false, 0.55, 0.28);
      }
    } else if (action === 'cancel') toast('好的，这次不计入记录');
    else if (action === 'delete') toast('已删除这条记录');
    if (r.new_badges && r.new_badges.length) {
      if (window.Break3D && !$('breakPanel').hidden) {
        setTimeout(() => window.Break3D.celebrate(true, 0.72, 0.72), 450);
      }
      setTimeout(() => toast(`🏅 解锁成就：${r.new_badges.join('、')}`), 2200);
    }
    renderBreaks();
  } catch (e) {
    toast(e.message, true);
  } finally {
    breakBusy = false;
  }
}

function onBreakPill() {
  const b = state && state.breaks;
  if (!b) return;
  if (b.active) breakAction('end');
  else openBreakPanel();
}

// ------------------------------------------------------------------ 设置
function openSettings() {
  document.querySelectorAll('#settings input[data-key]').forEach((el) => (el.checked = !!settings[el.dataset.key]));
  document.querySelectorAll('#settings [data-skey]').forEach((el) => {
    const v = settings[el.dataset.skey];
    if (v !== undefined && v !== null) el.value = String(v);
  });
  $('storeDir').textContent = (state && state.store_dir) || '';
  const isMac = state && state.platform === 'darwin';
  $('optCloseToTray').hidden = isMac;
  $('autostartDesc').textContent = isMac ? '开机登录 macOS 时自动打开。' : '开机后只在托盘显示，不弹窗口。';
  renderUpdateBox();
  $('settings').hidden = false;
}
function closeSettings() {
  $('settings').hidden = true;
}

// ------------------------------------------------------------------ 初始化
async function init() {
  $('btnRefresh').onclick = () => refresh(false, true, true);
  $('btnPrivacy').onclick = () => {
    privacyMode = !privacyMode;
    updatePrivacyBtn();
    setSetting('privacy_mode', privacyMode);
    render(true);
  };
  $('btnTheme').onclick = () => {
    const order = ['auto', 'light', 'dark'];
    themeMode = order[(order.indexOf(themeMode) + 1) % order.length];
    applyTheme();
    setSetting('theme', themeMode);
    const names = { auto: '跟随系统', light: '浅色模式', dark: '深色模式' };
    toast(`外观已切换为：${names[themeMode]}`);
  };
  if (window.matchMedia) {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    if (mq.addEventListener) mq.addEventListener('change', () => { if (themeMode === 'auto') applyTheme(); });
  }
  $('btnFolder').onclick = () => call('open_store').catch((e) => toast(e.message, true));
  $('btnUsagePage').onclick = () => call('open_usage_page').catch((e) => toast(e.message, true));
  $('btnSettings').onclick = openSettings;
  $('btnSettingsClose').onclick = closeSettings;
  $('btnCheckUpdate').onclick = checkUpdateNow;
  $('btnApplyUpdate').onclick = () => runUpdate(false);
  $('settings').onclick = (e) => { if (e.target === $('settings')) closeSettings(); };
  $('btnQuit').onclick = async () => {
    if (await confirmBox('退出程序', '退出后将不再在后台记录额度。确定退出？', '退出', 'danger solid')) call('quit_app');
  };
  document.querySelectorAll('#settings input[data-key]').forEach((el) => {
    el.onchange = async () => {
      await setSetting(el.dataset.key, el.checked);
      if (el.dataset.key.startsWith('break_')) refresh(true);
    };
  });
  document.querySelectorAll('#settings [data-skey]').forEach((el) => {
    el.onchange = async () => {
      const k = el.dataset.skey;
      await setSetting(k, el.tagName === 'SELECT' ? Number(el.value) : el.value);
      if (settings[k] !== undefined) el.value = String(settings[k]);
      refresh(true);
    };
  });
  $('breakPill').onclick = onBreakPill;
  $('breakBanner').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-brk]');
    if (!btn) return;
    if (btn.dataset.brk === 'open') openBreakPanel();
    else breakAction(btn.dataset.brk);
  });
  window.onBreakEvent = () => refresh(true);
  $('bpClose').onclick = closeBreakPanel;
  $('breakPanel').addEventListener('click', (e) => {
    if (e.target === $('breakPanel')) { closeBreakPanel(); return; }
    const modelBtn = e.target.closest('[data-model]');
    if (modelBtn && window.Break3D) {
      window.Break3D.setModel(modelBtn.dataset.model);
      syncBreak3DState();
      return;
    }
    const badgeEl = e.target.closest('[data-badge-spin]');
    if (badgeEl) {
      badgeEl.classList.remove('spin3d');
      void badgeEl.offsetWidth;
      badgeEl.classList.add('spin3d');
      if (window.Break3D) {
        const br = badgeEl.getBoundingClientRect();
        const ox = window.innerWidth ? (br.left + br.width * 0.5) / window.innerWidth : 0.65;
        const oy = window.innerHeight ? (br.top + br.height * 0.5) / window.innerHeight : 0.68;
        window.Break3D.celebrate(true, ox, oy);
      }
      return;
    }
    const btn = e.target.closest('[data-bp]');
    if (!btn) return;
    const act = btn.dataset.bp;
    if (act === 'start') breakAction('start', btn.dataset.tag || '');
    else if (act === 'delete') breakAction('delete', Number(btn.dataset.start));
    else breakAction(act);
  });
  [['bpMinutes', 'break_minutes'], ['bpGoal', 'break_goal']].forEach(([id, key]) => {
    $(id).onchange = async (e) => {
      await setSetting(key, Number(e.target.value));
      await refresh(true);
      renderBreakPanel(true);
    };
  });
  $('btnSave').onclick = saveCurrent;
  $('btnAdd').onclick = addNew;
  $('chkLaunch').onchange = (e) => setSetting('launch_after_switch', e.target.checked);

  // 总览：排序切换 / 行内按钮 / 点击行定位卡片
  $('overview').addEventListener('click', (e) => {
    const sortBtn = e.target.closest('[data-sort]');
    if (sortBtn) {
      sortMode = sortBtn.dataset.sort;
      localStorage.setItem('sortMode', sortMode);
      setSetting('sort_mode', sortMode);
      render(true);
      return;
    }
    const row = e.target.closest('.ov-row');
    if (!row) return;
    const btn = e.target.closest('[data-act]');
    const a = findAcc(row.dataset.id);
    if (btn && !btn.disabled) { onAction(btn.dataset.act, a || {}); return; }
    if (a) scrollToCard(a.id);
  });

  // 卡片：点击邮箱复制 / ⋯ 菜单展开/收起 + 卡片操作
  $('list').addEventListener('click', (e) => {
    const emailEl = e.target.closest('[data-copy-email]');
    if (emailEl) {
      e.stopPropagation();
      copyEmail(emailEl.dataset.copyEmail);
      return;
    }
    const toggle = e.target.closest('[data-menu="toggle"]');
    if (toggle) {
      e.stopPropagation();
      const wrap = toggle.closest('.card-menu-wrap');
      const wasOpen = wrap.classList.contains('open');
      closeMenus();
      if (!wasOpen) {
        wrap.classList.add('open');
        const dd = wrap.querySelector('.dropdown');
        if (dd) dd.hidden = false;
      }
      return;
    }
    const btn = e.target.closest('[data-act]');
    if (!btn || btn.disabled) return;
    closeMenus();
    const card = btn.closest('.card');
    const a = card && findAcc(card.dataset.id);
    if (a) onAction(btn.dataset.act, a);
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.card-menu-wrap')) closeMenus();
  });
  $('list').addEventListener('dblclick', (e) => {
    if (e.target.closest('button') || e.target.closest('.card-menu-wrap') || e.target.closest('[data-copy-email]')) return;
    const card = e.target.closest('.card');
    const a = card && findAcc(card.dataset.id);
    if (a && !a.is_current && a.valid) switchTo(a);
  });

  // 快捷键：Esc 关菜单/设置，F5 刷新，Ctrl+1..9 按当前显示顺序切号
  document.addEventListener('keydown', (e) => {
    if (!$('modal').hidden || busy) return;
    if (e.key === 'Escape' && document.querySelector('.card-menu-wrap.open')) {
      e.preventDefault(); closeMenus(); return;
    }
    if (!$('breakPanel').hidden) {
      if (e.key === 'Escape') { e.preventDefault(); closeBreakPanel(); }
      return;
    }
    if (!$('settings').hidden) {
      if (e.key === 'Escape') { e.preventDefault(); closeSettings(); }
      return;
    }
    if (e.key === 'F5') { e.preventDefault(); refresh(false, true, true); }
    if (e.ctrlKey && /^[1-9]$/.test(e.key) && state) {
      const a = orderedAccounts()[Number(e.key) - 1];
      if (a) { e.preventDefault(); switchTo(a); }
    }
  });

  await loadSettings();
  await refresh(true, true);
  setTimeout(() => refresh(true, true), 1200);
  setTimeout(() => refresh(true, true), 3200);
  setInterval(() => { if (!document.hidden) refresh(); }, 5000);
  setInterval(tick, 1000);
  window.addEventListener('focus', () => refresh(true, false, true));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(true, false, true); });
}

if (window.pywebview && window.pywebview.api) init();
else window.addEventListener('pywebviewready', init);
