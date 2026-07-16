const views = {
  settings: { title: '系统设置', subtitle: '集中管理 115 账号、Telegram 登录与频道、代理、Emby 与媒体库同步。' },
  dashboard: { title: '早安，Admin', subtitle: '今天系统自动入库与订阅监控正在运行。' },
  discover: { title: '发现资源', subtitle: '浏览 TMDB、豆瓣和平台热更海报，按影片搜索资源。' },
  account: { title: '115账号配置', subtitle: '保存 Cookie 和常用目录 ID，供独立 115 功能使用。' },
  monitor: { title: '115频道监控配置', subtitle: '配置 TG 频道和检查间隔，可手动执行一次监控。' },
  offline: { title: '离线设置', subtitle: '预留模块。' },
  clean: { title: '自动清理', subtitle: '清理指定 115 目录并可执行 115 助力。' },
};

views.subscription = { title: '订阅规则', subtitle: '配置自动订阅来源，并手动刷新订阅内容。' };
views['my-subscription'] = { title: '我的订阅', subtitle: '按电影、电视剧、日历和屏蔽名单查看订阅入库进度。' };
views['activity-log'] = { title: '系统日志', subtitle: '查看页面操作、订阅、推送和转存记录。' };

const themeStorageKey = 'nasemby-theme';

function activeTheme() {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

function updateThemeToggle() {
  const button = document.getElementById('theme-toggle');
  if (!button) return;
  const theme = activeTheme();
  button.dataset.theme = theme;
  const next = theme === 'light' ? '暗色背景' : '浅色背景';
  button.title = `切换${next}`;
  button.setAttribute('aria-label', `切换${next}`);
}

function setTheme(theme, persist = true) {
  const normalized = theme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = normalized;
  if (persist) {
    try { localStorage.setItem(themeStorageKey, normalized); } catch (error) {}
  }
  updateThemeToggle();
}

const configFields = [
  'ENV_115_COOKIES',
  'ENV_115_LINK_UPLOAD_PID',
  'ENV_115_UPLOAD_PID',
  'ENV_UPLOAD_PID',
  'ENV_115_TGMONITOR_SWITCH',
  'ENV_115_TG_CHANNEL',
  'ENV_CHECK_INTERVAL',
  'ENV_SUBSCRIPTION_SEARCH_INTERVAL',
  'ENV_TG_PHONE',
  'ENV_TG_API_ID',
  'ENV_TG_API_HASH',
  'ENV_TG_CHANNELS',
  'ENV_115_CLEAN_PID',
  'ENV_115_TRASH_PASSWORD',
  'ENV_TG_BOT_TOKEN',
  'ENV_TG_ADMIN_USER_ID',
  'ENV_TG_TRANSFER_NOTIFY_ENABLED',
  'ENV_TG_TRANSFER_NOTIFY_CHAT_IDS',
  'ENV_TG_TRANSFER_NOTIFY_WHITELIST',
  'ENV_TG_TRANSFER_NOTIFY_BLACKLIST',
  'ENV_TG_TRANSFER_NOTIFY_TEMPLATE',
  'ENV_TG_SUBSCRIPTION_NOTIFY_ENABLED',
  'ENV_TG_SUBSCRIPTION_NOTIFY_TEMPLATE',
  'ENV_PTTO115_SWITCH',
  'ENV_PTTO115_UPLOAD_PID',
  'ENV_PTTO123_SWITCH',
  'ENV_PTTO123_UPLOAD_PID',
  'ENV_123_CLIENT_ID',
  'ENV_123_CLIENT_SECRET',
  'ENV_EMBY_SERVER_URL',
  'ENV_EMBY_API_KEY',
  'ENV_MEDIA_LIBRARY_ADMIN',
  'ENV_MEDIA_LIBRARY_PASSWORD',
  'ENV_MEDIA_SYNC_CATEGORIES',
  'ENV_115_AUTO_CLASSIFY',
  'ENV_115_CLASSIFY_OVERWRITE',
  'ENV_115_CATEGORY_ROOT',
  'ENV_MOVIEPILOT_URL',
  'ENV_MOVIEPILOT_API_TOKEN',
  'ENV_MOVIEPILOT_USERNAME',
  'ENV_MOVIEPILOT_AUTO_SUBSCRIBE',
  'ENV_TORRA_URL',
  'ENV_TORRA_TOKEN',
  'ENV_TORRA_AUTO_SUBSCRIBE',
  'ENV_SYMEDIA_URL',
  'ENV_SYMEDIA_TOKEN',
  'ENV_SYMEDIA_USERNAME',
  'ENV_SYMEDIA_PASSWORD',
  'ENV_SYMEDIA_CHANNEL_TYPE',
  'ENV_SYMEDIA_CHANNEL_IDS',
  'ENV_SYMEDIA_PARENT_ID',
  'ENV_SYMEDIA_RULE_ID',
  'ENV_SYMEDIA_AUTO_SUBSCRIBE',
  'ENV_HDHIVE_CHECKIN_ENABLED',
  'ENV_HDHIVE_CHECKIN_GAMBLER',
  'ENV_HDHIVE_CHECKIN_NOTIFY',
  'ENV_HDHIVE_UNLOCK_POINTS_LIMIT',
  'ENV_HDHIVE_UNLOCK_RATE_LIMIT',
  'ENV_HDHIVE_EXPIRY_REMINDER',
  'ENV_HDHIVE_REMINDER_INTERVAL_HOURS',
  'ENV_PROXY',
];

const hdhiveConfigFields = [
  'ENV_HDHIVE_CHECKIN_ENABLED',
  'ENV_HDHIVE_CHECKIN_GAMBLER',
  'ENV_HDHIVE_CHECKIN_NOTIFY',
  'ENV_HDHIVE_UNLOCK_POINTS_LIMIT',
  'ENV_HDHIVE_UNLOCK_RATE_LIMIT',
  'ENV_HDHIVE_EXPIRY_REMINDER',
  'ENV_HDHIVE_REMINDER_INTERVAL_HOURS',
];

const tgTransferTemplateDefaults = [
  { key: 'poster', label: '海报', icon: '🖼️', sample: 'TMDB横版背景图', enabled: true },
  { key: 'title', label: '标题', icon: '📺', sample: '电视剧：绝命毒师 (2008) S01E01-E07', enabled: true },
  { key: 'entry', label: '入库', icon: '📥', sample: '转存入库: S01E01', enabled: true },
  { key: 'id', label: 'ID', icon: '🍿', sample: 'TMDB ID: 1396', enabled: true },
  { key: 'rating', label: '评分', icon: '⭐', sample: '评分: 8.9', enabled: true },
  { key: 'genre', label: '题材', icon: '🎭', sample: '题材: 惊悚犯罪', enabled: true },
  { key: 'region', label: '地区', icon: '📂', sample: '地区: 美国', enabled: true },
  { key: 'quality', label: '质量', icon: '🎞️', sample: '质量: [4K] [DV&HDR] [DTS] [MKV]', enabled: true },
  { key: 'size', label: '大小', icon: '💾', sample: '大小: 14.44 GB', enabled: true },
  { key: 'trigger', label: '触发', icon: '🎯', sample: '触发: 手动订阅: /自动分类/美剧', enabled: true },
  { key: 'channel', label: '频道', icon: '📢', sample: '频道: 爱影频道', enabled: true },
  { key: 'link', label: '链接', icon: '🔗', sample: '链接: https://115.com/...', enabled: true },
  { key: 'plot', label: '剧情', icon: '📝', sample: '剧情简介: 一段简介文本...', enabled: true },
];

const tgSubscriptionTemplateDefaults = [
  { key: 'poster', label: '海报', icon: '🖼️', sample: 'TMDB订阅海报/背景图', enabled: true },
  { key: 'title', label: '标题', icon: '📺', sample: '电视剧：诡秘之主 (2025) 特别篇', enabled: true },
  { key: 'season', label: '季集', icon: '📅', sample: '订阅季: 特别篇 / Season 0', enabled: true },
  { key: 'id', label: 'ID', icon: '🍿', sample: 'TMDB ID: 232230', enabled: true },
  { key: 'rating', label: '评分', icon: '⭐', sample: '评分: 8.4', enabled: true },
  { key: 'genre', label: '题材', icon: '🎭', sample: '题材: 动画/奇幻', enabled: true },
  { key: 'region', label: '地区', icon: '📂', sample: '地区: 大陆', enabled: true },
  { key: 'status', label: '状态', icon: '🧭', sample: '状态: 已订阅 / 待入库', enabled: true },
  { key: 'source', label: '来源', icon: '📢', sample: '来源: 手动订阅', enabled: true },
  { key: 'plot', label: '剧情', icon: '📝', sample: '剧情简介: 一段简介文本...', enabled: true },
];

const fallbackMediaCategories = [
  ['anime_movie', '动画电影'],
  ['hk_movie', '港台电影'],
  ['hk_tv', '港台剧集'],
  ['cn_movie', '国产电影'],
  ['cn_tv', '国产剧集'],
  ['anime', '国漫'],
  ['documentary', '纪录影片'],
  ['western_animation', '美漫'],
  ['variety', '综艺'],
];

let telegramChannels = [];
let telegramCodeRequested = false;
let telegramQuickAddOpen = false;
let pushStatusAutoCheckTimer = null;
const telegramChannelModes = [
  { key: 'manual', label: '手动', tip: '手动搜索资源，手动转存。' },
  { key: 'incoming', label: '入新', tip: '频道发布新资源后直接转存并接入库。' },
  { key: 'follow', label: '追更', tip: '只处理已订阅资源，入库优先频道。' },
  { key: 'rewash', label: '洗版', tip: '已入库资源按指定规则搜索更好版本。' },
  { key: 'complete', label: '补全', tip: '检索 Emby 剧集漏集并搜索补全。' },
  { key: 'full', label: '全量', tip: '执行入新、追更、洗版、补全全部规则。' },
];
const dashboardState = {
  airingPage: 1,
  airingTotalPages: 1,
  airingPageSize: 8,
  airingItems: [],
  libraryPage: 1,
  libraryPageSize: 8,
  libraryItems: [],
};
let dashboardSystemTimer = null;
let dashboardSystemLoading = false;

function toast(message) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

function parseTelegramTemplate(value, defaults = []) {
  let saved = [];
  try {
    saved = JSON.parse(String(value || ''));
  } catch {
    saved = [];
  }
  const byKey = new Map(Array.isArray(saved)
    ? saved.filter(item => item && typeof item === 'object').map(item => [item.key, item])
    : []);
  return defaults.map(row => ({ ...row, ...(byKey.get(row.key) || {}) }));
}

function renderTelegramTemplateRows(rootId, hiddenId, defaults = []) {
  const root = document.getElementById(rootId);
  const hidden = document.getElementById(hiddenId);
  if (!root || !hidden) return;
  const rows = parseTelegramTemplate(hidden.value, defaults);
  root.innerHTML = rows.map(row => `
    <label class="tg-template-row" data-template-row="${escapeHtml(row.key)}">
      <span class="tg-template-drag" aria-hidden="true">⋮⋮</span>
      <strong>${escapeHtml(row.label || '')}</strong>
      <span class="tg-template-sample">${escapeHtml(`${row.icon || ''} ${row.sample || ''}`.trim())}</span>
      <input type="checkbox" data-template-toggle="${escapeHtml(row.key)}" ${row.enabled ? 'checked' : ''}>
      <span class="settings-switch-ui"></span>
    </label>
  `).join('');
}

function syncTelegramTemplateField(rootId, hiddenId, defaults = []) {
  const root = document.getElementById(rootId);
  const hidden = document.getElementById(hiddenId);
  if (!root || !hidden) return;
  const current = parseTelegramTemplate(hidden.value, defaults);
  const rows = current.map(row => {
    const checkbox = root.querySelector(`[data-template-toggle="${row.key}"]`);
    return { ...row, enabled: checkbox ? checkbox.checked : Boolean(row.enabled) };
  });
  hidden.value = JSON.stringify(rows);
}

function syncTelegramNotifyTemplateFields() {
  syncTelegramTemplateField('tg-transfer-template-builder', 'ENV_TG_TRANSFER_NOTIFY_TEMPLATE', tgTransferTemplateDefaults);
  syncTelegramTemplateField('tg-subscription-template-builder', 'ENV_TG_SUBSCRIPTION_NOTIFY_TEMPLATE', tgSubscriptionTemplateDefaults);
}

function renderTelegramNotifyTemplates() {
  renderTelegramTemplateRows('tg-transfer-template-builder', 'ENV_TG_TRANSFER_NOTIFY_TEMPLATE', tgTransferTemplateDefaults);
  renderTelegramTemplateRows('tg-subscription-template-builder', 'ENV_TG_SUBSCRIPTION_NOTIFY_TEMPLATE', tgSubscriptionTemplateDefaults);
}

async function api(path, options = {}) {
  const { timeoutMs = 0, ...fetchOptions } = options;
  const controller = timeoutMs ? new AbortController() : null;
  const timer = timeoutMs ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...fetchOptions,
      signal: controller ? controller.signal : fetchOptions.signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false || data.success === false) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('请求超时，请检查代理或 Telegram 网络');
    throw err;
  } finally {
    if (timer) window.clearTimeout(timer);
  }
}

function logActivityEvent(action, message, meta = {}, options = {}) {
  const payload = {
    category: options.category || 'operation',
    status: options.status || 'info',
    action,
    message,
    meta,
  };
  fetch('/api/activity/event', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    keepalive: true,
  }).catch(err => console.warn('日志记录失败', err));
}

function collectConfig() {
  syncMediaCategoryField();
  syncTelegramNotifyTemplateFields();
  const payload = {};
  for (const key of configFields) {
    const el = document.getElementById(key);
    if (el) payload[key] = el.type === 'checkbox' ? (el.checked ? '1' : '0') : el.value;
    if (el && (key === 'ENV_TG_TRANSFER_NOTIFY_WHITELIST' || key === 'ENV_TG_TRANSFER_NOTIFY_BLACKLIST' || key === 'ENV_TG_TRANSFER_NOTIFY_CHAT_IDS')) {
      payload[key] = String(el.value || '').split(/\n+/).map(part => part.trim()).filter(Boolean).join(',');
    }
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'ENV_UPLOAD_PID')) {
    payload.ENV_115_LINK_UPLOAD_PID = payload.ENV_UPLOAD_PID;
    payload.ENV_115_UPLOAD_PID = payload.ENV_UPLOAD_PID;
  }
  return payload;
}

const configSectionLabels = {
  account: '115 账号设置',
  clean: '清理设置',
  library: '媒体库设置',
  moviepilot: 'MoviePilot 设置',
  proxy: '代理设置',
  settings: '配置',
  symedia: 'Symedia 设置',
  telegram: 'Telegram 设置',
  torra: 'Torra 设置',
};

async function loadConfig() {
  const data = await api('/api/config');
  const cfg = data.config || {};
  for (const key of configFields) {
    const el = document.getElementById(key);
    if (!el) continue;
    if (el.type === 'checkbox') {
      el.checked = ['1', 'true', 'yes', 'on'].includes(String(cfg[key] || '').toLowerCase());
    } else {
      el.value = cfg[key] || '';
    }
  }
  applyMediaCategoryField(cfg.ENV_MEDIA_SYNC_CATEGORIES || '');
  applyTelegramStoredUserFallback(cfg);
  renderTelegramNotifyTemplates();
  updatePushConnectionBadges();
  autoCheckPushConnections();
  updateTelegramAuthAction();
  updateSettingsMonitor();
}

async function saveConfig(showToast = true, section = '') {
  const payload = collectConfig();
  if (section) payload.__section = section;
  await api('/api/config', { method: 'POST', body: JSON.stringify(payload) });
  if (showToast !== false) toast(`${configSectionLabels[section] || '配置'}已保存`);
  loadActivityLogs().catch(err => console.warn('日志加载失败', err));
}

function setMoviePilotBadge(text, ok = false) {
  const badge = document.getElementById('moviepilot-status-badge');
  if (badge) {
    badge.textContent = text;
    badge.className = ok ? 'badge ok' : 'badge warn';
  }
  updateSettingsMonitor();
}

function moviePilotConfiguredFromForm() {
  return Boolean(
    String(document.getElementById('ENV_MOVIEPILOT_URL')?.value || '').trim()
    && String(document.getElementById('ENV_MOVIEPILOT_API_TOKEN')?.value || '').trim()
  );
}

async function checkMoviePilotStatus(showToast = true, options = {}) {
  if (options.saveBeforeCheck !== false) await saveConfig(false, 'moviepilot');
  setMoviePilotBadge('检测中', false);
  const data = await api('/api/moviepilot/status', { timeoutMs: 15000 });
  const ok = Boolean(data.configured && data.ok !== false);
  setMoviePilotBadge(ok ? '已连接' : (data.configured ? '连接异常' : '未配置'), ok);
  if (showToast) toast(data.message || (ok ? 'MoviePilot 连接正常' : 'MoviePilot 未配置'));
  return data;
}

async function pushMoviePilotSubscription(item, options = {}) {
  const payload = { item, auto: Boolean(options.auto) };
  const data = await api('/api/moviepilot/subscribe', {
    method: 'POST',
    body: JSON.stringify(payload),
    timeoutMs: 30000,
  });
  if (data.pushed) {
    toast(data.message || `已推送 MoviePilot：${data.title || item.title || ''}`);
  } else if (!options.auto && data.skipped) {
    toast(data.skipped);
  }
  return data;
}

function setTorraBadge(text, ok = false) {
  const badge = document.getElementById('torra-status-badge');
  if (badge) {
    badge.textContent = text;
    badge.className = ok ? 'badge ok' : 'badge warn';
  }
  updateSettingsMonitor();
}

function setSymediaBadge(text, ok = false) {
  const badge = document.getElementById('symedia-status-badge');
  if (badge) {
    badge.textContent = text;
    badge.className = ok ? 'badge ok' : 'badge warn';
  }
  updateSettingsMonitor();
}

function torraConfiguredFromForm() {
  return Boolean(
    String(document.getElementById('ENV_TORRA_URL')?.value || '').trim()
    && String(document.getElementById('ENV_TORRA_TOKEN')?.value || '').trim()
  );
}

async function checkTorraStatus(showToast = true, options = {}) {
  if (options.saveBeforeCheck !== false) await saveConfig(false, 'torra');
  setTorraBadge('检测中', false);
  const data = await api('/api/torra/status', { timeoutMs: 15000 });
  const ok = Boolean(data.configured && data.ok !== false);
  setTorraBadge(ok ? '已连接' : (data.configured ? '连接异常' : '未配置'), ok);
  if (showToast) toast(data.message || (ok ? 'Torra 连接正常' : 'Torra 未配置'));
  return data;
}

function symediaConfiguredFromForm() {
  const url = String(document.getElementById('ENV_SYMEDIA_URL')?.value || '').trim();
  const token = String(document.getElementById('ENV_SYMEDIA_TOKEN')?.value || '').trim();
  const username = String(document.getElementById('ENV_SYMEDIA_USERNAME')?.value || '').trim();
  const password = String(document.getElementById('ENV_SYMEDIA_PASSWORD')?.value || '').trim();
  return Boolean(url && (token || (username && password)));
}

async function checkSymediaStatus(showToast = true, options = {}) {
  if (options.saveBeforeCheck !== false) await saveConfig(false, 'symedia');
  setSymediaBadge('检测中', false);
  const data = await api('/api/symedia/status', { timeoutMs: 15000 });
  const ok = Boolean(data.configured && data.ok !== false);
  setSymediaBadge(ok ? '已连接' : (data.configured ? '连接异常' : '未配置'), ok);
  if (showToast) toast(data.message || (ok ? 'Symedia 连接正常' : 'Symedia 未配置'));
  return data;
}

function updatePushConnectionBadges() {
  setMoviePilotBadge(moviePilotConfiguredFromForm() ? '待检测' : '未配置', false);
  setTorraBadge(torraConfiguredFromForm() ? '待检测' : '未配置', false);
  setSymediaBadge(symediaConfiguredFromForm() ? '待检测' : '未配置', false);
  updateSettingsMonitor();
}

function autoCheckPushConnections() {
  window.clearTimeout(pushStatusAutoCheckTimer);
  pushStatusAutoCheckTimer = window.setTimeout(() => {
    if (moviePilotConfiguredFromForm()) {
      checkMoviePilotStatus(false, { saveBeforeCheck: false }).catch(err => {
        setMoviePilotBadge('连接失败', false);
        console.warn('MoviePilot 状态检测失败', err);
      });
    }
    if (torraConfiguredFromForm()) {
      checkTorraStatus(false, { saveBeforeCheck: false }).catch(err => {
        setTorraBadge('连接失败', false);
        console.warn('Torra 状态检测失败', err);
      });
    }
    if (symediaConfiguredFromForm()) {
      checkSymediaStatus(false, { saveBeforeCheck: false }).catch(err => {
        setSymediaBadge('连接失败', false);
        console.warn('Symedia 状态检测失败', err);
      });
    }
  }, 300);
}

async function pushTorraSubscription(item, options = {}) {
  const payload = { item, auto: Boolean(options.auto) };
  const data = await api('/api/torra/subscribe', {
    method: 'POST',
    body: JSON.stringify(payload),
    timeoutMs: 30000,
  });
  if (data.pushed) {
    toast(data.message || `已推送 Torra：${data.title || item.title || ''}`);
  } else if (!options.auto && data.skipped) {
    toast(data.skipped);
  }
  return data;
}

async function pushSymediaSubscription(item, options = {}) {
  const payload = { item, auto: Boolean(options.auto) };
  const data = await api('/api/symedia/subscribe', {
    method: 'POST',
    body: JSON.stringify(payload),
    timeoutMs: 30000,
  });
  if (data.pushed) {
    toast(data.message || `已推送 Symedia：${data.title || item.title || ''}`);
  } else if (!options.auto && data.skipped) {
    toast(data.skipped);
  }
  return data;
}

async function pushMySubscriptionToMoviePilot(key, button = null) {
  const item = subscriptionItemByKey(key);
  if (!item) {
    toast('没有找到订阅条目');
    return;
  }
  const oldText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = '推送中';
  }
  try {
    const data = await pushMoviePilotSubscription(item);
    if (!data.pushed && data.message) toast(data.message);
  } catch (err) {
    toast(`MoviePilot 推送失败：${err.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
    }
  }
}

async function pushMySubscriptionToTorra(key, button = null) {
  const item = subscriptionItemByKey(key);
  if (!item) {
    toast('没有找到订阅条目');
    return;
  }
  const oldText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = '推送中';
  }
  try {
    const data = await pushTorraSubscription(item);
    if (!data.pushed && data.message) toast(data.message);
  } catch (err) {
    toast(`Torra 推送失败：${err.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
    }
  }
}

async function pushMySubscriptionToSymedia(key, button = null) {
  const item = subscriptionItemByKey(key);
  if (!item) {
    toast('没有找到订阅条目');
    return;
  }
  const oldText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = '推送中';
  }
  try {
    const data = await pushSymediaSubscription(item);
    if (!data.pushed && data.message) toast(data.message);
  } catch (err) {
    toast(`Symedia 推送失败：${err.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldText;
    }
  }
}

function channelInputValue(item) {
  const username = String(item.username || '').trim();
  if (username) return `@${username.replace(/^@/, '')}`;
  return String(item.input || item.id || item.name || '').trim();
}

function normalizeTelegramChannelMode(value) {
  const key = String(value || '').trim().toLowerCase();
  if (telegramChannelModes.some(item => item.key === key)) return key;
  const byLabel = telegramChannelModes.find(item => item.label === String(value || '').trim());
  return byLabel?.key || 'incoming';
}

function telegramChannelModeLabel(value) {
  return telegramChannelModes.find(item => item.key === normalizeTelegramChannelMode(value))?.label || '入新';
}

function telegramChannelModeTip(value) {
  return telegramChannelModes.find(item => item.key === normalizeTelegramChannelMode(value))?.tip || '';
}

function normalizeTelegramChannelItem(item = {}) {
  const enabledValue = item.enabled;
  return {
    ...item,
    mode: normalizeTelegramChannelMode(item.mode),
    enabled: enabledValue !== false && !['0', 'false', 'off', 'disabled'].includes(String(enabledValue ?? '').toLowerCase()),
  };
}

function syncTelegramChannelInput(channels = telegramChannels) {
  const input = document.getElementById('telegram-channel-input');
  if (!input) return;
  input.value = channels.map(channelInputValue).filter(Boolean).join('\n');
}

function telegramChannelTitle(item) {
  return String(item.name || item.username || item.input || item.id || '-').trim() || '-';
}

function telegramChannelInitial(item) {
  const title = telegramChannelTitle(item);
  return title.slice(0, 1).toUpperCase();
}

function telegramChannelAvatar(item) {
  const photoUrl = String(item.photo_url || item.avatar_url || item.icon_url || '').trim();
  if (photoUrl) return `<img src="${escapeHtml(photoUrl)}" alt="">`;
  return escapeHtml(telegramChannelInitial(item));
}

function renderTelegramChannelList(channels = telegramChannels) {
  const list = document.getElementById('telegram-channel-list');
  if (!list) return;
  if (!channels.length) {
    list.innerHTML = '<div class="telegram-empty">还没有保存频道</div>';
    return;
  }
  list.innerHTML = channels.map((item, index) => `
    <div class="telegram-channel-row" data-telegram-channel-index="${index}">
      <div>
        <strong>${escapeHtml(telegramChannelTitle(item))}</strong>
        <span>${escapeHtml([item.username ? `@${item.username}` : '', item.id || ''].filter(Boolean).join(' · '))}</span>
      </div>
      <div class="telegram-channel-actions">
        <button class="ghost" type="button" data-telegram-channel-action="up" ${index === 0 ? 'disabled' : ''}>上移</button>
        <button class="ghost" type="button" data-telegram-channel-action="down" ${index === channels.length - 1 ? 'disabled' : ''}>下移</button>
        <button class="ghost danger" type="button" data-telegram-channel-action="delete">删除</button>
      </div>
    </div>
  `).join('');
}

function renderTelegramChannelBoard(channels = telegramChannels) {
  const board = document.getElementById('telegram-dark-board');
  const authorized = document.getElementById('telegram-auth-strip')?.classList.contains('authorized');
  if (!board) return;
  board.hidden = !authorized;
  if (!authorized) {
    board.innerHTML = '';
    return;
  }
  const rows = channels.map((rawItem, index) => {
    const item = normalizeTelegramChannelItem(rawItem);
    const enabled = item.enabled !== false;
    const mode = normalizeTelegramChannelMode(item.mode);
    return `
    <div class="telegram-dark-row${enabled ? '' : ' disabled'}" data-telegram-channel-index="${index}">
      <div class="telegram-dark-channel">
        <span class="telegram-dark-avatar" aria-hidden="true">${telegramChannelAvatar(item)}</span>
        <strong>${escapeHtml(telegramChannelTitle(item))}</strong>
        <button class="telegram-dark-switch${enabled ? ' on' : ''}" type="button" data-telegram-channel-toggle title="${enabled ? '停用频道' : '启用频道'}" aria-label="${enabled ? '停用频道' : '启用频道'}"></button>
      </div>
      <div class="telegram-dark-mode">
        ${telegramChannelModes.map(def => `
          <button class="${def.key === mode ? 'active' : ''}" type="button" data-telegram-channel-mode="${escapeHtml(def.key)}" title="${escapeHtml(def.tip)}">${escapeHtml(def.label)}</button>
        `).join('')}
      </div>
      <button class="telegram-dark-icon" type="button" data-telegram-channel-action="delete" aria-label="删除">⊖</button>
      <button class="telegram-dark-icon" type="button" data-telegram-channel-action="down" ${index === channels.length - 1 ? 'disabled' : ''} aria-label="下移">⇅</button>
    </div>
  `;
  }).join('');
  const addForm = telegramQuickAddOpen ? `
    <div class="telegram-add-form">
      <input id="telegram-channel-quick-input" placeholder="@channel_name / https://t.me/channel_name / -1001234567890">
      <button type="button" data-telegram-channel-save>保存</button>
      <button class="ghost" type="button" data-telegram-channel-cancel>取消</button>
    </div>
  ` : '';
  board.innerHTML = `${rows}${addForm}<button class="telegram-add-channel" type="button" data-telegram-channel-add>＋ 添加频道</button>`;
  if (telegramQuickAddOpen) document.getElementById('telegram-channel-quick-input')?.focus();
}

function renderTelegramChannels(channels = telegramChannels) {
  telegramChannels = (channels || []).map(normalizeTelegramChannelItem);
  renderTelegramChannelList(telegramChannels);
  renderTelegramChannelBoard(telegramChannels);
}

function setTelegramAuthorized(authorized, user) {
  const badge = document.getElementById('telegram-auth-badge');
  const session = document.getElementById('telegram-session');
  const current = document.getElementById('telegram-current-user');
  const strip = document.getElementById('telegram-auth-strip');
  const pill = document.getElementById('telegram-login-pill');
  const phoneInput = document.getElementById('ENV_TG_PHONE');
  const logoutButton = document.getElementById('telegram-logout');
  const codeInput = document.getElementById('telegram-code');
  if (badge) {
    badge.textContent = authorized ? '已登录' : '未登录';
    badge.className = authorized ? 'badge ok' : 'badge warn';
  }
  if (authorized) telegramCodeRequested = false;
  if (session) session.hidden = !authorized;
  const display = user?.display || user?.username || '-';
  if (current) current.textContent = display;
  if (strip) strip.classList.toggle('authorized', Boolean(authorized));
  if (pill) pill.hidden = !authorized;
  if (logoutButton) logoutButton.hidden = !authorized;
  if (authorized && codeInput) codeInput.value = '';
  if (authorized && phoneInput && display !== '-') {
    phoneInput.value = display;
  }
  updateTelegramAuthAction();
  renderTelegramChannelBoard(telegramChannels);
  updateSettingsMonitor();
}

function telegramStoredUserFromConfig(cfg = null) {
  const source = cfg ? cfg.ENV_TG_PHONE : document.getElementById('ENV_TG_PHONE')?.value;
  const value = String(source || '').trim();
  if (!value || value.startsWith('+') || /^\d+$/.test(value)) return null;
  return { display: value };
}

function applyTelegramStoredUserFallback(cfg = null) {
  const user = telegramStoredUserFromConfig(cfg);
  if (user) setTelegramAuthorized(true, user);
}

function setTelegramCodeStep(visible) {
  telegramCodeRequested = Boolean(visible);
  updateTelegramAuthAction();
}

function updateTelegramAuthAction() {
  const action = document.getElementById('telegram-auth-action');
  const code = String(document.getElementById('telegram-code')?.value || '').trim();
  const phone = String(document.getElementById('ENV_TG_PHONE')?.value || '').trim();
  const apiId = String(document.getElementById('ENV_TG_API_ID')?.value || '').trim();
  const apiHash = String(document.getElementById('ENV_TG_API_HASH')?.value || '').trim();
  if (!action) return;
  const readyToLogin = Boolean(code);
  const readyToSendCode = Boolean(phone && apiId && apiHash);
  action.textContent = readyToLogin ? '确认登录' : '获取验证码';
  action.classList.toggle('ready', readyToLogin || readyToSendCode);
  action.title = readyToLogin
    ? '使用验证码登录'
    : (readyToSendCode ? '发送 Telegram 验证码' : '请先填写手机号、App api_id 和 App api_hash');
}

function setTgApiModal(open) {
  const modal = document.getElementById('tg-api-modal');
  if (!modal) return;
  modal.hidden = !open;
  if (open) document.getElementById('ENV_TG_API_ID')?.focus();
}

async function saveTelegramCompactConfig(showToast = true) {
  const payload = collectConfig();
  payload.__section = 'telegram';
  await api('/api/config', { method: 'POST', body: JSON.stringify(payload) });
  if (showToast) toast('Telegram 配置已保存');
  loadActivityLogs().catch(err => console.warn('日志加载失败', err));
}

async function refreshTelegramStatus(showToast = false) {
  try {
    const data = await api('/api/telegram/status', { timeoutMs: 12000 });
    const fallbackUser = telegramStoredUserFromConfig();
    setTelegramAuthorized(Boolean(data.authorized) || Boolean(fallbackUser), data.user || fallbackUser || null);
    renderTelegramChannels(data.channels || []);
    syncTelegramChannelInput(data.channels || []);
    if (showToast) toast('Telegram 状态已刷新');
  } catch (err) {
    const fallbackUser = telegramStoredUserFromConfig();
    setTelegramAuthorized(Boolean(fallbackUser), fallbackUser || null);
    if (showToast) toast(`Telegram 状态刷新失败：${err.message}`);
  } finally {
    loadActivityLogs().catch(logErr => console.warn('日志加载失败', logErr));
    updateSettingsMonitor();
  }
}

async function sendTelegramCode() {
  const action = document.getElementById('telegram-auth-action');
  const payload = {
    phone: String(document.getElementById('ENV_TG_PHONE')?.value || '').trim(),
    api_id: String(document.getElementById('ENV_TG_API_ID')?.value || '').trim(),
    api_hash: String(document.getElementById('ENV_TG_API_HASH')?.value || '').trim(),
  };
  if (!payload.phone) {
    document.getElementById('ENV_TG_PHONE')?.focus();
    toast('请先输入手机号');
    return;
  }
  if (!payload.api_id || !payload.api_hash) {
    setTgApiModal(true);
    toast('请先填写 App api_id 和 App api_hash');
    return;
  }
  if (action) {
    action.disabled = true;
    action.classList.add('ready', 'loading');
    action.textContent = '发送中...';
  }
  try {
    await saveTelegramCompactConfig(false);
    await api('/api/telegram/send-code', { method: 'POST', body: JSON.stringify(payload), timeoutMs: 60000 });
    telegramCodeRequested = true;
    setTelegramAuthorized(false, null);
    document.getElementById('telegram-code')?.focus();
    toast('验证码已发送');
  } finally {
    if (action) {
      action.disabled = false;
      action.classList.remove('loading');
      updateTelegramAuthAction();
    }
    loadActivityLogs().catch(err => console.warn('日志加载失败', err));
  }
}

async function loginTelegram() {
  const payload = {
    code: document.getElementById('telegram-code')?.value || '',
  };
  const data = await api('/api/telegram/sign-in', { method: 'POST', body: JSON.stringify(payload) });
  telegramCodeRequested = false;
  setTelegramAuthorized(true, data.user || null);
  document.getElementById('telegram-code').value = '';
  toast('Telegram 登录成功');
  await refreshTelegramStatus(false);
}

async function handleTelegramAuthAction() {
  try {
    const code = String(document.getElementById('telegram-code')?.value || '').trim();
    if (code) {
      await loginTelegram();
    } else {
      await sendTelegramCode();
    }
  } catch (err) {
    toast(`Telegram 操作失败：${err.message}`);
  }
}

async function logoutTelegram() {
  telegramCodeRequested = false;
  telegramQuickAddOpen = false;
  const phone = document.getElementById('ENV_TG_PHONE');
  const code = document.getElementById('telegram-code');
  if (phone) phone.value = '';
  if (code) code.value = '';
  renderTelegramChannels([]);
  setTelegramAuthorized(false, null);
  toast('已退出 Telegram 登录');
  try {
    await api('/api/config', { method: 'POST', body: JSON.stringify({ ENV_TG_PHONE: '' }) });
    await api('/api/telegram/logout', { method: 'POST', body: '{}' });
  } catch (err) {
    toast(`已清除本地登录状态，远端退出失败：${err.message}`);
  }
}

async function saveTelegramChannels(lines = null, options = {}) {
  const input = document.getElementById('telegram-channel-input');
  const channels = lines || String(input?.value || '').split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const payload = { channels };
  if (options.resolve === false) payload.resolve = false;
  const data = await api('/api/telegram/channels', { method: 'POST', body: JSON.stringify(payload) });
  renderTelegramChannels(data.channels || []);
  syncTelegramChannelInput(data.channels || []);
  if (options.toast !== false) toast('频道已保存');
  return data;
}

async function saveTelegramChannelSettings(message = '频道模式已保存') {
  const data = await saveTelegramChannels(telegramChannels.map(normalizeTelegramChannelItem), { resolve: false, toast: false });
  if (message) toast(message);
  return data;
}

async function saveTelegramQuickChannel() {
  const input = document.getElementById('telegram-channel-quick-input');
  const value = String(input?.value || '').trim();
  if (!value) {
    input?.focus();
    toast('请输入频道用户名、链接或 ID');
    return;
  }
  const existing = telegramChannels.map(channelInputValue).filter(Boolean);
  telegramQuickAddOpen = false;
  await saveTelegramChannels([...existing, value]);
}

async function mutateTelegramChannel(index, action) {
  let data;
  if (action === 'delete') {
    data = await api(`/api/telegram/channels/${index}`, { method: 'DELETE' });
  } else {
    const to = action === 'up' ? index - 1 : index + 1;
    data = await api('/api/telegram/channels/reorder', {
      method: 'POST',
      body: JSON.stringify({ from: index, to }),
    });
  }
  renderTelegramChannels(data.channels || []);
  syncTelegramChannelInput(data.channels || []);
  toast(action === 'delete' ? '频道已删除' : '频道排序已更新');
}

function applyMediaCategoryField(value) {
  const selected = new Set(String(value || '').split(',').map(item => item.trim()).filter(Boolean));
  document.querySelectorAll('[data-media-category]').forEach(input => {
    input.checked = selected.size ? selected.has(input.value) : true;
  });
  syncMediaCategoryField();
}

function renderMediaCategoryOptions(options, selectedValue = '') {
  const menu = document.getElementById('media-category-list');
  if (!menu) return;
  const selected = new Set(String(selectedValue || document.getElementById('ENV_MEDIA_SYNC_CATEGORIES')?.value || '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean));
  const rows = (options && options.length ? options : fallbackMediaCategories)
    .map(item => Array.isArray(item) ? { id: item[0], name: item[1] } : item)
    .filter(item => item.id && item.name);
  menu.innerHTML = rows.map(item => `
    <label><span>${escapeHtml(item.name)}</span><input type="checkbox" value="${escapeHtml(item.id)}" data-media-category ${selected.size ? (selected.has(String(item.id)) ? 'checked' : '') : 'checked'}></label>
  `).join('');
  menu.querySelectorAll('[data-media-category]').forEach(input => {
    input.addEventListener('change', syncMediaCategoryField);
  });
  syncMediaCategoryField();
}

function syncMediaCategoryField() {
  const field = document.getElementById('ENV_MEDIA_SYNC_CATEGORIES');
  if (!field) return;
  field.value = Array.from(document.querySelectorAll('[data-media-category]:checked'))
    .map(input => input.value)
    .join(',');
  updateMediaCategorySummary();
}

function updateMediaCategorySummary() {
  const summary = document.getElementById('media-category-summary');
  if (!summary) return;
  const checked = Array.from(document.querySelectorAll('[data-media-category]:checked'));
  if (!checked.length) {
    summary.textContent = '请选择媒体库';
    return;
  }
  const labels = checked.map(input => input.closest('label')?.querySelector('span')?.textContent?.trim() || input.value);
  summary.textContent = labels.length <= 3 ? labels.join('、') : `已选择 ${labels.length} 个媒体库`;
}

function setSettingsMonitorValue(id, text, state = '') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = state || '';
}

function nextSettingsPollTime() {
  const channelMinutes = Number(document.getElementById('ENV_CHECK_INTERVAL')?.value || 0) || 5;
  const subscriptionMinutes = Number(document.getElementById('ENV_SUBSCRIPTION_SEARCH_INTERVAL')?.value || 0) || 5;
  const minutes = Math.min(Math.max(1, channelMinutes), Math.max(1, subscriptionMinutes));
  const next = new Date(Date.now() + Math.max(1, minutes) * 60 * 1000);
  return `${String(next.getHours()).padStart(2, '0')}:${String(next.getMinutes()).padStart(2, '0')}`;
}

function updateSettingsMonitor() {
  setSettingsMonitorValue('settings-monitor-core', '运行中', 'ok');
  const cookieReady = Boolean(String(document.getElementById('ENV_115_COOKIES')?.value || '').trim());
  setSettingsMonitorValue('settings-monitor-115', cookieReady ? '已配置' : '待配置', cookieReady ? 'ok' : 'warn');

  const tgAuthorized = document.getElementById('telegram-auth-strip')?.classList.contains('authorized');
  const botReady = Boolean(String(document.getElementById('ENV_TG_BOT_TOKEN')?.value || '').trim());
  setSettingsMonitorValue(
    'settings-monitor-tg',
    tgAuthorized ? '已登录' : (botReady ? 'Bot 已配置' : '未配置'),
    (tgAuthorized || botReady) ? 'ok' : 'warn',
  );

  const mpText = document.getElementById('moviepilot-status-badge')?.textContent?.trim() || '';
  const trText = document.getElementById('torra-status-badge')?.textContent?.trim() || '';
  const syText = document.getElementById('symedia-status-badge')?.textContent?.trim() || '';
  const pushOk = [mpText, trText, syText].some(text => text.includes('已连接'));
  const pushWarn = [mpText, trText, syText].some(text => text.includes('未配置') || text.includes('失败') || text.includes('异常'));
  setSettingsMonitorValue('settings-monitor-push', pushOk ? '已连接' : (pushWarn ? '待配置' : '待检测'), pushOk ? 'ok' : 'warn');
  setSettingsMonitorValue('settings-monitor-next', nextSettingsPollTime(), '');
}

async function saveAllSettings() {
  const button = document.getElementById('settings-save-all');
  const oldText = button?.textContent || '';
  if (button) {
    button.disabled = true;
    button.textContent = '保存中';
  }
  try {
    await saveConfig(false, 'settings');
    await saveHDHiveConfig(false);
    await Promise.allSettled([
      refreshHDHiveStatus(false),
      refreshTelegramStatus(false),
      moviePilotConfiguredFromForm() ? checkMoviePilotStatus(false, { saveBeforeCheck: false }) : Promise.resolve(),
      torraConfiguredFromForm() ? checkTorraStatus(false, { saveBeforeCheck: false }) : Promise.resolve(),
      symediaConfiguredFromForm() ? checkSymediaStatus(false, { saveBeforeCheck: false }) : Promise.resolve(),
    ]);
    updateSettingsMonitor();
    toast('所有系统设置已保存');
  } catch (err) {
    toast(`保存系统设置失败：${err.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldText || '保存所有更改';
    }
  }
}

async function resetActiveSettingsPanel() {
  const button = document.getElementById('settings-reset-active');
  const oldText = button?.textContent || '';
  if (button) {
    button.disabled = true;
    button.textContent = '重置中';
  }
  try {
    await loadConfig();
    const activeTarget = document.querySelector('.settings-tab.active')?.dataset.settingsTarget;
    if (activeTarget) setSettingsPanel(activeTarget);
    updateSettingsMonitor();
    toast('已恢复已保存设置');
  } catch (err) {
    toast(`重置失败：${err.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldText || '重置';
    }
  }
}

function setMediaCategoryMenu(open) {
  const menu = document.getElementById('media-category-list');
  const trigger = document.getElementById('media-category-trigger');
  if (!menu || !trigger) return;
  menu.hidden = !open;
  trigger.setAttribute('aria-expanded', String(open));
}

function setSettingsPanel(target) {
  document.querySelectorAll('.settings-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.settingsTarget === target);
  });
  document.querySelectorAll('.settings-panel').forEach(panel => {
    panel.classList.toggle('active', panel.dataset.settingsPanel === target);
  });
  if (target === 'hdhive') refreshHDHiveStatus(false);
  if (target === 'telegram') refreshTelegramStatus(false);
  updateSettingsMonitor();
}

function setActiveView(view) {
  const btn = document.querySelector(`.nav-item[data-view="${view}"]`);
  const meta = views[view] || { title: btn?.textContent?.trim() || '', subtitle: '' };
  document.body.classList.toggle('subscription-view-active', view === 'subscription');
  document.body.classList.toggle('settings-view-active', view === 'settings');
  document.querySelectorAll('.nav-item').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.view').forEach(x => x.classList.remove('active'));
  btn?.classList.add('active');
  document.getElementById(`view-${view}`)?.classList.add('active');
  document.getElementById('page-title').textContent = meta.title;
  document.getElementById('page-subtitle').textContent = meta.subtitle;
  if (view === 'dashboard') {
    loadDashboardStatus();
    startDashboardSystemLive();
  } else {
    stopDashboardSystemLive();
  }
  if (view === 'discover') initDiscoverPage();
  if (view === 'subscription') loadSubscriptionConfig().catch(err => toast(`订阅配置加载失败：${err.message}`));
  if (view === 'my-subscription') loadMySubscriptions().catch(err => toast(`订阅列表加载失败：${err.message}`));
  if (view === 'activity-log') loadActivityLogs().catch(err => toast(`日志加载失败：${err.message}`));
  if (view === 'settings') updateSettingsMonitor();
}

function syncPageHeaderFromCurrentView() {
  const activeBtn = document.querySelector('.nav-item.active');
  const activeLabel = activeBtn?.textContent?.trim() || '';
  const activeView = activeBtn?.dataset.view || '';
  const dashboardNode = document.querySelector('#view-dashboard.active, .dashboard-page, .emby-dashboard-wrap, .emby-dashboard-title');
  const hasDashboardContent = Boolean(
    dashboardNode && (dashboardNode.closest('.active') || dashboardNode.offsetParent !== null)
  );
  const isDashboard = activeView === 'dashboard' || activeLabel === '仪表盘' || activeLabel === '数据总览' || hasDashboardContent;
  const meta = isDashboard ? views.dashboard : views[activeView];
  if (!meta) return;
  document.getElementById('page-title').textContent = meta.title;
  document.getElementById('page-subtitle').textContent = meta.subtitle;
}

document.querySelectorAll('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => {
    setActiveView(btn.dataset.view);
    syncPageHeaderFromCurrentView();
    logActivityEvent('switch_view', `切换页面：${btn.textContent?.trim() || btn.dataset.view}`, {
      view: btn.dataset.view,
      label: btn.textContent?.trim() || '',
    });
  });
});

setActiveView(document.querySelector('.nav-item.active')?.dataset.view || 'dashboard');
syncPageHeaderFromCurrentView();
setTheme(activeTheme(), false);
document.getElementById('theme-toggle')?.addEventListener('click', () => {
  setTheme(activeTheme() === 'light' ? 'dark' : 'light');
});
setTimeout(syncPageHeaderFromCurrentView, 0);
setTimeout(syncPageHeaderFromCurrentView, 500);

document.getElementById('save-config')?.addEventListener('click', () => saveConfig(true, 'settings'));
document.getElementById('save-clean-config')?.addEventListener('click', () => saveConfig(true, 'clean'));
document.getElementById('save-account-config')?.addEventListener('click', () => saveConfig(true, 'account'));
document.getElementById('save-library-config')?.addEventListener('click', () => saveConfig(true, 'library'));
document.getElementById('save-proxy-config')?.addEventListener('click', () => saveConfig(true, 'proxy'));
document.getElementById('settings-save-all')?.addEventListener('click', saveAllSettings);
document.getElementById('settings-reset-active')?.addEventListener('click', resetActiveSettingsPanel);
document.getElementById('save-moviepilot-config')?.addEventListener('click', async () => {
  try {
    await saveConfig(true, 'moviepilot');
    await checkMoviePilotStatus(false, { saveBeforeCheck: false });
  } catch (err) {
    setMoviePilotBadge('连接失败', false);
    toast(`MoviePilot 连接失败：${err.message}`);
  }
});
document.getElementById('moviepilot-check')?.addEventListener('click', async () => {
  try {
    await checkMoviePilotStatus(true);
  } catch (err) {
    setMoviePilotBadge('连接失败', false);
    toast(`MoviePilot 连接失败：${err.message}`);
  } finally {
    loadActivityLogs().catch(error => console.warn('日志加载失败', error));
  }
});
document.getElementById('save-torra-config')?.addEventListener('click', async () => {
  try {
    await saveConfig(true, 'torra');
    await checkTorraStatus(false, { saveBeforeCheck: false });
  } catch (err) {
    setTorraBadge('连接失败', false);
    toast(`Torra 连接失败：${err.message}`);
  }
});
document.getElementById('torra-check')?.addEventListener('click', async () => {
  try {
    await checkTorraStatus(true);
  } catch (err) {
    setTorraBadge('连接失败', false);
    toast(`Torra 连接失败：${err.message}`);
  } finally {
    loadActivityLogs().catch(error => console.warn('日志加载失败', error));
  }
});
document.getElementById('save-symedia-config')?.addEventListener('click', async () => {
  try {
    await saveConfig(true, 'symedia');
    await checkSymediaStatus(false, { saveBeforeCheck: false });
  } catch (err) {
    setSymediaBadge('连接失败', false);
    toast(`Symedia 连接失败：${err.message}`);
  }
});
document.getElementById('symedia-check')?.addEventListener('click', async () => {
  try {
    await checkSymediaStatus(true);
  } catch (err) {
    setSymediaBadge('连接失败', false);
    toast(`Symedia 连接失败：${err.message}`);
  } finally {
    loadActivityLogs().catch(error => console.warn('日志加载失败', error));
  }
});
document.getElementById('hdhive-save-config')?.addEventListener('click', saveHDHiveConfig);
document.getElementById('hdhive-run-checkin')?.addEventListener('click', runHDHiveCheckin);
document.getElementById('dashboard-refresh')?.addEventListener('click', loadDashboardStatus);
document.getElementById('telegram-auth-action')?.addEventListener('click', handleTelegramAuthAction);
document.getElementById('telegram-code')?.addEventListener('input', updateTelegramAuthAction);
document.getElementById('ENV_TG_PHONE')?.addEventListener('input', updateTelegramAuthAction);
document.getElementById('ENV_TG_API_ID')?.addEventListener('input', updateTelegramAuthAction);
document.getElementById('ENV_TG_API_HASH')?.addEventListener('input', updateTelegramAuthAction);
['ENV_MOVIEPILOT_URL', 'ENV_MOVIEPILOT_API_TOKEN', 'ENV_TORRA_URL', 'ENV_TORRA_TOKEN', 'ENV_SYMEDIA_URL', 'ENV_SYMEDIA_TOKEN', 'ENV_SYMEDIA_USERNAME', 'ENV_SYMEDIA_PASSWORD'].forEach(id => {
  document.getElementById(id)?.addEventListener('input', updatePushConnectionBadges);
});
['ENV_115_COOKIES', 'ENV_TG_BOT_TOKEN', 'ENV_CHECK_INTERVAL', 'ENV_SUBSCRIPTION_SEARCH_INTERVAL'].forEach(id => {
  document.getElementById(id)?.addEventListener('input', updateSettingsMonitor);
  document.getElementById(id)?.addEventListener('change', updateSettingsMonitor);
});
document.getElementById('telegram-code')?.addEventListener('keydown', event => {
  if (event.key === 'Enter') handleTelegramAuthAction();
});
document.getElementById('telegram-logout')?.addEventListener('click', logoutTelegram);
document.getElementById('telegram-appid-open')?.addEventListener('click', () => setTgApiModal(true));
document.getElementById('telegram-appid-confirm')?.addEventListener('click', async () => {
  const apiId = String(document.getElementById('ENV_TG_API_ID')?.value || '').trim();
  const apiHash = String(document.getElementById('ENV_TG_API_HASH')?.value || '').trim();
  if (!apiId || !apiHash) {
    toast('请填写 App api_id 和 App api_hash');
    document.getElementById(apiId ? 'ENV_TG_API_HASH' : 'ENV_TG_API_ID')?.focus();
    return;
  }
  try {
    const authorized = document.getElementById('telegram-auth-strip')?.classList.contains('authorized');
    const phone = String(document.getElementById('ENV_TG_PHONE')?.value || '').trim();
    await saveTelegramCompactConfig(false);
    updateTelegramAuthAction();
    setTgApiModal(false);
    if (authorized) {
      toast('Telegram 配置已保存');
      return;
    }
    if (!phone) {
      document.getElementById('ENV_TG_PHONE')?.focus();
      toast('APPID 已保存，请输入手机号后获取验证码');
      return;
    }
    try {
      await sendTelegramCode();
    } catch (err) {
      toast(`APPID 已保存，验证码发送失败：${err.message}`);
      document.getElementById('telegram-auth-action')?.focus();
    }
  } catch (err) {
    toast(`APPID 保存失败：${err.message}`);
  }
});
document.getElementById('ENV_CHECK_INTERVAL')?.addEventListener('change', saveTelegramCompactConfig);
document.getElementById('ENV_SUBSCRIPTION_SEARCH_INTERVAL')?.addEventListener('change', saveTelegramCompactConfig);
document.querySelectorAll('[data-tg-api-close]').forEach(el => {
  el.addEventListener('click', () => setTgApiModal(false));
});
document.getElementById('telegram-refresh')?.addEventListener('click', () => refreshTelegramStatus(true));
document.getElementById('telegram-save-channels')?.addEventListener('click', () => saveTelegramChannels());
document.getElementById('save-telegram-config')?.addEventListener('click', () => saveTelegramCompactConfig(true));
document.getElementById('subscription-save')?.addEventListener('click', saveSubscriptionConfig);
document.getElementById('subscription-run')?.addEventListener('click', runSubscriptionNow);
document.getElementById('subscription-daily-airing-sync')?.addEventListener('click', syncDailyAiringSubscriptions);
document.querySelectorAll('.activity-log-refresh').forEach(btn => btn.addEventListener('click', () => {
  activityLogState.level = 'all';
  activityLogState.category = 'all';
  loadActivityLogs(true);
}));
document.querySelectorAll('[data-activity-level]').forEach(btn => {
  btn.addEventListener('click', () => {
    activityLogState.level = btn.dataset.activityLevel || 'all';
    renderActivityLogs();
  });
});
document.getElementById('activity-log-category')?.addEventListener('change', event => {
  activityLogState.category = event.target.value || 'all';
  renderActivityLogs();
});
document.querySelectorAll('.activity-log-clear').forEach(btn => btn.addEventListener('click', () => {
  clearActivityLogs().catch(err => toast(`清空日志失败：${err.message}`));
}));
document.querySelectorAll('[data-view-jump]').forEach(btn => {
  btn.addEventListener('click', () => {
    setActiveView(btn.dataset.viewJump || 'dashboard');
    syncPageHeaderFromCurrentView();
  });
});
document.getElementById('my-subscription-refresh')?.addEventListener('click', () => loadMySubscriptions(true));
document.getElementById('my-subscription-clear')?.addEventListener('click', clearMySubscriptions);

document.querySelectorAll('.settings-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    setSettingsPanel(btn.dataset.settingsTarget);
    logActivityEvent('switch_settings_panel', `切换设置页：${btn.textContent?.trim() || btn.dataset.settingsTarget}`, {
      panel: btn.dataset.settingsTarget,
      label: btn.textContent?.trim() || '',
    });
  });
});

document.addEventListener('change', event => {
  if (event.target.closest('input[name="subscription-mode"]')) {
    syncSubscriptionModeLabel();
  }
  const source = event.target.closest('[data-subscription-source]');
  if (source) {
    source.closest('.subscription-check-row')?.classList.toggle('selected', source.checked);
    renderSubscriptionSources(selectedSubscriptionSources());
  }
});

document.querySelectorAll('[data-media-category]').forEach(input => {
  input.addEventListener('change', syncMediaCategoryField);
});

document.getElementById('load-emby-libraries')?.addEventListener('click', async () => {
  const btn = document.getElementById('load-emby-libraries');
  if (btn) btn.disabled = true;
  try {
    const payload = {
      server_url: document.getElementById('ENV_EMBY_SERVER_URL')?.value || '',
      api_key: document.getElementById('ENV_EMBY_API_KEY')?.value || '',
      username: document.getElementById('ENV_MEDIA_LIBRARY_ADMIN')?.value || '',
      password: document.getElementById('ENV_MEDIA_LIBRARY_PASSWORD')?.value || '',
    };
    const data = await api('/api/emby/libraries', { method: 'POST', body: JSON.stringify(payload) });
    renderMediaCategoryOptions(data.libraries || [], document.getElementById('ENV_MEDIA_SYNC_CATEGORIES')?.value || '');
    setMediaCategoryMenu(true);
    toast(`已获取 ${data.libraries?.length || 0} 个媒体库`);
  } finally {
    if (btn) btn.disabled = false;
  }
});

document.getElementById('media-category-trigger')?.addEventListener('click', event => {
  event.stopPropagation();
  const menu = document.getElementById('media-category-list');
  setMediaCategoryMenu(Boolean(menu?.hidden));
});

document.getElementById('my-subscription-search')?.addEventListener('input', event => {
  mySubscriptionFilters.keyword = event.target.value.trim();
  renderSubscriptionPosterList();
});

document.addEventListener('click', event => {
  if (!event.target.closest('.media-category-picker')) setMediaCategoryMenu(false);
  if (event.target.closest('[data-hdhive-authorize]')) {
    window.open('/api/hdhive/authorize', '_blank', 'noopener,noreferrer');
    toast('影巢授权页已打开');
  }
});

document.getElementById('hdhive-authorize')?.addEventListener('click', async () => {
  window.open('/api/hdhive/authorize', '_blank', 'noopener,noreferrer');
  toast('影巢授权页已打开');
});

document.getElementById('hdhive-status')?.addEventListener('click', async () => {
  await refreshHDHiveStatus(true);
});

document.querySelectorAll('.hdhive-tab').forEach(btn => {
  btn.addEventListener('click', () => setHDHiveTab(btn.dataset.hdhiveTab));
});

function isHDHiveAuthorized(data) {
  const status = data?.status || {};
  const user = status.user || status.data?.user || status.account || {};
  const nickname = user.nickname || user.username || user.name || status.nickname || status.username;
  if (status.auth_required === true || status.code === 'REAUTH_REQUIRED') return false;
  return status.authorized === true || status.has_access_token === true || Boolean(nickname);
}

function formatHDHiveTime(seconds) {
  const value = Number(seconds || 0);
  if (!value) return '-';
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false });
}

function hdhiveAccountName(data) {
  const account = data?.account || {};
  if (account.display_name) return account.display_name;
  const status = data?.status || {};
  const user = status.user || status.data?.user || status.account || {};
  if (user.nickname || user.username || user.name) return user.nickname || user.username || user.name;
  return '影巢已授权账号';
}

function hdhiveAccountId(data) {
  const account = data?.account || {};
  if (account.short_hash) return `账号标识 ${account.short_hash}`;
  const status = data?.status || {};
  const hash = String(status.hdhive_user_hash || status.install_hash || data?.identity?.install_hash || '').trim();
  return hash ? hash.slice(0, 8) : '-';
}

function applyHDHiveConfig(cfg = {}) {
  for (const key of hdhiveConfigFields) {
    const el = document.getElementById(key);
    if (!el || !(key in cfg)) continue;
    if (el.type === 'checkbox') {
      el.checked = ['1', 'true', 'yes', 'on'].includes(String(cfg[key] || '').toLowerCase());
    } else {
      el.value = cfg[key] || '';
    }
  }
}

function renderHDHiveAccount(data) {
  const list = document.getElementById('hdhive-account-list');
  if (!list) return;
  const authorized = isHDHiveAuthorized(data);
  if (!authorized) {
    list.innerHTML = `
      <button class="hdhive-authorize-card" type="button" data-hdhive-authorize>
        <span>♁</span>
        <strong>授权新账号</strong>
      </button>
    `;
    return;
  }
  const status = data.status || {};
  const name = hdhiveAccountName(data);
  const config = data.config || {};
  const checkinEnabled = ['1', 'true', 'yes', 'on'].includes(String(config.ENV_HDHIVE_CHECKIN_ENABLED || '').toLowerCase());
  list.innerHTML = `
    <article class="hdhive-account-card">
      <strong>${escapeHtml(name)}</strong>
      <span>${escapeHtml(hdhiveAccountId(data))}</span>
      <div class="hdhive-account-tags">
        <em>主账号</em>
        <em>签到开</em>
        <em>随询</em>
      </div>
      <p>有效期 ${escapeHtml(formatHDHiveTime(status.expires_at))}</p>
    </article>
    <button class="hdhive-authorize-card" type="button" data-hdhive-authorize>
      <span>♁</span>
      <strong>授权新账号</strong>
    </button>
  `;
}

function normalizeHDHiveAccountCard(list, status, checkinEnabled) {
  const tags = list?.querySelector('.hdhive-account-tags');
  if (tags) {
    tags.innerHTML = `<em>主账号</em><em>${checkinEnabled ? '签到开' : '签到关'}</em><em>OpenAPI</em>`;
  }
  const expireText = list?.querySelector('.hdhive-account-card p');
  if (expireText) expireText.textContent = `有效期 ${formatHDHiveTime(status?.expires_at)}`;
  list?.querySelectorAll('[data-hdhive-authorize] span').forEach(el => { el.textContent = '+'; });
  list?.querySelectorAll('[data-hdhive-authorize] strong').forEach(el => { el.textContent = '授权新账号'; });
}

function renderHDHiveStatus(data) {
  const authorized = isHDHiveAuthorized(data);
  const accountCount = document.getElementById('hdhive-account-count');
  const checkinText = document.getElementById('hdhive-checkin-account-text');
  const nextCheckin = document.getElementById('hdhive-next-checkin');
  const checkinMessage = document.getElementById('hdhive-checkin-message');
  const config = data?.config || {};
  const checkinEnabled = ['1', 'true', 'yes', 'on'].includes(String(config.ENV_HDHIVE_CHECKIN_ENABLED || '').toLowerCase());
  if (accountCount) {
    accountCount.textContent = authorized ? '1 个账号' : '0 个账号';
    accountCount.className = authorized ? 'badge ok' : 'badge warn';
  }
  if (checkinText) checkinText.textContent = authorized ? '1 个账号' : '0 个账号';
  if (nextCheckin) nextCheckin.textContent = data?.checkin_state?.next_checkin_at || '未启用';
  if (accountCount) accountCount.textContent = authorized ? '1 个账号' : '0 个账号';
  if (checkinText) checkinText.textContent = authorized ? '1 个账号' : '0 个账号';
  if (nextCheckin) nextCheckin.textContent = checkinEnabled ? (data?.checkin_state?.next_checkin_at || '等待调度') : '未启用';
  const state = data?.checkin_state || {};
  if (checkinMessage) {
    const result = state.last_checkin_result;
    const error = state.last_checkin_error;
    if (error) {
      checkinMessage.textContent = `${state.last_checkin_at || ''} ${error}`.trim();
    } else if (result) {
      checkinMessage.textContent = `${state.last_checkin_at || ''} ${result.message || result.description || JSON.stringify(result)}`.trim();
    } else {
      checkinMessage.textContent = authorized ? '暂无签到记录' : '请先授权影巢账号';
    }
  }
  applyHDHiveConfig(data?.config || {});
  renderHDHiveAccount(data || {});
  normalizeHDHiveAccountCard(document.getElementById('hdhive-account-list'), data?.status || {}, checkinEnabled);
}

async function refreshHDHiveStatus(showToast = false) {
  try {
    const data = await api('/api/hdhive/status');
    renderHDHiveStatus(data);
    if (showToast) toast('影巢授权状态已刷新');
  } catch (err) {
    renderHDHiveStatus({ ok: false, error: err.message, config: collectHDHiveConfig() });
    if (showToast) toast(`影巢状态刷新失败：${err.message}`);
  }
}

function collectHDHiveConfig() {
  const payload = {};
  for (const key of hdhiveConfigFields) {
    const el = document.getElementById(key);
    if (!el) continue;
    payload[key] = el.type === 'checkbox' ? (el.checked ? '1' : '0') : el.value;
  }
  return payload;
}

async function saveHDHiveConfig() {
  const data = await api('/api/hdhive/config', { method: 'POST', body: JSON.stringify(collectHDHiveConfig()) });
  applyHDHiveConfig(data.config || {});
  refreshHDHiveStatus(false).catch(err => console.warn('影巢状态刷新失败', err));
  toast('影巢配置已保存');
}

async function runHDHiveCheckin() {
  const btn = document.getElementById('hdhive-run-checkin');
  if (btn) btn.disabled = true;
  try {
    const data = await api('/api/hdhive/checkin', { method: 'POST', body: '{}' });
    renderHDHiveStatus({ ...(await api('/api/hdhive/status')), checkin_state: data.checkin_state });
    toast('影巢签到已执行');
  } catch (err) {
    await refreshHDHiveStatus(false);
    toast(`影巢签到失败：${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function setHDHiveTab(target) {
  document.querySelectorAll('.hdhive-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.hdhiveTab === target);
  });
  document.querySelectorAll('.hdhive-tab-panel').forEach(panel => {
    panel.classList.toggle('active', panel.dataset.hdhivePanel === target);
  });
}

document.getElementById('status-btn')?.addEventListener('click', async () => {
  const data = await api('/api/status');
  toast(data.ok ? '项目运行正常' : '项目状态异常');
});

document.addEventListener('click', event => {
  const target = event.target.closest('[data-dashboard-view]');
  if (target) {
    setActiveView(target.dataset.dashboardView);
    return;
  }
  const pageAction = event.target.closest('[data-dashboard-page]');
  if (!pageAction) return;
  const action = pageAction.dataset.dashboardPage;
  if (action === 'airing-prev' && dashboardState.airingPage > 1) {
    loadDashboardAiringPage(dashboardState.airingPage - 1).catch(err => toast(`今日播出加载失败：${err.message}`));
  } else if (action === 'airing-next' && dashboardState.airingPage < dashboardState.airingTotalPages) {
    loadDashboardAiringPage(dashboardState.airingPage + 1).catch(err => toast(`今日播出加载失败：${err.message}`));
  } else if (action === 'library-prev' && dashboardState.libraryPage > 1) {
    dashboardState.libraryPage -= 1;
    renderDashboardLibrary();
  } else if (action === 'library-next') {
    const totalPages = Math.max(1, Math.ceil(dashboardState.libraryItems.length / dashboardState.libraryPageSize));
    if (dashboardState.libraryPage < totalPages) {
      dashboardState.libraryPage += 1;
      renderDashboardLibrary();
    }
  }
});

function dashboardPoster(item = {}) {
  return posterUrlFor(item) || subscriptionPoster(item);
}

function dashboardNextEpisodeText(item = {}) {
  const inLibrary = Number(item.library_episode_count || 0);
  const total = firstPositiveNumber(item.episode_total, item.total_episodes, item.episodes_total, item.episode_count);
  if (total > 0 && inLibrary >= total) return '已完整';
  const next = inLibrary + 1;
  if (next > 0) return `E${String(next).padStart(2, '0')}`;
  return '待更新';
}

function dashboardAiringCard(item = {}) {
  const poster = dashboardPoster(item);
  const season = subscriptionLatestSeason(item);
  const nextEpisode = dashboardNextEpisodeText(item);
  const inLibrary = Number(item.library_episode_count || 0);
  const total = firstPositiveNumber(item.episode_total, item.total_episodes, item.episodes_total, item.episode_count);
  const progress = total > 0 ? `${inLibrary}/${total}` : `${inLibrary}`;
  return `
    <article class="dashboard-airing-row">
      <div class="dashboard-airing-poster${poster ? ' has-image' : ''}">
        ${poster ? `<img src="${escapeHtml(poster)}" alt="${escapeHtml(item.title || '')}">` : `<span>${escapeHtml(item.title || '今日')}</span>`}
      </div>
      <div class="dashboard-airing-main">
        <h4 title="${escapeHtml(item.title || '')}">${escapeHtml(item.title || '-')}</h4>
        <p>${escapeHtml(season || '订阅剧集')} · 已入库 ${escapeHtml(progress)}</p>
      </div>
      <div class="dashboard-airing-episode">
        <span>待播</span>
        <strong>${escapeHtml(nextEpisode)}</strong>
      </div>
    </article>
  `;
}

function dashboardLibraryCard(item = {}) {
  const poster = dashboardPoster(item);
  const progress = subscriptionProgressText(item);
  const season = subscriptionLatestSeason(item);
  const inLibrary = Number(item.library_episode_count || 0);
  const total = firstPositiveNumber(item.episode_total, item.total_episodes, item.episodes_total, item.episode_count);
  const missing = total > 0 ? Math.max(0, total - inLibrary) : 0;
  const meta = [season, item.year || '', missing ? `缺 ${missing}` : '已完整'].filter(Boolean).join(' · ');
  return `
    <article class="dashboard-media-card">
      <div class="dashboard-media-poster${poster ? ' has-image' : ''}">
        ${poster ? `<img src="${escapeHtml(poster)}" alt="${escapeHtml(item.title || '')}">` : `<span>${escapeHtml(item.title || '订阅')}</span>`}
        <em>${escapeHtml(progress)}</em>
      </div>
      <h4 title="${escapeHtml(item.title || '')}">${escapeHtml(item.title || '-')}</h4>
      <p>${escapeHtml(meta)}</p>
    </article>
  `;
}

function formatDashboardBytes(value = 0) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let size = number;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size >= 10 || index === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[index]}`;
}

function formatDashboardSpeed(value = 0) {
  return `${formatDashboardBytes(value)}/s`;
}

function dashboardPercentCard(label, percent, detail = '') {
  const value = Number(percent || 0);
  const safe = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return `
    <div class="dashboard-system-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(safe.toFixed(1))}%</strong>
      <div class="dashboard-meter"><i style="width: ${safe}%"></i></div>
      ${detail ? `<p>${escapeHtml(detail)}</p>` : ''}
    </div>
  `;
}

function dashboardEmbyTypeLabel(type) {
  return {
    movies: '电影',
    tvshows: '剧集',
    music: '音乐',
    photos: '图片',
    books: '图书',
  }[String(type || '').toLowerCase()] || '';
}

function renderDashboardEmbyLibraries(emby = {}) {
  const list = document.getElementById('dashboard-emby-library-list');
  const subtitle = document.getElementById('dashboard-emby-subtitle');
  if (!list) return;
  const libraries = Array.isArray(emby.libraries) ? emby.libraries : [];
  if (subtitle) {
    subtitle.textContent = emby.ok
      ? `${emby.count || libraries.length || 0} 个媒体库`
      : (emby.error || '请在系统设置中配置 Emby');
  }
  if (!emby.ok) {
    list.innerHTML = `<div class="dashboard-empty">${escapeHtml(emby.error || 'Emby 未连接')}</div>`;
    return;
  }
  list.innerHTML = libraries.length ? libraries.map(item => {
    const name = item.name || item.Name || '';
    const label = dashboardEmbyTypeLabel(item.type || item.CollectionType || '');
    const poster = item.poster_url || item.image_url || '';
    const initial = name ? name.slice(0, 2) : '库';
    return `
      <article class="dashboard-emby-card">
        <div class="dashboard-emby-poster${poster ? ' has-image' : ''}">
          ${poster ? `<img src="${escapeHtml(poster)}" alt="${escapeHtml(name)}" loading="lazy" onerror="this.parentElement.classList.remove('has-image');this.remove();">` : ''}
          <span>${escapeHtml(initial)}</span>
        </div>
        <h4 title="${escapeHtml(name)}">${escapeHtml(name || '-')}</h4>
        <p>${escapeHtml(label || '媒体库')}</p>
      </article>
    `;
  }).join('') : '<div class="dashboard-empty">未获取到媒体库</div>';
}

function renderDashboardSystem(data = {}) {
  const grid = document.getElementById('dashboard-system-grid');
  const subtitle = document.getElementById('dashboard-system-subtitle');
  if (!grid) return;
  const memory = data.memory || {};
  const disk = data.disk || {};
  const network = data.network || {};
  const emby = data.emby || {};
  renderDashboardEmbyLibraries(emby);
  if (subtitle) {
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    subtitle.textContent = data.ok ? `实时更新中 · ${time}` : '设备状态读取失败。';
  }
  grid.innerHTML = `
    ${dashboardPercentCard('CPU', data.cpu?.percent || 0, '实时占用')}
    ${dashboardPercentCard('内存', memory.percent || 0, `${formatDashboardBytes(memory.used)} / ${formatDashboardBytes(memory.total)}`)}
    ${dashboardPercentCard('硬盘', disk.percent || 0, `${formatDashboardBytes(disk.used)} / ${formatDashboardBytes(disk.total)}`)}
    <div class="dashboard-system-card">
      <span>网速</span>
      <strong>↓ ${escapeHtml(formatDashboardSpeed(network.down_bps || 0))}</strong>
      <p>↑ ${escapeHtml(formatDashboardSpeed(network.up_bps || 0))}</p>
    </div>
  `;
}

async function loadDashboardSystemStatus() {
  if (dashboardSystemLoading) return null;
  dashboardSystemLoading = true;
  const subtitle = document.getElementById('dashboard-system-subtitle');
  if (subtitle) subtitle.textContent = '设备性能实时更新中...';
  try {
    const data = await api('/api/dashboard/system', { timeoutMs: 16000 });
    renderDashboardSystem(data);
    return data;
  } catch (err) {
    renderDashboardSystem({ ok: false, emby: { ok: false, error: err.message } });
    return null;
  } finally {
    dashboardSystemLoading = false;
  }
}

function stopDashboardSystemLive() {
  if (dashboardSystemTimer) {
    window.clearInterval(dashboardSystemTimer);
    dashboardSystemTimer = null;
  }
}

function startDashboardSystemLive() {
  stopDashboardSystemLive();
  dashboardSystemTimer = window.setInterval(() => {
    if (document.getElementById('view-dashboard')?.classList.contains('active')) {
      loadDashboardSystemStatus().catch(() => {});
    } else {
      stopDashboardSystemLive();
    }
  }, 5000);
}

function renderDashboardLibrary() {
  const list = document.getElementById('dashboard-library-list');
  const subtitle = document.getElementById('dashboard-library-subtitle');
  const pageLabel = document.getElementById('dashboard-library-page');
  if (!list) return;
  const items = dashboardState.libraryItems;
  const totalPages = Math.max(1, Math.ceil(items.length / dashboardState.libraryPageSize));
  dashboardState.libraryPage = Math.min(Math.max(1, dashboardState.libraryPage), totalPages);
  const start = (dashboardState.libraryPage - 1) * dashboardState.libraryPageSize;
  const rows = items.slice(start, start + dashboardState.libraryPageSize);
  if (subtitle) {
    const completed = items.filter(item => {
      const total = firstPositiveNumber(item.episode_total, item.total_episodes, item.episodes_total, item.episode_count);
      const current = Number(item.library_episode_count || 0);
      return total > 0 && current >= total;
    }).length;
    subtitle.textContent = `今天 ${items.length} 条入库/进度更新，${completed} 条完整入库。`;
  }
  if (pageLabel) pageLabel.textContent = `${dashboardState.libraryPage} / ${totalPages}`;
  list.innerHTML = rows.length ? rows.map(dashboardLibraryCard).join('') : '<div class="dashboard-empty">今日暂无入库数据</div>';
}

function dashboardTodayText() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

function dashboardIsTodayValue(value) {
  const text = String(value || '').trim();
  return Boolean(text && text.startsWith(dashboardTodayText()));
}

function dashboardTodayLibraryItems(items = []) {
  return items.filter(item => {
    const values = [
      item.library_updated_at,
      item.last_library_at,
      item.progress_updated_at,
      item.updated_at,
    ];
    return values.some(dashboardIsTodayValue);
  });
}

function dashboardSubscriptionKeyMap(items = []) {
  const map = new Map();
  items.forEach(item => {
    discoverSubscriptionLookupKeys(item).forEach(key => {
      if (!map.has(key)) map.set(key, item);
    });
  });
  return map;
}

function dashboardSubscribedDailyItems(dailyItems = [], subscriptions = []) {
  const subscriptionMap = dashboardSubscriptionKeyMap(subscriptions);
  const seen = new Set();
  return dailyItems
    .filter(item => item && item.airing_today && dashboardIsTodayValue(item.air_date))
    .map(item => {
      const matchedKey = discoverSubscriptionLookupKeys(item).find(key => subscriptionMap.has(key));
      if (!matchedKey) return null;
      const subscription = subscriptionMap.get(matchedKey) || {};
      const dedupeKey = matchedKey || `${item.tmdb_id || item.id || ''}:${normalizeSubscriptionLookupTitle(item.title || '')}`;
      if (seen.has(dedupeKey)) return null;
      seen.add(dedupeKey);
      return {
        ...subscription,
        ...item,
        library_episode_count: subscription.library_episode_count ?? item.library_episode_count,
        current_episode_count: subscription.current_episode_count ?? item.current_episode_count,
        progress_episode_count: subscription.progress_episode_count ?? item.progress_episode_count,
        total_episodes: item.total_episodes || item.episode_total || subscription.total_episodes,
        episode_total: item.episode_total || item.total_episodes || subscription.episode_total,
        current_season: item.current_season || subscription.current_season,
        latest_season: item.latest_season || subscription.latest_season,
      };
    })
    .filter(Boolean);
}

async function loadDashboardDailyAiringItems() {
  const rows = [];
  let page = 1;
  let totalPages = 1;
  do {
    const params = new URLSearchParams({
      timezone: 'Asia/Shanghai',
      page: String(page),
      limit: '24',
    });
    const data = await api(`/api/discover/daily-airing?${params.toString()}`, { timeoutMs: 20000 });
    rows.push(...(data.items || []));
    totalPages = Math.max(1, Number(data.total_pages || 1));
    page += 1;
  } while (page <= totalPages && page <= 8);
  return rows;
}

async function loadDashboardAiringPage(page = 1) {
  const list = document.getElementById('dashboard-airing-list');
  const subtitle = document.getElementById('dashboard-airing-subtitle');
  const pageLabel = document.getElementById('dashboard-airing-page');
  const items = dashboardState.airingItems;
  dashboardState.airingTotalPages = Math.max(1, Math.ceil(items.length / dashboardState.airingPageSize));
  dashboardState.airingPage = Math.min(Math.max(1, Number(page || 1)), dashboardState.airingTotalPages);
  const start = (dashboardState.airingPage - 1) * dashboardState.airingPageSize;
  const rows = items.slice(start, start + dashboardState.airingPageSize);
  if (subtitle) subtitle.textContent = `今天 ${items.length} 条已订阅剧集播出。`;
  if (pageLabel) pageLabel.textContent = `${dashboardState.airingPage} / ${dashboardState.airingTotalPages}`;
  if (list) list.innerHTML = rows.length ? rows.map(dashboardAiringCard).join('') : '<div class="dashboard-empty">今日暂无订阅播出数据</div>';
  return { items: rows, total_results: items.length, page: dashboardState.airingPage, total_pages: dashboardState.airingTotalPages };
}

async function loadDashboardStatus() {
  const summary = document.getElementById('dashboard-summary');
  if (!summary) return;
  try {
    const [statusResult, subscriptionResult, , dailyResult] = await Promise.allSettled([
      api('/api/status', { timeoutMs: 10000 }),
      api('/api/subscriptions/items?progress=1', { timeoutMs: 20000 }),
      loadDashboardSystemStatus(),
      loadDashboardDailyAiringItems(),
    ]);
    const data = statusResult.status === 'fulfilled' ? statusResult.value : { ok: false };
    const subscriptions = subscriptionResult.status === 'fulfilled' ? (subscriptionResult.value.items || []) : [];
    const sortedSubscriptions = [...subscriptions].sort((a, b) => {
      const aTotal = firstPositiveNumber(a.episode_total, a.total_episodes, a.episodes_total, a.episode_count);
      const bTotal = firstPositiveNumber(b.episode_total, b.total_episodes, b.episodes_total, b.episode_count);
      const aMissing = Math.max(0, aTotal - Number(a.library_episode_count || 0));
      const bMissing = Math.max(0, bTotal - Number(b.library_episode_count || 0));
      return bMissing - aMissing;
    });
    const dailyItems = dailyResult.status === 'fulfilled' ? dailyResult.value : [];
    dashboardState.libraryItems = dashboardTodayLibraryItems(sortedSubscriptions);
    dashboardState.libraryPage = 1;
    dashboardState.airingItems = dashboardSubscribedDailyItems(dailyItems, sortedSubscriptions);
    dashboardState.airingPage = 1;
    const airing = await loadDashboardAiringPage(1);
    renderDashboardLibrary();
    const totalMissing = sortedSubscriptions.reduce((sum, item) => {
      const total = firstPositiveNumber(item.episode_total, item.total_episodes, item.episodes_total, item.episode_count);
      return sum + Math.max(0, total - Number(item.library_episode_count || 0));
    }, 0);
    summary.innerHTML = `
      <div class="dashboard-card"><span>服务状态</span><strong>${data.ok ? '正常' : '异常'}</strong></div>
      <div class="dashboard-card"><span>今日播出</span><strong>${escapeHtml(airing.total_results || 0)}</strong></div>
      <div class="dashboard-card"><span>今日入库</span><strong>${escapeHtml(dashboardState.libraryItems.length)}</strong></div>
      <div class="dashboard-card"><span>待补集数</span><strong>${escapeHtml(totalMissing)}</strong></div>
    `;
  } catch (err) {
    summary.innerHTML = `<div class="dashboard-card wide"><span>服务状态</span><strong>加载失败：${escapeHtml(err.message)}</strong></div>`;
  }
}

document.getElementById('check-account')?.addEventListener('click', async () => {
  await saveConfig();
  const data = await api('/api/115/check', { method: 'POST', body: '{}' });
  toast(data.ok ? '115账号可用' : '检查失败');
});

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[ch]);
}

document.getElementById('run-clean')?.addEventListener('click', async () => {
  await saveConfig();
  await api('/api/115/cleanup/run', { method: 'POST', body: '{}' });
  toast('清理任务已触发');
});

document.getElementById('run-boost')?.addEventListener('click', async () => {
  await saveConfig();
  const text = document.getElementById('boost-text').value;
  await api('/api/115/boost', { method: 'POST', body: JSON.stringify({ text }) });
  toast('助力任务已执行');
});

const discoverSources = ['全球日播', 'TMDB', '豆瓣', '腾讯视频', '优酷', '爱奇艺', '芒果'];
const discoverPlatformSources = new Set(['腾讯视频', '优酷', '爱奇艺', '芒果']);
const discoverFilters = [
  { label: '类型', key: 'type', values: ['电影', '电视剧'] },
  { label: '趋势', key: 'trend', values: ['全部', '周榜', '日榜'] },
  { label: '排序', key: 'sort', values: ['热度降序', '热度升序', '上映时间降序', '上映时间升序', '评分最高', '评分最低'] },
  { label: '语言', key: 'language', values: ['全部', '中文', '英语', '日语', '韩语', '法语', '德语', '西语', '意语', '俄语', '葡语', '阿语', '印地语', '泰语'] },
  { label: '年份', key: 'year', values: ['全部', '2026', '2025', '2024', '2023', '2022', '2021', '2020年代', '2010年代', '2000年代', '90年代', '80年代'] },
  { label: '风格', key: 'genre', values: ['全部', '冒险', '奇幻', '动画', '剧情', '恐怖', '动作', '喜剧', '历史', '西部', '惊悚', '犯罪', '纪录片', '科幻', '悬疑', '音乐', '爱情', '家庭', '战争'] },
];
let discoverInitialized = false;
let discoverPage = 1;
let discoverLastPage = { page: 1, total_pages: 1, has_prev: false, has_next: false };
let discoverSearch = { active: false, title: '', type: '' };
let discoverResourceRows = [];
let discoverSeasonStatus = [];
let resourcePreviewState = { text: '', links: [], item: null };
let subscriptionConfigLoaded = false;
let mySubscriptionLoaded = false;
let mySubscriptionData = { items: [], blocked_titles: [] };
let mySubscriptionProgressRequest = null;
let discoverSubscriptionKeys = new Set();
let discoverSubscriptionKeyMap = new Map();
let discoverSubscriptionLoaded = false;
let mySubscriptionTab = 'tv';
let mySubscriptionFilters = { status: 'all', update: 'all', keyword: '', year: '' };
let activeSubscriptionMenuKey = '';
let mySubscriptionCalendar = {
  year: new Date().getFullYear(),
  month: new Date().getMonth(),
  view: 'month',
  type: 'all',
};
let mySubscriptionCalendarData = {
  key: '',
  entries: [],
  stats: {},
  errors: [],
  loading: false,
};

const subscriptionSourceDefs = [
  ['hot_movie', '热门电影'],
  ['movie_realtime', '电影实时热榜'],
  ['showing', '正在上映'],
  ['hot_tv', '热门剧集'],
  ['tv_realtime', '剧集实时热榜'],
  ['global_tv', '全球剧榜'],
  ['domestic_tv', '国产剧榜'],
  ['japanese_tv', '日剧榜'],
  ['korean_tv', '韩剧榜'],
  ['american_tv', '美剧榜'],
  ['anime_tv', '动画剧榜'],
  ['platform_tencent', '腾讯视频热更'],
  ['platform_youku', '优酷热更'],
  ['platform_iqiyi', '爱奇艺热更'],
  ['platform_mango', '芒果热更'],
];

subscriptionSourceDefs.length = 0;
subscriptionSourceDefs.push(
  ['hot_movie', '热门电影', 'movie'],
  ['movie_realtime', '电影实时热榜', 'movie'],
  ['hot_tv', '热门剧集', 'tv'],
  ['tv_realtime', '剧集实时热榜', 'tv'],
  ['global_tv', '全球剧榜', 'extra'],
  ['daily_airing', '全球日播', 'extra'],
  ['showing', '正在上映', 'movie'],
  ['domestic_tv', '国产剧榜', 'extra'],
  ['japanese_tv', '日剧榜', 'extra'],
  ['korean_tv', '韩剧榜', 'extra'],
  ['american_tv', '美剧榜', 'extra'],
  ['anime_tv', '动画剧榜', 'extra'],
  ['platform_tencent', '腾讯视频热更', 'platform'],
  ['platform_youku', '优酷热更', 'platform'],
  ['platform_iqiyi', '爱奇艺热更', 'platform'],
  ['platform_mango', '芒果热更', 'platform'],
);
const legacyLatestSubscriptionSources = ['hot_movie', 'movie_realtime', 'hot_tv', 'tv_realtime', 'global_tv', 'showing', 'domestic_tv', 'japanese_tv', 'korean_tv', 'american_tv', 'anime_tv', 'platform_tencent', 'platform_youku', 'platform_iqiyi', 'platform_mango'];
const latestSubscriptionSources = ['hot_movie', 'movie_realtime', 'hot_tv', 'tv_realtime', 'global_tv', 'daily_airing', 'showing', 'domestic_tv', 'japanese_tv', 'korean_tv', 'american_tv', 'anime_tv', 'platform_tencent', 'platform_youku', 'platform_iqiyi', 'platform_mango'];
const platformSubscriptionSources = ['platform_tencent', 'platform_youku', 'platform_iqiyi', 'platform_mango'];
const subscriptionSourceGroupLabels = { movie: '电影', tv: '剧集', extra: '剧集榜单', platform: '平台热更' };
const subscriptionSourceKeys = new Set(subscriptionSourceDefs.map(([key]) => key));
const subscriptionModeDefs = {
  moviepilot: { label: '模式1 MoviePilot', task: 'MoviePilot 推送' },
  torra: { label: '模式2 Torra', task: 'Torra 推送' },
  resource: { label: '模式3 资源转存', task: '精准资源搜索' },
  resource_then_pt: { label: '模式4 资源优先，PT兜底', task: '资源优先，PT兜底' },
  symedia: { label: '模式5 Symedia', task: 'Symedia 推送' },
};
const subscriptionResourceRuleDefs = [
  { key: 'resolution', label: '分辨率', items: [['4k', '4K'], ['1080p', '1080P'], ['720p_low', '720P及以下']] },
  { key: 'color', label: '色彩模式', items: [['dv_hdr', 'DV&HDR'], ['dv', 'DV'], ['hdr10', 'HDR10'], ['hdr', 'HDR']] },
  { key: 'audio', label: '音频规格', items: [['truehd', 'TRUEHD'], ['dtshdma', 'DTS-HD MA'], ['dtsx', 'DTS-X'], ['dtshd', 'DTS-HD'], ['dts', 'DTS'], ['eac3', 'EAC3'], ['ac3', 'AC3'], ['flac', 'FLAC'], ['aac', 'AAC']] },
  { key: 'extension', label: '扩展名', items: [['mkv', 'MKV'], ['mp4', 'MP4'], ['ts', 'TS'], ['iso', 'ISO'], ['rmvb', 'RMVB'], ['avi', 'AVI'], ['mov', 'MOV'], ['mpeg', 'MPEG'], ['mpg', 'MPG'], ['wmv', 'WMV'], ['minor', '小众格式']] },
  { key: 'size', label: '文件体积', items: [['big_to_small', '由大到小'], ['ge40g', '40G以上'], ['20_40g', '20-40G'], ['10_20g', '10-20G'], ['5_10g', '5-10G'], ['0_5g', '0-5G'], ['gt115g', '115G以上']] },
];
const defaultSubscriptionResourceRules = {
  enabled: false,
  auto_transfer: true,
  max_per_run: 8,
  groups: {
    resolution: { require: ['4k'], reject: [] },
    color: { require: ['dv'], reject: [] },
    audio: { require: [], reject: [] },
    extension: { require: ['mkv'], reject: [] },
    size: { require: [], reject: [] },
    keyword: { require: [], reject: [] },
    exclude_keyword: { require: [], reject: [] },
  },
};

function subscriptionSourceLabel(value = '') {
  const key = String(value || '').trim();
  if (!key) return '';
  const found = subscriptionSourceDefs.find(([sourceKey]) => sourceKey === key);
  return found ? found[1] : key;
}

function normalizeSubscriptionMode(value = '') {
  const key = String(value || '').trim();
  return subscriptionModeDefs[key] ? key : 'resource';
}

function subscriptionModeLabel(value = '') {
  return subscriptionModeDefs[normalizeSubscriptionMode(value)]?.label || subscriptionModeDefs.resource.label;
}

function subscriptionTaskLabel(value = '') {
  return subscriptionModeDefs[normalizeSubscriptionMode(value)]?.task || subscriptionModeDefs.resource.task;
}

function currentSubscriptionMode() {
  return normalizeSubscriptionMode(document.querySelector('input[name="subscription-mode"]:checked')?.value || 'resource');
}

function syncSubscriptionModeLabel(mode = currentSubscriptionMode()) {
  const normalized = normalizeSubscriptionMode(mode);
  const active = document.getElementById('subscription-mode-active');
  if (active) active.textContent = subscriptionModeLabel(normalized);
  document.querySelectorAll('.subscription-mode-card').forEach(card => {
    const input = card.querySelector('input[name="subscription-mode"]');
    card.classList.toggle('active', normalizeSubscriptionMode(input?.value) === normalized && Boolean(input?.checked));
  });
}

function discoverItemKey(item) {
  if (item.subscription_key) return String(item.subscription_key);
  if (item.dedupe_key) return String(item.dedupe_key);
  const media = normalizeMediaType(item);
  const title = normalizeSubscriptionLookupTitle(item.title || item.name);
  const tmdbId = String(item.tmdb_id || '').trim();
  if (media === 'tv' && tmdbId && title) {
    const season = subscriptionSeasonLookupValue(item);
    return `tv:${title}:tmdb:${tmdbId}${season !== '' ? `:season:${season}` : ''}`;
  }
  return String(item.id || item.tmdb_id || `${media}:${item.title || ''}`);
}

function normalizeSubscriptionLookupTitle(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[（(]\s*(?:19|20)\d{2}\s*[）)]/g, '')
    .replace(/\s+/g, '')
    .replace(/[·•：:，,。.!！?？《》"'“”‘’\-\[\]【】_]/g, '');
}

function subscriptionSeasonLookupValue(item = {}) {
  for (const key of ['target_season', 'current_season', 'latest_season', 'season_number', 'season']) {
    const value = item[key];
    if (value === undefined || value === null || value === '') continue;
    const number = Number(value);
    if (Number.isFinite(number) && number >= 0) return String(Math.trunc(number));
  }
  return '';
}

function discoverSubscriptionLookupKeys(item = {}) {
  const media = normalizeMediaType(item);
  const ids = [item.id, item.tmdb_id, item.source_id].map(value => String(value || '').trim()).filter(Boolean);
  const title = normalizeSubscriptionLookupTitle(item.title || item.name);
  const year = String(item.year || discoverYearText(item) || '').slice(0, 4);
  const keys = [];
  ids.forEach(id => {
    keys.push(`id:${id}`);
    keys.push(`${media}:${id}`);
  });
  if (title) {
    keys.push(`title:${media}:${title}`);
    if (year) keys.push(`title:${media}:${title}:${year}`);
  }
  return keys;
}

function rememberDiscoverSubscription(item = {}) {
  const storageKey = discoverItemKey(item);
  if (storageKey) {
    discoverSubscriptionKeys.add(storageKey);
    discoverSubscriptionKeyMap.set(storageKey, storageKey);
  }
  discoverSubscriptionLookupKeys(item).forEach(key => {
    discoverSubscriptionKeys.add(key);
    if (storageKey) discoverSubscriptionKeyMap.set(key, storageKey);
  });
}

function isDiscoverSubscribed(item = {}) {
  return discoverSubscriptionLookupKeys(item).some(key => discoverSubscriptionKeys.has(key));
}

function syncDiscoverSubscriptionItems(items = []) {
  discoverSubscriptionKeys = new Set();
  discoverSubscriptionKeyMap = new Map();
  (items || []).forEach(rememberDiscoverSubscription);
  discoverSubscriptionLoaded = true;
}

function forgetDiscoverSubscription(item = {}, storageKey = '') {
  const keys = [...discoverSubscriptionLookupKeys(item), storageKey || discoverItemKey(item)].filter(Boolean);
  keys.forEach(key => {
    discoverSubscriptionKeys.delete(key);
    discoverSubscriptionKeyMap.delete(key);
  });
}

function discoverSubscriptionDeleteKey(item = {}) {
  const lookupKey = discoverSubscriptionLookupKeys(item).find(key => discoverSubscriptionKeyMap.has(key));
  return lookupKey ? discoverSubscriptionKeyMap.get(lookupKey) : discoverItemKey(item);
}

async function ensureDiscoverSubscriptionState(force = false) {
  if (discoverSubscriptionLoaded && !force) return;
  const data = await api('/api/subscriptions/items');
  syncDiscoverSubscriptionItems(data.items || []);
}

function discoverSubscribeIcon() {
  return `
    <span class="discover-signal-icon" aria-hidden="true">
      <span></span><span></span><span></span><span></span>
    </span>
  `;
}

function buildDiscoverSubscriptionItem(item) {
  const mediaType = normalizeMediaType(item);
  const poster = posterUrlFor(item);
  const year = discoverYearText(item);
  const id = String(item.tmdb_id || item.id || '').trim();
  return {
    id: id || discoverItemKey(item),
    tmdb_id: /^\d+$/.test(id) ? id : '',
    source_id: id,
    source: item.source || item.source_key || getActiveDiscoverSource(),
    source_key: item.source_key || '',
    title: item.title || item.name || '',
    original_title: item.original_title || item.original_name || '',
    media_type: mediaType,
    type: mediaType,
    year,
    poster_url: poster,
    poster: poster,
    backdrop_url: item.backdrop_url || item.backdrop || '',
    rating: item.rating || item.vote_average || '',
    overview: item.overview || item.description || '',
    episode_count: item.episode_count || item.total_episodes || '',
  };
}

async function toggleDiscoverSubscription(item, button = null) {
  const payload = buildDiscoverSubscriptionItem(item);
  if (!payload.title) {
    toast('\u7f3a\u5c11\u8ba2\u9605\u6807\u9898');
    return;
  }
  if (button) button.disabled = true;
  try {
    const subscribed = isDiscoverSubscribed(item);
    let data = null;
    if (subscribed) {
      const key = discoverSubscriptionDeleteKey(item) || discoverItemKey(payload);
      data = await api('/api/subscriptions/delete', { method: 'POST', body: JSON.stringify({ key, item: payload }) });
      if (data?.items) syncDiscoverSubscriptionItems(data.items);
      else forgetDiscoverSubscription(item, key);
      const stillSubscribed = isDiscoverSubscribed(item);
      updateDiscoverSubscribeButton(button, stillSubscribed);
      toast(stillSubscribed ? '取消订阅失败：未找到订阅记录' : '已取消订阅');
    } else {
      data = await api('/api/subscriptions/save', { method: 'POST', body: JSON.stringify({ item: payload }) });
      if (data?.items) syncDiscoverSubscriptionItems(data.items);
      else {
        rememberDiscoverSubscription(payload);
        rememberDiscoverSubscription(item);
      }
      updateDiscoverSubscribeButton(button, true);
      const task = data?.subscription_task || data?.auto_transfer || {};
      const queued = Number(task.queued || 0);
      const label = task.task_label || subscriptionTaskLabel(task.mode || currentSubscriptionMode());
      toast(queued ? `${data?.message || '已添加到我的订阅'}，后处理已排队（${label}）` : (data?.message || '\u5df2\u6dfb\u52a0\u5230\u6211\u7684\u8ba2\u9605'));
    }
    mySubscriptionLoaded = false;
    if (data?.items) {
      renderMySubscriptionItems(data);
      mySubscriptionLoaded = true;
    }
    if (document.getElementById('view-my-subscription')?.classList.contains('active')) {
      loadMySubscriptions(true).catch(err => console.warn('订阅列表刷新失败', err));
    }
    if (document.getElementById('view-dashboard')?.classList.contains('active')) {
      loadDashboardStatus().catch(() => {});
    }
    refreshMySubscriptionProgress().catch(err => console.warn('订阅进度刷新失败', err));
    loadActivityLogs().catch(err => console.warn('日志加载失败', err));
  } catch (err) {
    toast(`\u8ba2\u9605\u5931\u8d25\uff1a${err.message}`);
    loadActivityLogs().catch(logErr => console.warn('日志加载失败', logErr));
  } finally {
    if (button) button.disabled = false;
  }
}

function updateDiscoverSubscribeButton(button, subscribed) {
  if (!button) return;
  const actionLabel = subscribed ? '取消订阅' : '订阅';
  button.classList.remove('active');
  button.dataset.subscribed = subscribed ? '1' : '0';
  button.title = actionLabel;
  button.setAttribute('aria-label', actionLabel);
  const status = button.closest('.discover-poster-art')?.querySelector('.discover-poster-subscription-status');
  if (status) {
    status.textContent = subscribed ? '已订阅' : '未订阅';
    status.classList.toggle('subscribed', subscribed);
  }
}

function renderSubscriptionSources(selected = []) {
  const list = document.getElementById('subscription-source-list');
  if (!list) return;
  const active = new Set((selected && selected.length ? selected : latestSubscriptionSources) || []);
  const chips = subscriptionSourceDefs
    .filter(([key]) => active.has(key))
    .map(([key, label]) => `
      <span class="subscription-source-chip" data-subscription-chip="${escapeHtml(key)}">
        ${escapeHtml(label)}
        <button type="button" data-subscription-remove="${escapeHtml(key)}" aria-label="移除 ${escapeHtml(label)}">×</button>
      </span>
    `).join('');
  list.innerHTML = `
    <div class="subscription-source-select">
      <div class="subscription-source-trigger" id="subscription-source-trigger" role="button" tabindex="0" aria-expanded="false">
        <span class="subscription-source-chip-list">${chips || '<em>请选择榜单</em>'}</span>
        <span class="subscription-source-arrow">⌄</span>
      </div>
      <div class="subscription-source-menu" id="subscription-source-menu" hidden>
        ${subscriptionSourceDefs.map(([key, label]) => `
          <label class="subscription-check-row${active.has(key) ? ' selected' : ''}">
            <input type="checkbox" value="${escapeHtml(key)}" data-subscription-source ${active.has(key) ? 'checked' : ''}>
            <span>${escapeHtml(label)}</span>
          </label>
        `).join('')}
      </div>
    </div>
  `;
}

function cloneResourceRules(value = defaultSubscriptionResourceRules) {
  return JSON.parse(JSON.stringify(value || defaultSubscriptionResourceRules));
}

function mergeSubscriptionResourceRules(config = {}) {
  const merged = cloneResourceRules(defaultSubscriptionResourceRules);
  const source = config && typeof config === 'object' ? config : {};
  merged.enabled = Boolean(source.enabled);
  merged.auto_transfer = source.auto_transfer !== false;
  merged.max_per_run = Math.max(1, Math.min(50, Number(source.max_per_run || merged.max_per_run || 8)));
  const groups = source.groups && typeof source.groups === 'object' ? source.groups : {};
  Object.keys(merged.groups).forEach(key => {
    const group = groups[key] && typeof groups[key] === 'object' ? groups[key] : {};
    merged.groups[key].require = Array.isArray(group.require) ? group.require.map(String).filter(Boolean) : merged.groups[key].require;
    merged.groups[key].reject = Array.isArray(group.reject) ? group.reject.map(String).filter(Boolean) : merged.groups[key].reject;
  });
  return merged;
}

function subscriptionRuleChipState(group = {}, key = '') {
  if ((group.require || []).includes(key)) return 'require';
  if ((group.reject || []).includes(key)) return 'reject';
  return 'ignore';
}

function renderSubscriptionRuleKeywordRows(rules) {
  const keyword = rules.groups.keyword || { require: [], reject: [] };
  const exclude = rules.groups.exclude_keyword || { require: [], reject: [] };
  const rows = [
    ['keyword', '关键词', keyword.require || []],
    ['exclude_keyword', '屏蔽过滤', exclude.require || []],
  ];
  return rows.map(([key, label, values]) => `
    <div class="subscription-resource-rule-row">
      <span class="subscription-resource-rule-label">${escapeHtml(label)}</span>
      <div class="subscription-resource-keyword-list" data-resource-keyword-list="${escapeHtml(key)}">
        ${(values || []).map(value => `
          <button type="button" class="subscription-resource-keyword" data-resource-keyword-remove="${escapeHtml(key)}" data-value="${escapeHtml(value)}">
            ${escapeHtml(value)} <span>×</span>
          </button>
        `).join('')}
        <label class="subscription-resource-keyword-add">
          <input type="text" data-resource-keyword-input="${escapeHtml(key)}" placeholder="+ 添加">
          <button type="button" data-resource-keyword-add="${escapeHtml(key)}">添加</button>
        </label>
      </div>
    </div>
  `).join('');
}

function renderSubscriptionResourceRules(config = {}) {
  const root = document.getElementById('subscription-resource-rules');
  if (!root) return;
  const rules = mergeSubscriptionResourceRules(config);
  const enabled = document.getElementById('subscription-resource-rules-enabled');
  const limit = document.getElementById('subscription-resource-rules-limit');
  if (enabled) enabled.checked = Boolean(rules.enabled);
  if (limit) limit.value = rules.max_per_run || 8;
  root.innerHTML = `
    ${subscriptionResourceRuleDefs.map(def => {
      const group = rules.groups[def.key] || { require: [], reject: [] };
      return `
        <div class="subscription-resource-rule-row">
          <span class="subscription-resource-rule-label">${escapeHtml(def.label)}</span>
          <div class="subscription-resource-rule-chips">
            ${def.items.map(([key, label]) => {
              const state = subscriptionRuleChipState(group, key);
              return `
                <button type="button" class="subscription-resource-chip ${state}" data-resource-rule-chip data-group="${escapeHtml(def.key)}" data-key="${escapeHtml(key)}" data-state="${escapeHtml(state)}">
                  <span class="subscription-resource-chip-handle">::</span>
                  <span class="subscription-resource-chip-mark">${state === 'reject' ? '×' : state === 'require' ? '✓' : ''}</span>
                  <span>${escapeHtml(label)}</span>
                </button>
              `;
            }).join('')}
          </div>
        </div>
      `;
    }).join('')}
    ${renderSubscriptionRuleKeywordRows(rules)}
  `;
}

function cycleSubscriptionRuleChip(button) {
  const state = button.dataset.state || 'ignore';
  const next = state === 'ignore' ? 'require' : state === 'require' ? 'reject' : 'ignore';
  button.dataset.state = next;
  button.classList.remove('ignore', 'require', 'reject');
  button.classList.add(next);
  const mark = button.querySelector('.subscription-resource-chip-mark');
  if (mark) mark.textContent = next === 'reject' ? '×' : next === 'require' ? '✓' : '';
  const label = button.textContent?.replace(/[:✓×]/g, '').trim() || button.dataset.key || '';
  logActivityEvent('change_resource_rule', `资源规则已调整：${label}`, {
    group: button.dataset.group || '',
    key: button.dataset.key || '',
    state: next === 'require' ? '必须命中' : next === 'reject' ? '必须排除' : '忽略',
  }, { category: 'subscription' });
}

function addSubscriptionResourceKeyword(key) {
  const input = document.querySelector(`[data-resource-keyword-input="${CSS.escape(key)}"]`);
  const value = input?.value.trim();
  if (!input || !value) return;
  const list = document.querySelector(`[data-resource-keyword-list="${CSS.escape(key)}"]`);
  if (!list) return;
  const exists = Array.from(list.querySelectorAll('[data-resource-keyword-remove]'))
    .some(button => (button.dataset.value || '').toLowerCase() === value.toLowerCase());
  if (exists) {
    input.value = '';
    return;
  }
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'subscription-resource-keyword';
  button.dataset.resourceKeywordRemove = key;
  button.dataset.value = value;
  button.innerHTML = `${escapeHtml(value)} <span>×</span>`;
  list.insertBefore(button, input.closest('.subscription-resource-keyword-add'));
  input.value = '';
  logActivityEvent('change_resource_keyword', `添加资源${key === 'exclude_keyword' ? '屏蔽词' : '关键词'}：${value}`, {
    group: key,
    keyword: value,
  }, { category: 'subscription' });
}

function collectSubscriptionResourceRules() {
  const rules = mergeSubscriptionResourceRules(defaultSubscriptionResourceRules);
  rules.enabled = Boolean(document.getElementById('subscription-resource-rules-enabled')?.checked);
  rules.auto_transfer = true;
  rules.max_per_run = Math.max(1, Math.min(50, Number(document.getElementById('subscription-resource-rules-limit')?.value || 8)));
  Object.keys(rules.groups).forEach(key => {
    rules.groups[key] = { require: [], reject: [] };
  });
  document.querySelectorAll('[data-resource-rule-chip]').forEach(button => {
    const group = button.dataset.group || '';
    const key = button.dataset.key || '';
    const state = button.dataset.state || 'ignore';
    if (!rules.groups[group] || !key) return;
    if (state === 'require') rules.groups[group].require.push(key);
    if (state === 'reject') rules.groups[group].reject.push(key);
  });
  document.querySelectorAll('[data-resource-keyword-list]').forEach(list => {
    const key = list.dataset.resourceKeywordList || '';
    if (!rules.groups[key]) return;
    rules.groups[key].require = Array.from(list.querySelectorAll('[data-resource-keyword-remove]'))
      .map(button => button.dataset.value || '')
      .filter(Boolean);
    rules.groups[key].reject = [];
  });
  return rules;
}

function renderSubscriptionSummary(data = {}) {
  const root = document.getElementById('subscription-summary');
  if (!root) return;
  const stats = data.stats || {};
  const task = data.subscription_task || data.auto_transfer || {};
  const mode = normalizeSubscriptionMode(task.mode || data.config?.mode || currentSubscriptionMode());
  const taskLabel = task.task_label || subscriptionTaskLabel(mode);
  const taskText = task.queued
    ? `已排队 ${task.queued}`
    : (mode === 'moviepilot' || mode === 'torra' || mode === 'symedia'
      ? `${escapeHtml(task.pushed ?? 0)} / ${escapeHtml((task.pushed ?? 0) + (task.skipped ?? 0))}`
      : `${escapeHtml(task.transferred ?? 0)} / ${escapeHtml(task.searched ?? 0)}${task.fallback_pushed ? `，兜底 ${escapeHtml(task.fallback_pushed)}` : ''}`);
  root.innerHTML = `
    <div class="subscription-summary-card"><span>总数</span><strong>${escapeHtml(stats.total ?? 0)}</strong></div>
    <div class="subscription-summary-card"><span>电影</span><strong>${escapeHtml(stats.movie ?? 0)}</strong></div>
    <div class="subscription-summary-card"><span>剧集</span><strong>${escapeHtml(stats.tv ?? 0)}</strong></div>
    <div class="subscription-summary-card"><span>订阅模式</span><strong>${escapeHtml(subscriptionModeLabel(mode))}</strong></div>
    ${task.enabled ? `<div class="subscription-summary-card"><span>后处理：${escapeHtml(taskLabel)}</span><strong>${taskText}</strong></div>` : ''}
  `;
}

function normalizeBlockedSubscriptionTitleKey(value) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, '');
}

function parseBlockedSubscriptionTitles(value) {
  const raw = Array.isArray(value)
    ? value
    : String(value || '').split(/[\n,，;；|]+/);
  const seen = new Set();
  return raw
    .map(item => String(item || '').trim())
    .filter(item => {
      const key = normalizeBlockedSubscriptionTitleKey(item);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function setMySubscriptionBlockedTitles(titles) {
  const blocked = parseBlockedSubscriptionTitles(titles);
  mySubscriptionData = {
    ...(mySubscriptionData || {}),
    items: Array.isArray(mySubscriptionData.items) ? mySubscriptionData.items : [],
    blocked_titles: blocked,
  };
  return blocked;
}

function blockedSubscriptionTitles() {
  return parseBlockedSubscriptionTitles(mySubscriptionData.blocked_titles || []);
}

function currentSubscriptionYears() {
  const year = new Date().getFullYear();
  return [year, year - 1, year - 2].join(',');
}

function applyLatestSubscriptionPreset() {
  document.querySelectorAll('[data-subscription-source]').forEach(input => {
    input.checked = latestSubscriptionSources.includes(input.value);
    input.closest('.subscription-check-row')?.classList.toggle('selected', input.checked);
  });
  const years = document.getElementById('subscription-movie-years');
  const rating = document.getElementById('subscription-tv-min-rating');
  const taskTime = document.getElementById('subscription-task-time');
  if (years) years.value = currentSubscriptionYears();
  if (rating) rating.value = '0';
  if (taskTime) taskTime.value = taskTime.value || '08:30';
  renderSubscriptionSources(latestSubscriptionSources);
}

function normalizeSubscriptionSourceSelection(sources = []) {
  const hasSaved = Array.isArray(sources) && sources.length > 0;
  const selected = (hasSaved ? sources : latestSubscriptionSources)
    .map(value => String(value || '').trim())
    .filter(value => subscriptionSourceKeys.has(value));
  const selectedSet = new Set(selected);
  if (!hasSaved || legacyLatestSubscriptionSources.every(key => selectedSet.has(key))) {
    selectedSet.add('daily_airing');
  }
  if (![...selectedSet].some(key => platformSubscriptionSources.includes(key))) {
    platformSubscriptionSources.forEach(key => selectedSet.add(key));
  }
  return subscriptionSourceDefs.map(([key]) => key).filter(key => selectedSet.has(key));
}

function selectedSubscriptionSources() {
  const selected = Array.from(document.querySelectorAll('[data-subscription-source]:checked'))
    .map(input => input.value)
    .filter(value => subscriptionSourceKeys.has(value));
  return selected.length ? selected : latestSubscriptionSources;
}

function setSubscriptionSourceMenu(open) {
  const menu = document.getElementById('subscription-source-menu');
  const trigger = document.getElementById('subscription-source-trigger');
  if (!menu || !trigger) return;
  menu.hidden = !open;
  trigger.setAttribute('aria-expanded', String(open));
}

function syncSubscriptionDailyOnlyState() {
  // Removed from the simplified榜单订阅 UI.
}

function polishSubscriptionPage() {
  const card = document.querySelector('#view-subscription .subscription-card');
  if (!card || card.dataset.polished === '1') return;
  card.dataset.polished = '1';
  card.classList.add('subscription-card-clean');
  document.getElementById('subscription-latest-preset')?.addEventListener('click', applyLatestSubscriptionPreset);
}

function applySubscriptionConfig(config = {}) {
  const douban = config.douban || {};
  polishSubscriptionPage();
  const mode = normalizeSubscriptionMode(config.mode || config.subscription_mode || 'resource');
  const selectedSources = normalizeSubscriptionSourceSelection(douban.sources);
  const enabled = document.getElementById('subscription-enabled');
  const movieEnabled = document.getElementById('subscription-movie-enabled');
  const tvEnabled = document.getElementById('subscription-tv-enabled');
  const years = document.getElementById('subscription-movie-years');
  const rating = document.getElementById('subscription-tv-min-rating');
  const excludeTitles = document.getElementById('subscription-exclude-titles');
  const taskTime = document.getElementById('subscription-task-time');
  const taskEnabled = document.getElementById('subscription-task-enabled');
  if (enabled) enabled.checked = Boolean(douban.enabled);
  if (movieEnabled) movieEnabled.checked = douban.movie_enabled !== false;
  if (tvEnabled) tvEnabled.checked = douban.tv_enabled !== false;
  if (years) years.value = Array.isArray(douban.movie_years) && douban.movie_years.length ? douban.movie_years.join(',') : currentSubscriptionYears();
  if (rating) rating.value = douban.tv_min_rating ?? '0';
  if (excludeTitles) {
    excludeTitles.value = Array.isArray(douban.exclude_titles)
      ? douban.exclude_titles.join('\n')
      : String(douban.exclude_titles || '');
  }
  setMySubscriptionBlockedTitles(douban.exclude_titles || []);
  if (taskTime) taskTime.value = douban.task_time || '08:30';
  if (taskEnabled) taskEnabled.checked = douban.task_enabled !== false;
  const modeInput = document.querySelector(`input[name="subscription-mode"][value="${mode}"]`);
  if (modeInput) modeInput.checked = true;
  syncSubscriptionModeLabel(mode);
  renderSubscriptionSources(selectedSources);
  renderSubscriptionResourceRules(config.resource_rules || defaultSubscriptionResourceRules);
  syncSubscriptionDailyOnlyState();
  const state = document.getElementById('subscription-state');
  if (state) {
    state.textContent = douban.enabled ? '已启用' : '未启用';
    state.classList.toggle('active', Boolean(douban.enabled));
    state.textContent = douban.enabled ? '已启用' : '未启用';
  }
}

function collectSubscriptionConfig(options = {}) {
  const enabledInput = document.getElementById('subscription-enabled');
  const movieInput = document.getElementById('subscription-movie-enabled');
  const tvInput = document.getElementById('subscription-tv-enabled');
  const taskInput = document.getElementById('subscription-task-enabled');
  return {
    mode: currentSubscriptionMode(),
    mode_switch_push: options.modeSwitchPush !== false,
    douban: {
      enabled: enabledInput ? Boolean(enabledInput.checked) : true,
      movie_enabled: movieInput ? Boolean(movieInput.checked) : true,
      tv_enabled: tvInput ? Boolean(tvInput.checked) : true,
      movie_years: document.getElementById('subscription-movie-years')?.value || '',
      tv_min_rating: document.getElementById('subscription-tv-min-rating')?.value || '0',
      exclude_titles: document.getElementById('subscription-exclude-titles')?.value || '',
      daily_only: false,
      sources: selectedSubscriptionSources(),
      task_time: document.getElementById('subscription-task-time')?.value || '08:30',
      task_enabled: taskInput ? Boolean(taskInput.checked) : true,
    },
    resource_rules: collectSubscriptionResourceRules(),
  };
}


const activityLogState = {
  logs: [],
  level: 'all',
  category: 'all',
};

function activityLogLevel(status) {
  const value = String(status || 'info').toLowerCase();
  if (value === 'error') return 'error';
  if (value === 'skip' || value === 'warning' || value === 'warn') return 'warning';
  if (value === 'start' || value === 'debug') return 'debug';
  return 'info';
}

function activityLogLevelLabel(status) {
  const labels = { info: 'INFO', debug: 'DEBUG', error: 'ERROR', warning: 'WARNING' };
  return labels[activityLogLevel(status)] || 'INFO';
}

function activityLogCategoryLabel(category) {
  const labels = { operation: '页面操作', subscription: '订阅', push: '推送', transfer: '转存', system: '系统' };
  return labels[category] || category || '系统';
}

function activityLogTimeParts(value) {
  const text = String(value || '').trim();
  const match = text.match(/^(\d{4}-\d{2}-\d{2})\s+(.+)$/);
  return match ? [match[1], match[2]] : ['', text];
}

function activityLogDetailText(meta = {}) {
  return [
    meta.title,
    meta.resource_title ? `资源=${meta.resource_title}` : '',
    meta.rule ? `规则=${meta.rule}` : '',
    meta.mode ? `模式=${meta.mode}` : '',
    meta.task ? `任务=${meta.task}` : '',
    meta.target ? `目标=${meta.target}` : '',
    meta.view ? `页面=${meta.view}` : '',
    meta.panel ? `面板=${meta.panel}` : '',
    meta.section ? `设置=${meta.section}` : '',
    meta.source_title && meta.source_title !== meta.title ? `原始=${meta.source_title}` : '',
    meta.source ? `来源=${meta.source}` : '',
    meta.reason ? `原因=${meta.reason}` : '',
    meta.error ? `错误=${meta.error}` : '',
    meta.result_message ? `结果=${meta.result_message}` : '',
    meta.already_exists !== undefined ? `已有订阅=${meta.already_exists ? '是' : '否'}` : '',
    meta.search_triggered !== undefined ? `触发搜索=${meta.search_triggered ? '是' : '否'}` : '',
    meta.search_error ? `搜索错误=${meta.search_error}` : '',
    meta.subscribe_id ? `订阅ID=${meta.subscribe_id}` : '',
    meta.subscription_id ? `订阅ID=${meta.subscription_id}` : '',
    meta.task_id ? `任务ID=${meta.task_id}` : '',
    meta.metadata_pending ? '待 TMDB 匹配' : '',
    meta.media_type,
    meta.tmdb_id ? `TMDB=${meta.tmdb_id}` : '',
    meta.year ? `年份=${meta.year}` : '',
    meta.season_name,
    meta.season !== undefined && meta.season !== null && meta.season !== '' ? `季=${meta.season}` : '',
    meta.total !== undefined ? `总数=${meta.total}` : '',
    meta.movie !== undefined ? `电影=${meta.movie}` : '',
    meta.tv !== undefined ? `剧集=${meta.tv}` : '',
    meta.items !== undefined ? `条目=${meta.items}` : '',
    meta.count !== undefined ? `数量=${meta.count}` : '',
    meta.queued !== undefined ? `队列=${meta.queued}` : '',
    meta.searched !== undefined ? `搜索=${meta.searched}` : '',
    meta.matched !== undefined ? `命中=${meta.matched}` : '',
    meta.transferred !== undefined ? `转存=${meta.transferred}` : '',
    meta.pushed !== undefined ? `推送=${meta.pushed}` : '',
    meta.fallback_pushed !== undefined ? `兜底=${meta.fallback_pushed}` : '',
    meta.links !== undefined ? `链接=${meta.links}` : '',
    meta.libraries !== undefined ? `媒体库=${meta.libraries}` : '',
    meta.channels !== undefined ? `频道=${meta.channels}` : '',
    meta.skipped ? `跳过=${meta.skipped}` : '',
    meta.first_error ? `首个问题=${meta.first_error}` : '',
    meta.removed_count ? `删除=${meta.removed_count}` : '',
    meta.auto_transfer ? `精准转存=${meta.auto_transfer}` : '',
    meta.resource_rules ? `资源规则=${meta.resource_rules}` : '',
    meta.enabled !== undefined ? `启用=${meta.enabled ? '是' : '否'}` : '',
    meta.task_enabled !== undefined ? `定时=${meta.task_enabled ? '开' : '关'}` : '',
    meta.cache_hits ? `缓存=${meta.cache_hits}` : '',
    meta.sources ? `来源=${meta.sources}` : '',
    meta.target_pid ? `目录=${meta.target_pid}` : '',
    meta.status_code ? `HTTP=${meta.status_code}` : '',
  ].filter(Boolean).map(value => String(value)).join('  ');
}

function syncActivityLogControls() {
  document.querySelectorAll('[data-activity-level]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.activityLevel === activityLogState.level);
  });
  const category = document.getElementById('activity-log-category');
  if (category) category.value = activityLogState.category;
}

function renderActivityLogs(logs = activityLogState.logs) {
  const roots = Array.from(document.querySelectorAll('.activity-log-list'));
  if (!roots.length) return;
  activityLogState.logs = Array.isArray(logs) ? logs : [];
  syncActivityLogControls();
  const filtered = activityLogState.logs.filter(row => {
    const level = activityLogLevel(row.status);
    const category = String(row.category || 'system');
    return (activityLogState.level === 'all' || level === activityLogState.level)
      && (activityLogState.category === 'all' || category === activityLogState.category);
  });
  if (!filtered.length) {
    roots.forEach(root => { root.innerHTML = '<div class="activity-log-empty">暂无日志</div>'; });
    return;
  }
  const html = filtered.map((row, index) => {
    const meta = row.meta && typeof row.meta === 'object' ? row.meta : {};
    const [date, time] = activityLogTimeParts(row.time);
    const detail = activityLogDetailText(meta);
    const level = activityLogLevel(row.status);
    const category = activityLogCategoryLabel(row.category);
    return `
      <div class="activity-log-row ${escapeHtml(level)} ${index % 2 ? 'alternate' : ''}">
        <span class="activity-log-level">${escapeHtml(activityLogLevelLabel(row.status))}</span>
        <span class="activity-log-time"><em>${escapeHtml(date)}</em><strong>${escapeHtml(time)}</strong></span>
        <div class="activity-log-message">
          <strong>${escapeHtml(row.message || row.action || '')}</strong>
          <p>${escapeHtml([category, detail].filter(Boolean).join('  '))}</p>
        </div>
      </div>
    `;
  }).join('');
  roots.forEach(root => { root.innerHTML = html; });
}

async function loadActivityLogs(showToast = false) {
  document.querySelectorAll('.activity-log-list').forEach(root => { root.innerHTML = '<div class="activity-log-empty">正在加载日志...</div>'; });
  const data = await api('/api/activity/logs?limit=500', { timeoutMs: 12000 });
  renderActivityLogs(data.logs || []);
  if (showToast) toast('日志已刷新');
}

async function clearActivityLogs() {
  await api('/api/activity/clear', { method: 'POST', body: '{}' });
  activityLogState.logs = [];
  renderActivityLogs([]);
  toast('日志已清空');
}
async function loadSubscriptionConfig() {
  const data = await api('/api/subscriptions/config');
  applySubscriptionConfig(data.config || {});
  try {
    const itemsData = await api('/api/subscriptions/items', { timeoutMs: 12000 });
    renderSubscriptionSummary({
      ...itemsData,
      config: data.config || {},
      last_run_at: itemsData.last_run_at || data.config?.douban?.last_run_at || '',
    });
  } catch (err) {
    renderSubscriptionSummary({ config: data.config || {}, last_run_at: data.config?.douban?.last_run_at || '', stats: { total: 0, movie: 0, tv: 0 }, errors: [`订阅统计加载失败：${err.message}`] });
  }
  subscriptionConfigLoaded = true;
}

async function saveSubscriptionConfig(showToast = true, options = {}) {
  const data = await api('/api/subscriptions/config', { method: 'POST', body: JSON.stringify(collectSubscriptionConfig(options)) });
  applySubscriptionConfig(data.config || {});
  if (document.getElementById('view-my-subscription')?.classList.contains('active')) {
    renderMySubscriptionItems(mySubscriptionData);
  } else {
    syncMySubscriptionTabs();
  }
  const modeSwitchTask = data.mode_switch_task || {};
  if (showToast !== false) {
    const queued = Number(modeSwitchTask.queued || 0);
    if (modeSwitchTask.mode_switched && queued) {
      toast(`榜单订阅配置已保存，已排队补推 ${queued} 条到 ${modeSwitchTask.new_label || subscriptionModeLabel(modeSwitchTask.new_mode)}`);
    } else if (modeSwitchTask.mode_switched) {
      toast(`榜单订阅配置已保存，${modeSwitchTask.reason || '没有需要补推的订阅'}`);
    } else {
      toast('榜单订阅配置已保存');
    }
  }
  loadActivityLogs().catch(err => console.warn('日志加载失败', err));
}

async function runSubscriptionNow() {
  const btn = document.getElementById('subscription-run');
  const oldText = btn?.textContent || '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '执行中';
  }
  try {
    toast('正在执行订阅，请稍等...');
    await saveSubscriptionConfig(false, { modeSwitchPush: false });
    const data = await api('/api/subscriptions/run', { method: 'POST', body: '{}', timeoutMs: 300000 });
    renderSubscriptionSummary(data);
    renderMySubscriptionItems(data);
    mySubscriptionLoaded = true;
    mySubscriptionTab = data.stats?.tv ? 'tv' : 'movie';
    syncMySubscriptionTabs();
    setActiveView('my-subscription');
    refreshMySubscriptionProgress().catch(err => console.warn('订阅进度刷新失败', err));
    loadActivityLogs().catch(err => console.warn('日志加载失败', err));
    const total = Number(data.stats?.total || 0);
    const movie = Number(data.stats?.movie || 0);
    const tv = Number(data.stats?.tv || 0);
    const skipped = Array.isArray(data.errors) ? data.errors.length : 0;
    const skippedText = skipped ? `，跳过 ${skipped} 条` : '';
    const task = data.subscription_task || data.auto_transfer || {};
    const queued = Number(task.queued || 0);
    const queueText = queued ? `，后处理已排队 ${queued} 条（${task.task_label || subscriptionTaskLabel(task.mode)}）` : '';
    toast(`订阅已刷新：${total} 条（电影 ${movie} / 剧集 ${tv}${skippedText}）${queueText}`);
  } catch (err) {
    toast(`订阅执行失败：${err.message}`);
    loadActivityLogs().catch(logErr => console.warn('日志加载失败', logErr));
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = oldText;
    }
  }
}

async function syncDailyAiringSubscriptions() {
  const btn = document.getElementById('subscription-daily-airing-sync');
  const oldText = btn?.textContent || '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '检测中';
  }
  try {
    toast('正在检测全球日播...');
    await saveSubscriptionConfig(false, { modeSwitchPush: false });
    const data = await api('/api/subscriptions/daily-airing/sync', {
      method: 'POST',
      body: JSON.stringify({ limit: 72 }),
      timeoutMs: 300000,
    });
    renderSubscriptionSummary(data);
    renderMySubscriptionItems(data);
    syncDiscoverSubscriptionItems(data.items || []);
    mySubscriptionLoaded = true;
    mySubscriptionTab = data.stats?.tv ? 'tv' : 'movie';
    syncMySubscriptionTabs();
    setActiveView('my-subscription');
    refreshMySubscriptionProgress().catch(err => console.warn('订阅进度刷新失败', err));
    loadActivityLogs().catch(err => console.warn('日志加载失败', err));
    const added = Number(data.added_count || 0);
    const skipped = Number(data.skipped_count || 0);
    const checked = Number(data.checked_count || added + skipped || 0);
    const task = data.subscription_task || data.auto_transfer || {};
    const queued = Number(task.queued || 0);
    const queueText = queued ? `，后处理已排队 ${queued} 条（${task.task_label || subscriptionTaskLabel(task.mode)}）` : '';
    toast(`全球日播检测完成：检测 ${checked} 条，新增 ${added} 条，跳过 ${skipped} 条${queueText}`);
  } catch (err) {
    toast(`全球日播检测失败：${err.message}`);
    loadActivityLogs().catch(logErr => console.warn('日志加载失败', logErr));
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = oldText;
    }
  }
}

function subscriptionTypeLabel(item = {}) {
  return item.media_type === 'tv' || item.type === 'tv' ? '剧集' : '电影';
}

function subscriptionMediaKind(item = {}) {
  const raw = String(item.media_type || item.type || '').toLowerCase();
  if (raw === 'tv' || raw.includes('剧') || raw.includes('tv')) return 'tv';
  return 'movie';
}

function subscriptionPoster(item = {}) {
  return posterUrlFor(item) || item.poster_url || item.poster || '';
}

function subscriptionDate(item = {}) {
  const values = [item.air_date, item.release_date, item.updated_at, item.created_at, item.year ? `${item.year}-06-01` : ''];
  for (const value of values) {
    const text = String(value || '').trim();
    const match = text.match(/(20\d{2}|19\d{2})[-/](\d{1,2})(?:[-/](\d{1,2}))?/);
    if (match) {
      const day = match[3] || '1';
      return `${match[1]}-${String(match[2]).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    }
  }
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

function subscriptionIsInLibrary(item = {}) {
  return Boolean(item.in_library || Number(item.library_episode_count || 0) > 0);
}

function firstPositiveNumber(...values) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number) && number > 0) return number;
  }
  return 0;
}

function subscriptionLatestSeason(item = {}) {
  if (subscriptionMediaKind(item) === 'movie') return '电影';
  for (const value of [item.target_season, item.current_season, item.latest_season, item.season_number, item.season]) {
    if (String(value) === '0') return item.season_name || '特别篇';
  }
  if (String(item.season_type || '').includes('special')) return item.season_name || '特别篇';
  const latest = firstPositiveNumber(item.current_season, item.latest_season, item.season_number, item.season);
  return `第${latest || 1}季`;
}

function subscriptionProgressText(item = {}) {
  const total = firstPositiveNumber(item.episode_total, item.total_episodes, item.episodes_total, item.episode_count);
  const current = firstPositiveNumber(
    item.current_episode_count,
    item.aired_episode_count,
    item.latest_episode,
    item.progress_episode_count,
    item.library_episode_count,
  );
  if (subscriptionMediaKind(item) === 'movie') return subscriptionIsInLibrary(item) ? '1/1' : '0/1';
  if (total > 0) return `${current}/${total}`;
  return current > 0 ? `${current}` : '0/?';
}

function subscriptionStatus(item = {}) {
  const text = subscriptionProgressText(item);
  if (text.includes('/')) {
    const [current, total] = text.split('/').map(value => Number(value));
    if (Number.isFinite(current) && Number.isFinite(total) && total > 0 && current >= total) return 'done';
  }
  return subscriptionIsInLibrary(item) && subscriptionMediaKind(item) === 'movie' ? 'done' : 'pending';
}

function subscriptionItemByKey(key) {
  const items = Array.isArray(mySubscriptionData.items) ? mySubscriptionData.items : [];
  return items.find(item => discoverItemKey(item) === key) || null;
}

function subscriptionDetailPaths(detail = {}, item = {}) {
  const raw = detail.library_paths || detail.paths || item.library_paths || item.library_path || [];
  const values = Array.isArray(raw) ? raw : [raw];
  return values.map(value => String(value || '').trim()).filter(Boolean);
}

function formatChineseNumber(value) {
  const number = Number(value || 0);
  const digits = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九'];
  if (number <= 0) return '';
  if (number < 10) return digits[number];
  if (number === 10) return '十';
  if (number < 20) return `十${digits[number - 10]}`;
  if (number < 100) {
    const tens = Math.floor(number / 10);
    const ones = number % 10;
    return `${digits[tens]}十${ones ? digits[ones] : ''}`;
  }
  return String(number);
}

function subscriptionSeasonNumber(season = {}) {
  const value = Number(season.season_number ?? season.season ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function subscriptionSeasonLabel(season = {}) {
  const number = subscriptionSeasonNumber(season);
  if (number === 0) return '特别篇';
  return `第${formatChineseNumber(number)}季`;
}

function subscriptionSeasonTitle(season = {}) {
  const label = subscriptionSeasonLabel(season);
  const name = String(season.name || '').trim();
  if (!name) return label;
  const generic = new Set([label, `第 ${subscriptionSeasonNumber(season)} 季`, `Season ${subscriptionSeasonNumber(season)}`, 'Specials', '特别篇']);
  return generic.has(name) ? label : `${label} · ${name}`;
}

function renderSubscriptionSeasonDetail(seasons = []) {
  const rows = Array.isArray(seasons)
    ? [...seasons].sort((a, b) => subscriptionSeasonNumber(a) - subscriptionSeasonNumber(b))
    : [];
  if (!rows.length) {
    return '<div class="my-sub-detail-empty">暂无季集信息</div>';
  }
  const tabs = rows.map((season, index) => `
    <button class="${index === 0 ? 'active' : ''}" type="button" data-sub-detail-season-tab="${index}">
      ${escapeHtml(subscriptionSeasonLabel(season))}
    </button>
  `).join('');
  const panels = rows.map((season, index) => {
    const episodes = Array.isArray(season.episodes) ? season.episodes : [];
    const libraryCount = Number(season.library_count || 0);
    const episodeCount = Number(season.episode_count || episodes.length || 0);
    return `
      <div class="my-sub-detail-season" data-sub-detail-season-panel="${index}" ${index === 0 ? '' : 'hidden'}>
        <div class="my-sub-detail-season-head">
          <strong>${escapeHtml(subscriptionSeasonTitle(season))}</strong>
          <span>${escapeHtml(libraryCount)}/${escapeHtml(episodeCount || '?')} 集入库</span>
        </div>
        ${season.overview ? `<p class="my-sub-detail-season-overview">${escapeHtml(season.overview)}</p>` : ''}
        <div class="my-sub-detail-episode-list">
          ${episodes.length ? episodes.map(ep => `
            <article class="my-sub-detail-episode">
              <div class="my-sub-detail-episode-head">
                <strong>E${String(ep.episode_number || ep.number || '').padStart(2, '0')} ${escapeHtml(ep.title || '')}</strong>
                <span class="${ep.in_library ? 'is-library' : 'is-missing-library'}">${ep.in_library ? '已入库' : '待补'}</span>
              </div>
              <p>${escapeHtml(ep.overview || '暂无单集简介')}</p>
              <div class="my-sub-detail-episode-meta">
                ${ep.air_date ? `<span>${escapeHtml(ep.air_date)}</span>` : ''}
                ${ep.runtime ? `<span>${escapeHtml(ep.runtime)} 分钟</span>` : ''}
              </div>
              <div class="my-sub-detail-episode-paths">
                ${(ep.library_paths || []).length
                  ? ep.library_paths.map(path => `<code>${escapeHtml(path)}</code>`).join('')
                  : '<em>暂无这一集的入库路径</em>'}
              </div>
            </article>
          `).join('') : '<em>暂无单集明细</em>'}
        </div>
      </div>
    `;
  }).join('');
  return `
    <div class="my-sub-detail-season-browser">
      <div class="my-sub-detail-season-tabs">${tabs}</div>
      <div class="my-sub-detail-season-panels">${panels}</div>
    </div>
  `;
}

function renderSubscriptionCast(cast = []) {
  const rows = Array.isArray(cast) ? cast.filter(person => person && person.name).slice(0, 12) : [];
  if (!rows.length) return '<div class="my-sub-detail-empty">暂无演员信息</div>';
  return `
    <div class="my-sub-detail-cast">
      ${rows.map(person => `
        <div class="my-sub-detail-cast-card">
          ${person.profile_url ? `<img src="${escapeHtml(person.profile_url)}" alt="${escapeHtml(person.name)}">` : '<span></span>'}
          <strong>${escapeHtml(person.name)}</strong>
          <em>${escapeHtml(person.character || '')}</em>
        </div>
      `).join('')}
    </div>
  `;
}

function parkMySubscriptionResourcePanel() {
  const panel = document.getElementById('my-subscription-resource-panel');
  const list = document.getElementById('my-subscription-list');
  const host = list?.parentElement;
  if (panel && host && list && panel.parentElement !== host) {
    host.insertBefore(panel, list);
  }
  if (panel) panel.className = 'discover-resource-panel';
  return panel;
}

function placeMySubscriptionResourcePanelInDetail() {
  const panel = parkMySubscriptionResourcePanel();
  clearDiscoverResourcePanel();
  const anchor = document.getElementById('my-sub-detail-resource-anchor');
  if (panel && anchor) anchor.insertAdjacentElement('afterend', panel);
  return panel;
}

function mySubscriptionCardForKey(key) {
  return Array.from(document.querySelectorAll('.my-sub-card'))
    .find(card => card.dataset.subscriptionKey === key) || null;
}

function placeMySubscriptionResourcePanelAfterCard(card) {
  const panel = document.getElementById('my-subscription-resource-panel');
  clearDiscoverResourcePanel();
  if (panel && card) {
    panel.className = 'discover-resource-panel inline';
    card.insertAdjacentElement('afterend', panel);
  }
  return panel;
}

function renderSubscriptionDetail(data = {}, key = '') {
  parkMySubscriptionResourcePanel();
  const root = document.getElementById('my-subscription-detail');
  if (!root) return;
  const item = data.item || subscriptionItemByKey(key) || {};
  const detail = data.detail || {};
  const merged = { ...item, ...detail };
  const poster = posterUrlFor(merged) || subscriptionPoster(item);
  const backdrop = posterUrlFor({ poster_url: detail.backdrop_url || item.backdrop_url || '' });
  const title = detail.title || item.title || '';
  const mediaType = detail.media_type || subscriptionMediaKind(item);
  const paths = subscriptionDetailPaths(detail, item);
  const genres = Array.isArray(detail.genres) ? detail.genres : String(detail.genres || '').split(/[\/,]/).map(value => value.trim()).filter(Boolean);
  const progress = mediaType === 'tv'
    ? `${Number(detail.library_episode_count || item.library_episode_count || 0)}/${Number(detail.episode_count || item.episode_count || item.total_episodes || 0) || '?'}`
    : (detail.in_library || item.in_library ? '已入库' : '未入库');
  root.hidden = false;
  root.innerHTML = `
    <button class="my-subscription-back" type="button" data-subscription-detail-close>返回列表</button>
    <div class="my-subscription-detail-hero" ${backdrop ? `style="--detail-backdrop: url(&quot;${escapeHtml(backdrop)}&quot;)"` : ''}>
      <div class="my-subscription-detail-poster">
        ${poster ? `<img src="${escapeHtml(poster)}" alt="${escapeHtml(title)}">` : `<div class="my-sub-detail-poster-empty">${escapeHtml(title || '订阅')}</div>`}
      </div>
      <div class="my-subscription-detail-main">
        <h2>${escapeHtml(title || '-')} ${detail.year ? `<span>${escapeHtml(detail.year)}</span>` : ''}</h2>
        <div class="my-subscription-detail-actions">
          <span>${mediaType === 'tv' ? '电视剧' : '电影'}</span>
          <span class="${detail.in_library || item.in_library ? 'is-library' : 'is-missing-library'}">${detail.in_library || item.in_library ? '已入库' : '待补'}</span>
          <span>${escapeHtml(progress)}</span>
          ${detail.rating ? `<span>评分 ${escapeHtml(detail.rating)}</span>` : ''}
          ${detail.runtime ? `<span>${escapeHtml(detail.runtime)}</span>` : ''}
        </div>
        <p class="my-subscription-detail-overview">${escapeHtml(detail.overview || item.overview || '暂无简介')}</p>
        <div class="my-sub-detail-toolbar">
          <button class="my-sub-detail-action-search" type="button" data-subscription-detail-search="${escapeHtml(key)}">搜索资源</button>
          <button class="my-sub-detail-action-moviepilot" type="button" data-subscription-moviepilot="${escapeHtml(key)}">推送 MoviePilot</button>
          <button class="my-sub-detail-action-torra" type="button" data-subscription-torra="${escapeHtml(key)}">推送 Torra</button>
          <button class="my-sub-detail-action-symedia" type="button" data-subscription-symedia="${escapeHtml(key)}">推送 Symedia</button>
          <button class="my-sub-detail-action-refresh" type="button" data-subscription-detail-refresh="${escapeHtml(key)}">刷新详情</button>
        </div>
      </div>
    </div>
    <span id="my-sub-detail-resource-anchor" hidden></span>
    <div class="my-sub-detail-meta">
      <div><strong>TMDB</strong><span>${escapeHtml(detail.tmdb_id || item.tmdb_id || '-')}</span></div>
      <div><strong>类型</strong><span>${escapeHtml(genres.join(' / ') || '-')}</span></div>
      <div><strong>国家/语言</strong><span>${escapeHtml([detail.country, detail.language].filter(Boolean).join(' / ') || '-')}</span></div>
      <div><strong>日期</strong><span>${escapeHtml(detail.date || item.air_date || item.release_date || '-')}</span></div>
    </div>
    <div class="my-sub-detail-section">
      <h3>整体入库路径</h3>
      ${paths.length ? `<div class="my-sub-detail-paths">${paths.map(path => `<code>${escapeHtml(path)}</code>`).join('')}</div>` : '<div class="my-sub-detail-empty">暂无入库路径</div>'}
    </div>
    <div class="my-sub-detail-section">
      <h3>演员</h3>
      ${renderSubscriptionCast(detail.cast || [])}
    </div>
    <div class="my-sub-detail-section">
      <h3>分集详情</h3>
      ${renderSubscriptionSeasonDetail(data.seasons || [])}
    </div>
  `;
  placeMySubscriptionResourcePanelInDetail();
}

function setMySubscriptionDetailMode(active) {
  document.querySelector('.my-subscription-shell')?.classList.toggle('detail-mode', Boolean(active));
}

async function openMySubscriptionDetail(key, refresh = false) {
  parkMySubscriptionResourcePanel();
  const root = document.getElementById('my-subscription-detail');
  const resourcePanel = document.getElementById('my-subscription-resource-panel');
  if (!root || !key) return;
  activeSubscriptionMenuKey = '';
  setMySubscriptionDetailMode(true);
  renderSubscriptionPosterList();
  if (resourcePanel) {
    resourcePanel.hidden = true;
    resourcePanel.innerHTML = '';
  }
  root.hidden = false;
  root.innerHTML = '<div class="subscription-loading">正在加载详情...</div>';
  root.scrollIntoView({ behavior: 'smooth', block: 'start' });
  try {
    const data = await api(`/api/subscriptions/detail?key=${encodeURIComponent(key)}${refresh ? '&refresh=1' : ''}`);
    renderSubscriptionDetail(data, key);
  } catch (err) {
    root.innerHTML = `<div class="subscription-error">详情加载失败：${escapeHtml(err.message)}</div>`;
  }
}

function closeMySubscriptionDetail() {
  const root = document.getElementById('my-subscription-detail');
  if (!root) return;
  parkMySubscriptionResourcePanel();
  setMySubscriptionDetailMode(false);
  root.hidden = true;
  root.innerHTML = '';
}

async function searchMySubscriptionResources(key, sourceCard = null) {
  const item = subscriptionItemByKey(key);
  const detailOpen = document.querySelector('.my-subscription-shell')?.classList.contains('detail-mode');
  if (!item) {
    toast('没有找到订阅条目');
    return;
  }
  activeSubscriptionMenuKey = '';
  if (!detailOpen) renderSubscriptionPosterList();
  const panel = detailOpen
    ? placeMySubscriptionResourcePanelInDetail()
    : placeMySubscriptionResourcePanelAfterCard((sourceCard && sourceCard.isConnected) ? sourceCard : mySubscriptionCardForKey(key));
  if (!panel) {
    toast('没有找到资源显示位置');
    return;
  }
  panel.hidden = false;
  await openDiscoverResourceSearch(item, panel);
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function daysSinceSubscriptionUpdate(item = {}) {
  const date = new Date(subscriptionDate(item));
  if (Number.isNaN(date.getTime())) return 9999;
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  return Math.floor((today - day) / 86400000);
}

function mySubscriptionCounts() {
  const items = Array.isArray(mySubscriptionData.items) ? mySubscriptionData.items : [];
  return items.reduce((counts, item) => {
    const kind = subscriptionMediaKind(item);
    if (kind === 'movie') counts.movie += 1;
    else counts.tv += 1;
    counts.calendar += 1;
    return counts;
  }, {
    movie: 0,
    tv: 0,
    calendar: 0,
    blocked: blockedSubscriptionTitles().length,
  });
}

function setMySubscriptionTabLabels() {
  const labels = {
    movie: '电影订阅',
    tv: '电视剧订阅',
    calendar: '订阅日历',
    blocked: '被屏蔽订阅',
  };
  const counts = mySubscriptionCounts();
  document.querySelectorAll('.my-sub-tab').forEach(btn => {
    const tab = btn.dataset.subscriptionTab || '';
    const label = labels[tab] || btn.textContent || '';
    const count = counts[tab] ?? 0;
    btn.innerHTML = `${escapeHtml(label)} <span class="my-sub-tab-count">${escapeHtml(count)}</span>`;
  });
}

function filteredSubscriptionItems() {
  const items = Array.isArray(mySubscriptionData.items) ? mySubscriptionData.items : [];
  if (mySubscriptionTab === 'blocked') return [];
  const kind = mySubscriptionTab === 'movie' ? 'movie' : 'tv';
  return items.filter(item => {
    if (mySubscriptionTab !== 'calendar' && subscriptionMediaKind(item) !== kind) return false;
    if (mySubscriptionFilters.keyword && !String(item.title || '').toLowerCase().includes(mySubscriptionFilters.keyword.toLowerCase())) return false;
    if (mySubscriptionFilters.year && String(item.year || '') !== mySubscriptionFilters.year) return false;
    if (mySubscriptionFilters.status !== 'all' && subscriptionStatus(item) !== mySubscriptionFilters.status) return false;
    const days = daysSinceSubscriptionUpdate(item);
    if (mySubscriptionFilters.update === 'today' && days !== 0) return false;
    if (mySubscriptionFilters.update === '3' && days > 3) return false;
    if (mySubscriptionFilters.update === '7' && days > 7) return false;
    return true;
  });
}

function syncMySubscriptionTabs() {
  setMySubscriptionTabLabels();
  document.querySelectorAll('.my-sub-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.subscriptionTab === mySubscriptionTab);
  });
  document.querySelector('[data-subscription-panel="list"]')?.classList.toggle('active', mySubscriptionTab !== 'calendar');
  document.querySelector('[data-subscription-panel="calendar"]')?.classList.toggle('active', mySubscriptionTab === 'calendar');
  const filters = document.querySelector('.my-subscription-filters');
  if (filters) filters.hidden = mySubscriptionTab === 'blocked';
}

function renderBlockedSubscriptionList() {
  const titles = blockedSubscriptionTitles();
  if (!titles.length) {
    return '<div class="subscription-empty">暂无被屏蔽订阅</div>';
  }
  return titles.map(title => `
    <article class="my-sub-blocked-card">
      <div>
        <strong title="${escapeHtml(title)}">${escapeHtml(title)}</strong>
        <span>已加入排除订阅，自动订阅会跳过这个标题。</span>
      </div>
      <button type="button" data-subscription-unblock="${escapeHtml(title)}">取消屏蔽</button>
    </article>
  `).join('');
}

function renderSubscriptionPosterList() {
  parkMySubscriptionResourcePanel();
  const list = document.getElementById('my-subscription-list');
  if (!list) return;
  list.classList.toggle('my-subscription-blocked-list', mySubscriptionTab === 'blocked');
  if (mySubscriptionTab === 'blocked') {
    activeSubscriptionMenuKey = '';
    setMySubscriptionDetailMode(false);
    list.innerHTML = renderBlockedSubscriptionList();
    return;
  }
  const items = filteredSubscriptionItems();
  if (!items.length) {
    list.innerHTML = '<div class="subscription-empty">暂无订阅内容</div>';
    return;
  }
  list.innerHTML = items.map(item => {
    const poster = subscriptionPoster(item);
    const key = discoverItemKey(item);
    const days = Math.max(0, daysSinceSubscriptionUpdate(item));
    const rating = scoreFor(item);
    const source = item.source_label || subscriptionSourceLabel(item.source_key) || item.source || '';
    const season = subscriptionLatestSeason(item);
    const menuOpen = activeSubscriptionMenuKey === key;
    return `
      <article class="my-sub-card" data-subscription-key="${escapeHtml(key)}">
        <div class="my-sub-poster${poster ? ' has-image' : ''}">
          ${poster ? `<img src="${escapeHtml(poster)}" alt="${escapeHtml(item.title || '')}">` : `<span class="my-sub-placeholder">${escapeHtml(item.title || '订阅')}</span>`}
          <span class="my-sub-season">${escapeHtml(season)}</span>
          ${rating ? `<span class="my-sub-rating">★ ${escapeHtml(rating)}</span>` : ''}
          <span class="my-sub-progress">▣ ${escapeHtml(subscriptionProgressText(item))}</span>
          <button class="my-sub-more" type="button" data-subscription-menu="${escapeHtml(key)}" aria-label="订阅菜单">⋮</button>
          <div class="my-sub-menu" ${menuOpen ? '' : 'hidden'}>
            <button type="button" data-subscription-detail="${escapeHtml(key)}">详情</button>
            <button type="button" data-subscription-search="${escapeHtml(key)}">搜索资源</button>
            <button type="button" data-subscription-moviepilot="${escapeHtml(key)}">推送 MoviePilot</button>
            <button type="button" data-subscription-torra="${escapeHtml(key)}">推送 Torra</button>
            <button type="button" data-subscription-symedia="${escapeHtml(key)}">推送 Symedia</button>
            <button type="button" data-subscription-copy="${escapeHtml(key)}">复制标题</button>
            <button type="button" data-subscription-block="${escapeHtml(key)}">屏蔽订阅</button>
            <button type="button" data-subscription-delete="${escapeHtml(key)}">删除订阅</button>
          </div>
        </div>
        <h3 title="${escapeHtml(item.title || '')}">${escapeHtml(item.title || '')}</h3>
        <p>${escapeHtml([item.year || '', source].filter(Boolean).join(' · '))}</p>
        <em>◷ ${days === 0 ? '今天' : `${days}天前`}</em>
      </article>
    `;
  }).join('');
}

function subscriptionCalendarKey() {
  return `${mySubscriptionCalendar.year}-${String(mySubscriptionCalendar.month + 1).padStart(2, '0')}-${mySubscriptionCalendar.type}`;
}

function subscriptionCalendarEntryDay(entry = {}) {
  const match = String(entry.date || '').match(/^\d{4}-\d{2}-(\d{2})/);
  return match ? Number(match[1]) : 0;
}

function subscriptionCalendarTodayKey() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

function subscriptionCalendarEntryStatus(entry = {}) {
  if (entry.in_library) return { label: '已入库', className: 'in', priority: 3 };
  const date = String(entry.date || '');
  const today = subscriptionCalendarTodayKey();
  if (date > today) return { label: '将更', className: 'upcoming', priority: 2 };
  if (date === today) return { label: '未入库', className: 'missing', priority: 0 };
  return { label: '待入', className: 'wait', priority: 1 };
}

function formatSubscriptionCalendarEpisodeRange(values = []) {
  const episodes = [...new Set(values.map(value => Number(value || 0)).filter(value => value > 0))]
    .sort((a, b) => a - b);
  if (!episodes.length) return '';
  const ranges = [];
  let start = episodes[0];
  let prev = episodes[0];
  for (const current of episodes.slice(1)) {
    if (current === prev + 1) {
      prev = current;
      continue;
    }
    ranges.push(start === prev ? `${start}` : `${start}-${prev}`);
    start = current;
    prev = current;
  }
  ranges.push(start === prev ? `${start}` : `${start}-${prev}`);
  return `第 ${ranges.join('、')} 集`;
}

function formatSubscriptionCalendarEpisodeCode(season, values = []) {
  const episodes = [...new Set(values.map(value => Number(value || 0)).filter(value => value > 0))]
    .sort((a, b) => a - b);
  const seasonNumber = Number(season || 0);
  if (!episodes.length || !seasonNumber) return '';
  if (episodes.length === 1) {
    return `S${String(seasonNumber).padStart(2, '0')}E${String(episodes[0]).padStart(2, '0')}`;
  }
  const first = episodes[0];
  const last = episodes[episodes.length - 1];
  const contiguous = episodes.every((value, index) => index === 0 || value === episodes[index - 1] + 1);
  if (contiguous) {
    return `S${String(seasonNumber).padStart(2, '0')}E${String(first).padStart(2, '0')}-E${String(last).padStart(2, '0')}`;
  }
  return `S${String(seasonNumber).padStart(2, '0')} ${episodes.map(value => `E${String(value).padStart(2, '0')}`).join('/')}`;
}

function groupSubscriptionCalendarEntries(rows = []) {
  const groups = new Map();
  for (const entry of rows) {
    const groupKey = [
      entry.date || '',
      entry.key || entry.tmdb_id || entry.title || '',
      entry.season_number || '',
    ].join('|');
    if (!groups.has(groupKey)) {
      groups.set(groupKey, []);
    }
    groups.get(groupKey).push(entry);
  }
  return Array.from(groups.values()).map(groupRows => {
    const sortedRows = [...groupRows].sort((a, b) => Number(a.episode_number || 0) - Number(b.episode_number || 0));
    const first = sortedRows[0] || {};
    const episodeNumbers = sortedRows.map(row => row.episode_number);
    const episodeTitles = sortedRows
      .map(row => String(row.episode_title || '').trim())
      .filter(Boolean);
    const allInLibrary = sortedRows.length > 0 && sortedRows.every(row => row.in_library);
    const libraryPaths = sortedRows.flatMap(row => Array.isArray(row.library_paths) ? row.library_paths : []);
    return {
      ...first,
      episode_count_for_day: sortedRows.length,
      episode_numbers: episodeNumbers,
      episode_label: formatSubscriptionCalendarEpisodeCode(first.season_number, episodeNumbers) || first.episode_label || '',
      episode_title: episodeNumbers.length > 1
        ? formatSubscriptionCalendarEpisodeRange(episodeNumbers)
        : (episodeTitles[0] || first.episode_title || formatSubscriptionCalendarEpisodeRange(episodeNumbers)),
      progress_text: episodeNumbers.length > 1 ? formatSubscriptionCalendarEpisodeRange(episodeNumbers) : first.progress_text,
      in_library: allInLibrary,
      library_paths: libraryPaths,
    };
  });
}

function sortSubscriptionCalendarEntries(rows = []) {
  return [...rows].sort((a, b) => {
    const statusA = subscriptionCalendarEntryStatus(a);
    const statusB = subscriptionCalendarEntryStatus(b);
    if (statusA.priority !== statusB.priority) return statusA.priority - statusB.priority;
    const titleCompare = String(a.title || '').localeCompare(String(b.title || ''), 'zh-Hans-CN');
    if (titleCompare) return titleCompare;
    const seasonA = Number(a.season_number || 0);
    const seasonB = Number(b.season_number || 0);
    if (seasonA !== seasonB) return seasonA - seasonB;
    return Number(a.episode_number || 0) - Number(b.episode_number || 0);
  });
}

function subscriptionCalendarPosterHtml(entry = {}) {
  const poster = posterUrlFor({ poster_url: entry.poster_url || entry.poster || '' });
  if (!poster) return '<span class="my-sub-calendar-poster-fallback"></span>';
  return `
    <span class="my-sub-calendar-poster-shell">
      <img src="${escapeHtml(poster)}" alt="" loading="lazy" onerror="this.hidden=true;this.nextElementSibling.hidden=false;">
      <span class="my-sub-calendar-poster-fallback" hidden></span>
    </span>
  `;
}

async function loadMySubscriptionCalendar(force = false) {
  const key = subscriptionCalendarKey();
  if (mySubscriptionCalendarData.loading) return;
  if (!force && mySubscriptionCalendarData.key === key) return;
  mySubscriptionCalendarData = {
    ...mySubscriptionCalendarData,
    key,
    loading: true,
    error: '',
  };
  renderSubscriptionCalendar();
  try {
    const data = await api(`/api/subscriptions/calendar?year=${encodeURIComponent(mySubscriptionCalendar.year)}&month=${encodeURIComponent(mySubscriptionCalendar.month + 1)}&type=${encodeURIComponent(mySubscriptionCalendar.type)}`, { timeoutMs: 120000 });
    mySubscriptionCalendarData = {
      key,
      entries: Array.isArray(data.entries) ? data.entries : [],
      stats: data.stats || {},
      errors: Array.isArray(data.errors) ? data.errors : [],
      loading: false,
      error: '',
    };
  } catch (err) {
    mySubscriptionCalendarData = {
      key,
      entries: [],
      stats: {},
      errors: [],
      loading: false,
      error: err.message,
    };
  }
  renderSubscriptionCalendar();
}

function renderSubscriptionCalendar() {
  const root = document.getElementById('my-subscription-calendar');
  const title = document.getElementById('my-sub-calendar-title');
  const summary = document.getElementById('my-sub-calendar-summary');
  if (!root) return;
  if (mySubscriptionTab !== 'calendar') return;
  const { year, month } = mySubscriptionCalendar;
  if (title) title.textContent = `${year}年${month + 1}月`;
  const key = subscriptionCalendarKey();
  if (mySubscriptionCalendarData.key !== key && !mySubscriptionCalendarData.loading) {
    root.innerHTML = '<div class="subscription-loading">正在加载订阅播出日历...</div>';
    if (summary) summary.textContent = '按 TMDB 分集播出日期生成日历。';
    loadMySubscriptionCalendar().catch(err => console.warn('订阅日历加载失败', err));
    return;
  }
  if (mySubscriptionCalendarData.loading) {
    root.innerHTML = '<div class="subscription-loading">正在加载订阅播出日历...</div>';
    if (summary) summary.textContent = '按 TMDB 分集播出日期生成日历。';
    return;
  }
  if (mySubscriptionCalendarData.error) {
    root.innerHTML = `<div class="subscription-error">订阅日历加载失败：${escapeHtml(mySubscriptionCalendarData.error)}</div>`;
    if (summary) summary.textContent = '播出日历加载失败，请稍后重试。';
    return;
  }
  const entries = Array.isArray(mySubscriptionCalendarData.entries) ? mySubscriptionCalendarData.entries : [];
  const stats = mySubscriptionCalendarData.stats || {};
  const errorCount = Array.isArray(mySubscriptionCalendarData.errors) ? mySubscriptionCalendarData.errors.length : 0;
  const calendarStatusCounts = entries.reduce((counts, entry) => {
    const status = subscriptionCalendarEntryStatus(entry);
    counts[status.className] = (counts[status.className] || 0) + 1;
    return counts;
  }, {});
  if (summary) {
    const suffix = errorCount ? `，${errorCount} 条未能生成播出日` : '';
    summary.textContent = `本月 ${stats.entries ?? entries.length} 个播出集，覆盖 ${stats.titles ?? 0} 个订阅，已入库 ${calendarStatusCounts.in || 0}，待入 ${calendarStatusCounts.wait || 0}，未入库 ${calendarStatusCounts.missing || 0}，将更 ${calendarStatusCounts.upcoming || 0}${suffix}。`;
  }
  const first = new Date(year, month, 1);
  const days = new Date(year, month + 1, 0).getDate();
  const startOffset = (first.getDay() + 6) % 7;
  const buckets = new Map();
  for (const entry of entries) {
    const day = subscriptionCalendarEntryDay(entry);
    if (!day) continue;
    if (!buckets.has(day)) buckets.set(day, []);
    buckets.get(day).push(entry);
  }
  const today = new Date();
  const cells = [];
  for (let i = 0; i < startOffset; i += 1) cells.push('<div class="my-sub-day muted"></div>');
  for (let day = 1; day <= days; day += 1) {
    const rows = sortSubscriptionCalendarEntries(groupSubscriptionCalendarEntries(buckets.get(day) || []));
    const statusGroups = rows.reduce((groups, entry) => {
      const status = subscriptionCalendarEntryStatus(entry);
      groups[status.className] = {
        label: status.label,
        count: (groups[status.className]?.count || 0) + 1,
      };
      return groups;
    }, {});
    const isToday = today.getFullYear() === year && today.getMonth() === month && today.getDate() === day;
    cells.push(`
      <div class="my-sub-day${isToday ? ' today' : ''}">
        <div class="my-sub-day-head">
          <strong>${month + 1}/${day}</strong>
          <span>${rows.length || ''}</span>
          ${statusGroups.in ? `<em class="in">${statusGroups.in.label} ${statusGroups.in.count}</em>` : ''}
          ${statusGroups.wait ? `<em class="wait">${statusGroups.wait.label} ${statusGroups.wait.count}</em>` : ''}
          ${statusGroups.missing ? `<em class="missing">${statusGroups.missing.label} ${statusGroups.missing.count}</em>` : ''}
          ${statusGroups.upcoming ? `<em class="upcoming">${statusGroups.upcoming.label} ${statusGroups.upcoming.count}</em>` : ''}
        </div>
        <div class="my-sub-day-items">
          ${rows.map(entry => {
            const status = subscriptionCalendarEntryStatus(entry);
            return `
              <div class="my-sub-day-item">
                ${subscriptionCalendarPosterHtml(entry)}
                <div><b title="${escapeHtml(entry.title || '')}">${escapeHtml(entry.title || '')}</b><small>${escapeHtml([entry.episode_label, entry.episode_title, entry.progress_text].filter(Boolean).join(' · '))}</small></div>
                <em class="${escapeHtml(status.className)}">${escapeHtml(status.label)}</em>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `);
  }
  root.innerHTML = cells.join('');
}

function renderMySubscriptionItems(data = {}) {
  const payload = data && typeof data === 'object' ? data : {};
  const previous = mySubscriptionData || {};
  const blockedSource = payload.blocked_titles !== undefined
    ? payload.blocked_titles
    : payload.config?.douban?.exclude_titles;
  mySubscriptionData = {
    ...previous,
    ...payload,
    items: Array.isArray(payload.items) ? payload.items : (Array.isArray(previous.items) ? previous.items : []),
    blocked_titles: blockedSource !== undefined
      ? parseBlockedSubscriptionTitles(blockedSource)
      : parseBlockedSubscriptionTitles(previous.blocked_titles || []),
  };
  if (Array.isArray(payload.items)) {
    mySubscriptionCalendarData = { ...mySubscriptionCalendarData, key: '' };
  }
  syncMySubscriptionTabs();
  renderSubscriptionPosterList();
  renderSubscriptionCalendar();
}

async function loadMySubscriptions(force = false) {
  if (mySubscriptionLoaded && !force) return;
  const list = document.getElementById('my-subscription-list');
  if (list && !(Array.isArray(mySubscriptionData.items) && mySubscriptionData.items.length)) {
    list.innerHTML = '<div class="subscription-loading">正在加载订阅...</div>';
  }
  const data = await api('/api/subscriptions/items', { timeoutMs: 12000 });
  renderMySubscriptionItems(data);
  mySubscriptionLoaded = true;
  refreshMySubscriptionProgress().catch(err => console.warn('订阅进度刷新失败', err));
}

async function refreshMySubscriptionProgress() {
  if (mySubscriptionProgressRequest) return mySubscriptionProgressRequest;
  mySubscriptionProgressRequest = api('/api/subscriptions/items?progress=1', { timeoutMs: 130000 })
    .then(data => {
      renderMySubscriptionItems(data);
      mySubscriptionLoaded = true;
      return data;
    })
    .finally(() => {
      mySubscriptionProgressRequest = null;
    });
  return mySubscriptionProgressRequest;
}

async function deleteMySubscription(key) {
  if (!key) return;
  await api('/api/subscriptions/delete', { method: 'POST', body: JSON.stringify({ key }) });
  mySubscriptionLoaded = false;
  await loadMySubscriptions(true);
  loadActivityLogs().catch(err => console.warn('日志加载失败', err));
  toast('已删除订阅');
}

async function blockMySubscription(key) {
  if (!key) return;
  const item = subscriptionItemByKey(key);
  const title = item?.title || item?.name || '';
  if (!item || !title) {
    toast('没有找到要屏蔽的订阅');
    return;
  }
  if (!window.confirm(`屏蔽订阅「${title}」？\n会从当前订阅移除，并加入排除订阅。`)) return;
  const data = await api('/api/subscriptions/block', {
    method: 'POST',
    body: JSON.stringify({ key, item }),
  });
  if (data.config) applySubscriptionConfig(data.config);
  renderMySubscriptionItems(data);
  syncDiscoverSubscriptionItems(data.items || []);
  mySubscriptionLoaded = true;
  loadActivityLogs().catch(err => console.warn('日志加载失败', err));
  toast(`已屏蔽订阅：${title}`);
}

async function unblockSubscriptionTitle(title) {
  const value = String(title || '').trim();
  if (!value) return;
  const data = await api('/api/subscriptions/unblock', {
    method: 'POST',
    body: JSON.stringify({ title: value }),
  });
  if (data.config) applySubscriptionConfig(data.config);
  renderMySubscriptionItems(data);
  mySubscriptionLoaded = true;
  loadActivityLogs().catch(err => console.warn('日志加载失败', err));
  toast(`已取消屏蔽：${value}`);
}

async function clearMySubscriptions() {
  if (!window.confirm('确定清空所有订阅内容？')) return;
  await api('/api/subscriptions/clear', { method: 'POST', body: '{}' });
  mySubscriptionLoaded = false;
  await loadMySubscriptions(true);
  loadActivityLogs().catch(err => console.warn('日志加载失败', err));
  toast('订阅内容已清空');
}

function normalizeMediaType(item) {
  return item.media_type || (item.type === '电视剧' ? 'tv' : item.type === '电影' ? 'movie' : 'movie');
}

function getActiveDiscoverSource() {
  return document.querySelector('#discover-source-tabs .discover-source-tab.active')?.dataset.source || 'TMDB';
}

function getActiveDiscoverFilter(key) {
  return document.querySelector(`#discover-filter-panel [data-filter-key="${key}"] .discover-filter-chip.active`)?.dataset.value || '';
}

function updateDiscoverFilterPanel() {
  const panel = document.getElementById('discover-filter-panel');
  if (!panel) return;
  panel.hidden = getActiveDiscoverSource() === '全球日播';
}

function setDiscoverStatus(message, type = 'success') {
  const status = document.getElementById('discover-status');
  if (!status) return;
  if (type !== 'error') {
    status.hidden = true;
    status.textContent = '';
    return;
  }
  status.hidden = false;
  status.className = `discover-status ${type}`;
  status.textContent = message;
}

function renderDiscoverShell() {
  const tabs = document.getElementById('discover-source-tabs');
  const panel = document.getElementById('discover-filter-panel');
  if (!tabs || !panel) return;
  tabs.innerHTML = discoverSources.map((source, index) => (
    `<button type="button" class="discover-source-tab${index === 0 ? ' active' : ''}" data-source="${source}">${source}</button>`
  )).join('');
  panel.innerHTML = discoverFilters.map(group => `
    <div class="discover-filter-row" data-filter-key="${group.key}">
      <div class="discover-filter-label">${group.label}</div>
      <div class="discover-filter-options">
        ${group.values.map((value, index) => `<button type="button" class="discover-filter-chip${index === 0 ? ' active' : ''}" data-value="${value}">${value}</button>`).join('')}
      </div>
    </div>
  `).join('');
}

function posterUrlFor(item) {
  const url = item.poster_url || item.poster || item.cover || '';
  if (url && /^https?:\/\/(?:image\.tmdb\.org|img[1239]\.doubanio\.com|[^/]*iqiyipic\.com|[^/]*qpic\.cn|[^/]*ykimg\.com|[^/]*alicdn\.com)\//.test(url)) {
    return `/api/image?url=${encodeURIComponent(url)}`;
  }
  return url;
}

function discoverYearText(item) {
  if (item.airing_today) {
    return [item.air_date || '今日播出', item.airing_category || ''].filter(Boolean).join(' · ');
  }
  const year = String(item.year || '').trim();
  return year ? year.replace(/\s*-\s*现在\s*$/g, '') : '';
}

function parkDiscoverResourcePanel() {
  const panel = document.getElementById('discover-resource-panel');
  const page = document.querySelector('.discover-page');
  if (panel && page && panel.parentElement !== page) page.appendChild(panel);
  if (panel) panel.className = 'discover-resource-panel';
  return panel;
}

function clearDiscoverResourcePanel() {
  discoverResourceRows = [];
  discoverSeasonStatus = [];
  document.querySelectorAll('.discover-resource-panel').forEach(panel => {
    panel.hidden = true;
    panel.innerHTML = '';
    panel.__resourceSearchItem = null;
    if (panel.id === 'discover-resource-panel') {
      parkDiscoverResourcePanel();
    } else if (panel.id === 'my-subscription-resource-panel') {
      parkMySubscriptionResourcePanel();
    } else if (panel.classList.contains('inline')) {
      panel.remove();
      return;
    }
    panel.className = 'discover-resource-panel';
  });
}

function closeResourcePanelsForOutsideClick(event) {
  const target = event.target;
  if (!target?.closest) return false;
  if (target.closest('.resource-preview-modal')) return false;
  const visiblePanels = Array.from(document.querySelectorAll('.discover-resource-panel'))
    .filter(panel => !panel.hidden);
  if (!visiblePanels.length) return false;
  if (target.closest('.discover-resource-panel')) return false;
  if (target.closest('[data-discover-action="search"], [data-subscription-search], [data-subscription-detail-search]')) return false;

  clearDiscoverResourcePanel();
  const actionable = target.closest('button, a, input, select, textarea, label, [role="button"], [tabindex]');
  return !actionable;
}

function compactTitle(title, max = 13) {
  const value = String(title || '');
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function scoreFor(item) {
  const score = item.rating ?? item.vote_average ?? item.score ?? item.rating_num ?? '';
  const numeric = Number(String(score).replace(/[^\d.]/g, ''));
  if (Number.isFinite(numeric) && numeric > 0) return numeric.toFixed(1);
  return '';
}

function discoverLibraryStatus(item = {}) {
  return Boolean(item.in_library || Number(item.library_episode_count || 0) > 0);
}

function progressFor(item) {
  if (item.airing_today) return item.airing_category || '今日播出';
  const count = item.library_episode_count || item.episode_count || '';
  const total = item.total_episodes || item.episode_total || item.episodes_total || '';
  if (count && total && String(count) !== String(total)) return `${count}/${total}`;
  return count ? String(count) : '';
}

function isPlatformDiscoverItem(item = {}) {
  return ['腾讯视频', '优酷', '爱奇艺', '芒果'].includes(item.source)
    || ['tencent', 'youku', 'iqiyi', 'mango'].includes(item.source_key);
}

function renderDiscoverCards(items) {
  const grid = document.getElementById('discover-poster-grid');
  if (!grid) return;
  if (!items.length) {
    grid.innerHTML = '<div class="discover-empty">当前条件没有获取到海报数据</div>';
    return;
  }
  grid.innerHTML = items.map((item, index) => {
    const poster = posterUrlFor(item);
    const type = normalizeMediaType(item);
    const rating = scoreFor(item);
    const platformItem = isPlatformDiscoverItem(item);
    const ratingLabel = rating || (platformItem ? '暂无' : '');
    const episodeCount = progressFor(item);
    const libraryStatus = discoverLibraryStatus(item) ? '已入库' : '';
    const subscribed = isDiscoverSubscribed(item);
    const dailyStatus = item.airing_today ? (subscribed ? '已订阅' : '未订阅') : '';
    const cornerBadge = dailyStatus
      ? `<span class="discover-poster-subscription-status${dailyStatus === '已订阅' ? ' subscribed' : ''}">${dailyStatus}</span>`
      : (libraryStatus ? '<span class="discover-poster-library-status">已入库</span>' : (ratingLabel ? `<span class="discover-poster-rating">${escapeHtml(String(ratingLabel).slice(0, 3))}</span>` : ''));
    return `
      <article class="discover-poster-card" data-index="${index}">
        <div class="discover-poster-art${poster ? ' has-image' : ''}">
          ${poster ? `<img src="${escapeHtml(poster)}" alt="${escapeHtml(item.title || '')}">` : ''}
          ${episodeCount ? `<span class="discover-poster-episode-badge${item.airing_today ? ' daily' : ''}">${escapeHtml(episodeCount)}</span>` : ''}
          ${cornerBadge}
          <button type="button" class="discover-card-action subscribe" data-discover-action="subscribe" data-subscribed="${subscribed ? '1' : '0'}" title="${subscribed ? '取消订阅' : '订阅'}" aria-label="${subscribed ? '取消订阅' : '订阅'}">${discoverSubscribeIcon()}</button>
          <div class="discover-poster-actions">
            <button type="button" class="discover-card-action search" data-discover-action="search" title="搜索资源">⌕</button>
          </div>
        </div>
        <h3 title="${escapeHtml(item.title || '')}">${escapeHtml(item.title || '')}</h3>
        <p>${escapeHtml(discoverYearText(item))}</p>
      </article>
    `;
  }).join('');
  grid.__items = items;
}

function renderDiscoverPagination(data = {}) {
  const pagination = document.getElementById('discover-pagination');
  if (!pagination) return;
  discoverLastPage = {
    page: Number(data.page || discoverPage || 1),
    total_pages: Number(data.total_pages || 1),
    has_prev: Boolean(data.has_prev),
    has_next: Boolean(data.has_next),
  };
  pagination.hidden = false;
  const page = discoverLastPage.page;
  const total = Math.max(1, discoverLastPage.total_pages);
  const pages = [];
  const addPage = value => {
    if (value >= 1 && value <= total && !pages.includes(value)) pages.push(value);
  };
  if (page > 3) addPage(1);
  for (let value = page - 2; value <= page + 2; value += 1) addPage(value);
  if (total <= 50) addPage(total);
  pages.sort((a, b) => a - b);
  pagination.innerHTML = `
    <span class="discover-pagination-meta">第 ${page} / ${total} 页</span>
    <div class="discover-pagination-actions">
      <button type="button" class="discover-page-nav" data-discover-page="prev" ${discoverLastPage.has_prev ? '' : 'disabled'}>上一页</button>
      ${pages.map((num, index) => {
        const prev = pages[index - 1];
        const gap = prev && num - prev > 1 ? '<span class="discover-page-ellipsis">...</span>' : '';
        return `${gap}<button type="button" class="discover-page-pill${num === page ? ' active' : ''}" data-discover-page-number="${num}">${num}</button>`;
      }).join('')}
      <button type="button" class="discover-page-nav" data-discover-page="next" ${discoverLastPage.has_next ? '' : 'disabled'}>下一页</button>
      <input id="discover-page-input" type="number" min="1" max="${total}" value="${page}">
      <button type="button" class="discover-page-nav" data-discover-page="jump">跳转</button>
    </div>
  `;
}

async function loadDiscoverData() {
  clearDiscoverResourcePanel();
  const grid = document.getElementById('discover-poster-grid');
  if (grid) grid.innerHTML = '<div class="discover-loading">正在获取海报...</div>';
  const params = new URLSearchParams({ page: String(discoverPage), limit: '16' });
  let url = '';
  let forceSubscriptionState = false;
  if (discoverSearch.active) {
    params.set('title', discoverSearch.title);
    if (discoverSearch.type) params.set('type', discoverSearch.type);
    url = `/api/discover/search?${params.toString()}`;
  } else {
    const source = getActiveDiscoverSource();
    updateDiscoverFilterPanel();
    forceSubscriptionState = source === '全球日播';
    if (source === '全球日播') {
      params.set('timezone', 'Asia/Shanghai');
      url = `/api/discover/daily-airing?${params.toString()}`;
    } else if (source === 'TMDB') {
      params.set('type', getActiveDiscoverFilter('type') === '电视剧' ? 'tv' : 'movie');
      ['trend', 'sort', 'language', 'year', 'genre'].forEach(key => {
        const value = getActiveDiscoverFilter(key);
        if (value && value !== '全部') params.set(key, value);
      });
      url = `/api/discover/tmdb?${params.toString()}`;
    } else if (discoverPlatformSources.has(source)) {
      params.set('platform', source);
      url = `/api/discover/platform-hot?${params.toString()}`;
    } else {
      url = `/api/discover/douban?${params.toString()}`;
    }
  }
  try {
    const data = await api(url);
    if (data.success === false) throw new Error(data.error || '获取失败');
    await ensureDiscoverSubscriptionState(forceSubscriptionState);
    renderDiscoverCards(data.items || []);
    renderDiscoverPagination(data);
    setDiscoverStatus(discoverSearch.active ? `影片搜索「${discoverSearch.title}」已获取 ${(data.items || []).length} 条` : `${data.source || '发现'} 已获取 ${(data.items || []).length} 条`);
  } catch (err) {
    if (grid) grid.innerHTML = '<div class="discover-empty">获取失败</div>';
    setDiscoverStatus(`获取失败：${err.message}`, 'error');
  }
}

async function openDiscoverResourceSearch(item, container = null) {
  const panel = container || document.getElementById('discover-resource-panel');
  if (!panel) return;
  panel.hidden = false;
  panel.__resourceSearchItem = item;
  panel.innerHTML = '<div class="discover-resource-loading">正在搜索资源...</div>';
  const params = new URLSearchParams({
    title: item.title || '',
    type: normalizeMediaType(item),
  });
  const year = discoverYearText(item);
  if (year) params.set('year', year);
  if (item.source_key || item.source) params.set('source', item.source_key || item.source);
  const id = String(item.tmdb_id || item.id || '').trim();
  if (/^\d+$/.test(id)) params.set('tmdb_id', id);
  try {
    const data = await api(`/api/discover/resources/search?${params.toString()}`);
    if (data.success === false) throw new Error(data.error || '搜索失败');
    discoverResourceRows = (data.items || []).filter(row => isPreciseResourceRowClient(row, item.title || ''));
    discoverSeasonStatus = data.seasons || [];
    const sourceDefs = Array.isArray(data.sources) ? data.sources : [];
    const sourceLabels = new Map(sourceDefs.map(source => [String(source.key || ''), source.label || source.key || '来源']));
    const sourceCounts = new Map();
    discoverResourceRows.forEach(row => {
      const key = String(row.source_key || row.source || 'unknown');
      sourceCounts.set(key, (sourceCounts.get(key) || 0) + 1);
      if (!sourceLabels.has(key)) sourceLabels.set(key, row.source_label || row.source || key);
    });
    const sourceTabs = [
      { key: 'all', label: '全部', count: discoverResourceRows.length },
      ...Array.from(sourceCounts.entries()).map(([key, count]) => ({ key, label: sourceLabels.get(key) || key, count })),
    ];
    const errorNotices = Array.isArray(data.errors) && data.errors.length
      ? `<div class="discover-resource-notices">${data.errors.map(error => `<p>${escapeHtml(error)}</p>`).join('')}</div>`
      : '';
    panel.innerHTML = `
      <div class="discover-resource-season-strip" hidden></div>
      ${errorNotices}
      <div class="discover-resource-tabs">
        ${sourceTabs.map((source, index) => (
          `<button type="button" class="discover-resource-tab${index === 0 ? ' active' : ''}" data-source="${escapeHtml(source.key)}">${escapeHtml(source.label)} (${source.count || 0})</button>`
        )).join('')}
      </div>
      <div class="discover-resource-results"></div>
    `;
    renderDiscoverResourceRows('all', panel);
  } catch (err) {
    panel.innerHTML = `<div class="discover-resource-empty">搜索失败：${escapeHtml(err.message)}</div>`;
  }
}

function chipResourceText(text) {
  return escapeHtml(text || '').replace(/(S\d{2}E\d{2}(?:[-–]E?\d{2,3})?|(?:23\.976|24|25|29\.97|30|50|59\.94|60|120)fps|8K|4K|4320p|2160p|1440p|1080p|720p|Dolby Vision|DoVi|杜比视界|HDR10\+|HDR10|HDR Vivid|HDR|HLG|SDR|H\.?265|x265|HEVC|H\.?264|x264|AV1|VP9|DTS-HD MA|DTS-HD|AAC(?:2\.0)?|EAC3|AC3|Atmos|IMAX|BluRay|REMUX|WEB-DL|WEBRip|Netflix|Bilibili|Disney\+|MKV|MP4|M2TS|TS|AVI|MOV|WMV|FLV|ISO|RMVB|MPEG|MPG|TMDB ID:\s*\d+|https?:\/\/[^\s]+)/gi, '<span class="resource-chip">$1</span>');
}

function stripResourceOriginalHeaders(text) {
  return String(text || '')
    .replace(/^\s*[\[【][^\]\】\n]*(?:资源转存)?\s*原始消息\s*[\]\】]?\s*\n?/gim, '')
    .replace(/^\s*(?:HDHiveAPI|TG\s*频道[:：][^\n]*|频道搜索|资源转存)\s*(?:资源转存)?\s*原始消息\s*\n?/gim, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function cleanResourceTitle(text) {
  let title = stripResourceOriginalHeaders(text);
  title = title.split('\n').find(line => line.trim()) || title;
  title = title
    .replace(/\[?\s*TMDB\s*ID[-:：\s]*\d+\s*\]?/gi, ' ')
    .replace(/\[?\s*tmdbid[-:：\s]*\d+\s*\]?/gi, ' ')
    .replace(/\b(?:23\.976|24|25|29\.97|30|50|59\.94|60|120)\s*(?:fps|帧|帧率|Hz)\b/gi, ' ')
    .replace(/\b(?:fps|帧率)\s*[:：]?\s*(?:23\.976|24|25|29\.97|30|50|59\.94|60|120)\b/gi, ' ')
    .replace(/\b(?:4320|2160|1440|1080|720|576|480)[pi]\b/gi, ' ')
    .replace(/\b(?:8K|4K|UHD)\b/gi, ' ')
    .replace(/\b(?:Dolby\s*Vision|DoVi|DV)\b|杜比视界/gi, ' ')
    .replace(/\b(?:HDR\s*Vivid|HDR\s*10\s*\+|HDR\s*10|HLG|HDR|SDR)\b|菁彩HDR/gi, ' ')
    .replace(/\b(?:H[.\s_-]?265|x265|HEVC|H[.\s_-]?264|x264|AVC|AV1|VP9|MPEG[.\s_-]?2|VC[.\s_-]?1)\b/gi, ' ')
    .replace(/\b(?:IMAX\s*Enhanced|IMAX|10\s*bit|Hi10P|Atmos|DTS\s*:\s*X|DTS[-\s]?X|REPACK|PROPER|UNCUT)\b|杜比全景声|高码率|高码版|高码/gi, ' ')
    .replace(/\b(?:WEB[.\s_-]?DL\s*\/\s*WEB[.\s_-]?Rip|WEB[.\s_-]?Rip\s*\/\s*WEB[.\s_-]?DL|WEB[.\s_-]?DL|WEB[.\s_-]?Rip|UHD\s*Blu[-\s]?Ray|UHD\s*BD|Blu[-\s]?Ray|BluRay|BDRip|BRRip|HDTV|DVDRip|REMUX|DVD|WEB)\b|蓝光/gi, ' ')
    .replace(/\b(?:AAC\s*2(?:[.\s]*0)?|AAC|E[.\s_-]?AC[.\s_-]?3|AC[.\s_-]?3|DDP?\s*5[.\s]*1|TrueHD|DTS[.\s_-]?HD[.\s_-]?MA(?:[.\s_-]?5[.\s]*1)?|DTS[.\s_-]?HD|DTS(?:[.\s_-]?HD)?|FLAC|MP3)\b/gi, ' ')
    .replace(/\b(?:Bilibili|B-Global|BGlobal|Netflix|NF|AMZN|Amazon|Disney\+?|DSNP|DisneyPlus|AppleTV\+?|ATVP|Apple\s*TV|HBO|HMAX|MAX|Hulu|Paramount\+?|PMTP|Peacock|iQIYI|IQ|Tencent|WeTV|Youku)\b|哔哩哔哩|B站|爱奇艺|腾讯视频|优酷/gi, ' ')
    .replace(/\b(?:MP4|MKV|M2TS|TS|AVI|MOV|WMV|FLV|ISO|RMVB|MPEG|MPG)\b/gi, ' ')
    .replace(/(?:内嵌|外挂)?(?:简中|繁中|中字|中文字幕|字幕)(?:字幕)?(?:\[[^\]]+\])?/gi, ' ')
    .replace(/^\s*[^\w\u4e00-\u9fff]*(?:电视剧|电影|动漫|剧集)\s*[：:]\s*/i, '')
    .replace(/\[\s*\]/g, ' ')
    .replace(/\[[^\]]{1,24}\]\s*$/g, ' ')
    .replace(/\s*[._|｜/\\]+\s*/g, ' ')
    .replace(/\s+-\s+/g, ' - ')
    .replace(/\s{2,}/g, ' ')
    .replace(/\s+(?:版本|版)$/g, '')
    .trim();
  return title || stripResourceOriginalHeaders(text);
}

function normalizedResourceInfo(value) {
  return String(value || '').replace(/[\s._-]+/g, '').toLowerCase();
}

function uniqueResourceInfo(parts = []) {
  const seen = new Set();
  const rows = [];
  for (const part of parts) {
    if (Array.isArray(part)) {
      for (const value of uniqueResourceInfo(part)) {
        const key = normalizedResourceInfo(value);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        rows.push(value);
      }
      continue;
    }
    const text = String(part || '').trim();
    const key = normalizedResourceInfo(text);
    if (!text || seen.has(key)) continue;
    seen.add(key);
    rows.push(text);
  }
  return rows;
}

function detectResourceTag(text, patterns = []) {
  for (const [pattern, label] of patterns) {
    if (pattern.test(text)) return label;
  }
  return '';
}

function resourceFallbackTags(item) {
  const text = `${item.title || ''} ${item.subtitle || ''} ${item.quality || ''} ${item.full_text || ''}`;
  const frameMatch = text.match(/\b(23\.976|24|25|29\.97|30|50|59\.94|60|120)\s*(?:fps|帧|帧率|Hz)\b/i)
    || text.match(/\b(?:fps|帧率)\s*[:：]?\s*(23\.976|24|25|29\.97|30|50|59\.94|60|120)\b/i);
  const frameRate = frameMatch ? `${frameMatch[1]}fps` : '';
  const audioCodec = detectResourceTag(text, [
    [/\bDTS[.\s_-]?HD[.\s_-]?MA(?:[.\s_-]?5[.\s]*1)?\b/i, 'DTS-HD MA'],
    [/\bDTS[.\s_-]?HD\b/i, 'DTS-HD'],
    [/\bDDP\s*5[.\s]*1\b/i, 'DDP5.1'],
    [/\bAAC\s*2(?:[.\s]*0)?\b/i, 'AAC2.0'],
    [/\bAAC\b/i, 'AAC'],
    [/\bE[.\s_-]?AC[.\s_-]?3\b/i, 'EAC3'],
    [/\bAC[.\s_-]?3\b/i, 'AC3'],
    [/\bTrueHD\b/i, 'TrueHD'],
    [/\bDTS(?:[.\s_-]?HD)?\b/i, 'DTS'],
    [/\bFLAC\b/i, 'FLAC'],
  ]);
  const resolution = detectResourceTag(text, [
    [/\b4320[pi]\b/i, '4320p'],
    [/\b2160[pi]\b/i, '2160p'],
    [/\b1440[pi]\b/i, '1440p'],
    [/\b1080[pi]\b/i, '1080p'],
    [/\b720[pi]\b/i, '720p'],
    [/\b8K\b/i, '8K'],
    [/\b(?:4K|UHD)\b/i, '4K'],
  ]);
  const videoCodec = detectResourceTag(text, [
    [/\b(?:H[.\s_-]?265|x265|HEVC)\b/i, 'H.265'],
    [/\b(?:H[.\s_-]?264|x264|AVC)\b/i, 'H.264'],
    [/\bAV1\b/i, 'AV1'],
    [/\bVP9\b/i, 'VP9'],
  ]);
  const dolbyVision = /\b(?:Dolby\s*Vision|DoVi|DV)\b|杜比视界/i.test(text) ? '杜比视界' : '';
  const dynamicRange = detectResourceTag(text, [
    [/\bHDR\s*Vivid\b|菁彩HDR/i, 'HDR Vivid'],
    [/\bHDR\s*10\s*\+/i, 'HDR10+'],
    [/\bHDR\s*10\b/i, 'HDR10'],
    [/\bHLG\b/i, 'HLG'],
    [/\bHDR\b/i, 'HDR'],
    [/\bSDR\b/i, 'SDR'],
  ]);
  const enhancementTags = [
    /\bIMAX\s*Enhanced\b/i.test(text) ? 'IMAX Enhanced' : '',
    /\bIMAX\b/i.test(text) ? 'IMAX' : '',
    /\b(?:10\s*bit|Hi10P)\b/i.test(text) ? '10bit' : '',
    /\bAtmos\b|杜比全景声/i.test(text) ? 'Atmos' : '',
    /\bDTS\s*:\s*X\b|\bDTS[-\s]?X\b/i.test(text) ? 'DTS:X' : '',
    /\bREPACK\b/i.test(text) ? 'REPACK' : '',
    /\bPROPER\b/i.test(text) ? 'PROPER' : '',
  ].filter(Boolean);
  const resourceMedium = detectResourceTag(text, [
    [/\bUHD\s*Blu[-\s]?Ray\b|\bUHD\s*BD\b/i, 'UHD BluRay'],
    [/\bBlu[-\s]?Ray\b|\bBD\b|蓝光/i, 'BluRay'],
    [/\bWEB\b|WEB[.\s_-]?(?:DL|Rip)/i, 'WEB'],
    [/\bHDTV\b/i, 'HDTV'],
    [/\bDVD\b/i, 'DVD'],
  ]);
  const releaseMethod = detectResourceTag(text, [
    [/\bWEB[.\s_-]?DL\s*\/\s*WEB[.\s_-]?Rip\b|\bWEB[.\s_-]?Rip\s*\/\s*WEB[.\s_-]?DL\b/i, 'WEB-DL/WEBRip'],
    [/\bREMUX\b/i, 'REMUX'],
    [/\bWEB[.\s_-]?DL\b/i, 'WEB-DL'],
    [/\bWEB[.\s_-]?Rip\b/i, 'WEBRip'],
    [/\bBluRay\b|蓝光/i, 'BluRay'],
    [/\bBDRip\b/i, 'BDRip'],
    [/\bHDTV\b/i, 'HDTV'],
  ]);
  const streamingPlatform = detectResourceTag(text, [
    [/\b(?:Bilibili|B-Global|BGlobal)\b|哔哩哔哩|B站/i, 'Bilibili'],
    [/\b(?:Netflix|NF)\b/i, 'Netflix'],
    [/\b(?:AMZN|Amazon)\b/i, 'Amazon'],
    [/\b(?:Disney\+?|DSNP|DisneyPlus)\b/i, 'Disney+'],
    [/\b(?:AppleTV\+?|ATVP|Apple\s*TV)\b/i, 'Apple TV+'],
    [/\b(?:HBO|HMAX|MAX)\b/i, 'Max'],
    [/\bHulu\b/i, 'Hulu'],
    [/\b(?:iQIYI|IQ)\b|爱奇艺/i, 'iQIYI'],
    [/\b(?:Tencent|WeTV)\b|腾讯视频/i, 'Tencent'],
    [/\bYouku\b|优酷/i, 'Youku'],
  ]);
  const extMatch = text.match(/\.(mkv|mp4|ts|m2ts|avi|mov|wmv|flv|iso)\b/i)
    || text.match(/\b(MKV|MP4|M2TS|TS|AVI|MOV|WMV|FLV|ISO|RMVB|MPEG|MPG)\b/i);
  const fileExtension = extMatch ? extMatch[1].toUpperCase() : '';
  return [frameRate, audioCodec, resolution, videoCodec, dolbyVision, dynamicRange, enhancementTags, resourceMedium, releaseMethod, streamingPlatform, fileExtension];
}

function resourceTagParts(item) {
  return uniqueResourceInfo([
    item.frame_rate,
    item.audio_codec,
    item.resolution,
    item.video_codec,
    item.dolby_vision,
    item.dynamic_range,
    item.enhancement_tags,
    item.resource_medium,
    item.release_method,
    item.streaming_platform,
    item.file_extension,
    resourceFallbackTags(item),
  ]);
}

function renderResourceTags(item) {
  const tags = resourceTagParts(item);
  if (!tags.length) return '';
  return `<div class="discover-resource-tags">${tags.map(tag => `<span>${escapeHtml(tag)}</span>`).join('')}</div>`;
}

function resourceSublineText(item) {
  const tagKeys = new Set(resourceTagParts(item).map(normalizedResourceInfo));
  return uniqueResourceInfo([item.subtitle, item.quality])
    .filter(part => !tagKeys.has(normalizedResourceInfo(part)))
    .join('  ');
}

function resourcePopoverText(item) {
  const raw = stripResourceOriginalHeaders(item.full_text || [
    item.title || '',
    [resourceTagParts(item).join(' '), item.quality].filter(Boolean).join(' '),
    item.size ? `大小：${item.size}` : '',
    item.preview_url || item.url ? `链接：${item.preview_url || item.url}` : '',
  ].filter(Boolean).join('\n'));
  const lines = raw.split('\n');
  const firstIndex = lines.findIndex(line => line.trim());
  if (firstIndex >= 0) {
    const cleaned = cleanResourceTitle(lines[firstIndex]);
    if (cleaned) lines[firstIndex] = cleaned;
  }
  return lines.join('\n').trim();
}

function renderResourcePopover(item) {
  const text = resourcePopoverText(item);
  return `<div class="resource-popover">${chipResourceText(text)}</div>`;
}

function resourcePreviewLinks(item = {}) {
  const raw = item.raw && typeof item.raw === 'object' ? item.raw : {};
  const links = [
    item.share_url,
    item.url,
    raw.share_url,
    raw.media_url,
    raw.url,
    item.preview_url,
  ].map(value => String(value || '').trim()).filter(Boolean);
  return Array.from(new Set(links));
}

function compactResourceMatchText(value = '') {
  return String(value || '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}

function isPreciseResourceRowClient(item = {}, keyword = '') {
  const target = compactResourceMatchText(keyword);
  if (!target) return true;
  const raw = item.raw && typeof item.raw === 'object' ? item.raw : {};
  const titleText = [
    item.title,
    item.subtitle,
    item.name,
    raw.title,
    raw.name,
  ].map(value => String(value || '').trim()).filter(Boolean).join(' ');
  if (titleText) return compactResourceMatchText(titleText).includes(target);
  const fallbackText = [item.full_text, raw.note, raw.text].join(' ');
  return compactResourceMatchText(fallbackText).includes(target);
}

function is115ShareLink(value = '') {
  return /^https?:\/\/(?:115|115cdn|anxia)\.com\/s\/[A-Za-z0-9]+(?:\?password=[A-Za-z0-9]+)?/i.test(String(value || '').trim());
}

function firstTransferableResourceLink(item = {}, links = resourcePreviewLinks(item)) {
  const raw = item.raw && typeof item.raw === 'object' ? item.raw : {};
  const values = [
    item.share_url,
    raw.share_url,
    item.url,
    item.preview_url,
    raw.url,
    ...links,
  ].map(value => String(value || '').trim()).filter(Boolean);
  return values.find(is115ShareLink) || '';
}

async function transferResourceItemTo115(item = {}, button = null) {
  const oldText = button?.textContent || '';
  if (button) {
    button.disabled = true;
    button.textContent = '转存中';
  }
  try {
    if (item.source_key === 'hdhive') {
      const points = Number(item.points || item.raw?.unlock_points || 0);
      if (!item.unlocked && points > 0) {
        const ok = window.confirm(`该影巢资源需要 ${points} 积分解锁。继续转存会先解锁再转存，是否继续？`);
        if (!ok) return;
      }
      const data = await api('/api/yingchao/transfer', { method: 'POST', body: JSON.stringify({ item }) });
      toast(data.ok ? '已提交转存到 115' : '转存失败');
      return;
    }
    const shareUrl = firstTransferableResourceLink(item);
    if (!shareUrl) {
      toast('没有可直接转存的 115 链接');
      return;
    }
    const data = await api('/api/115/transfer', { method: 'POST', body: JSON.stringify({ share_url: shareUrl }) });
    toast(data.ok ? '已提交转存到 115' : '转存失败');
  } catch (err) {
    toast(`转存失败：${err.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldText || '转存';
    }
  }
}

function resourcePreviewText(item = {}) {
  const title = cleanResourceTitle(item.title || item.name || '');
  const lines = [
    title,
    resourceTagParts(item).join(' '),
    item.subtitle || item.quality || '',
    item.size ? `大小：${item.size}` : '',
    item.date ? `日期：${item.date}` : '',
    item.drive || item.source_label || item.source ? `来源：${item.drive || item.source_label || item.source}` : '',
    item.password ? `提取码：${item.password}` : '',
    resourcePopoverText(item),
  ].filter(Boolean);
  return Array.from(new Set(lines.join('\n').split('\n').map(line => line.trim()).filter(Boolean))).join('\n');
}

function renderResourcePreviewPanel(item, index) {
  return '';
}

function resourcePreviewModalElements() {
  return {
    modal: document.getElementById('resource-preview-modal'),
    title: document.getElementById('resource-preview-title'),
    source: document.getElementById('resource-preview-source'),
    status: document.getElementById('resource-preview-status'),
    body: document.getElementById('resource-preview-body'),
    links: document.getElementById('resource-preview-links'),
  };
}

function setResourcePreviewModal(open) {
  const { modal } = resourcePreviewModalElements();
  if (!modal) return;
  modal.hidden = !open;
  document.body.classList.toggle('resource-preview-open', Boolean(open));
}

function setResourcePreviewLoading(item = {}) {
  const { title, source, status, body, links } = resourcePreviewModalElements();
  resourcePreviewState = { text: '', links: [], item };
  if (title) title.textContent = cleanResourceTitle(item.title || item.name || '') || '预览内容';
  if (source) source.textContent = '预览当前资源信息';
  if (status) {
    status.hidden = true;
    status.textContent = '';
  }
  if (body) body.innerHTML = '<div class="resource-preview-loading">正在整理资源信息...</div>';
  if (links) links.innerHTML = '';
  setResourcePreviewModal(true);
}

function renderResourcePreviewModal(data = {}, item = {}) {
  const { title, source, status, body, links } = resourcePreviewModalElements();
  const previewText = String(data.text || resourcePreviewText(item) || '').trim();
  const previewLinks = Array.isArray(data.links) ? data.links.filter(Boolean) : resourcePreviewLinks(item);
  resourcePreviewState = { text: previewText, links: previewLinks, item };
  if (title) title.textContent = cleanResourceTitle(data.title || item.title || item.name || '') || '预览内容';
  if (source) {
    source.textContent = previewLinks.length ? `已整理 ${previewLinks.length} 个可用链接` : '使用当前资源信息';
  }
  if (status) {
    const errors = Array.isArray(data.errors) ? data.errors.filter(Boolean) : [];
    status.hidden = !errors.length;
    status.textContent = errors.length ? `部分链接提取失败：${errors.slice(0, 2).join('；')}` : '';
  }
  if (body) {
    body.innerHTML = previewText
      ? chipResourceText(previewText)
      : '<div class="resource-preview-empty">没有可提取的预览内容</div>';
  }
  if (links) {
    const preferred = Array.isArray(data.preferred_links) ? new Set(data.preferred_links) : new Set();
    links.innerHTML = previewLinks.length
      ? previewLinks.map(link => `<a class="${preferred.has(link) ? 'preferred' : ''}" href="${escapeHtml(link)}" target="_blank" rel="noreferrer">${escapeHtml(link)}</a>`).join('')
      : '<div class="resource-preview-empty">没有可打开的链接</div>';
  }
}

async function openResourcePreviewModal(index, button = null) {
  const item = discoverResourceRows[Number(index)];
  if (!item) return;
  setResourcePreviewLoading(item);
  renderResourcePreviewModal({
    success: true,
    title: item.title || '',
    text: resourcePreviewText(item),
    links: resourcePreviewLinks(item),
    preferred_links: resourcePreviewLinks(item).filter(is115ShareLink),
  }, item);
}

function seasonKeysForRow(row) {
  const text = `${row.title || ''} ${row.subtitle || ''} ${row.quality || ''}`;
  const keys = new Set();
  if (row.season) keys.add(String(row.season));
  for (const match of text.matchAll(/第?\s*(\d{1,2})\s*[-~至]\s*(\d{1,2})\s*季/g)) {
    const start = Number(match[1]);
    const end = Number(match[2]);
    if (start > 0 && end >= start && end <= 60) {
      for (let value = start; value <= end; value += 1) keys.add(String(value));
    }
  }
  for (const match of text.matchAll(/(?:第\s*)?(\d{1,2})\s*季全/g)) {
    const end = Number(match[1]);
    if (end > 0 && end <= 60) {
      for (let value = 1; value <= end; value += 1) keys.add(String(value));
    }
  }
  for (const match of text.matchAll(/\bS(\d{1,2})\b/gi)) {
    keys.add(String(Number(match[1])));
  }
  return Array.from(keys);
}

function seasonGroupsForRows(rows) {
  const groups = new Map();
  for (const row of rows) {
    const seasonKeys = seasonKeysForRow(row);
    const episodes = Array.isArray(row.episodes) ? row.episodes : [];
    if (!seasonKeys.length && !episodes.length) continue;
    const keys = seasonKeys.length ? seasonKeys : ['1'];
    for (const key of keys) {
      if (!groups.has(key)) groups.set(key, new Set());
      for (const ep of episodes) {
        const num = Number(ep);
        if (num > 0 && num <= 300) groups.get(key).add(num);
      }
    }
  }
  return Array.from(groups.entries())
    .sort(([a], [b]) => (Number(a) || 999) - (Number(b) || 999))
    .map(([season, episodes]) => ({ season, episodes: Array.from(episodes).sort((a, b) => a - b) }));
}

function renderDiscoverSeasonStrip(rows, root = document) {
  const strip = root.querySelector('.discover-resource-season-strip');
  if (!strip) return;
  const seasons = seasonGroupsForRows(rows);
  if (!seasons.length) {
    strip.hidden = true;
    strip.innerHTML = '';
    return;
  }
  strip.hidden = false;
  strip.innerHTML = seasons.map(season => `
    <div class="discover-season-group">
      <span>第 ${escapeHtml(season.season || '1')} 季</span>
      <div class="discover-episode-grid">
        ${season.episodes.map(ep => `<button type="button" class="discover-episode-pill">${escapeHtml(ep)}</button>`).join('')}
      </div>
    </div>
  `).join('');
}

function renderDiscoverResourceRows(sourceKey = 'all', root = document) {
  const list = root.querySelector('.discover-resource-results');
  if (!list) return;
  const rows = discoverResourceRows
    .map((item, originalIndex) => ({ item, originalIndex }))
    .filter(row => sourceKey === 'all' || row.item.source_key === sourceKey);
  renderDiscoverSeasonStrip(rows.map(row => row.item), root);
  if (!rows.length) {
    const canPushMoviePilot = root.id === 'my-subscription-resource-panel';
    list.innerHTML = `
      <div class="discover-resource-empty">没有搜索到资源</div>
      ${canPushMoviePilot ? '<div class="discover-resource-empty-actions"><button type="button" data-moviepilot-push-current>推送到 MoviePilot 订阅</button><button type="button" data-torra-push-current>推送到 Torra 订阅</button><button type="button" data-symedia-push-current>推送到 Symedia 订阅</button></div>' : ''}
    `;
    return;
  }
  list.innerHTML = rows.map(({ item, originalIndex }) => {
    const subline = resourceSublineText(item);
    const title = cleanResourceTitle(item.title || '');
    return `
      <article class="discover-resource-row">
        <div class="discover-resource-main">
          <div class="discover-resource-title-wrap">
            <h4>${escapeHtml(title)}</h4>
            ${renderResourcePopover(item)}
          </div>
          ${renderResourceTags(item)}
          ${subline ? `<p class="discover-resource-subline">${escapeHtml(subline)}</p>` : ''}
          <p class="discover-resource-meta">${escapeHtml([item.drive || item.source_label || item.source, item.size, item.date].filter(Boolean).join('  '))}</p>
          <p class="discover-resource-origin">${escapeHtml([item.source_label || item.source, item.date].filter(Boolean).join('  ·  '))}</p>
        </div>
        <div class="discover-resource-actions">
          ${item.url || item.preview_url ? `<a class="resource-action-btn resource-action-link" href="${escapeHtml(item.url || item.preview_url)}" target="_blank" rel="noreferrer">链接</a>` : ''}
          <button type="button" class="resource-action-btn resource-action-preview" data-resource-preview="${originalIndex}" aria-expanded="false">预览</button>
          <button type="button" class="resource-action-btn resource-action-transfer" data-resource-transfer="${originalIndex}">转存</button>
        </div>
        ${renderResourcePreviewPanel(item, originalIndex)}
      </article>
    `;
  }).join('');
}

function initDiscoverPage() {
  if (!discoverInitialized) {
    renderDiscoverShell();
    document.getElementById('discover-search-button')?.addEventListener('click', () => {
      const keyword = document.getElementById('discover-search-input')?.value.trim() || '';
      if (!keyword) return toast('请输入影片名');
      discoverSearch = { active: true, title: keyword, type: document.getElementById('discover-search-type')?.value || '' };
      discoverPage = 1;
      loadDiscoverData();
    });
    document.getElementById('discover-search-input')?.addEventListener('keydown', event => {
      if (event.key === 'Enter') document.getElementById('discover-search-button')?.click();
    });
    discoverInitialized = true;
  }
  if (!document.getElementById('discover-poster-grid')?.__items) loadDiscoverData();
}

async function runGlobalSearch() {
  const input = document.getElementById('global-search-input');
  const keyword = input?.value.trim() || '';
  if (!keyword) return;
  setActiveView('discover');
  syncPageHeaderFromCurrentView();
  initDiscoverPage();
  const discoverInput = document.getElementById('discover-search-input');
  if (discoverInput) discoverInput.value = keyword;
  discoverSearch = {
    active: true,
    title: keyword,
    type: document.getElementById('discover-search-type')?.value || '',
  };
  discoverPage = 1;
  await loadDiscoverData();
}

document.getElementById('global-search-input')?.addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    runGlobalSearch().catch(err => toast(`搜索失败：${err.message}`));
  }
});

document.addEventListener('keydown', async event => {
  if (event.key === 'Escape' && !document.getElementById('resource-preview-modal')?.hidden) {
    setResourcePreviewModal(false);
    return;
  }
  const ruleKeywordInput = event.target.closest('[data-resource-keyword-input]');
  if (ruleKeywordInput && event.key === 'Enter') {
    event.preventDefault();
    addSubscriptionResourceKeyword(ruleKeywordInput.dataset.resourceKeywordInput || '');
    return;
  }
  if (event.target?.id === 'subscription-source-trigger' && (event.key === 'Enter' || event.key === ' ')) {
    event.preventDefault();
    const menu = document.getElementById('subscription-source-menu');
    setSubscriptionSourceMenu(Boolean(menu?.hidden));
    return;
  }
  const quickInput = event.target.closest('#telegram-channel-quick-input');
  if (!quickInput) return;
  if (event.key === 'Enter') {
    event.preventDefault();
    try {
      await saveTelegramQuickChannel();
    } catch (err) {
      toast(`频道保存失败：${err.message}`);
    }
  }
  if (event.key === 'Escape') {
    telegramQuickAddOpen = false;
    renderTelegramChannelBoard(telegramChannels);
  }
});

document.addEventListener('click', async event => {
  if (closeResourcePanelsForOutsideClick(event)) return;

  const subTab = event.target.closest('[data-subscription-tab]');
  if (subTab) {
    mySubscriptionTab = subTab.dataset.subscriptionTab || 'tv';
    syncMySubscriptionTabs();
    renderSubscriptionPosterList();
    renderSubscriptionCalendar();
    return;
  }
  const subFilter = event.target.closest('[data-sub-filter]');
  if (subFilter) {
    const key = subFilter.dataset.subFilter;
    mySubscriptionFilters[key] = subFilter.dataset.value || 'all';
    subFilter.parentElement.querySelectorAll('[data-sub-filter]').forEach(btn => btn.classList.remove('active'));
    subFilter.classList.add('active');
    renderSubscriptionPosterList();
    return;
  }
  if (event.target.closest('#my-sub-calendar-today')) {
    const now = new Date();
    mySubscriptionCalendar.year = now.getFullYear();
    mySubscriptionCalendar.month = now.getMonth();
    renderSubscriptionCalendar();
    return;
  }
  if (event.target.closest('#my-sub-calendar-prev')) {
    mySubscriptionCalendar.month -= 1;
    if (mySubscriptionCalendar.month < 0) {
      mySubscriptionCalendar.month = 11;
      mySubscriptionCalendar.year -= 1;
    }
    renderSubscriptionCalendar();
    return;
  }
  if (event.target.closest('#my-sub-calendar-next')) {
    mySubscriptionCalendar.month += 1;
    if (mySubscriptionCalendar.month > 11) {
      mySubscriptionCalendar.month = 0;
      mySubscriptionCalendar.year += 1;
    }
    renderSubscriptionCalendar();
    return;
  }
  const calendarType = event.target.closest('[data-calendar-type]');
  if (calendarType) {
    mySubscriptionCalendar.type = calendarType.dataset.calendarType || 'all';
    calendarType.parentElement.querySelectorAll('[data-calendar-type]').forEach(btn => btn.classList.remove('active'));
    calendarType.classList.add('active');
    renderSubscriptionCalendar();
    return;
  }
  const calendarView = event.target.closest('[data-calendar-view]');
  if (calendarView) {
    mySubscriptionCalendar.view = calendarView.dataset.calendarView || 'month';
    calendarView.parentElement.querySelectorAll('[data-calendar-view]').forEach(btn => btn.classList.remove('active'));
    calendarView.classList.add('active');
    renderSubscriptionCalendar();
    return;
  }
  const sourceRemove = event.target.closest('[data-subscription-remove]');
  if (sourceRemove) {
    event.stopPropagation();
    const key = sourceRemove.dataset.subscriptionRemove;
    const selected = selectedSubscriptionSources().filter(value => value !== key);
    renderSubscriptionSources(selected.length ? selected : latestSubscriptionSources);
    setSubscriptionSourceMenu(false);
    return;
  }
  if (event.target.closest('#subscription-source-trigger')) {
    const menu = document.getElementById('subscription-source-menu');
    setSubscriptionSourceMenu(Boolean(menu?.hidden));
    return;
  }
  if (!event.target.closest('.subscription-source-select')) {
    setSubscriptionSourceMenu(false);
  }
  const resourceRuleChip = event.target.closest('[data-resource-rule-chip]');
  if (resourceRuleChip) {
    cycleSubscriptionRuleChip(resourceRuleChip);
    return;
  }
  const keywordAdd = event.target.closest('[data-resource-keyword-add]');
  if (keywordAdd) {
    addSubscriptionResourceKeyword(keywordAdd.dataset.resourceKeywordAdd || '');
    return;
  }
  const keywordRemove = event.target.closest('[data-resource-keyword-remove]');
  if (keywordRemove) {
    logActivityEvent('change_resource_keyword', `移除资源关键词：${keywordRemove.dataset.value || ''}`, {
      group: keywordRemove.dataset.resourceKeywordRemove || '',
      keyword: keywordRemove.dataset.value || '',
      operation: 'remove',
    }, { category: 'subscription' });
    keywordRemove.remove();
    return;
  }
  if (event.target.closest('#subscription-latest-preset')) {
    applyLatestSubscriptionPreset();
    return;
  }
  if (event.target.closest('[data-subscription-detail-close]')) {
    closeMySubscriptionDetail();
    return;
  }
  const subscriptionSeasonTab = event.target.closest('[data-sub-detail-season-tab]');
  if (subscriptionSeasonTab) {
    const browser = subscriptionSeasonTab.closest('.my-sub-detail-season-browser');
    const index = subscriptionSeasonTab.dataset.subDetailSeasonTab || '0';
    browser?.querySelectorAll('[data-sub-detail-season-tab]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.subDetailSeasonTab === index);
    });
    browser?.querySelectorAll('[data-sub-detail-season-panel]').forEach(panel => {
      panel.hidden = panel.dataset.subDetailSeasonPanel !== index;
    });
    return;
  }
  const subscriptionDetailRefresh = event.target.closest('[data-subscription-detail-refresh]');
  if (subscriptionDetailRefresh) {
    await openMySubscriptionDetail(subscriptionDetailRefresh.dataset.subscriptionDetailRefresh || '', true);
    return;
  }
  const subscriptionDetailSearch = event.target.closest('[data-subscription-detail-search]');
  if (subscriptionDetailSearch) {
    await searchMySubscriptionResources(subscriptionDetailSearch.dataset.subscriptionDetailSearch || '');
    return;
  }
  const subscriptionMenu = event.target.closest('[data-subscription-menu]');
  if (subscriptionMenu) {
    event.stopPropagation();
    const key = subscriptionMenu.dataset.subscriptionMenu || '';
    activeSubscriptionMenuKey = activeSubscriptionMenuKey === key ? '' : key;
    renderSubscriptionPosterList();
    return;
  }
  const subscriptionDetail = event.target.closest('[data-subscription-detail]');
  if (subscriptionDetail) {
    await openMySubscriptionDetail(subscriptionDetail.dataset.subscriptionDetail || '');
    return;
  }
  const subscriptionSearch = event.target.closest('[data-subscription-search]');
  if (subscriptionSearch) {
    await searchMySubscriptionResources(subscriptionSearch.dataset.subscriptionSearch || '', subscriptionSearch.closest('.my-sub-card'));
    return;
  }
  const subscriptionMoviePilot = event.target.closest('[data-subscription-moviepilot]');
  if (subscriptionMoviePilot) {
    activeSubscriptionMenuKey = '';
    await pushMySubscriptionToMoviePilot(subscriptionMoviePilot.dataset.subscriptionMoviepilot || '', subscriptionMoviePilot);
    return;
  }
  const subscriptionTorra = event.target.closest('[data-subscription-torra]');
  if (subscriptionTorra) {
    activeSubscriptionMenuKey = '';
    await pushMySubscriptionToTorra(subscriptionTorra.dataset.subscriptionTorra || '', subscriptionTorra);
    return;
  }
  const subscriptionSymedia = event.target.closest('[data-subscription-symedia]');
  if (subscriptionSymedia) {
    activeSubscriptionMenuKey = '';
    await pushMySubscriptionToSymedia(subscriptionSymedia.dataset.subscriptionSymedia || '', subscriptionSymedia);
    return;
  }
  const subscriptionCopy = event.target.closest('[data-subscription-copy]');
  if (subscriptionCopy) {
    const item = subscriptionItemByKey(subscriptionCopy.dataset.subscriptionCopy || '');
    const title = item?.title || '';
    if (title) {
      try {
        if (navigator.clipboard) {
          await navigator.clipboard.writeText(title);
        } else {
          const textarea = document.createElement('textarea');
          textarea.value = title;
          document.body.appendChild(textarea);
          textarea.select();
          document.execCommand('copy');
          textarea.remove();
        }
      } catch {
        const textarea = document.createElement('textarea');
        textarea.value = title;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        textarea.remove();
      }
    }
    activeSubscriptionMenuKey = '';
    renderSubscriptionPosterList();
    toast(title ? '已复制标题' : '没有可复制的标题');
    return;
  }
  const subscriptionBlock = event.target.closest('[data-subscription-block]');
  if (subscriptionBlock) {
    activeSubscriptionMenuKey = '';
    await blockMySubscription(subscriptionBlock.dataset.subscriptionBlock || '');
    return;
  }
  const subscriptionDelete = event.target.closest('[data-subscription-delete]');
  if (subscriptionDelete) {
    activeSubscriptionMenuKey = '';
    await deleteMySubscription(subscriptionDelete.dataset.subscriptionDelete || '');
    return;
  }
  const subscriptionUnblock = event.target.closest('[data-subscription-unblock]');
  if (subscriptionUnblock) {
    await unblockSubscriptionTitle(subscriptionUnblock.dataset.subscriptionUnblock || '');
    return;
  }
  if (!event.target.closest('.my-sub-menu')) {
    activeSubscriptionMenuKey = '';
    document.querySelectorAll('.my-sub-menu:not([hidden])').forEach(menu => {
      menu.hidden = true;
    });
  }
  if (event.target.closest('[data-telegram-channel-add]')) {
    telegramQuickAddOpen = true;
    renderTelegramChannelBoard(telegramChannels);
    return;
  }
  if (event.target.closest('[data-telegram-channel-cancel]')) {
    telegramQuickAddOpen = false;
    renderTelegramChannelBoard(telegramChannels);
    return;
  }
  if (event.target.closest('[data-telegram-channel-save]')) {
    try {
      await saveTelegramQuickChannel();
    } catch (err) {
      toast(`频道保存失败：${err.message}`);
    }
    return;
  }
  const telegramChannelMode = event.target.closest('[data-telegram-channel-mode]');
  if (telegramChannelMode) {
    const row = telegramChannelMode.closest('[data-telegram-channel-index]');
    const index = Number(row?.dataset.telegramChannelIndex || -1);
    const mode = normalizeTelegramChannelMode(telegramChannelMode.dataset.telegramChannelMode);
    if (index >= 0 && telegramChannels[index]) {
      telegramChannels[index] = normalizeTelegramChannelItem({ ...telegramChannels[index], mode });
      renderTelegramChannelBoard(telegramChannels);
      try {
        await saveTelegramChannelSettings(`频道模式已切换为：${telegramChannelModeLabel(mode)}`);
      } catch (err) {
        toast(`频道模式保存失败：${err.message}`);
        await refreshTelegramStatus(false).catch(() => {});
      }
    }
    return;
  }
  const telegramChannelToggle = event.target.closest('[data-telegram-channel-toggle]');
  if (telegramChannelToggle) {
    const row = telegramChannelToggle.closest('[data-telegram-channel-index]');
    const index = Number(row?.dataset.telegramChannelIndex || -1);
    if (index >= 0 && telegramChannels[index]) {
      const enabled = normalizeTelegramChannelItem(telegramChannels[index]).enabled === false;
      telegramChannels[index] = normalizeTelegramChannelItem({ ...telegramChannels[index], enabled });
      renderTelegramChannelBoard(telegramChannels);
      try {
        await saveTelegramChannelSettings(enabled ? '频道已启用' : '频道已停用');
      } catch (err) {
        toast(`频道状态保存失败：${err.message}`);
        await refreshTelegramStatus(false).catch(() => {});
      }
    }
    return;
  }
  const telegramChannelAction = event.target.closest('[data-telegram-channel-action]');
  if (telegramChannelAction) {
    const row = telegramChannelAction.closest('[data-telegram-channel-index]');
    const index = Number(row?.dataset.telegramChannelIndex || -1);
    if (index >= 0) await mutateTelegramChannel(index, telegramChannelAction.dataset.telegramChannelAction);
    return;
  }
  const sourceTab = event.target.closest('.discover-source-tab');
  if (sourceTab) {
    document.querySelectorAll('.discover-source-tab').forEach(x => x.classList.remove('active'));
    sourceTab.classList.add('active');
    discoverSearch = { active: false, title: '', type: '' };
    discoverPage = 1;
    updateDiscoverFilterPanel();
    await loadDiscoverData();
    return;
  }
  const filterChip = event.target.closest('.discover-filter-chip');
  if (filterChip) {
    filterChip.parentElement.querySelectorAll('.discover-filter-chip').forEach(x => x.classList.remove('active'));
    filterChip.classList.add('active');
    discoverSearch = { active: false, title: '', type: '' };
    discoverPage = 1;
    await loadDiscoverData();
    return;
  }
  const pageAction = event.target.closest('[data-discover-page]');
  if (pageAction) {
    const action = pageAction.dataset.discoverPage;
    if (action === 'prev') discoverPage = Math.max(1, discoverPage - 1);
    if (action === 'next') discoverPage += 1;
    if (action === 'jump') discoverPage = Math.max(1, Number(document.getElementById('discover-page-input')?.value || 1));
    await loadDiscoverData();
    return;
  }
  const pageNumber = event.target.closest('[data-discover-page-number]');
  if (pageNumber) {
    discoverPage = Math.max(1, Number(pageNumber.dataset.discoverPageNumber || 1));
    await loadDiscoverData();
    return;
  }
  if (event.target.closest('[data-discover-page-clear]')) {
    discoverPage = 1;
    await loadDiscoverData();
    return;
  }
  const discoverAction = event.target.closest('[data-discover-action]');
  if (discoverAction) {
    const card = discoverAction.closest('.discover-poster-card');
    const item = document.getElementById('discover-poster-grid').__items?.[Number(card?.dataset.index)];
    if (!item) return;
    if (discoverAction.dataset.discoverAction === 'search') await openDiscoverResourceSearch(item, resourcePanelForCard(card));
    if (discoverAction.dataset.discoverAction === 'subscribe') await toggleDiscoverSubscription(item, discoverAction);
    return;
  }
  const discoverCardBlank = event.target.closest('.discover-poster-card');
  if (discoverCardBlank && !event.target.closest('button, a, input, select, textarea')) {
    const panel = document.getElementById('discover-resource-panel');
    if (panel && !panel.hidden) {
      clearDiscoverResourcePanel();
      return;
    }
  }
  const resourceTab = event.target.closest('.discover-resource-tab');
  if (resourceTab) {
    resourceTab.parentElement.querySelectorAll('.discover-resource-tab').forEach(x => x.classList.remove('active'));
    resourceTab.classList.add('active');
    renderDiscoverResourceRows(resourceTab.dataset.source || 'all', resourceTab.closest('.discover-resource-panel') || document);
    return;
  }
  const moviePilotCurrent = event.target.closest('[data-moviepilot-push-current]');
  if (moviePilotCurrent) {
    const panel = moviePilotCurrent.closest('.discover-resource-panel');
    const item = panel?.__resourceSearchItem;
    if (!item) return toast('没有找到当前订阅条目');
    moviePilotCurrent.disabled = true;
    const oldText = moviePilotCurrent.textContent;
    moviePilotCurrent.textContent = '推送中';
    try {
      await pushMoviePilotSubscription(item);
    } catch (err) {
      toast(`MoviePilot 推送失败：${err.message}`);
    } finally {
      moviePilotCurrent.disabled = false;
      moviePilotCurrent.textContent = oldText;
    }
    return;
  }
  const torraCurrent = event.target.closest('[data-torra-push-current]');
  if (torraCurrent) {
    const panel = torraCurrent.closest('.discover-resource-panel');
    const item = panel?.__resourceSearchItem;
    if (!item) return toast('没有找到当前订阅条目');
    torraCurrent.disabled = true;
    const oldText = torraCurrent.textContent;
    torraCurrent.textContent = '推送中';
    try {
      await pushTorraSubscription(item);
    } catch (err) {
      toast(`Torra 推送失败：${err.message}`);
    } finally {
      torraCurrent.disabled = false;
      torraCurrent.textContent = oldText;
    }
    return;
  }
  const symediaCurrent = event.target.closest('[data-symedia-push-current]');
  if (symediaCurrent) {
    const panel = symediaCurrent.closest('.discover-resource-panel');
    const item = panel?.__resourceSearchItem;
    if (!item) return toast('没有找到当前订阅条目');
    symediaCurrent.disabled = true;
    const oldText = symediaCurrent.textContent;
    symediaCurrent.textContent = '推送中';
    try {
      await pushSymediaSubscription(item);
    } catch (err) {
      toast(`Symedia 推送失败：${err.message}`);
    } finally {
      symediaCurrent.disabled = false;
      symediaCurrent.textContent = oldText;
    }
    return;
  }
  if (event.target.closest('[data-resource-close]')) {
    const panel = event.target.closest('.discover-resource-panel');
    if (panel) panel.hidden = true;
    return;
  }
  if (event.target.closest('[data-resource-preview-close]')) {
    setResourcePreviewModal(false);
    return;
  }
  const previewCopyBtn = event.target.closest('[data-resource-preview-copy]');
  if (previewCopyBtn) {
    const value = resourcePreviewState.text || '';
    if (!value) {
      toast('没有可复制内容');
      return;
    }
    await navigator.clipboard.writeText(value);
    toast('已复制内容');
    return;
  }
  const previewTransferBtn = event.target.closest('[data-resource-preview-transfer]');
  if (previewTransferBtn) {
    await transferResourceItemTo115(resourcePreviewState.item || {}, previewTransferBtn);
    return;
  }
  const copyBtn = event.target.closest('[data-copy]');
  if (copyBtn) {
    await navigator.clipboard.writeText(copyBtn.dataset.copy || '');
    toast(copyBtn.classList.contains('resource-preview-copy') ? '已复制内容' : '已复制链接');
    return;
  }
  const previewBtn = event.target.closest('[data-resource-preview]');
  if (previewBtn) {
    await openResourcePreviewModal(previewBtn.dataset.resourcePreview, previewBtn);
    return;
  }
  const transferBtn = event.target.closest('[data-resource-transfer]');
  if (transferBtn) {
    const index = Number(transferBtn.dataset.resourceTransfer);
    const item = discoverResourceRows[index];
    if (!item) return;
    await transferResourceItemTo115(item, transferBtn);
    return;
  }
});

function isHDHiveAuthorized(data) {
  const status = data?.status || {};
  return status.auth_required !== true && (status.authorized === true || status.has_access_token === true || Boolean(data?.account?.hash));
}

function formatHDHiveTime(seconds) {
  const value = Number(seconds || 0);
  if (!value) return '-';
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false });
}

function hdhiveAccountName(data) {
  return data?.account?.display_name || '影巢已授权账号';
}

function hdhiveAccountId(data) {
  const shortHash = data?.account?.short_hash || '';
  return shortHash ? `账号标识 ${shortHash}` : '账号标识 -';
}

function setHDHiveCheckbox(id, value) {
  const checked = ['1', 'true', 'yes', 'on'].includes(String(value || '').toLowerCase());
  const el = document.getElementById(id);
  if (el) el.checked = checked;
}

function applyHDHiveConfig(cfg = {}) {
  for (const key of hdhiveConfigFields) {
    const el = document.getElementById(key);
    if (!el || !(key in cfg)) continue;
    if (el.type === 'checkbox') {
      el.checked = ['1', 'true', 'yes', 'on'].includes(String(cfg[key] || '').toLowerCase());
    } else {
      el.value = cfg[key] || '';
    }
  }
  setHDHiveCheckbox('hdhive-account-checkin-enabled', cfg.ENV_HDHIVE_CHECKIN_ENABLED);
  setHDHiveCheckbox('hdhive-account-gambler', cfg.ENV_HDHIVE_CHECKIN_GAMBLER);
}

function collectHDHiveConfig() {
  const payload = {};
  for (const key of hdhiveConfigFields) {
    const editorId = key === 'ENV_HDHIVE_CHECKIN_ENABLED'
      ? 'hdhive-account-checkin-enabled'
      : key === 'ENV_HDHIVE_CHECKIN_GAMBLER'
        ? 'hdhive-account-gambler'
        : '';
    const el = document.getElementById(editorId) || document.getElementById(key);
    if (!el) continue;
    payload[key] = el.type === 'checkbox' ? (el.checked ? '1' : '0') : el.value;
  }
  return payload;
}

function renderHDHiveAccount(data) {
  const list = document.getElementById('hdhive-account-list');
  if (!list) return;
  const authorized = isHDHiveAuthorized(data);
  if (!authorized) {
    list.innerHTML = `
      <div class="hdhive-account-row">
        <button class="hdhive-authorize-card" type="button" data-hdhive-authorize>
          <span>+</span>
          <strong>授权新账号</strong>
        </button>
      </div>
    `;
    return;
  }
  const cfg = data.config || {};
  const status = data.status || {};
  const account = data.account || {};
  const displayName = hdhiveAccountName(data);
  const enabled = ['1', 'true', 'yes', 'on'].includes(String(cfg.ENV_HDHIVE_CHECKIN_ENABLED || '').toLowerCase());
  const gambler = ['1', 'true', 'yes', 'on'].includes(String(cfg.ENV_HDHIVE_CHECKIN_GAMBLER || '').toLowerCase());
  list.innerHTML = `
    <div class="hdhive-account-row">
      <button class="hdhive-account-card hdhive-account-card-button" type="button" data-hdhive-action="focus-account">
        <strong>${escapeHtml(displayName)}</strong>
        <span>${escapeHtml(hdhiveAccountId(data))}</span>
        <div class="hdhive-account-tags">
          <em>主账号</em>
          <em>${enabled ? '签到开' : '签到关'}</em>
          <em>资源请求账号</em>
        </div>
        <p>有效期 ${escapeHtml(formatHDHiveTime(status.expires_at))}</p>
      </button>
      <button class="hdhive-authorize-card" type="button" data-hdhive-authorize>
        <span>+</span>
        <strong>授权新账号</strong>
      </button>
    </div>
    <section class="hdhive-account-editor" id="hdhive-account-editor">
      <div class="hdhive-account-editor-head">
        <div>
          <strong>${escapeHtml(displayName)}</strong>
          <span>${escapeHtml(account.short_hash || '-')}</span>
        </div>
        <em>资源请求账号</em>
      </div>
      <div class="hdhive-account-meta">
        <label>账号标识<input value="${escapeHtml(account.short_hash || '')}" readonly></label>
        <label>授权来源<input value="${escapeHtml(account.display_source === 'api' ? '影巢接口' : account.display_source === 'local' ? '手动命名' : '授权令牌')}" readonly></label>
        <label>授权有效期<input value="${escapeHtml(formatHDHiveTime(status.expires_at))}" readonly></label>
      </div>
      <label class="hdhive-display-name">显示名称<input id="hdhive-account-display-name" value="${escapeHtml(displayName)}"></label>
      <div class="hdhive-account-switches">
        <label class="settings-switch card-switch">
          <input id="hdhive-account-checkin-enabled" type="checkbox" ${enabled ? 'checked' : ''}>
          <span class="settings-switch-ui"></span>
          <strong>启用该账号签到</strong>
        </label>
        <label class="settings-switch card-switch">
          <input id="hdhive-account-gambler" type="checkbox" ${gambler ? 'checked' : ''}>
          <span class="settings-switch-ui"></span>
          <strong>赌狗模式</strong>
        </label>
      </div>
      <div class="hdhive-account-actions">
        <button class="ghost success" type="button" data-hdhive-action="manual-checkin">手动签到</button>
        <button class="ghost" type="button" data-hdhive-authorize>重新授权</button>
        <button type="button" data-hdhive-action="save-account">保存账号</button>
      </div>
    </section>
  `;
}

function renderHDHiveCheckinHistory(data) {
  const panel = document.querySelector('[data-hdhive-panel="checkin"]');
  if (!panel) return;
  let history = panel.querySelector('#hdhive-checkin-history');
  if (!history) {
    history = document.createElement('div');
    history.id = 'hdhive-checkin-history';
    history.className = 'hdhive-checkin-history';
    panel.appendChild(history);
  }
  const rows = data?.checkin_state?.checkin_history || [];
  if (!rows.length) {
    history.innerHTML = '<div class="hdhive-empty">暂无签到记录</div>';
    return;
  }
  history.innerHTML = `
    <div class="hdhive-history-head">
      <span>时间</span><span>结果</span><span>活动</span><span>积分</span>
    </div>
    ${rows.map(row => `
      <div class="hdhive-history-row">
        <span>${escapeHtml(row.time || '-')}</span>
        <strong class="${row.ok ? 'ok' : 'fail'}">${row.ok ? (row.checked_in ? '成功' : '已签过') : '失败'}</strong>
        <span>${escapeHtml(row.message || '-')}</span>
        <em>${Number(row.points || 0)}</em>
      </div>
    `).join('')}
  `;
}

function renderHDHiveStatus(data) {
  window.__hdhiveData = data || {};
  const authorized = isHDHiveAuthorized(data);
  const cfg = data?.config || {};
  const enabled = ['1', 'true', 'yes', 'on'].includes(String(cfg.ENV_HDHIVE_CHECKIN_ENABLED || '').toLowerCase());
  const accountCount = document.getElementById('hdhive-account-count');
  const checkinText = document.getElementById('hdhive-checkin-account-text');
  const nextCheckin = document.getElementById('hdhive-next-checkin');
  const checkinMessage = document.getElementById('hdhive-checkin-message');
  if (accountCount) {
    accountCount.textContent = authorized ? '1 个账号' : '0 个账号';
    accountCount.className = authorized ? 'badge ok' : 'badge warn';
  }
  if (checkinText) checkinText.textContent = authorized ? '1 个账号' : '0 个账号';
  if (nextCheckin) nextCheckin.textContent = enabled ? (data?.checkin_state?.next_checkin_at || '等待调度') : '未启用';
  const last = (data?.checkin_state?.checkin_history || [])[0];
  if (checkinMessage) {
    checkinMessage.textContent = last
      ? `${last.time || ''} ${last.ok ? (last.checked_in ? '签到成功' : '今日已签到') : '签到失败'}，积分 ${Number(last.points || 0)}`
      : (authorized ? '暂无签到记录' : '请先授权影巢账号');
  }
  renderHDHiveAccount(data || {});
  applyHDHiveConfig(cfg);
  renderHDHiveCheckinHistory(data || {});
}

async function refreshHDHiveStatus(showToast = false) {
  try {
    const data = await api('/api/hdhive/status');
    renderHDHiveStatus(data);
    if (showToast) toast('影巢授权状态已刷新');
  } catch (err) {
    renderHDHiveStatus({ ok: false, error: err.message, config: collectHDHiveConfig() });
    if (showToast) toast(`影巢状态刷新失败：${err.message}`);
  }
}

function resourcePanelForCard(card) {
  const panel = document.getElementById('discover-resource-panel');
  clearDiscoverResourcePanel();
  if (panel && card) {
    panel.className = 'discover-resource-panel inline';
    card.insertAdjacentElement('afterend', panel);
  }
  return panel;
}

async function saveHDHiveAccount() {
  const displayName = document.getElementById('hdhive-account-display-name')?.value || '';
  await api('/api/hdhive/account', { method: 'POST', body: JSON.stringify({ display_name: displayName }) });
  await saveHDHiveConfig(false);
  toast('影巢账号已保存');
  refreshHDHiveStatus(false).catch(err => console.warn('影巢状态刷新失败', err));
}

async function saveHDHiveConfig(showToast = true) {
  const data = await api('/api/hdhive/config', { method: 'POST', body: JSON.stringify(collectHDHiveConfig()) });
  applyHDHiveConfig(data.config || {});
  if (showToast) toast('影巢配置已保存');
  refreshHDHiveStatus(false).catch(err => console.warn('影巢状态刷新失败', err));
}

async function runHDHiveCheckin() {
  const btn = document.getElementById('hdhive-run-checkin');
  if (btn) btn.disabled = true;
  try {
    const data = await api('/api/hdhive/checkin', { method: 'POST', body: '{}' });
    renderHDHiveStatus({ ...(await api('/api/hdhive/status')), checkin_state: data.checkin_state });
    toast('影巢签到已执行');
  } catch (err) {
    await refreshHDHiveStatus(false);
    toast(`影巢签到失败：${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

document.addEventListener('click', event => {
  const action = event.target.closest('[data-hdhive-action]')?.dataset.hdhiveAction;
  if (action === 'manual-checkin') runHDHiveCheckin();
  if (action === 'save-account') saveHDHiveAccount();
  if (action === 'focus-account') document.getElementById('hdhive-account-display-name')?.focus();
});

document.addEventListener('change', event => {
  if (event.target.id === 'hdhive-account-checkin-enabled') {
    const top = document.getElementById('ENV_HDHIVE_CHECKIN_ENABLED');
    if (top) top.checked = event.target.checked;
  }
  if (event.target.id === 'ENV_HDHIVE_CHECKIN_ENABLED') {
    const editor = document.getElementById('hdhive-account-checkin-enabled');
    if (editor) editor.checked = event.target.checked;
  }
});

function seasonKeysForRow(row) {
  const text = `${row.title || ''} ${row.subtitle || ''} ${row.quality || ''}`;
  const keys = new Set();
  if (row.season) keys.add(String(Number(row.season) || row.season));
  for (const match of text.matchAll(/第?\s*(\d{1,2})\s*[-~至到]\s*(\d{1,2})\s*季/g)) {
    const start = Number(match[1]);
    const end = Number(match[2]);
    if (start > 0 && end >= start && end <= 60) {
      for (let value = start; value <= end; value += 1) keys.add(String(value));
    }
  }
  for (const match of text.matchAll(/(?:第\s*)?(\d{1,2})\s*季/g)) {
    const value = Number(match[1]);
    if (value > 0 && value <= 60) keys.add(String(value));
  }
  for (const match of text.matchAll(/\bS(\d{1,2})\b/gi)) {
    keys.add(String(Number(match[1])));
  }
  return Array.from(keys);
}

function episodeNumbersForRow(row) {
  const text = `${row.title || ''} ${row.subtitle || ''} ${row.quality || ''}`;
  const episodes = new Set(Array.isArray(row.episodes) ? row.episodes.map(Number).filter(Boolean) : []);
  const ranges = [
    ...text.matchAll(/E(\d{1,3})\s*[-~至到]\s*E?(\d{1,3})/gi),
    ...text.matchAll(/(?:第)?\s*(\d{1,3})\s*[-~至到]\s*(\d{1,3})\s*(?:集|话|期)/g),
  ];
  for (const match of ranges) {
    const start = Number(match[1]);
    const end = Number(match[2]);
    if (start > 0 && end >= start && end <= 300) {
      for (let value = start; value <= end; value += 1) episodes.add(value);
    }
  }
  for (const match of text.matchAll(/(?:更新至|更至|更新|全|共)\s*(\d{1,3})\s*(?:集|话|期)/g)) {
    const end = Number(match[1]);
    if (end > 0 && end <= 300) {
      for (let value = 1; value <= end; value += 1) episodes.add(value);
    }
  }
  for (const match of text.matchAll(/(?:第|\b)(\d{1,3})(?:集|话|期)|E(\d{1,3})/gi)) {
    const value = Number(match[1] || match[2]);
    if (value > 0 && value <= 300) episodes.add(value);
  }
  return Array.from(episodes).sort((a, b) => a - b);
}

function seasonGroupsForRows(rows) {
  const groups = new Map();
  for (const row of rows) {
    const seasonKeys = seasonKeysForRow(row);
    const episodes = episodeNumbersForRow(row);
    if (!seasonKeys.length && !episodes.length) continue;
    const keys = seasonKeys.length ? seasonKeys : ['1'];
    for (const key of keys) {
      if (!groups.has(key)) groups.set(key, new Set());
      for (const ep of episodes) groups.get(key).add(ep);
    }
  }
  return Array.from(groups.entries())
    .sort(([a], [b]) => (Number(a) || 999) - (Number(b) || 999))
    .map(([season, episodes]) => ({ season, episodes: Array.from(episodes).sort((a, b) => a - b) }));
}

function renderDiscoverSeasonStrip(rows, root = document) {
  const strip = root.querySelector('.discover-resource-season-strip');
  if (!strip) return;
  const seasons = discoverSeasonStatus.length ? discoverSeasonStatus : seasonGroupsForRows(rows);
  if (!seasons.length) {
    strip.hidden = true;
    strip.innerHTML = '';
    return;
  }
  strip.hidden = false;
  strip.innerHTML = seasons.map(season => `
      <div class="discover-season-group">
        <span>第 ${escapeHtml(season.season || '1')} 季</span>
        <div class="discover-episode-grid">
          ${(season.episodes || []).map(ep => {
            const missing = (season.missing_episodes || []).map(Number).includes(Number(ep));
            return `<button type="button" class="discover-episode-pill${missing ? ' missing' : ''}" title="${missing ? 'Emby 缺集' : ''}">${escapeHtml(ep)}</button>`;
          }).join('') || '<em class="discover-season-warning">没有获取到集数</em>'}
        </div>
        ${season.notice ? `<em class="discover-season-warning">${escapeHtml(season.notice)}</em>` : ''}
      </div>
  `).join('');
  updateDiscoverFilterPanel();
}

loadConfig().catch(err => toast(`配置加载失败：${err.message}`));
function renderDiscoverSeasonStrip(rows, root = document) {
  const strip = root.querySelector('.discover-resource-season-strip');
  if (!strip) return;
  const seasons = discoverSeasonStatus.length ? discoverSeasonStatus : seasonGroupsForRows(rows);
  if (!seasons.length) {
    strip.hidden = true;
    strip.innerHTML = '';
    return;
  }
  const summary = seasons[0]?.summary || null;
  const summaryTotal = Number(summary?.total_episodes || 0);
  const summaryCurrent = Number(summary?.current_episodes || summary?.resource_episodes || summary?.library_episodes || 0);
  const summaryLibrary = Number(summary?.library_episodes || 0);
  const summaryHtml = summary ? `
    <div class="discover-season-summary">
      <strong>当前 ${escapeHtml(summaryCurrent)}/${escapeHtml(summaryTotal || '?')}</strong>
      <span>${escapeHtml(summary.total_seasons || seasons.length)} 季</span>
      <span>Emby ${escapeHtml(summaryLibrary)}/${escapeHtml(summaryTotal || '?')}</span>
      ${summary.missing_episodes ? `<em>缺 ${escapeHtml(summary.missing_episodes)} 集</em>` : '<em class="ok">已完整</em>'}
    </div>
  ` : '';
  strip.hidden = false;
  strip.innerHTML = summaryHtml + seasons.map(season => {
    const missingSet = new Set((season.missing_episodes || []).map(Number));
    return `
      <div class="discover-season-group">
        <span>第 ${escapeHtml(season.season || '1')} 季</span>
        <div class="discover-episode-grid">
          ${(season.episodes || []).map(ep => {
            const missing = missingSet.has(Number(ep));
            return `<button type="button" class="discover-episode-pill${missing ? ' missing' : ''}" title="${missing ? 'Emby 缺集' : 'Emby 已有'}">${escapeHtml(ep)}</button>`;
          }).join('') || '<em class="discover-season-warning">TMDB 未提供集数</em>'}
        </div>
        ${season.notice ? `<em class="discover-season-warning">${escapeHtml(season.notice)}</em>` : ''}
      </div>
    `;
  }).join('');
}

refreshHDHiveStatus(false);
refreshTelegramStatus(false);
