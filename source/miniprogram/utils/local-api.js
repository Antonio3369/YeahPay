/**
 * 小程序本地数据层（不发起网络请求）
 * 登录态写在本地 storage；列表/KPI 返回空结构，保证页面可进、图表不因缺字段崩溃。
 */

const { LOCAL_DB_KEY, ROLE_MAP } = require('./config');
const { getRole, getToken } = require('./auth');

const DEFAULT_THRESHOLDS = [
  { threshold_key: 'inactive_days', label: '未激活天數', value: 14 },
  { threshold_key: 'dormant_days', label: '已沉默天數', value: 30 },
  { threshold_key: 'weekly_decline_pct', label: '交易周環比下降', value: 30 },
  { threshold_key: 'monthly_decline_pct', label: '交易月環比下降', value: 30 },
  { threshold_key: 'failure_ratio_pct', label: '交易失敗比例', value: 20 },
  { threshold_key: 'weekly_spike_pct', label: '交易異動', value: 200 }
];

function emptyPage() {
  return { items: [], total: 0, has_more: false, summary: { cases: 0, resolved: 0, following: 0, avg_touch_count: 0, avg_resolve_days: 0 } };
}

function roleFromUsername(username) {
  const u = String(username || '').trim().toLowerCase();
  if (u === 'sales' || u === '销售' || u === '銷售') return 'sales';
  if (u === 'supervisor' || u === 'agent' || u === '代理' || u === '代理商') return 'supervisor';
  return 'admin';
}

function displayName(username, role) {
  return username || ROLE_MAP[role] || '用戶';
}

function readDb() {
  try {
    const raw = wx.getStorageSync(LOCAL_DB_KEY);
    if (raw && typeof raw === 'object') return raw;
  } catch (e) { /* ignore */ }
  return {
    users: [
      {
        id: 1,
        username: 'admin',
        role: 'admin',
        is_active: true,
        display_name: '管理員',
        scope_type: 'all',
        scope_value: '',
        scope_label: '全部機構',
        email: null,
        phone: null
      }
    ],
    thresholds: DEFAULT_THRESHOLDS.map(row => ({ ...row })),
    nextUserId: 2
  };
}

function writeDb(db) {
  try {
    wx.setStorageSync(LOCAL_DB_KEY, db);
  } catch (e) {
    console.error('[local-api] persist failed', e);
  }
}

function currentUser() {
  const token = getToken();
  const role = getRole() || 'admin';
  let username = 'admin';
  const prefix = 'yp-local-';
  if (token && token.indexOf(prefix) === 0) {
    const rest = token.slice(prefix.length);
    const dash = rest.indexOf('-');
    username = dash >= 0 ? decodeURIComponent(rest.slice(dash + 1)) : rest;
  }
  return {
    username,
    role,
    display_name: displayName(username, role),
    scope_label: role === 'admin' ? '全部機構' : displayName(username, role),
    scope_type: role === 'admin' ? 'all' : role
  };
}

function parsePath(path) {
  const qIndex = path.indexOf('?');
  return qIndex >= 0 ? path.slice(0, qIndex) : path;
}

function handle(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const data = options.data || {};
  const pathname = parsePath(path);

  if (pathname === '/api/v1/auth/login' && method === 'POST') {
    const username = String(data.username || '').trim();
    const password = data.password;
    if (!username) throw new Error('請輸入賬號');
    if (!password) throw new Error('請輸入密碼');
    const role = roleFromUsername(username);
    const token = 'yp-local-' + role + '-' + encodeURIComponent(username);
    const db = readDb();
    if (!db.users.some(u => u.username === username)) {
      db.users.push({
        id: db.nextUserId++,
        username,
        role,
        is_active: true,
        display_name: displayName(username, role),
        scope_type: role === 'admin' ? 'all' : role,
        scope_value: '',
        scope_label: role === 'admin' ? '全部機構' : displayName(username, role),
        email: null,
        phone: null
      });
      writeDb(db);
    }
    const user = db.users.find(u => u.username === username);
    return {
      access_token: token,
      token_type: 'bearer',
      user: { username, role, display_name: user.display_name }
    };
  }

  if (pathname === '/api/v1/auth/me') {
    return currentUser();
  }

  if (pathname === '/api/v1/scope/agents') return [];
  if (pathname === '/api/v1/scope/sales') return [];

  if (pathname === '/api/v1/v2/dashboard/kpi') {
    return {
      merchant_count: 0,
      active_merchant_count: 0,
      active_rate: 0,
      this_month_amount: 0,
      last_month_amount: 0,
      this_month_avg: 0,
      last_month_avg: 0,
      fail_rate: 0,
      mom_pct: 0,
      alert_count: 0
    };
  }
  if (pathname === '/api/v1/v2/dashboard/monthly-trend') return [];
  if (pathname === '/api/v1/v2/dashboard/daily-collection') return [];
  if (pathname === '/api/v1/v2/dashboard/wallet-distribution') return [];
  if (pathname === '/api/v1/v2/dashboard/status-distribution') return [];

  if (pathname === '/api/v1/v2/merchants/list') return emptyPage();
  if (pathname === '/api/v1/v2/silent/list') return emptyPage();
  if (pathname === '/api/v1/v2/silent/summary') {
    return { total: 0, sleeping: 0, risky: 0, thresholds: { silent_days: 14, silent_max_days: 180, silent_amount: 300, risky_days: 180, risky_amount: 100, risky_lookback_days: 90 } };
  }
  if (pathname === '/api/v1/fails/summary') return [];
  if (pathname === '/api/v1/fails/detail') return emptyPage();
  if (pathname === '/api/v1/alerts') return emptyPage();
  if (pathname === '/api/v1/pushes' && method === 'GET') return emptyPage();
  if (pathname === '/api/v1/pushes' && method === 'POST') return { id: 0, ok: true };
  if (pathname === '/api/v1/pushes/inbox') return emptyPage();
  if (pathname === '/api/v1/pushes/recipients') return [];
  if (/^\/api\/v1\/(alerts|pushes)\/\d+\/(followup|resolve|start)$/.test(pathname)) return { ok: true };
  if (pathname === '/api/v1/reviews') return emptyPage();
  if (/^\/api\/v1\/reviews\/[^/]+\/[^/]+$/.test(pathname)) {
    return { source: { id: '', name: '本地模式' }, records: [], touch_count: 0 };
  }
  if (/^\/api\/v1\/reviews\/[^/]+\/[^/]+\/effect$/.test(pathname)) {
    return { available: false, data_ready: false, reason: '小程序未接入 API' };
  }

  if (pathname === '/api/v1/v2/team/rank') return [];
  if (pathname === '/api/v1/v2/sales-performance/rank') return [];
  if (pathname === '/api/v1/v2/sales-performance/trend') return [];
  if (pathname === '/api/v1/v2/sales-performance/existing-trend') return [];
  if (pathname === '/api/v1/v2/sales-performance/history') return [];
  if (pathname === '/api/v1/v2/sales-performance/summary') return { merchants: 0, volume: 0 };
  if (pathname === '/api/v1/v2/overseas/top10') return emptyPage();
  if (pathname === '/api/v1/v2/mastercard/rank') return emptyPage();

  if (/^\/api\/v1\/v2\/entity\/merchant\/[^/]+\/info$/.test(pathname)) {
    const id = decodeURIComponent(pathname.split('/')[6] || '');
    return { id, name: '本地模式（無遠端數據）', short_name: id || '本地' };
  }
  if (/^\/api\/v1\/v2\/entity\/merchant\/[^/]+\/breakdown$/.test(pathname)) {
    return { wallet: [], status: [] };
  }
  if (/^\/api\/v1\/merchants\/[^/]+$/.test(pathname)) {
    return { daily_stats: [], mtd: {} };
  }
  if (/^\/api\/v1\/v2\/entity\/agent\/[^/]+$/.test(pathname) && !pathname.endsWith('/breakdown') && !pathname.endsWith('/trend')) {
    const id = decodeURIComponent(pathname.split('/').pop());
    return { agent_id: id, agent_name: id, this_month_volume: 0 };
  }
  if (/\/breakdown$/.test(pathname) && pathname.indexOf('/entity/') !== -1) {
    return { wallet: [], status: [] };
  }
  if (/\/trend$/.test(pathname) && pathname.indexOf('/entity/agent/') !== -1) return [];
  if (/^\/api\/v1\/v2\/entity\/sales\/[^/]+$/.test(pathname)) {
    const name = decodeURIComponent(pathname.split('/').pop());
    return {
      sales_name: name,
      this_month_vol: 0,
      merchant_count: 0,
      previous_merchant_count: 0,
      active_count: 0,
      previous_active_count: 0
    };
  }
  if (/\/merchants$/.test(pathname) && pathname.indexOf('/entity/sales/') !== -1) return emptyPage();

  if (pathname === '/api/v1/admin/users' && method === 'GET') return readDb().users;
  if (pathname === '/api/v1/admin/users' && method === 'POST') {
    const db = readDb();
    const username = String(data.username || '').trim();
    if (!username) throw new Error('請輸入賬號');
    if (db.users.some(u => u.username === username)) throw new Error('賬號已存在');
    const user = {
      id: db.nextUserId++,
      username,
      role: data.role || 'sales',
      is_active: true,
      display_name: data.display_name || username,
      scope_type: data.scope_type || (data.role === 'admin' ? 'all' : data.role),
      scope_value: data.scope_value || '',
      scope_label: data.display_name || username,
      sales_names: data.sales_names || [],
      email: data.email || null,
      phone: data.phone || null
    };
    db.users.push(user);
    writeDb(db);
    return user;
  }
  if (/^\/api\/v1\/admin\/users\/\d+$/.test(pathname) && method === 'PUT') {
    const id = Number(pathname.split('/').pop());
    const db = readDb();
    const user = db.users.find(u => u.id === id);
    if (!user) throw new Error('賬號不存在');
    Object.assign(user, data);
    writeDb(db);
    return user;
  }
  if (/\/password$/.test(pathname) && method === 'PUT') return { ok: true };

  if (pathname === '/api/v1/admin/thresholds' && method === 'GET') return readDb().thresholds;
  if (pathname.indexOf('/api/v1/admin/thresholds/') === 0 && method === 'PUT') {
    const key = decodeURIComponent(pathname.slice('/api/v1/admin/thresholds/'.length));
    const db = readDb();
    const row = db.thresholds.find(item => item.threshold_key === key);
    if (row) row.value = data.value;
    writeDb(db);
    return row || { threshold_key: key, value: data.value };
  }
  if (pathname === '/api/v1/v2/admin/regenerate-alerts') {
    return { scanned: 0, matched: 0, inserted: 0, stale_closed: 0 };
  }
  if (pathname === '/api/v1/v2/admin/data-refresh-status') {
    return {
      can_publish: false,
      candidate_exists: false,
      publish_date: '',
      a_table: { latest_date: '' },
      b_table: { latest_date: '' },
      local_cloud_consistency: { text: '小程序未接入 API', consistent: false, checked_at: '' },
      candidate_validation: { ok: false, issues: ['小程序不請求後端'], warnings: [] },
      entity_changes: {}
    };
  }
  if (pathname === '/api/v1/v2/admin/confirm-data-refresh') {
    throw new Error('小程序未接入 API，無法確認數據發布');
  }

  console.warn('[local-api] unhandled', method, pathname);
  if (method === 'GET') return emptyPage();
  return { ok: true };
}

module.exports = { handle };
