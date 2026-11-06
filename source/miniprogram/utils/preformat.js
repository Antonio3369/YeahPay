/**
 * 数据预格式化助手
 * 微信小程序的 WXML 不能直接调用 Page 方法（如 {{formatNum(x)}}）
 * 所有展示数据必须在 setData 之前格式化好
 */

const { fHKD, fNum, fMoneyWan, fPct, fTime, avatarChar } = require('./format');

const ALERT_RULE_LABELS = {
  inactive_days: '未激活',
  dormant_days: '已沉默',
  weekly_decline_pct: '交易周环比下降',
  monthly_decline_pct: '交易月环比下降',
  failure_ratio_pct: '交易失败',
  weekly_spike_pct: '交易异动'
};

/**
 * 格式化单个预警项
 */
function formatAlert(a) {
  const tags = a.tags || [];
  const tagLabels = a.tag_labels || tags.map(tag => tag.label || ALERT_RULE_LABELS[tag.key]).filter(Boolean);
  return {
    ...a,
    tags,
    tag_labels: tagLabels,
    tag_label_text: tagLabels.join('、'),
    status_label: ({ unfollowed: '待跟進', following: '跟進中', resolved: '已解決' })[a.status] || '',
    status_class: ({ unfollowed: 'pen', following: 'flw', resolved: 'res' })[a.status] || 'ign',
    metric_fmt: tags.length > 1 ? tags.map(tag => fPct(tag.metric_value)).join(' / ') : fPct(a.metric_value),
    created_fmt: fTime(a.created_at)
  };
}

function formatAlertList(list) {
  return (list || []).map(formatAlert);
}

function formatPush(p) {
  const alertTags = p.alert_tags || [];
  const alertTagLabels = p.alert_tag_labels || alertTags.map(tag => tag.label).filter(Boolean);
  return {
    ...p,
    status_label: p.status === 'resolved' ? '已解決' : '跟進中',
    status_class: p.status === 'resolved' ? 'res' : 'flw',
    source_label: p.source_label || (p.is_sender ? '我推送的' : '推送給我的'),
    created_fmt: fTime(p.created_at),
    touch_label: p.touch_type === 'offline' ? '線下觸達' : (p.touch_type === 'online' ? '線上觸達' : ''),
    alert_tags: alertTags,
    alert_tag_labels: alertTagLabels,
    alert_tag_label_text: alertTagLabels.join('、')
  };
}

/**
 * 格式化单个商户
 */
function formatMerchant(m) {
  return {
    ...m,
    last_amount_fmt: fHKD(m.last_month_amount),
    this_amount_fmt: fHKD(m.this_month_amount),
    mom_fmt: fPct(m.daily_mom_pct),
    mom_raw: +m.daily_mom_pct || 0
  };
}

function formatMerchantList(list) {
  return (list || []).map(formatMerchant);
}

/**
 * 格式化单个飞虎队/团队成员
 */
function formatTeamMember(t) {
  return {
    ...t,
    last_amount_fmt: fMoneyWan(t.last_month_amount),
    this_amount_fmt: fMoneyWan(t.this_month_amount),
    mom_fmt: fPct(t.daily_mom_pct),
    mom_raw: +t.daily_mom_pct || 0,
    alert_warn: +t.unfollowed_alerts > 3
  };
}

function formatTeamList(list) {
  return (list || []).map(formatTeamMember);
}

/**
 * 格式化销售画像卡片
 */
function formatSalesCard(m) {
  return {
    ...m,
    avatar: avatarChar(m.sales_name),
    mom_fmt: fPct(m.mom)
  };
}

/**
 * 格式化子页面（失败/沉默/境外卡/万事达）表格行
 */
function formatFailRow(item) {
  return {
    ...item,
    amount_fmt: fNum(item.amount || item.fail_amount || 0)
  };
}

function formatSilentRow(item) {
  const typeLabels = { sleeping: '沉睡', risky: '高危' };
  const statusLabels = {
    RECOVERY_SUGGESTED: '建議恢復',
    HIGH_RISK: '高危',
    SLEEPING: '沉睡'
  };
  const type = item.merchant_type || '';
  const rawStatus = String(item.status_tag || '').toUpperCase();
  return {
    ...item,
    amount_fmt: fNum(item.recent_30d_amount || 0),
    binding_days_fmt: (item.binding_days || 0) + ' 天',
    merchant_type_label: typeLabels[type] || type || '—',
    status_label: statusLabels[rawStatus] || '',
    status_class: type === 'risky' ? 'silent-risk' : 'silent-sleep'
  };
}

function formatOverseasRow(item) {
  return { ...item, amount_fmt: fNum(item.overseas_amount || 0) };
}

function formatMastercardRow(item) {
  return { ...item, amount_fmt: fNum(item.total_amount || 0) };
}

module.exports = {
  formatAlert,
  formatAlertList,
  formatPush,
  formatMerchant,
  formatMerchantList,
  formatTeamMember,
  formatTeamList,
  formatSalesCard,
  formatFailRow,
  formatSilentRow,
  formatOverseasRow,
  formatMastercardRow
};
