const { get, post } = require('../../utils/api');
const { fHKD, fHKDFull, fNum, fCompactHKD } = require('../../utils/format');
const { getRole } = require('../../utils/auth');
const CHART = require('../../utils/chart-theme');

function shortDate(value) {
  const match = String(value || '').match(/^\d{4}-(\d{2})-(\d{2})/);
  return match ? match[1] + '-' + match[2] : String(value || '-').slice(0, 10);
}

Page({
  data: {
    id: '',
    role: '',
    info: null,
    breakdown: { wallet: [], status: [] },
    dailyStats: [],
    merchantSummary: {
      recentAmount: 'HK$0', recentCount: 0, mtdAmount: 'HK$0', mtdCount: 0,
      firstBound: '-', lastTrans: '-', salesName: '-', agentId: '', agentName: '-'
    },
    walletReady: false,
    statusReady: false,
    trendReady: false,
    pushRecipients: [],
    pushRecipientIndex: -1,
    pushNote: '',
    pushing: false,
    ecWallet: { lazyLoad: true },
    ecStatus: { lazyLoad: true },
    ecTrend: { lazyLoad: true }
  },
  onLoad(options) {
    const id = options.id;
    if (!id) { wx.showToast({ title: '缺少商戶ID', icon: 'none' }); return; }
    this.setData({ id, role: getRole() });
    this.loadDetail(id);
    this.loadPushRecipients();
  },

  onShow() {
    if (this._loadedOnce && this.data.id) {
      this.loadDetail(this.data.id);
      this.loadPushRecipients();
    }
  },

  async loadPushRecipients() {
    try {
      const rows = await get('/api/v1/pushes/recipients', { silent: true });
      this.setData({ pushRecipients: rows || [] });
    } catch (e) {
      console.error('[merchant-detail] 推送目标读取失败', e);
    }
  },

  onPushRecipientChange(e) {
    this.setData({ pushRecipientIndex: Number(e.detail.value) });
  },

  onPushNoteInput(e) {
    this.setData({ pushNote: e.detail.value });
  },

  goAccountManagement() {
    wx.navigateTo({ url: '/pages/account-management/account-management' });
  },

  async pushMerchant() {
    const index = this.data.pushRecipientIndex;
    const recipient = this.data.pushRecipients[index];
    if (!recipient) {
      wx.showToast({ title: '請先選擇接收賬號', icon: 'none' });
      return;
    }
    const modal = await new Promise(resolve => wx.showModal({
      title: '推送商戶',
      content: '將「' + (this.data.info?.short_name || this.data.info?.name || this.data.id) + '」推送給 ' + recipient.display_name + '？',
      success: resolve
    }));
    if (!modal.confirm || this.data.pushing) return;
    this.setData({ pushing: true });
    try {
      await post('/api/v1/pushes', {
        merchant_id: String(this.data.id),
        recipient_id: recipient.id,
        note: this.data.pushNote || ''
      });
      wx.showToast({ title: '已推送，對方可開始跟進', icon: 'success' });
      this.setData({ pushRecipientIndex: -1, pushNote: '' });
    } catch (e) {
      wx.showToast({ title: e.message || '推送失敗', icon: 'none' });
    } finally {
      this.setData({ pushing: false });
    }
  },
  onReady() {
    this._pageReady = true;
    if (this._chartsPending) this._waitForEchartsAndInit(0);
  },
  async loadDetail(id) {
    if (this._loadingDetail) return;
    this._loadingDetail = true;
    wx.showLoading({ title: '載入中...', mask: true });
    try {
      const [info, breakdown, merchant] = await Promise.all([
        get('/api/v1/v2/entity/merchant/' + id + '/info'),
        get('/api/v1/v2/entity/merchant/' + id + '/breakdown'),
        get('/api/v1/merchants/' + id)
      ]);
      const safeBreakdown = breakdown || { wallet: [], status: [] };
      const dailyStats = (Array.isArray(merchant?.daily_stats) ? merchant.daily_stats : [])
        .map(row => ({ ...row, period_label: shortDate(row.period), amount_fmt: fHKDFull(row.amount) }));
      const walletRows = (Array.isArray(safeBreakdown.wallet) ? safeBreakdown.wallet : [])
        .map(row => ({ ...row, volume_fmt: fHKDFull(row.volume) }));
      const statusRows = (Array.isArray(safeBreakdown.status) ? safeBreakdown.status : [])
        .map(row => ({ ...row, volume_fmt: fHKDFull(row.volume) }));
      const recentAmount = dailyStats.reduce((sum, row) => sum + (+row.amount || 0), 0);
      const recentCount = dailyStats.reduce((sum, row) => sum + (+row.count || 0), 0);
      const mtd = merchant?.mtd || {};
      this.setData({
        info,
        breakdown: {
          wallet: walletRows,
          status: statusRows
        },
        dailyStats,
        merchantSummary: {
          recentAmount: fHKDFull(recentAmount),
          recentCount,
          mtdAmount: fHKDFull(mtd.mtd_amount || 0),
          mtdCount: +mtd.mtd_count || 0,
          firstBound: String(info?.first_bound_time || merchant?.first_bound_time || '-').slice(0, 10),
          lastTrans: String(merchant?.last_trans_time || '-').replace('T', ' ').slice(0, 16),
          salesName: info?.sales_name || '-',
          agentId: info?.agent_id ? String(info.agent_id) : '',
          agentName: info?.agent_name || '-'
        },
        walletReady: safeBreakdown.wallet?.length > 0,
        statusReady: safeBreakdown.status?.length > 0,
        trendReady: dailyStats.length > 0
      }, () => {
        this._chartsPending = true;
        this._waitForEchartsAndInit(0);
      });
    } catch (e) {
      console.error('[merchant-detail]', e);
      // 数据发布后商户若已删除/注销，不能继续显示上一次加载的旧详情。
      this.setData({
        info: null,
        breakdown: { wallet: [], status: [] },
        dailyStats: [],
        walletReady: false,
        statusReady: false,
        trendReady: false
      });
      wx.showToast({ title: e.message || '商戶詳情載入失敗', icon: 'none' });
    } finally {
      this._loadingDetail = false;
      this._loadedOnce = true;
      wx.hideLoading();
    }
  },

  goSalesDetail() {
    const name = this.data.merchantSummary.salesName;
    if (name && name !== '-') {
      wx.navigateTo({ url: '/pages/sales-detail/sales-detail?name=' + encodeURIComponent(name) });
    }
  },

  goAgentDetail() {
    const id = this.data.merchantSummary.agentId;
    if (id) wx.navigateTo({ url: '/pages/agent-detail/agent-detail?id=' + encodeURIComponent(id) });
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
    wx.nextTick(() => this._initCharts(0));
  },

  _initCharts(attempt = 0) {
    const missing = [];
    [
      ['#merchantWalletChart', this._buildWalletChart()],
      ['#merchantStatusChart', this._buildStatusChart()],
      ['#merchantTrendChart', this._buildTrendChart()]
    ].forEach(([selector, init]) => {
      const component = this.selectComponent(selector);
      if (component && typeof component.init === 'function' && init) {
        if (component.chart && typeof component.chart.dispose === 'function') component.chart.dispose();
        component.init(init);
      } else if (init) {
        missing.push(selector);
      }
    });
    if (missing.length && attempt < 10) {
      setTimeout(() => this._initCharts(attempt + 1), 80);
    } else if (missing.length) {
      console.error('[merchant-detail] 图表组件未找到', missing);
    }
  },

  _newChart() {
    return (canvas, width, height, dpr) => {
      const echartsLib = wx._echarts;
      return echartsLib ? echartsLib.init(canvas, null, { width, height, devicePixelRatio: dpr }) : null;
    };
  },

  _buildWalletChart() {
    const data = this.data.breakdown.wallet || [];
    return (canvas, width, height, dpr) => {
      const chart = this._newChart()(canvas, width, height, dpr);
      if (!chart) return null;
      chart.setOption(this._pieOption(data, CHART.palette));
      return chart;
    };
  },

  _buildStatusChart() {
    const data = this.data.breakdown.status || [];
    return (canvas, width, height, dpr) => {
      const chart = this._newChart()(canvas, width, height, dpr);
      if (!chart) return null;
      chart.setOption(this._pieOption(data, CHART.statusPalette));
      return chart;
    };
  },

  _pieOption(rows, colors) {
    return {
      tooltip: {
        trigger: 'item',
        confine: true,
        backgroundColor: CHART.surface,
        borderColor: CHART.primary,
        textStyle: { color: CHART.text },
        formatter: params => params.name + '\n' + fCompactHKD(params.value) + '（' + params.percent + '%）'
      },
      color: colors,
      series: [{
        type: 'pie',
        radius: ['42%', '72%'],
        center: ['50%', '50%'],
        label: { color: CHART.muted, fontSize: 10, formatter: '{b}\n{d}%' },
        data: rows.map(row => ({ name: row.name || '未標記', value: Math.abs(+row.volume || 0) }))
      }]
    };
  },

  _buildTrendChart() {
    const data = this.data.dailyStats || [];
    return (canvas, width, height, dpr) => {
      const chart = this._newChart()(canvas, width, height, dpr);
      if (!chart) return null;
      chart.setOption({
        tooltip: {
          trigger: 'axis',
          confine: true,
          backgroundColor: CHART.surface,
          borderColor: CHART.primary,
          textStyle: { color: CHART.text },
          formatter: params => {
            const first = Array.isArray(params) ? params[0] : params;
            return (first?.axisValue || '') + '\n' + fCompactHKD(first?.value || 0);
          }
        },
        ...(data.length ? {} : { graphic: { type: 'text', left: 'center', top: 'middle', style: { text: '暫無數據', fill: CHART.muted, fontSize: 14 } } }),
        grid: { left: 8, right: 8, top: 18, bottom: 30, containLabel: true },
        xAxis: {
          type: 'category',
          data: data.map(row => row.period_label || shortDate(row.period)),
          axisLabel: { color: CHART.muted, fontSize: 9 },
          axisLine: { lineStyle: { color: CHART.axis } },
          axisTick: { show: false }
        },
        yAxis: { type: 'value', axisLabel: { color: CHART.muted, fontSize: 10, formatter: value => fCompactHKD(value) }, splitLine: { lineStyle: { color: CHART.grid } } },
        series: [{
          name: '每日交易額', type: 'line', smooth: true, symbol: 'none',
          data: data.map(row => +row.amount || 0),
          lineStyle: { width: 3, color: CHART.primary },
          itemStyle: { color: CHART.primary },
          areaStyle: { color: CHART.primaryArea }
        }]
      });
      return chart;
    };
  }
});
