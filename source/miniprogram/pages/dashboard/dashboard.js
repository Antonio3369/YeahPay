/**
 * 工作台页面逻辑
 */

const { get, getMe } = require('../../utils/api');
const { getRole, getScope, setScope, buildScopeParam, isLoggedIn } = require('../../utils/auth');
const { fNum, fPct, fCompactAmount, fCompactHKD } = require('../../utils/format');
const CHART = require('../../utils/chart-theme');

// echarts 实例从 ec-canvas 注入到 wx._echarts
// 在 onLoad 中获取，确保 ec-canvas 已经初始化

Page({
  // echarts 实例，由 onLoad 时赋值（从 ec-canvas 注入）
  echartsInstance: null,

  data: {
    role: '',
    lockedScopeLabel: '',
    currentScopeLabel: '全部機構',
    activeScope: 'all',
    scopePickerOpen: false,
    scopeTab: 'all',
    scopeSearch: '',
    agentOptions: [],
    salesOptions: [],
    filteredAgents: [],
    filteredSales: [],

    kpi: {
      merchant_count: 0,
      active_merchant_count: 0,
      active_rate: '0.0%',
      this_month_amount: '0',
      last_month_amount: '0',
      this_month_avg: '0',
      last_month_avg: '0',
      fail_rate: '0.00%',
      mom_pct: '+0%',
      mom_direction: '同比下降',
      mom_pct_raw: 0,
      alert_count: 0
    },

    trendReady: false,
    dailyReady: false,
    walletReady: false,
    statusReady: false,

    // ec 配置：每个图表配置 onInit 回调（ec-canvas 自动调用）
    ecMonthly: { lazyLoad: true },
    ecDaily: { lazyLoad: true },
    ecWallet: { lazyLoad: true },
    ecStatus: { lazyLoad: true }
  },

  // 数据缓存
  _trendData: null,
  _dailyData: null,
  _walletData: null,
  _statusData: null,

  onLoad() {
    this._pageReady = false;
    this._pageVisible = false;
    this._loadSerial = 0;
    this._chartScheduleSerial = 0;
    this._chartsPending = false;
    const app = getApp();
    const role = app.getRole();
    this.setData({ role: role || '' });
    this.echartsInstance = wx._echarts || null;
    if (!this.echartsInstance) {
      setTimeout(() => { this.echartsInstance = wx._echarts || null; }, 100);
    }
    if (role !== 'admin') {
      getMe().then(user => {
        this.setData({ lockedScopeLabel: user?.scope_label || user?.display_name || '' });
      }).catch(() => {});
    }
  },

  onReady() {
    this._pageReady = true;
    if (this._chartsPending) this._scheduleChartInit();
  },

  async onShow() {
    this._pageVisible = true;
    if (!isLoggedIn()) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    // 每次回到工作台都重新读取代理商/销售主数据，确保数据库发布后的
    // 新增、删除和改名立即反映到视角选择器。
    if (getApp().getRole() === 'admin') await this.initScope();
    this.loadAllData();
    // 页面从 tab 切回后，canvas 节点可能需要一个渲染周期才能恢复尺寸。
    // 已有实例只做 resize，不销毁重建，避免切换时出现空白画布。
    setTimeout(() => this._resizeCharts(), 180);
  },

  onHide() {
    this._pageVisible = false;
    if (this.data.scopePickerOpen) {
      wx.showTabBar({ animation: false });
      this.setData({ scopePickerOpen: false });
    }
    // 让页面隐藏前尚未完成的请求失效，防止旧请求回来后覆盖下一次 onShow。
    this._loadSerial += 1;
    this._chartsPending = false;
  },

  async initScope() {
    const role = getApp().getRole();
    if (role !== 'admin') return;
    try {
      const [agents, sales] = await Promise.all([
        get('/api/v1/scope/agents', { silent: true }),
        get('/api/v1/scope/sales', { silent: true })
      ]);
      const agentOptions = (agents || []).map(a => ({
        ...a, label: a.agent_name || '未命名代理商', value: 'agent_' + a.agent_id
      }));
      const salesOptions = (sales || []).map(s => ({
        ...s, label: s.sales_name || '未命名銷售', value: 'sales_' + s.sales_name
      }));
      let scope = getScope();
      let selected = scope === 'all'
        ? { label: '全部機構' }
        : (scope.startsWith('agent_') ? agentOptions : salesOptions).find(o => o.value === scope);
      // 已保存视角在新数据库中不存在时必须回到全部机构，不能继续携带
      // 失效参数请求接口，否则页面会看起来像“更新后数据全没了”。
      if (!selected) {
        scope = 'all';
        selected = { label: '全部機構' };
        setScope(scope);
        getApp().setScope(scope);
      }
      const scopeTab = scope.startsWith('agent_') ? 'agents' : scope.startsWith('sales_') ? 'sales' : 'all';
      this.setData({
        agentOptions, salesOptions,
        filteredAgents: agentOptions,
        filteredSales: salesOptions,
        currentScopeLabel: selected?.label || '全部機構',
        activeScope: scope,
        scopeTab
      });
    } catch (e) { console.warn('[dashboard] 加载视角失败', e); }
  },

  openScopePicker() {
    const scope = getScope();
    // 全机构不是一个可供继续展开的列表；打开选择器时直接展示代理商，
    // 用户仍可通过顶部标签切换到销售，避免弹窗打开后只看到一行“全部机构”。
    const scopeTab = scope.startsWith('sales_') ? 'sales' : 'agents';
    wx.hideTabBar({ animation: false });
    this.setData({ scopePickerOpen: true, scopeSearch: '', scopeTab }, () => this.filterScopeOptions());
  },

  closeScopePicker() {
    wx.showTabBar({ animation: false });
    this.setData({ scopePickerOpen: false, scopeSearch: '' }, () => {
      // scopePickerOpen 使用 wx:if 卸载了原生 canvas；弹窗关闭后需要重新挂载并初始化图表。
      if (this._pageVisible && this.data.trendReady) {
        this._chartsPending = true;
        this._chartsNeedRefresh = true;
        this._scheduleChartInit();
      }
    });
  },

  stopScopeEvent() {},

  switchScopeTab(e) {
    this.setData({ scopeTab: e.currentTarget.dataset.tab, scopeSearch: '' }, () => this.filterScopeOptions());
  },

  onScopeSearch(e) {
    this.setData({ scopeSearch: (e.detail.value || '').trim() }, () => this.filterScopeOptions());
  },

  filterScopeOptions() {
    const keyword = this.data.scopeSearch.toLowerCase();
    const match = item => !keyword || String(item.label || '').toLowerCase().includes(keyword)
      || String(item.agent_id || '').toLowerCase().includes(keyword)
      || String(item.sales_name || '').toLowerCase().includes(keyword);
    this.setData({
      filteredAgents: this.data.agentOptions.filter(match),
      filteredSales: this.data.salesOptions.filter(match)
    });
  },

  onScopeSelect(e) {
    const scope = e.currentTarget.dataset.scope || 'all';
    const label = e.currentTarget.dataset.label || '全部機構';
    setScope(scope);
    getApp().setScope(scope);
    wx.showTabBar({ animation: false });
    this.setData({ scopePickerOpen: false, scopeSearch: '', currentScopeLabel: label, activeScope: scope });
    this.loadAllData();
  },

  async loadAllData() {
    const role = getApp().getRole();
    // 未登录时不请求（避免 401）
    if (!role) return;
    const requestSerial = ++this._loadSerial;
    const scope = getScope();
    const sp = buildScopeParam(role, scope);
    // KPI 新增字段不能复用旧的 GET 缓存，切回工作台时强制取最新口径。
    const kpiPath = '/api/v1/v2/dashboard/kpi' + (sp ? '?' + sp.substring(1) + '&' : '?') + '_ts=' + Date.now();

    try {
      const requests = [
        get(kpiPath),
        get('/api/v1/v2/dashboard/monthly-trend' + (sp ? '?' + sp.substring(1) : '')),
        get('/api/v1/v2/dashboard/daily-collection' + (sp ? '?' + sp.substring(1) : '')),
        get('/api/v1/v2/dashboard/wallet-distribution' + (sp ? '?' + sp.substring(1) : '')),
        get('/api/v1/v2/dashboard/status-distribution' + (sp ? '?' + sp.substring(1) : ''))
      ];
      const settled = await Promise.all(requests.map(p => p.then(
        value => ({ ok: true, value }),
        error => ({ ok: false, error })
      )));

      // onShow 期间可能又触发了新的请求，旧结果不能再更新页面和图表。
      if (requestSerial !== this._loadSerial || !this._pageVisible) return;

      const [kpiResult, trendResult, dailyResult, walletResult, statusResult] = settled;
      if (kpiResult.ok) {
        const kpi = kpiResult.value;
        this.setData({
          kpi: {
            merchant_count: kpi.merchant_count || 0,
            active_merchant_count: kpi.active_merchant_count || 0,
            active_rate: Number(kpi.active_rate || 0).toFixed(1) + '%',
            this_month_amount: fCompactAmount(kpi.this_month_amount || 0),
            last_month_amount: fCompactAmount(kpi.last_month_amount || 0),
            this_month_avg: fCompactAmount(kpi.this_month_avg || 0),
            last_month_avg: fCompactAmount(kpi.last_month_avg || 0),
            fail_rate: Number(kpi.fail_rate || 0).toFixed(2) + '%',
            mom_pct: fPct(kpi.mom_pct || 0),
            mom_direction: Number(kpi.mom_pct || 0) >= 0 ? '同比增長' : '同比下降',
            mom_pct_raw: kpi.mom_pct || 0,
            alert_count: kpi.alert_count || 0
          }
        });
      }

      // 缓存数据
      if (trendResult.ok) this._trendData = Array.isArray(trendResult.value) ? trendResult.value : [];
      if (dailyResult.ok) this._dailyData = Array.isArray(dailyResult.value) ? dailyResult.value : [];
      if (walletResult.ok) this._walletData = Array.isArray(walletResult.value) ? walletResult.value : [];
      if (statusResult.ok) this._statusData = Array.isArray(statusResult.value) ? statusResult.value : [];
      const failedCount = settled.filter(item => !item.ok).length;
      if (failedCount) {
        console.error('[dashboard] 部分接口加载失败', settled.filter(item => !item.ok).map(item => item.error));
        wx.showToast({ title: `${failedCount}項數據載入失敗，請重試`, icon: 'none' });
      }

      // lazyLoad 模式不会自动触发 init；数据就绪后由页面主动初始化。
      this.setData({
        trendReady: true,
        dailyReady: true,
        walletReady: true,
        statusReady: true,
        ecMonthly: { lazyLoad: true },
        ecDaily: { lazyLoad: true },
        ecWallet: { lazyLoad: true },
        ecStatus: { lazyLoad: true }
      }, () => {
        if (requestSerial !== this._loadSerial || !this._pageVisible) return;
        this._chartsPending = true;
        this._chartsNeedRefresh = true;
        this._scheduleChartInit();
      });
    } catch (e) {
      console.error('[dashboard] 加载失败', e);
      wx.showToast({ title: '加載失敗', icon: 'none' });
    }
  },

  _scheduleChartInit() {
    const serial = ++this._chartScheduleSerial;
    this._waitForEchartsAndInit(0, serial);
  },

  _waitForEchartsAndInit(attempt, serial) {
    if (serial !== this._chartScheduleSerial || !this._pageVisible) return;
    if (!this._pageReady && attempt < 20) {
      setTimeout(() => this._waitForEchartsAndInit(attempt + 1, serial), 80);
      return;
    }
    this.echartsInstance = this.echartsInstance || wx._echarts || null;
    if (!this.echartsInstance && attempt < 12) {
      setTimeout(() => this._waitForEchartsAndInit(attempt + 1, serial), 100);
      return;
    }
    // 给页面从隐藏态恢复后的 canvas 一个完整渲染周期。
    setTimeout(() => {
      if (serial !== this._chartScheduleSerial || !this._pageVisible) return;
      wx.nextTick(() => this._initCharts(0, serial));
    }, attempt === 0 ? 100 : 0);
  },

  _resizeCharts() {
    if (!this._pageVisible) return;
    ['#chartMonthly', '#chartDaily', '#chartWallet', '#chartStatus'].forEach(selector => {
      const component = this.selectComponent(selector);
      const chart = component && component.chart;
      if (!chart || typeof chart.resize !== 'function') return;
      try {
        if (typeof chart.isDisposed !== 'function' || !chart.isDisposed()) chart.resize();
      } catch (e) {
        console.warn('[dashboard] 图表尺寸恢复失败', selector, e);
      }
    });
  },

  _initCharts(attempt = 0, serial = this._chartScheduleSerial) {
    if (serial !== this._chartScheduleSerial || !this._pageVisible) return;
    const missing = [];
    const initializing = [];
    const needRefresh = !!this._chartsNeedRefresh;
    // 只消费一次刷新标记。已有图表不再反复销毁，切回页面时只 resize。
    this._chartsNeedRefresh = false;
    [
      ['#chartMonthly', () => this._buildMonthlyOnInit()],
      ['#chartDaily', () => this._buildDailyOnInit()],
      ['#chartWallet', () => this._buildWalletOnInit()],
      ['#chartStatus', () => this._buildStatusOnInit()]
    ].forEach(([selector, build]) => {
      const component = this.selectComponent(selector);
      if (component && typeof component.init === 'function') {
        const chart = component.chart;
        const chartReady = chart && (typeof chart.isDisposed !== 'function' || !chart.isDisposed());
        if (chartReady && !needRefresh) {
          try { chart.resize(); } catch (e) { console.warn('[dashboard] 图表刷新尺寸失败', selector, e); }
          return;
        }
        if (chartReady && needRefresh && typeof chart.dispose === 'function') {
          try { chart.dispose(); } catch (e) { console.warn('[dashboard] 图表销毁失败', selector, e); }
          // ec-canvas.init 的判断依赖 chart 是否为空；dispose 后必须清空引用。
          component.chart = null;
        }
        // ec-canvas 的新 canvas 初始化是异步的，不能在同一组件上重复发起。
        if (component._yeahpayInitPending) {
          initializing.push(selector);
          return;
        }
        component._yeahpayInitPending = true;
        const initChart = build();
        // 由 ec-canvas 的回调统一结束“初始化中”状态，避免四个图表各自
        // 启动一轮全量重试，导致初始化任务叠加并拖死模拟器。
        component.init((canvas, width, height, dpr) => {
          const result = initChart(canvas, width, height, dpr);
          component._yeahpayInitPending = false;
          return result;
        });
        initializing.push(selector);
        setTimeout(() => {
          // canvas 查询异常时给统一重试机制一个释放锁的机会；这里只释放
          // 当前组件，不再从四个定时器分别触发全量 _initCharts。
          if (component._yeahpayInitPending && this._pageVisible && serial === this._chartScheduleSerial) {
            component._yeahpayInitPending = false;
          }
        }, 1200);
      } else {
        missing.push(selector);
      }
    });

    // 组件可能还在首次 WXML 渲染队列中，给它一个短暂重试窗口。
    if ((missing.length || initializing.length) && attempt < 15) {
      setTimeout(() => this._initCharts(attempt + 1, serial), 100);
    } else if (missing.length || initializing.length) {
      console.error('[dashboard] 图表组件初始化超时', { missing, initializing });
    }
  },

  /**
   * 月度趋势图 onInit
   * ec-canvas 在 canvas ready 后调用此函数，传入 (canvas, width, height, dpr)
   * 返回 echarts 实例即可
   */
  _buildMonthlyOnInit() {
    const data = this._trendData || [];
    return (canvas, width, height, dpr) => {
      const echartsLib = this.echartsInstance || wx._echarts;
      const chart = echartsLib ? echartsLib.init(canvas, null, { width, height, devicePixelRatio: dpr }) : null;
      if (!chart) return null;
      chart.setOption({
        tooltip: {
          trigger: 'axis', confine: true, backgroundColor: CHART.surface, borderColor: CHART.primary,
          textStyle: { color: CHART.text },
          formatter: params => {
            const rows = Array.isArray(params) ? params : [params];
            const month = rows[0]?.axisValue || '';
            return month + rows.map(item => '\n' + item.marker + item.seriesName + '：' + fCompactHKD((+item.value || 0) * 1e8)).join('');
          }
        },
        ...(data.length ? {} : { graphic: { type: 'text', left: 'center', top: 'middle', style: { text: '暫無數據', fill: CHART.muted, fontSize: 14 } } }),
        grid: { left: 8, right: 8, top: 16, bottom: 28, containLabel: true },
        xAxis: {
          type: 'category',
          data: data.map(d => { const dt = new Date(d.month); return (dt.getMonth() + 1) + '月'; }),
          axisLabel: { color: CHART.muted, fontSize: 9 },
          axisLine: { lineStyle: { color: CHART.axis } },
          axisTick: { show: false }
        },
        yAxis: {
          type: 'value',
          axisLabel: { color: CHART.muted, fontSize: 10, formatter: v => v.toFixed(1) + '億' },
          splitLine: { lineStyle: { color: CHART.grid } }
        },
        series: [{
          name: '交易額', type: 'bar',
          data: data.map(d => (+d.total_amount || 0) / 1e8),
          itemStyle: {
            color: CHART.primary,
            borderRadius: [2, 2, 0, 0]
          },
          barWidth: '55%'
        }]
      });
      return chart;
    };
  },

  _buildDailyOnInit() {
    const data = this._dailyData || [];
    return (canvas, width, height, dpr) => {
      const echartsLib = this.echartsInstance || wx._echarts;
      const chart = echartsLib ? echartsLib.init(canvas, null, { width, height, devicePixelRatio: dpr }) : null;
      if (!chart) return null;
      chart.setOption({
        tooltip: {
          trigger: 'axis', confine: true, backgroundColor: CHART.surface, borderColor: CHART.primary,
          textStyle: { color: CHART.text },
          formatter: params => {
            const rows = Array.isArray(params) ? params : [params];
            const day = rows[0]?.axisValue || '';
            return day + rows.map(item => '\n' + item.marker + item.seriesName + '：' + fCompactHKD((+item.value || 0) * 1e4)).join('');
          }
        },
        ...(data.length ? {} : { graphic: { type: 'text', left: 'center', top: 'middle', style: { text: '暫無數據', fill: CHART.muted, fontSize: 14 } } }),
        legend: { data: ['本月', '上月'], bottom: 0, textStyle: { color: CHART.muted, fontSize: 10 } },
        grid: { left: 8, right: 8, top: 16, bottom: 36, containLabel: true },
        xAxis: {
          type: 'category',
          data: data.map(d => d.day),
          axisLabel: { color: CHART.muted, fontSize: 9 },
          axisLine: { lineStyle: { color: CHART.axis } },
          axisTick: { show: false }
        },
        yAxis: {
          type: 'value',
          axisLabel: { color: CHART.muted, fontSize: 10, formatter: v => v.toFixed(1) + '萬' },
          splitLine: { lineStyle: { color: CHART.grid } }
        },
        series: [
          { name: '本月', type: 'line', smooth: true, data: data.map(d => (+d.this_month || 0) / 1e4), itemStyle: { color: CHART.primary }, lineStyle: { width: 2, color: CHART.primary }, symbol: 'none' },
          { name: '上月', type: 'line', smooth: true, data: data.map(d => (+d.last_month || 0) / 1e4), itemStyle: { color: CHART.neutral }, lineStyle: { width: 1.5, type: 'dashed', color: CHART.neutral }, symbol: 'none' }
        ]
      });
      return chart;
    };
  },

  _buildWalletOnInit() {
    const data = this._walletData || [];
    return (canvas, width, height, dpr) => {
      const echartsLib = this.echartsInstance || wx._echarts;
      const chart = echartsLib ? echartsLib.init(canvas, null, { width, height, devicePixelRatio: dpr }) : null;
      if (!chart) return null;
      chart.setOption({
        tooltip: { trigger: 'item', confine: true, backgroundColor: CHART.surface, borderColor: CHART.primary, textStyle: { color: CHART.text }, formatter: item => item.name + '\n' + fCompactHKD(item.value) + '（' + item.percent + '%）' },
        ...(data.length ? {} : { graphic: { type: 'text', left: 'center', top: 'middle', style: { text: '暫無數據', fill: CHART.muted, fontSize: 14 } } }),
        color: CHART.palette,
        series: [{
          type: 'pie', radius: ['45%', '75%'],
          label: { color: CHART.muted, fontSize: 10, formatter: '{b}\n{d}%' },
          data: data.map(d => ({ name: d.name || '未標記', value: +d.volume || 0 }))
        }]
      });
      return chart;
    };
  },

  _buildStatusOnInit() {
    const data = this._statusData || [];
    return (canvas, width, height, dpr) => {
      const echartsLib = this.echartsInstance || wx._echarts;
      const chart = echartsLib ? echartsLib.init(canvas, null, { width, height, devicePixelRatio: dpr }) : null;
      if (!chart) return null;
      chart.setOption({
        tooltip: { trigger: 'item', confine: true, backgroundColor: CHART.surface, borderColor: CHART.primary, textStyle: { color: CHART.text }, formatter: item => item.name + '\n' + fCompactHKD(item.value) + '（' + item.percent + '%）' },
        ...(data.length ? {} : { graphic: { type: 'text', left: 'center', top: 'middle', style: { text: '暫無數據', fill: CHART.muted, fontSize: 14 } } }),
        color: CHART.statusPalette,
        series: [{
          type: 'pie', radius: ['45%', '75%'],
          label: { color: CHART.muted, fontSize: 10, formatter: '{b}\n{d}%' },
          data: data.map(d => ({ name: d.name || '未標記', value: +d.volume || 0 }))
        }]
      });
      return chart;
    };
  }
});
