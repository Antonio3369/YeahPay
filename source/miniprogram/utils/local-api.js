/**
 * 小程序本地数据层：账号在 storage，看板数据来自上传文件（dataset）。
 */

const { LOCAL_DB_KEY, ROLE_MAP } = require('./config');
const { getRole, getToken } = require('./auth');
const dataset = require('./dataset');

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

  if (pathname === '/api/v1/auth/me') return currentUser();

  if (pathname === '/api/v1/scope/agents') return dataset.agents();
  if (pathname === '/api/v1/scope/sales') return dataset.sales();

  if (pathname === '/api/v1/v2/dashboard/kpi') return dataset.kpi(path);
  if (pathname === '/api/v1/v2/dashboard/monthly-trend') return dataset.monthlyTrend(path);
  if (pathname === '/api/v1/v2/dashboard/daily-collection') return dataset.dailyCollection(path);
  if (pathname === '/api/v1/v2/dashboard/wallet-distribution') return dataset.groupVolume(path, 'pay_wallet', true);
  if (pathname === '/api/v1/v2/dashboard/status-distribution') return dataset.groupVolume(path, 'trans_status', false);

  if (pathname === '/api/v1/v2/merchants/list') return dataset.merchantList(path);
  if (pathname === '/api/v1/v2/silent/list') return dataset.silentList(path);
  if (pathname === '/api/v1/v2/silent/summary') return dataset.silentSummary(path);
  if (pathname === '/api/v1/fails/summary') return dataset.failsSummary(path);
  if (pathname === '/api/v1/fails/detail') return dataset.failsDetail(path);
  if (pathname === '/api/v1/alerts') return emptyPage();
  if (pathname === '/api/v1/pushes' && method === 'GET') return emptyPage();
  if (pathname === '/api/v1/pushes' && method === 'POST') return { id: 0, ok: true };
  if (pathname === '/api/v1/pushes/inbox') return emptyPage();
  if (pathname === '/api/v1/pushes/recipients') return [];
  if (/^\/api\/v1\/(alerts|pushes)\/\d+\/(followup|resolve|start)$/.test(pathname)) return { ok: true };
  if (pathname === '/api/v1/reviews') return emptyPage();
  if (/^\/api\/v1\/reviews\/[^/]+\/[^/]+$/.test(pathname)) {
    return { source: { id: '', name: '本地文件' }, records: [], touch_count: 0 };
  }
  if (/^\/api\/v1\/reviews\/[^/]+\/[^/]+\/effect$/.test(pathname)) {
    return { available: false, data_ready: false, reason: '預警復盤仍為本地空數據' };
  }

  if (pathname === '/api/v1/v2/team/rank') return dataset.teamRank(path);
  if (pathname === '/api/v1/v2/sales-performance/rank') return dataset.salesPerformanceRank(path);
  if (pathname === '/api/v1/v2/sales-performance/trend') return dataset.salesPerformanceTrend(path);
  if (pathname === '/api/v1/v2/sales-performance/existing-trend') return dataset.salesPerformanceTrend(path);
  if (pathname === '/api/v1/v2/sales-performance/history') return dataset.salesPerformanceHistory();
  if (pathname === '/api/v1/v2/sales-performance/summary') return dataset.salesPerformanceSummary(path);
  if (pathname === '/api/v1/v2/overseas/top10') return dataset.overseasTop(path);
  if (pathname === '/api/v1/v2/mastercard/rank') return dataset.mastercardRank(path);

  if (/^\/api\/v1\/v2\/entity\/merchant\/[^/]+\/info$/.test(pathname)) {
    return dataset.merchantInfo(decodeURIComponent(pathname.split('/')[6] || ''));
  }
  if (/^\/api\/v1\/v2\/entity\/merchant\/[^/]+\/breakdown$/.test(pathname)) {
    return dataset.merchantBreakdown(decodeURIComponent(pathname.split('/')[6] || ''));
  }
  if (/^\/api\/v1\/merchants\/[^/]+$/.test(pathname)) {
    return dataset.merchantDaily(decodeURIComponent(pathname.split('/').pop()));
  }
  if (/^\/api\/v1\/v2\/entity\/agent\/[^/]+$/.test(pathname)) {
    return dataset.agentInfo(decodeURIComponent(pathname.split('/').pop()));
  }
  if (/\/breakdown$/.test(pathname) && pathname.indexOf('/entity/agent/') !== -1) {
    return { wallet: [], status: [] };
  }
  if (/\/trend$/.test(pathname) && pathname.indexOf('/entity/agent/') !== -1) return [];
  if (/^\/api\/v1\/v2\/entity\/sales\/[^/]+$/.test(pathname)) {
    return dataset.salesInfo(decodeURIComponent(pathname.split('/').pop()));
  }
  if (/\/breakdown$/.test(pathname) && pathname.indexOf('/entity/sales/') !== -1) {
    return { wallet: [], status: [] };
  }
  if (/\/merchants$/.test(pathname) && pathname.indexOf('/entity/sales/') !== -1) {
    const name = decodeURIComponent(pathname.split('/')[6] || '');
    return dataset.salesMerchants(name, path);
  }

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
    const meta = dataset.publishedMeta() || {};
    const has = !!(meta.txnCount || meta.merchantCount);
    return {
      can_publish: false,
      candidate_exists: has,
      publish_date: meta.asOf || '',
      a_table: { latest_date: meta.asOf || '' },
      b_table: { latest_date: meta.maxDate || meta.asOf || '' },
      local_cloud_consistency: {
        text: has ? '已從本地文件載入' : '尚未上傳數據文件',
        consistent: has,
        checked_at: meta.importedAt || ''
      },
      candidate_validation: { ok: has, issues: has ? [] : ['請上傳 JSON 或 CSV'], warnings: [] },
      entity_changes: {
        merchant_added: meta.merchantCount || 0,
        merchant_removed: 0,
        agent_added: 0,
        agent_removed: 0,
        sales_added: 0,
        sales_removed: 0,
        stale_binding_count: 0
      }
    };
  }
  if (pathname === '/api/v1/v2/admin/confirm-data-refresh') {
    throw new Error('請在本頁直接上傳並確認文件，不再走後端發布');
  }

  console.warn('[local-api] unhandled', method, pathname);
  if (method === 'GET') return emptyPage();
  return { ok: true };
}

module.exports = { handle };
