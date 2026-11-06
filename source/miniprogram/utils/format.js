/**
 * 数据格式化工具模块
 * 统一金额、百分比、数字的展示格式
 */

/**
 * 格式化金额为香港元显示
 * 例：287654321.5 → "HK$2.9億"
 *     38901234.0  → "HK$3890.1萬"
 *     5000.0      → "HK$5000"
 */
function fHKD(v) {
  v = +(v || 0);
  if (v >= 1e8) return 'HK$' + (v / 1e8).toFixed(1) + '億';
  if (v >= 1e4) return 'HK$' + (v / 1e4).toFixed(1) + '萬';
  if (v <= 0) return 'HK$0';
  return 'HK$' + v.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

/**
 * 完整香港元金额：保留实际金额，使用千位分隔符。
 * 例：1207711 → "HK$1,207,711"
 */
function fHKDFull(v) {
  v = +(v || 0);
  if (!Number.isFinite(v) || v === 0) return 'HK$0';
  const sign = v < 0 ? '-' : '';
  const amount = Math.abs(v).toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2
  });
  return sign + 'HK$' + amount;
}

/**
 * 仅返回数字（万单位），无前缀无单位
 * 例：4003000   → "400.3"
 *     40000000  → "4000"
 *     4500      → "0.5"
 */
function fNum(v) {
  v = +(v || 0);
  if (v <= 0) return '0';
  const wan = v / 10000;
  if (wan >= 10000) return (wan / 10000).toFixed(2);           // 亿级
  if (wan >= 1) return wan.toFixed(1).replace(/\.0$/, '');      // ≥1万：去掉末尾 .0
  return wan.toFixed(1);                                         // <1万：保留 1 位小数
}

/**
 * 紧凑金额（万/亿），适合工作台 KPI、图表坐标轴和提示。
 * 统一保留一位小数：6037374 → "603.7萬"；123456789 → "1.2億"
 */
function fCompactAmount(v) {
  v = +(v || 0);
  if (!Number.isFinite(v) || v <= 0) return '0.0萬';
  if (v >= 1e8) return (v / 1e8).toFixed(1) + '億';
  return (v / 1e4).toFixed(1) + '萬';
}

/** 紧凑香港元金额，适合图表坐标轴和 tooltip。 */
function fCompactHKD(v) {
  return 'HK$' + fCompactAmount(v);
}

/**
 * 紧凑金额：以“万”为单位，适合窄表格显示。
 * 例如 225400 -> "$23万"，不显示 HK 和小数。
 */
function fMoneyWan(v) {
  v = +(v || 0);
  if (v === 0) return '$0';
  const sign = v < 0 ? '-' : '';
  const wan = Math.round(Math.abs(v) / 10000);
  return wan === 0 ? '$0' : sign + '$' + wan + '万';
}

/**
 * 格式化百分比
 * 例：12.5  → "+12.5%"
 *     -11.6 → "-11.6%"
 */
function fPct(v) {
  v = +(v || 0);
  return (v > 0 ? '+' : '') + v.toFixed(1) + '%';
}

/**
 * 格式化日期
 * 例：2026-07-28 → "07-28"
 */
function fDate(dateStr) {
  if (!dateStr) return '';
  return dateStr.substring(5, 10);
}

/**
 * 格式化时间
 * 例：2026-07-28T09:15:00 → "07-28 09:15"
 */
function fTime(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return mm + '-' + dd + ' ' + hh + ':' + min;
}

/**
 * 获取头像首字母
 */
function avatarChar(name) {
  return (name || '').trim().charAt(0).toUpperCase();
}

module.exports = {
  fHKD,
  fHKDFull,
  fNum,
  fCompactAmount,
  fCompactHKD,
  fMoneyWan,
  fPct,
  fDate,
  fTime,
  avatarChar
};
