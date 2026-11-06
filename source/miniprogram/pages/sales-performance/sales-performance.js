/**
 * 销售业绩页逻辑
 */
const { get } = require('../../utils/api');
const { getRole, getScope, buildScopeParam } = require('../../utils/auth');
const { fNum } = require('../../utils/format');
const { formatSalesCard } = require('../../utils/preformat');
const CHART = require('../../utils/chart-theme');

Page({
  data: {
    perfTab: 'new',
    perfMetric: 'merchants',
    stats: null,
    rankList: [],
    trendData: [],
    ecTrend: { lazyLoad: true },
    // 切换增量/存量时强制重建 ec-canvas，避免复用已 dispose 的 ECharts 实例
    chartVisible: false,
    salesGrid: [],
    historyTable: [],
    historyPeriods: [],
    role: '',
    accessDenied: false
  },

  _historyRaw: [],

  onLoad() {
    const role = getApp().getRole();
    if (role === 'sales') {
      this.setData({ role, accessDenied: true });
      return;
    }
    this.setData({ role, accessDenied: false });
    this.loadAll();
  },

  async loadAll() {
    wx.showLoading({ title: '載入中...', mask: true });
    const requestTab = this.data.perfTab;
    const role = getApp().getRole();
    const scope = getScope();
    const sp = buildScopeParam(role, scope);
    const scopeQuery = sp ? '?' + sp.substring(1) : '';
    const isExisting = this.data.perfTab === 'existing';
    // 页面显示“近 6 月”，趋势接口也必须明确传入同一窗口，不能依赖后端默认值。
    const trendEndpoint = isExisting
      ? '/api/v1/v2/sales-performance/existing-trend?months=6' + sp
      : '/api/v1/v2/sales-performance/trend?months=6' + sp;

    try {
      // 不使用 Promise.all 直接包住业务结果：云端后端如果还没有部署
      // 新增的 summary 接口，不能让已经存在的排名、趋势、历史数据一起消失。
      const results = await Promise.all([
        this._safeGet('/api/v1/v2/sales-performance/rank?period=monthly' + sp),
        this._safeGet(trendEndpoint),
        this._safeGet('/api/v1/v2/sales-performance/history?period=monthly&data_type=' + this.data.perfTab + '&months=6' + sp),
        this._safeGet('/api/v1/v2/team/rank' + scopeQuery),
        this._safeGet('/api/v1/v2/sales-performance/summary?data_type=' + this.data.perfTab + scopeQuery)
      ]);

      // 用户快速切换 Tab 时，丢弃旧 Tab 的慢请求结果，避免旧图表覆盖新图表。
      if (requestTab !== this.data.perfTab) return;

      const [rankResult, trendResult, historyResult, teamResult, summaryResult] = results;
      const rank = rankResult.ok ? rankResult.data : [];
      const trend = trendResult.ok ? trendResult.data : [];
      const history = historyResult.ok ? historyResult.data : [];
      const team = teamResult.ok ? teamResult.data : [];
      const rankArr = Array.isArray(rank) ? rank : (rank?.items || []);
      const trendArr = Array.isArray(trend) ? trend : [];
      const teamArr = Array.isArray(team) ? team : [];
      this._historyRaw = Array.isArray(history) ? history : [];

      // TOP3 排名：格式化 new_volume
      const rankTop3 = rankArr.slice(0, 3).map(r => ({
        ...r,
        new_volume_fmt: fNum(r.new_volume || 0)
      }));

      // 销售画像：合并 rank+team 并格式化
      const grid = this._buildSalesGrid(rankArr, teamArr).map(formatSalesCard);
      // summary 是新接口。接口暂时未部署时，用同一份趋势接口的最新月份
      // 作为兼容回退，避免页面所有模块因为一个 404 一起变空。
      const summary = summaryResult.ok
        ? summaryResult.data
        : this._fallbackSummary(trendArr, rankArr, teamArr, this.data.perfTab);
      const stats = this._formatSummaryStats(summary, this.data.perfTab);

      this.setData({
        stats,
        rankList: rankTop3,
        trendData: trendArr,
        ecTrend: { lazyLoad: true },
        chartVisible: true,
        salesGrid: grid
      }, () => {
        this._chartPending = true;
        this._waitForEchartsAndInit(0);
      });

      this._renderHistoryTable();

      const failed = results.filter(item => !item.ok);
      if (failed.length && failed.length < results.length) {
        console.warn('[sales-perf] 部分接口未返回，已使用可用数据渲染', failed.map(item => item.path));
        wx.showToast({ title: '部分业绩数据接口未同步，已显示可用数据', icon: 'none' });
      }
      if (failed.length === results.length) {
        throw new Error('銷售業績接口均未返回數據');
      }
    } catch (e) {
      console.error('[sales-perf] 加载失败', e);
      wx.showToast({ title: e.message || '銷售業績載入失敗', icon: 'none' });
    } finally {
      wx.hideLoading();
    }
  },

  /** 单个接口失败不影响其他模块；path 只用于日志定位。 */
  async _safeGet(path) {
    try {
      return { ok: true, path, data: await get(path, { silent: true }) };
    } catch (error) {
      console.error('[sales-perf] 接口失败', path, error);
      return { ok: false, path, error };
    }
  },

  /**
   * 兼容尚未部署 summary 的旧云端后端。
   * 趋势接口与 summary 使用相同的发布日期和数据口径，取最新月份即可。
   */
  _fallbackSummary(trend, rank, team, dataType) {
    const rows = (trend || []).slice().sort((a, b) =>
      String(a.month || a.period || '').localeCompare(String(b.month || b.period || ''))
    );
    const latest = rows[rows.length - 1] || {};
    const merchants = dataType === 'existing'
      ? (+latest.existing_merchants || 0)
      : (+latest.new_merchants || 0);
    const volume = dataType === 'existing'
      ? (+latest.existing_volume || 0)
      : (+latest.new_volume || 0);
    const activeSales = dataType === 'existing'
      ? team.filter(item => (+item.this_month_amount || 0) > 0).length
      : rank.filter(item => (+item.new_merchants || 0) > 0).length;
    return {
      ...(dataType === 'existing'
        ? { active_merchants: merchants, existing_volume: volume }
        : { new_merchants: merchants, new_volume: volume }),
      avg_volume: merchants > 0 ? volume / merchants : 0,
      active_sales: activeSales
    };
  },

  onReady() {
    this._pageReady = true;
    if (this._chartPending) this._waitForEchartsAndInit(0);
  },

  _waitForEchartsAndInit(attempt) {
    if (!this._pageReady && attempt < 20) {
      setTimeout(() => this._waitForEchartsAndInit(attempt + 1), 80);
      return;
    }
    if (!wx._echarts && attempt < 12) {
      setTimeout(() => this._waitForEchartsAndInit(attempt + 1), 100);
      return;
    }
    wx.nextTick(() => this._initTrendChart(0));
  },

  _initTrendChart(attempt = 0) {
    if (!this.data.trendData.length) return;
    const component = this.selectComponent('#salesTrendChart');
    if (component && typeof component.init === 'function') {
      if (component.chart && typeof component.chart.dispose === 'function') {
        try { component.chart.dispose(); } catch (e) { console.warn('[sales-perf] 释放旧图表失败', e); }
        component.chart = null;
      }
      component.init(this._buildTrendChart());
      return;
    }
    if (attempt < 10) {
      setTimeout(() => this._initTrendChart(attempt + 1), 80);
    } else {
      console.error('[sales-perf] 趋势图组件未找到');
    }
  },

  _buildTrendChart() {
    const rows = (this.data.trendData || []).slice(-6);
    const isExisting = this.data.perfTab === 'existing';
    return (canvas, width, height, dpr) => {
      const echartsLib = wx._echarts;
      const chart = echartsLib ? echartsLib.init(canvas, null, { width, height, devicePixelRatio: dpr }) : null;
      if (!chart) return null;
      const labels = rows.map(row => String(row.month || row.period || '').slice(0, 7));
      chart.setOption({
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis', backgroundColor: CHART.surface, borderColor: CHART.primary, textStyle: { color: CHART.text } },
        legend: {
          data: isExisting ? ['存量商戶', '存量交易額'] : ['新增商戶', '首月交易額'],
          bottom: 0,
          textStyle: { color: CHART.muted, fontSize: 10 }
        },
        grid: { left: 12, right: 12, top: 18, bottom: 42, containLabel: true },
        xAxis: {
          type: 'category',
          data: labels.map(label => label ? label.slice(5) + '月' : '-'),
          axisLabel: { color: CHART.muted, fontSize: 9 },
          axisLine: { lineStyle: { color: CHART.axis } },
          axisTick: { show: false }
        },
        yAxis: [
          { type: 'value', axisLabel: { color: CHART.muted, fontSize: 10 }, splitLine: { lineStyle: { color: CHART.grid } } },
          { type: 'value', axisLabel: { color: CHART.muted, fontSize: 10, formatter: value => (value / 10000).toFixed(1) + '萬' }, splitLine: { show: false } }
        ],
        series: [
          { name: isExisting ? '存量商戶' : '新增商戶', type: 'line', yAxisIndex: 0, smooth: true, symbol: 'circle', symbolSize: 6, data: rows.map(row => +(isExisting ? row.existing_merchants : row.new_merchants) || 0), itemStyle: { color: CHART.primary }, lineStyle: { width: 3, color: CHART.primary }, areaStyle: { color: CHART.primaryArea } },
          { name: isExisting ? '存量交易額' : '首月交易額', type: 'bar', yAxisIndex: 1, data: rows.map(row => +(isExisting ? row.existing_volume : row.new_volume) || 0), barWidth: '34%', itemStyle: { color: CHART.secondary, borderRadius: [3, 3, 0, 0] } }
        ]
      });
      return chart;
    };
  },

  _formatSummaryStats(summary, dataType) {
    const row = summary || {};
    const merchants = dataType === 'existing'
      ? (+row.active_merchants || 0)
      : (+row.new_merchants || 0);
    const volume = dataType === 'existing'
      ? (+row.existing_volume || 0)
      : (+row.new_volume || 0);
    return {
      newMerchants: merchants,
      totalVolume: fNum(volume),
      avgVolume: fNum(+row.avg_volume || 0),
      activeCount: +row.active_sales || 0
    };
  },

  _buildSalesGrid(rankArr, teamArr) {
    const teamMap = {};
    teamArr.forEach(t => { teamMap[t.sales_name] = t; });
    let merged = rankArr.slice(0, 10).map(r => {
      const t = teamMap[r.sales_name] || {};
      return { sales_name: r.sales_name, existing: +t.merchant_count || 0, new: +r.new_merchants || 0, mom: +t.daily_mom_pct || 0 };
    });
    if (merged.length < 12) {
      teamArr.forEach(t => {
        if (merged.find(m => m.sales_name === t.sales_name)) return;
        merged.push({ sales_name: t.sales_name, existing: +t.merchant_count || 0, new: 0, mom: +t.daily_mom_pct || 0 });
      });
    }
    return merged.slice(0, 6);
  },

  _renderHistoryTable() {
    const rows = this._historyRaw;
    const metric = this.data.perfMetric;
    const unitFn = {
      merchants: v => v,
      txn_count: v => v.toLocaleString('en-US'),
      total_volume: v => fNum(v),
      avg_volume: v => fNum(v)
    };
    const byPeriod = {};
    rows.forEach(r => { byPeriod[r.period] = (byPeriod[r.period] || 0) + 1; });
    const periods = Object.keys(byPeriod).sort().map(p => {
      const dt = new Date(p);
      return (dt.getMonth() + 1) + '月';
    });
    const salesNames = [...new Set(rows.map(r => r.sales_name))];
    let maxVal = 0;
    const cellVals = {};
    rows.forEach(r => {
      const v = +r[metric] || 0;
      const k = r.sales_name + '|' + r.period;
      cellVals[k] = v;
      if (v > maxVal) maxVal = v;
    });
    const periodKeys = Object.keys(byPeriod).sort();
    const table = salesNames.map(sn => {
      const cells = periodKeys.map(p => {
        const v = cellVals[sn + '|' + p] || 0;
        const display = unitFn[metric](v);
        let color = '#94A3B8', bg = 'transparent';
        if (maxVal > 0) {
          const ratio = v / maxVal;
          if (ratio >= 0.8) { color = '#0B1120'; bg = '#22D3EE'; }
          else if (ratio >= 0.5) { color = '#22D3EE'; bg = 'rgba(34,211,238,0.35)'; }
          else if (ratio >= 0.2) { color = '#22D3EE'; bg = 'rgba(34,211,238,0.15)'; }
          else if (v > 0) { color = '#22D3EE'; }
        }
        return { display, color, bg };
      });
      return { sales_name: sn, cells };
    });
    this.setData({ historyTable: table, historyPeriods: periods });
  },

  switchPerfTab(e) {
    const perfTab = e.currentTarget.dataset.tab;
    if (perfTab === this.data.perfTab) return;
    // 先卸载旧 ec-canvas，再在接口返回后挂载新实例，解决切换后空白。
    this._chartPending = false;
    this.setData({ perfTab, chartVisible: false, trendData: [] }, () => this.loadAll());
  },

  goSalesDetail(e) {
    const name = e.currentTarget.dataset.name;
    if (name) wx.navigateTo({ url: '/pages/sales-detail/sales-detail?name=' + encodeURIComponent(name) });
  },

  switchMetric(e) {
    this.setData({ perfMetric: e.currentTarget.dataset.metric });
    this._renderHistoryTable();
  }
});
