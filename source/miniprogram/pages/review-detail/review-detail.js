const { get } = require('../../utils/api');
const { fCompactHKD } = require('../../utils/format');
const CHART = require('../../utils/chart-theme');

Page({
  data: { loading: true, sourceType: '', source: null, records: [], touchCount: 0, effect: null, effectView: null, ecEffect: { lazyLoad: true } },
  onReady() {
    this._pageReady = true;
    this.initEffectChart();
  },
  onLoad(options) {
    if (!options.kind || !options.id) { wx.showToast({ title: '缺少復盤記錄', icon: 'none' }); return; }
    this.loadDetail(options.kind, options.id);
  },
  async loadDetail(kind, id) {
    try {
      const [result, effect] = await Promise.all([
        get('/api/v1/reviews/' + kind + '/' + id),
        get('/api/v1/reviews/' + kind + '/' + id + '/effect', { silent: true }).catch(e => ({ available: false, reason: e.message }))
      ]);
      this.setData({ loading: false, sourceType: kind, source: result.source, records: result.records || [], touchCount: result.touch_count || 0, effect, effectView: this.buildEffectView(effect) }, () => this.initEffectChart());
    } catch (e) {
      this.setData({ loading: false });
      wx.showToast({ title: e.message || '復盤讀取失敗', icon: 'none' });
    }
  },

  buildEffectView(effect) {
    if (!effect || !effect.data_ready) return null;
    const pct = value => value === null || value === undefined ? '—' : ((value > 0 ? '+' : '') + Number(value).toFixed(1) + '%');
    return {
      resolvedDate: effect.resolved_date,
      latestDate: effect.latest_data_date,
      beforeAmount: fCompactHKD(effect.before_amount), beforeCount: effect.before_count,
      after7Amount: fCompactHKD(effect.after7_amount), after7Count: effect.after7_count,
      after7Days: effect.after7_days, after7Change: pct(effect.after7_amount_change_pct),
      after30Amount: fCompactHKD(effect.after30_amount), after30Count: effect.after30_count,
      after30Days: effect.after30_days, after30Change: pct(effect.after30_amount_change_pct),
      effectLabel: effect.effect_label
    };
  },

  initEffectChart() {
    if (!this._pageReady || !this.data.effect || !this.data.effect.data_ready) return;
    const component = this.selectComponent('#reviewEffectChart');
    if (!component || typeof component.init !== 'function') return;
    const trend = this.data.effect.trend || [];
    component.init((canvas, width, height, dpr) => {
      const echartsLib = wx._echarts;
      if (!echartsLib) return null;
      const chart = echartsLib.init(canvas, null, { width, height, devicePixelRatio: dpr });
      chart.setOption({
        grid: { left: 44, right: 18, top: 18, bottom: 30 },
        xAxis: { type: 'category', boundaryGap: false, data: trend.map(row => String(row.period).slice(5)), axisLine: { lineStyle: { color: CHART.axis } }, axisLabel: { color: CHART.muted, fontSize: 10, interval: Math.max(Math.floor(trend.length / 5), 1) } },
        yAxis: { type: 'value', axisLabel: { color: CHART.muted, fontSize: 10, formatter: value => fCompactHKD(value) }, splitLine: { lineStyle: { color: CHART.grid } } },
        tooltip: { trigger: 'axis', confine: true, backgroundColor: CHART.surface, borderColor: CHART.primary, textStyle: { color: CHART.text }, formatter: params => { const item = params[0]; return item.axisValue + '\n' + fCompactHKD(item.value); } },
        series: [{ type: 'line', smooth: true, symbol: 'circle', symbolSize: 5, data: trend.map(row => row.amount), lineStyle: { color: CHART.primary, width: 3 }, itemStyle: { color: CHART.primaryLight }, areaStyle: { color: CHART.primaryArea } }]
      });
      return chart;
    });
  }
});
