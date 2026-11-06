const { get } = require('../../utils/api');
const { fHKD, fCompactHKD } = require('../../utils/format');
const CHART = require('../../utils/chart-theme');

function countCompare(current, previous) {
  const now = Number(current || 0);
  const before = Number(previous || 0);
  const diff = now - before;
  if (!before) {
    return {
      text: diff === 0 ? '較上月持平' : '較上月 +' + diff + ' 家',
      className: diff > 0 ? 'compare-up' : 'compare-flat'
    };
  }
  const pct = diff / before * 100;
  const sign = diff > 0 ? '+' : '';
  return {
    text: '較上月 ' + sign + diff + ' 家（' + (pct > 0 ? '+' : '') + pct.toFixed(1) + '%）',
    className: diff > 0 ? 'compare-up' : (diff < 0 ? 'compare-down' : 'compare-flat')
  };
}

Page({
  data: {
    name: '',
    avatar: 'S',
    info: null,
    breakdown: { wallet: [], status: [] },
    merchants: [],
    walletReady: false,
    statusReady: false,
    merchantPage: 0,
    merchantPageSize: 20,
    merchantTotal: 0,
    merchantHasMore: false,
    merchantLoadingMore: false,
    ecWallet: { lazyLoad: true },
    ecStatus: { lazyLoad: true },
    loading: true,
    loadError: ''
  },

  onLoad(options) {
    const name = options.name ? decodeURIComponent(options.name) : '';
    if (!name) {
      this.setData({ loading: false, loadError: '缺少銷售名稱' });
      return;
    }
    this.setData({ name, avatar: name.trim().charAt(0).toUpperCase() || 'S' });
    this.loadDetail(name);
  },

  onShow() {
    if (this._loadedOnce && this.data.name) this.loadDetail(this.data.name);
  },

  async loadDetail(name) {
    if (this._loadingDetail) return;
    this._loadingDetail = true;
    wx.showLoading({ title: '載入中...', mask: true });
    try {
      const encoded = encodeURIComponent(name);
      const [infoResult, breakdownResult, merchantsResult] = await Promise.all([
        get('/api/v1/v2/entity/sales/' + encoded),
        get('/api/v1/v2/entity/sales/' + encoded + '/breakdown'),
        get('/api/v1/v2/entity/sales/' + encoded + '/merchants?page=1&page_size=' + this.data.merchantPageSize)
      ]);
      const breakdown = breakdownResult || { wallet: [], status: [] };
      const merchantRows = merchantsResult?.items || (Array.isArray(merchantsResult) ? merchantsResult : []);
      const merchants = merchantRows.map((merchant, index) => ({
        ...merchant,
        rank: index + 1,
        amount_fmt: fHKD(merchant.tv || 0)
      }));
      this.setData({
        info: {
          ...infoResult,
          this_month_vol_fmt: fHKD(infoResult?.this_month_vol || 0),
          merchant_compare: countCompare(infoResult?.merchant_count, infoResult?.previous_merchant_count),
          active_compare: countCompare(infoResult?.active_count, infoResult?.previous_active_count)
        },
        breakdown: {
          wallet: Array.isArray(breakdown.wallet) ? breakdown.wallet : [],
          status: Array.isArray(breakdown.status) ? breakdown.status : []
        },
        merchants,
        merchantPage: 1,
        merchantTotal: merchantsResult?.total || merchants.length,
        merchantHasMore: merchantsResult?.has_more !== undefined
          ? !!merchantsResult.has_more
          : ((merchantsResult?.total || 0) > this.data.merchantPageSize),
        walletReady: (breakdown.wallet || []).length > 0,
        statusReady: (breakdown.status || []).length > 0,
        loading: false,
        loadError: ''
      }, () => this._waitForEchartsAndInit(0));
    } catch (e) {
      console.error('[sales-detail] 加载失败', e);
      this.setData({
        info: null,
        breakdown: { wallet: [], status: [] },
        merchants: [],
        merchantTotal: 0,
        walletReady: false,
        statusReady: false,
        loading: false,
        loadError: e.message || '銷售詳情載入失敗'
      });
    } finally {
      this._loadingDetail = false;
      this._loadedOnce = true;
      wx.hideLoading();
    }
  },

  async loadMoreMerchants() {
    if (this.data.merchantLoadingMore || !this.data.merchantHasMore) return;
    this.setData({ merchantLoadingMore: true });
    try {
      const page = this.data.merchantPage + 1;
      const result = await get('/api/v1/v2/entity/sales/' + encodeURIComponent(this.data.name)
        + '/merchants?page=' + page + '&page_size=' + this.data.merchantPageSize, { silent: true });
      const rows = (result?.items || []).map((merchant, index) => ({
        ...merchant,
        rank: this.data.merchants.length + index + 1,
        amount_fmt: fHKD(merchant.tv || 0)
      }));
      this.setData({
        merchants: this.data.merchants.concat(rows),
        merchantPage: page,
        merchantTotal: result?.total || this.data.merchantTotal,
        merchantHasMore: result?.has_more !== undefined
          ? !!result.has_more
          : ((result?.total || 0) > page * this.data.merchantPageSize),
        merchantLoadingMore: false
      });
    } catch (e) {
      console.error('[sales-detail] 加载更多商户失败', e);
      this.setData({ merchantLoadingMore: false });
    }
  },

  onReachBottom() {
    this.loadMoreMerchants();
  },

  goMerchantDetail(e) {
    const id = e.currentTarget.dataset.id;
    if (id) wx.navigateTo({ url: '/pages/merchant-detail/merchant-detail?id=' + id });
  },

  _waitForEchartsAndInit(attempt) {
    if (!wx._echarts && attempt < 12) {
      setTimeout(() => this._waitForEchartsAndInit(attempt + 1), 100);
      return;
    }
    wx.nextTick(() => this._initCharts());
  },

  _initCharts() {
    [
      ['#salesWalletChart', this._buildPieChart(this.data.breakdown.wallet, CHART.palette)],
      ['#salesStatusChart', this._buildPieChart(this.data.breakdown.status, CHART.statusPalette)]
    ].forEach(([selector, init]) => {
      const component = this.selectComponent(selector);
      if (component && typeof component.init === 'function' && init) {
        if (component.chart && typeof component.chart.dispose === 'function') component.chart.dispose();
        component.init(init);
      }
    });
  },

  _buildPieChart(rows, colors) {
    return (canvas, width, height, dpr) => {
      const chart = wx._echarts ? wx._echarts.init(canvas, null, { width, height, devicePixelRatio: dpr }) : null;
      if (!chart) return null;
      chart.setOption({
        tooltip: { trigger: 'item', confine: true, backgroundColor: CHART.surface, borderColor: CHART.primary, textStyle: { color: CHART.text }, formatter: item => item.name + '\n' + fCompactHKD(item.value) + '（' + item.percent + '%）' },
        color: colors,
        series: [{
          type: 'pie', radius: ['42%', '72%'],
          label: { color: CHART.muted, fontSize: 10, formatter: '{b}\n{d}%' },
          data: (rows || []).map(row => ({ name: row.name || '未標記', value: Math.abs(+row.volume || 0) }))
        }]
      });
      return chart;
    };
  }
});
