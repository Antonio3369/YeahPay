/**
 * 小程序本地数据集：从聊天文件导入 JSON/CSV，写入 USER_DATA_PATH，供 local-api 查询。
 */

const DATASET_FILE = 'yeahpay-dataset.json';
const META_KEY = 'yp_dataset_meta';

function filePath() {
  return (wx.env && wx.env.USER_DATA_PATH ? wx.env.USER_DATA_PATH : '') + '/' + DATASET_FILE;
}

function pad2(n) {
  return n < 10 ? '0' + n : String(n);
}

function ymd(value) {
  const text = String(value || '').trim();
  const match = text.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (!match) return '';
  return match[1] + '-' + pad2(+match[2]) + '-' + pad2(+match[3]);
}

function addDays(dateStr, days) {
  const parts = dateStr.split('-').map(Number);
  const dt = new Date(parts[0], parts[1] - 1, parts[2] + days);
  return dt.getFullYear() + '-' + pad2(dt.getMonth() + 1) + '-' + pad2(dt.getDate());
}

function monthStart(dateStr) {
  return dateStr.slice(0, 8) + '01';
}

function shiftMonth(dateStr, delta) {
  const y = +dateStr.slice(0, 4);
  const m = +dateStr.slice(5, 7) + delta;
  const dt = new Date(y, m - 1, 1);
  return dt.getFullYear() + '-' + pad2(dt.getMonth() + 1) + '-01';
}

function daysInMonth(dateStr) {
  const y = +dateStr.slice(0, 4);
  const m = +dateStr.slice(5, 7);
  return new Date(y, m, 0).getDate();
}

function isSuccess(status) {
  const s = String(status || '').trim();
  if (!s) return true;
  const lower = s.toLowerCase();
  return s === '交易成功' || s === '成功' || lower === 'success' || lower === 'ok';
}

function isFail(status) {
  const s = String(status || '');
  const lower = s.toLowerCase();
  return s.indexOf('失败') >= 0 || s.indexOf('失敗') >= 0 || lower === 'failed' || lower === 'fail';
}

function num(value) {
  const n = Number(String(value == null ? '' : value).replace(/,/g, ''));
  return Number.isFinite(n) ? n : 0;
}

function pick(row, keys) {
  for (let i = 0; i < keys.length; i++) {
    if (row[keys[i]] != null && row[keys[i]] !== '') return row[keys[i]];
  }
  return '';
}

function normalizeHeader(name) {
  return String(name || '').replace(/^\ufeff/, '').trim().toLowerCase();
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = '';
  let inQuotes = false;
  const src = String(text || '').replace(/^\ufeff/, '');
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (inQuotes) {
      if (ch === '"') {
        if (src[i + 1] === '"') { cell += '"'; i++; }
        else inQuotes = false;
      } else cell += ch;
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      row.push(cell);
      cell = '';
    } else if (ch === '\n') {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
    } else if (ch !== '\r') {
      cell += ch;
    }
  }
  if (cell.length || row.length) {
    row.push(cell);
    rows.push(row);
  }
  if (!rows.length) return [];
  const headers = rows[0].map(normalizeHeader);
  return rows.slice(1).filter(r => r.some(v => String(v).trim() !== '')).map(r => {
    const obj = {};
    headers.forEach((h, idx) => { obj[h] = r[idx] == null ? '' : String(r[idx]).trim(); });
    return obj;
  });
}

function asMerchant(row) {
  const id = String(pick(row, ['id', 'merchant_id', '商户编号', '商戶編號', 'merchantid']) || '').trim();
  if (!id) return null;
  return {
    id,
    name: String(pick(row, ['name', 'merchant_name', '商户名称', '商戶名稱', 'short_name']) || id),
    short_name: String(pick(row, ['short_name', 'merchant_short_name', '简称', '簡稱']) || pick(row, ['name', 'merchant_name']) || id),
    agent_id: String(pick(row, ['agent_id', 'direct_agent_id', '代理商编号', '代理商編號']) || ''),
    agent_name: String(pick(row, ['agent_name', 'direct_agent_name', '代理商', '代理商名称']) || ''),
    sales_name: String(pick(row, ['sales_name', 'sales_person', '业务员', '業務員', 'bd_person']) || ''),
    status: String(pick(row, ['status', 'merchant_status', '状态', '狀態']) || '正常'),
    bound_sn: String(pick(row, ['bound_sn', 'sn']) || '1')
  };
}

function asTransaction(row) {
  const merchantId = String(pick(row, ['merchant_id', '商户编号', '商戶編號', 'id']) || '').trim();
  const created = ymd(pick(row, ['created_at', 'trans_date', '交易时间', '交易時間', 'date', '日期']));
  if (!merchantId || !created) return null;
  return {
    merchant_id: merchantId,
    created_at: created,
    amount: num(pick(row, ['amount', '金额', '金額', '交易额', '交易額'])),
    trans_status: String(pick(row, ['trans_status', 'status', '交易状态', '交易狀態']) || '交易成功'),
    pay_wallet: String(pick(row, ['pay_wallet', 'wallet', '卡包', '钱包', '錢包']) || 'UNKNOWN'),
    card_brand: String(pick(row, ['card_brand', '卡组织', '卡組織', 'brand']) || ''),
    country: String(pick(row, ['country', 'country_region', '国家', '國家', '地区']) || '')
  };
}

function looksLikeTransactions(rows) {
  if (!rows.length) return false;
  const keys = Object.keys(rows[0]).join(' ');
  return /amount|金额|金額|created_at|交易时间|交易時間|trans_status/.test(keys);
}

function parseObject(payload) {
  if (Array.isArray(payload)) {
    return looksLikeTransactions(payload)
      ? { merchants: [], transactions: payload.map(asTransaction).filter(Boolean) }
      : { merchants: payload.map(asMerchant).filter(Boolean), transactions: [] };
  }
  const merchantsSrc = payload.merchants || payload.merchant || [];
  const txSrc = payload.transactions || payload.ods_transactions_all || payload.items || [];
  const merchants = (Array.isArray(merchantsSrc) ? merchantsSrc : []).map(asMerchant).filter(Boolean);
  const transactions = (Array.isArray(txSrc) ? txSrc : []).map(asTransaction).filter(Boolean);
  return {
    asOf: ymd(payload.as_of || payload.asOf || payload.publish_date || payload.data_refresh_date),
    merchants,
    transactions
  };
}

function parseText(text, fileName) {
  const trimmed = String(text || '').replace(/^\ufeff/, '').trim();
  if (!trimmed) throw new Error('文件是空的');
  const lower = String(fileName || '').toLowerCase();
  let parsed;
  if (lower.endsWith('.json') || trimmed.charAt(0) === '{' || trimmed.charAt(0) === '[') {
    try {
      parsed = parseObject(JSON.parse(trimmed));
    } catch (e) {
      throw new Error('JSON 無法解析：' + (e.message || '格式錯誤'));
    }
  } else {
    const rows = parseCsv(trimmed);
    if (!rows.length) throw new Error('CSV 沒有有效行');
    parsed = looksLikeTransactions(rows)
      ? { merchants: [], transactions: rows.map(asTransaction).filter(Boolean) }
      : { merchants: rows.map(asMerchant).filter(Boolean), transactions: [] };
  }
  if (!parsed.transactions.length && !parsed.merchants.length) {
    throw new Error('未識別到商戶或交易字段。請使用 JSON（merchants/transactions）或帶表頭的 CSV。');
  }
  return parsed;
}

function inferMerchants(transactions, existing) {
  const map = {};
  (existing || []).forEach(m => { map[m.id] = m; });
  transactions.forEach(t => {
    if (!map[t.merchant_id]) {
      map[t.merchant_id] = {
        id: t.merchant_id,
        name: t.merchant_id,
        short_name: t.merchant_id,
        agent_id: '',
        agent_name: '',
        sales_name: '',
        status: '正常',
        bound_sn: '1'
      };
    }
  });
  return Object.keys(map).map(k => map[k]);
}

function summarize(bundle) {
  const dates = bundle.transactions.map(t => t.created_at).filter(Boolean).sort();
  const asOf = bundle.asOf || (dates.length ? dates[dates.length - 1] : '');
  return {
    asOf,
    merchantCount: bundle.merchants.length,
    txnCount: bundle.transactions.length,
    minDate: dates[0] || '',
    maxDate: dates[dates.length - 1] || asOf
  };
}

function mergeCandidate(base, incoming, kind) {
  const next = {
    asOf: incoming.asOf || (base && base.asOf) || '',
    merchants: (base && base.merchants) ? base.merchants.slice() : [],
    transactions: (base && base.transactions) ? base.transactions.slice() : []
  };
  if (kind === 'merchants' || incoming.merchants.length) {
    const map = {};
    next.merchants.forEach(m => { map[m.id] = m; });
    incoming.merchants.forEach(m => { map[m.id] = m; });
    next.merchants = Object.keys(map).map(k => map[k]);
  }
  if (kind === 'transactions' || incoming.transactions.length) {
    next.transactions = incoming.transactions;
    if (incoming.asOf) next.asOf = incoming.asOf;
  }
  next.merchants = inferMerchants(next.transactions, next.merchants);
  next.meta = summarize(next);
  return next;
}

let memory = null;

function loadSync() {
  if (memory) return memory;
  try {
    const fs = wx.getFileSystemManager();
    const raw = fs.readFileSync(filePath(), 'utf8');
    memory = JSON.parse(raw);
  } catch (e) {
    memory = { merchants: [], transactions: [], asOf: '', meta: summarize({ merchants: [], transactions: [] }) };
  }
  return memory;
}

function saveSync(bundle) {
  const payload = {
    asOf: bundle.meta.asOf,
    merchants: bundle.merchants,
    transactions: bundle.transactions,
    meta: bundle.meta
  };
  const fs = wx.getFileSystemManager();
  fs.writeFileSync(filePath(), JSON.stringify(payload), 'utf8');
  wx.setStorageSync(META_KEY, {
    asOf: payload.meta.asOf,
    merchantCount: payload.meta.merchantCount,
    txnCount: payload.meta.txnCount,
    minDate: payload.meta.minDate,
    maxDate: payload.meta.maxDate,
    importedAt: new Date().toISOString()
  });
  memory = payload;
  return payload.meta;
}

function publishedMeta() {
  try {
    return wx.getStorageSync(META_KEY) || loadSync().meta || {};
  } catch (e) {
    return {};
  }
}

function parseQuery(path) {
  const out = {};
  const i = String(path || '').indexOf('?');
  if (i < 0) return out;
  String(path).slice(i + 1).split('&').forEach(pair => {
    if (!pair) return;
    const eq = pair.indexOf('=');
    const k = decodeURIComponent(eq >= 0 ? pair.slice(0, eq) : pair);
    const v = decodeURIComponent(eq >= 0 ? pair.slice(eq + 1) : '');
    out[k] = v;
  });
  return out;
}

function merchantMap(ds) {
  const map = {};
  (ds.merchants || []).forEach(m => { map[String(m.id)] = m; });
  return map;
}

function scopedMerchants(ds, query) {
  const agentId = query.agent_id || '';
  const sales = query.sales_person || '';
  return (ds.merchants || []).filter(m => {
    if (m.status && String(m.status).indexOf('注销') >= 0) return false;
    if (agentId && String(m.agent_id) !== agentId) return false;
    if (sales && String(m.sales_name) !== sales) return false;
    return true;
  });
}

function scopedTxns(ds, query) {
  const allowed = {};
  scopedMerchants(ds, query).forEach(m => { allowed[m.id] = true; });
  const asOf = ds.asOf || (ds.meta && ds.meta.asOf) || '';
  return (ds.transactions || []).filter(t => allowed[t.merchant_id] && (!asOf || t.created_at <= asOf));
}

function reportWindow(ds) {
  const asOf = ds.asOf || (ds.meta && ds.meta.asOf) || '';
  if (!asOf) return null;
  const thisStart = monthStart(asOf);
  const cutoff = addDays(asOf, 1);
  const lastStart = shiftMonth(thisStart, -1);
  const day = +asOf.slice(8, 10);
  const lastDays = Math.min(day, daysInMonth(lastStart));
  const lastEnd = lastStart.slice(0, 8) + pad2(lastDays);
  return { asOf, thisStart, cutoff, lastStart, lastEnd, day };
}

function kpi(path) {
  const ds = loadSync();
  const w = reportWindow(ds);
  const merchants = scopedMerchants(ds, parseQuery(path));
  if (!w) {
    return {
      merchant_count: merchants.length,
      active_merchant_count: 0, active_rate: 0,
      this_month_amount: 0, last_month_amount: 0,
      this_month_avg: 0, last_month_avg: 0,
      fail_rate: 0, mom_pct: 0, alert_count: 0
    };
  }
  const txns = scopedTxns(ds, parseQuery(path));
  let thisAmt = 0, lastAmt = 0, thisCnt = 0, failCnt = 0;
  const active = {};
  const prevActive = {};
  txns.forEach(t => {
    const ok = isSuccess(t.trans_status);
    if (t.created_at >= w.thisStart && t.created_at < w.cutoff) {
      thisCnt += 1;
      if (isFail(t.trans_status)) failCnt += 1;
      if (ok) {
        thisAmt += t.amount;
        active[t.merchant_id] = true;
      }
    }
    if (ok && t.created_at >= w.lastStart && t.created_at <= w.lastEnd) {
      lastAmt += t.amount;
      prevActive[t.merchant_id] = true;
    }
  });
  const activeCount = Object.keys(active).length;
  const prevCount = Object.keys(prevActive).length;
  const mom = lastAmt > 0 ? Math.round((thisAmt - lastAmt) / lastAmt * 1000) / 10 : 0;
  return {
    merchant_count: merchants.length,
    active_merchant_count: activeCount,
    active_rate: merchants.length ? Math.round(activeCount / merchants.length * 1000) / 10 : 0,
    this_month_amount: thisAmt,
    last_month_amount: lastAmt,
    this_month_avg: activeCount ? thisAmt / activeCount : 0,
    last_month_avg: prevCount ? lastAmt / prevCount : 0,
    fail_rate: thisCnt ? Math.round(failCnt / thisCnt * 10000) / 100 : 0,
    mom_pct: mom,
    alert_count: 0
  };
}

function monthlyTrend(path) {
  const ds = loadSync();
  const w = reportWindow(ds);
  if (!w) return [];
  const start = shiftMonth(w.thisStart, -5);
  const buckets = {};
  scopedTxns(ds, parseQuery(path)).forEach(t => {
    if (!isSuccess(t.trans_status) || t.created_at < start || t.created_at >= w.cutoff) return;
    const key = t.created_at.slice(0, 7) + '-01';
    buckets[key] = (buckets[key] || 0) + t.amount;
  });
  return Object.keys(buckets).sort().map(month => ({
    month,
    total_amount: buckets[month],
    total_amount_wan: Math.round(buckets[month] / 10000)
  }));
}

function dailyCollection(path) {
  const ds = loadSync();
  const w = reportWindow(ds);
  if (!w) return [];
  const thisMap = {};
  const lastMap = {};
  scopedTxns(ds, parseQuery(path)).forEach(t => {
    if (!isSuccess(t.trans_status)) return;
    const day = +t.created_at.slice(8, 10);
    if (t.created_at >= w.thisStart && t.created_at < w.cutoff) {
      thisMap[day] = (thisMap[day] || 0) + t.amount;
    }
    if (t.created_at >= w.lastStart && t.created_at <= w.lastEnd) {
      lastMap[day] = (lastMap[day] || 0) + t.amount;
    }
  });
  const days = [];
  for (let d = 1; d <= w.day; d++) {
    days.push({
      day: d,
      this_month: thisMap[d] || 0,
      last_month: lastMap[d] || 0
    });
  }
  return days;
}

function groupVolume(path, field, successOnly) {
  const ds = loadSync();
  const w = reportWindow(ds);
  if (!w) return [];
  const buckets = {};
  scopedTxns(ds, parseQuery(path)).forEach(t => {
    if (t.created_at < w.thisStart || t.created_at >= w.cutoff) return;
    if (successOnly && !isSuccess(t.trans_status)) return;
    const name = t[field] || 'UNKNOWN';
    if (!buckets[name]) buckets[name] = { name, volume: 0, cnt: 0 };
    buckets[name].volume += successOnly ? t.amount : Math.abs(t.amount);
    buckets[name].cnt += 1;
  });
  return Object.keys(buckets).map(k => buckets[k]).sort((a, b) => b.volume - a.volume);
}

function merchantList(path) {
  const ds = loadSync();
  const q = parseQuery(path);
  const w = reportWindow(ds);
  let rows = scopedMerchants(ds, q);
  const keyword = (q.keyword || '').toLowerCase();
  if (keyword) {
    rows = rows.filter(m =>
      String(m.id).toLowerCase().indexOf(keyword) >= 0 ||
      String(m.name).toLowerCase().indexOf(keyword) >= 0 ||
      String(m.short_name).toLowerCase().indexOf(keyword) >= 0
    );
  }
  const txns = scopedTxns(ds, q);
  const thisAmt = {};
  const lastAmt = {};
  if (w) {
    txns.forEach(t => {
      if (!isSuccess(t.trans_status)) return;
      if (t.created_at >= w.thisStart && t.created_at < w.cutoff) {
        thisAmt[t.merchant_id] = (thisAmt[t.merchant_id] || 0) + t.amount;
      }
      if (t.created_at >= w.lastStart && t.created_at < w.thisStart) {
        lastAmt[t.merchant_id] = (lastAmt[t.merchant_id] || 0) + t.amount;
      }
    });
  }
  const items = rows.map(m => {
    const last = lastAmt[m.id] || 0;
    const cur = thisAmt[m.id] || 0;
    const daysThis = w ? w.day : 1;
    const daysLast = w ? daysInMonth(w.lastStart) : 1;
    const avgLast = last / daysLast;
    const avgThis = cur / daysThis;
    const mom = avgLast > 0 ? Math.round((avgThis - avgLast) / avgLast * 1000) / 10 : null;
    return {
      ...m,
      last_month_amount: last,
      this_month_amount: cur,
      daily_mom_pct: mom,
      trend: mom == null ? 'neutral' : (mom >= 0 ? 'up' : 'down')
    };
  }).sort((a, b) => b.last_month_amount - a.last_month_amount);
  const page = Math.max(1, +q.page || 1);
  const pageSize = Math.max(1, +q.page_size || 20);
  const start = (page - 1) * pageSize;
  return {
    total: items.length,
    page,
    page_size: pageSize,
    has_more: start + pageSize < items.length,
    items: items.slice(start, start + pageSize)
  };
}

function agents() {
  const map = {};
  loadSync().merchants.forEach(m => {
    if (!m.agent_id) return;
    if (!map[m.agent_id]) map[m.agent_id] = { agent_id: m.agent_id, agent_name: m.agent_name || m.agent_id, merchant_count: 0 };
    map[m.agent_id].merchant_count += 1;
  });
  return Object.keys(map).map(k => map[k]).sort((a, b) => b.merchant_count - a.merchant_count);
}

function sales() {
  const map = {};
  loadSync().merchants.forEach(m => {
    if (!m.sales_name) return;
    if (!map[m.sales_name]) map[m.sales_name] = { sales_name: m.sales_name, merchant_count: 0 };
    map[m.sales_name].merchant_count += 1;
  });
  return Object.keys(map).map(k => map[k]).sort((a, b) => b.merchant_count - a.merchant_count);
}

function teamRank(path) {
  const ds = loadSync();
  const w = reportWindow(ds);
  const q = parseQuery(path);
  const map = {};
  scopedMerchants(ds, q).forEach(m => {
    const name = m.sales_name || '未分配';
    if (!map[name]) {
      map[name] = { sales_name: name, merchant_count: 0, this_month_amount: 0, last_month_amount: 0, unfollowed_alerts: 0 };
    }
    map[name].merchant_count += 1;
  });
  if (w) {
    scopedTxns(ds, q).forEach(t => {
      if (!isSuccess(t.trans_status)) return;
      const m = merchantMap(ds)[t.merchant_id];
      const name = (m && m.sales_name) || '未分配';
      if (!map[name]) return;
      if (t.created_at >= w.thisStart && t.created_at < w.cutoff) map[name].this_month_amount += t.amount;
      if (t.created_at >= w.lastStart && t.created_at < w.thisStart) map[name].last_month_amount += t.amount;
    });
  }
  return Object.keys(map).map(k => {
    const row = map[k];
    const daysThis = w ? w.day : 1;
    const daysLast = w ? daysInMonth(w.lastStart) : 1;
    const avgThis = row.this_month_amount / daysThis;
    const avgLast = row.last_month_amount / daysLast;
    row.daily_mom_pct = avgLast > 0 ? Math.round((avgThis - avgLast) / avgLast * 1000) / 10 : 0;
    return row;
  }).sort((a, b) => b.this_month_amount - a.this_month_amount);
}

function failsDetail(path) {
  const ds = loadSync();
  const w = reportWindow(ds);
  const q = parseQuery(path);
  const mmap = merchantMap(ds);
  const items = [];
  scopedTxns(ds, q).forEach(t => {
    if (!isFail(t.trans_status)) return;
    if (w && t.created_at < addDays(w.asOf, -29)) return;
    const m = mmap[t.merchant_id] || {};
    items.push({
      merchant_id: t.merchant_id,
      merchant_name: m.name || t.merchant_id,
      created_at: t.created_at,
      amount: t.amount,
      trans_status: t.trans_status
    });
  });
  items.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  const page = Math.max(1, +q.page || 1);
  const pageSize = Math.max(1, +q.page_size || 30);
  const start = (page - 1) * pageSize;
  return {
    total: items.length,
    has_more: start + pageSize < items.length,
    items: items.slice(start, start + pageSize)
  };
}

function failsSummary(path) {
  const ds = loadSync();
  const w = reportWindow(ds);
  const q = parseQuery(path);
  const mmap = merchantMap(ds);
  const byMerchant = {};
  scopedTxns(ds, q).forEach(t => {
    if (!isFail(t.trans_status)) return;
    if (w && t.created_at < addDays(w.asOf, -29)) return;
    const m = mmap[t.merchant_id] || {};
    if (!byMerchant[t.merchant_id]) {
      byMerchant[t.merchant_id] = { merchant_id: t.merchant_id, merchant_name: m.name || t.merchant_id, fail_count: 0, amount: 0 };
    }
    byMerchant[t.merchant_id].fail_count += 1;
    byMerchant[t.merchant_id].amount += t.amount;
  });
  return Object.keys(byMerchant).map(k => byMerchant[k]).sort((a, b) => b.fail_count - a.fail_count);
}

function silentSummary(path) {
  const ds = loadSync();
  const w = reportWindow(ds);
  const q = parseQuery(path);
  const last = {};
  scopedTxns(ds, q).forEach(t => {
    if (!isSuccess(t.trans_status)) return;
    if (!last[t.merchant_id] || t.created_at > last[t.merchant_id]) last[t.merchant_id] = t.created_at;
  });
  let sleeping = 0;
  let risky = 0;
  scopedMerchants(ds, q).forEach(m => {
    const lastDay = last[m.id];
    if (!w || !lastDay) { sleeping += 1; return; }
    const gap = (new Date(w.asOf) - new Date(lastDay)) / 86400000;
    if (gap >= 180) risky += 1;
    else if (gap >= 14) sleeping += 1;
  });
  return {
    total: sleeping + risky,
    sleeping,
    risky,
    thresholds: { silent_days: 14, silent_max_days: 180, silent_amount: 300, risky_days: 180, risky_amount: 100, risky_lookback_days: 90 }
  };
}

function silentList(path) {
  const ds = loadSync();
  const w = reportWindow(ds);
  const q = parseQuery(path);
  const last = {};
  const amt = {};
  scopedTxns(ds, q).forEach(t => {
    if (!isSuccess(t.trans_status)) return;
    if (!last[t.merchant_id] || t.created_at > last[t.merchant_id]) last[t.merchant_id] = t.created_at;
    amt[t.merchant_id] = (amt[t.merchant_id] || 0) + t.amount;
  });
  let rows = scopedMerchants(ds, q).map(m => {
    const lastDay = last[m.id] || '';
    const gap = w && lastDay ? Math.floor((new Date(w.asOf) - new Date(lastDay)) / 86400000) : 999;
    return {
      ...m,
      merchant_id: m.id,
      last_trans_date: lastDay,
      silent_days: gap,
      amount: amt[m.id] || 0
    };
  }).filter(m => m.silent_days >= 14);
  const keyword = (q.keyword || '').toLowerCase();
  if (keyword) rows = rows.filter(m => String(m.name).toLowerCase().indexOf(keyword) >= 0 || String(m.id).indexOf(keyword) >= 0);
  const page = Math.max(1, +q.page || 1);
  const pageSize = Math.max(1, +q.page_size || 20);
  const start = (page - 1) * pageSize;
  return { total: rows.length, has_more: start + pageSize < rows.length, items: rows.slice(start, start + pageSize) };
}

function mastercardRank(path) {
  const ds = loadSync();
  const w = reportWindow(ds);
  const q = parseQuery(path);
  const map = {};
  scopedTxns(ds, q).forEach(t => {
    if (!w || t.created_at < w.thisStart || t.created_at >= w.cutoff) return;
    if (!isSuccess(t.trans_status)) return;
    const brand = String(t.card_brand || '').toLowerCase();
    if (brand.indexOf('master') < 0 && brand.indexOf('万事达') < 0 && brand.indexOf('萬事達') < 0) return;
    const m = merchantMap(ds)[t.merchant_id] || { id: t.merchant_id, name: t.merchant_id };
    if (!map[t.merchant_id]) map[t.merchant_id] = { merchant_id: t.merchant_id, name: m.name, amount: 0 };
    map[t.merchant_id].amount += t.amount;
  });
  let items = Object.keys(map).map(k => map[k]).sort((a, b) => b.amount - a.amount);
  const keyword = (q.keyword || '').toLowerCase();
  if (keyword) items = items.filter(r => String(r.name).toLowerCase().indexOf(keyword) >= 0);
  const page = Math.max(1, +q.page || 1);
  const pageSize = Math.max(1, +q.page_size || 20);
  const start = (page - 1) * pageSize;
  return { total: items.length, has_more: start + pageSize < items.length, items: items.slice(start, start + pageSize) };
}

function overseasTop(path) {
  const ds = loadSync();
  const w = reportWindow(ds);
  const q = parseQuery(path);
  const map = {};
  scopedTxns(ds, q).forEach(t => {
    if (!w || t.created_at < w.thisStart || t.created_at >= w.cutoff) return;
    if (!isSuccess(t.trans_status)) return;
    const country = String(t.country || '');
    const overseas = country && country !== 'HK' && country !== '香港' && country.toLowerCase() !== 'hong kong';
    if (!overseas) return;
    const m = merchantMap(ds)[t.merchant_id] || { id: t.merchant_id, name: t.merchant_id };
    if (!map[t.merchant_id]) map[t.merchant_id] = { merchant_id: t.merchant_id, name: m.name, amount: 0, country };
    map[t.merchant_id].amount += t.amount;
  });
  let items = Object.keys(map).map(k => map[k]).sort((a, b) => b.amount - a.amount).slice(0, 10);
  const keyword = (q.keyword || '').toLowerCase();
  if (keyword) items = items.filter(r => String(r.name).toLowerCase().indexOf(keyword) >= 0);
  return { items };
}

function merchantInfo(id) {
  const m = merchantMap(loadSync())[id];
  if (!m) return { id, name: id, short_name: id };
  return { ...m, short_name: m.short_name || m.name };
}

function merchantBreakdown(id) {
  const ds = loadSync();
  const w = reportWindow(ds);
  const wallet = {};
  const status = {};
  (ds.transactions || []).forEach(t => {
    if (t.merchant_id !== id) return;
    if (w && (t.created_at < w.thisStart || t.created_at >= w.cutoff)) return;
    if (isSuccess(t.trans_status)) {
      wallet[t.pay_wallet] = (wallet[t.pay_wallet] || 0) + t.amount;
    }
    status[t.trans_status] = (status[t.trans_status] || 0) + Math.abs(t.amount);
  });
  return {
    wallet: Object.keys(wallet).map(name => ({ name, volume: wallet[name] })),
    status: Object.keys(status).map(name => ({ name, volume: status[name] }))
  };
}

function merchantDaily(id) {
  const ds = loadSync();
  const w = reportWindow(ds);
  const days = {};
  let mtdAmt = 0;
  let mtdCount = 0;
  (ds.transactions || []).forEach(t => {
    if (t.merchant_id !== id || !isSuccess(t.trans_status)) return;
    if (w && t.created_at >= w.thisStart && t.created_at < w.cutoff) {
      mtdAmt += t.amount;
      mtdCount += 1;
    }
    if (!w || addDays(w.asOf, -29) <= t.created_at) {
      days[t.created_at] = days[t.created_at] || { period: t.created_at, amount: 0, count: 0 };
      days[t.created_at].amount += t.amount;
      days[t.created_at].count += 1;
    }
  });
  return {
    daily_stats: Object.keys(days).sort().map(k => days[k]),
    mtd: { amount: mtdAmt, count: mtdCount }
  };
}

function agentInfo(agentId) {
  const ds = loadSync();
  const w = reportWindow(ds);
  const merchants = ds.merchants.filter(m => String(m.agent_id) === String(agentId));
  let volume = 0;
  if (w) {
    const allowed = {};
    merchants.forEach(m => { allowed[m.id] = true; });
    ds.transactions.forEach(t => {
      if (allowed[t.merchant_id] && isSuccess(t.trans_status) && t.created_at >= w.thisStart && t.created_at < w.cutoff) {
        volume += t.amount;
      }
    });
  }
  const first = merchants[0] || {};
  return { agent_id: agentId, agent_name: first.agent_name || agentId, merchant_count: merchants.length, this_month_volume: volume };
}

function salesInfo(name) {
  const ds = loadSync();
  const w = reportWindow(ds);
  const merchants = ds.merchants.filter(m => m.sales_name === name);
  let vol = 0;
  if (w) {
    const allowed = {};
    merchants.forEach(m => { allowed[m.id] = true; });
    ds.transactions.forEach(t => {
      if (allowed[t.merchant_id] && isSuccess(t.trans_status) && t.created_at >= w.thisStart && t.created_at < w.cutoff) vol += t.amount;
    });
  }
  return {
    sales_name: name,
    this_month_vol: vol,
    merchant_count: merchants.length,
    previous_merchant_count: merchants.length,
    active_count: merchants.length,
    previous_active_count: merchants.length
  };
}

function salesMerchants(name, path) {
  const q = parseQuery(path);
  q.sales_person = name;
  return merchantList('?' + Object.keys(q).map(k => k + '=' + encodeURIComponent(q[k])).join('&'));
}

function salesPerformanceTrend(path) {
  return monthlyTrend(path);
}

function salesPerformanceRank(path) {
  return teamRank(path).map(row => ({
    sales_name: row.sales_name,
    new_volume: row.this_month_amount,
    merchant_count: row.merchant_count
  }));
}

function salesPerformanceSummary(path) {
  const rank = teamRank(path);
  return {
    merchants: rank.reduce((s, r) => s + r.merchant_count, 0),
    volume: rank.reduce((s, r) => s + r.this_month_amount, 0)
  };
}

function salesPerformanceHistory() {
  return [];
}

module.exports = {
  parseText,
  mergeCandidate,
  saveSync,
  loadSync,
  publishedMeta,
  kpi,
  monthlyTrend,
  dailyCollection,
  groupVolume,
  merchantList,
  agents,
  sales,
  teamRank,
  failsDetail,
  failsSummary,
  silentSummary,
  silentList,
  mastercardRank,
  overseasTop,
  merchantInfo,
  merchantBreakdown,
  merchantDaily,
  agentInfo,
  salesInfo,
  salesMerchants,
  salesPerformanceTrend,
  salesPerformanceRank,
  salesPerformanceSummary,
  salesPerformanceHistory
};
