const { get } = require('../../utils/api');
const { getRole, getScope, buildScopeParam } = require('../../utils/auth');
const { formatSilentRow } = require('../../utils/preformat');

Page({
  data: {
    summary: { total: 0, sleeping: 0, risky: 0 },
    thresholds: { silent_days: 14, silent_max_days: 180, silent_amount: 300, risky_days: 180, risky_amount: 100, risky_lookback_days: 90 },
    list: [],
    searchText: '', page: 0, pageSize: 20, total: 0, hasMore: false, loading: true, loadingMore: false
  },
  onShow() { this.loadSilent(true); },
  onUnload() {
    if (this._searchTimer) clearTimeout(this._searchTimer);
  },
  buildListUrl(page) {
    const role = getApp().getRole();
    const scope = getScope();
    const sp = buildScopeParam(role, scope);
    const query = ['page=' + page, 'page_size=' + this.data.pageSize];
    if (this.data.searchText) query.push('keyword=' + encodeURIComponent(this.data.searchText));
    if (sp) query.push(sp.substring(1));
    return '/api/v1/v2/silent/list?' + query.join('&');
  },
  async loadSilent(reset = true) {
    if (!reset && (this.data.loading || this.data.loadingMore || !this.data.hasMore)) return;
    const nextPage = reset ? 1 : this.data.page + 1;
    this.setData(reset
      ? { loading: true, loadingMore: false, list: [], page: 0, total: 0, hasMore: false }
      : { loadingMore: true });
    if (reset) wx.showLoading({ title: '載入中...', mask: true });
    const role = getApp().getRole();
    const scope = getScope();
    const sp = buildScopeParam(role, scope);
    try {
      const [summary, listRes] = await Promise.all([
        get('/api/v1/v2/silent/summary' + (sp ? '?' + sp.substring(1) : '')),
        get(this.buildListUrl(nextPage), { silent: !reset })
      ]);
      const items = (listRes?.items || []).map(formatSilentRow);
      this.setData({
        summary: { total: summary?.total || 0, sleeping: summary?.sleeping || 0, risky: summary?.risky || 0 },
        thresholds: summary?.thresholds || this.data.thresholds,
        list: reset ? items : this.data.list.concat(items),
        page: nextPage,
        total: listRes?.total || 0,
        hasMore: listRes?.has_more !== undefined
          ? !!listRes.has_more
          : ((listRes?.total || 0) > nextPage * this.data.pageSize),
        loading: false,
        loadingMore: false
      });
    } catch (e) {
      console.error('[silent]', e);
      this.setData({ loading: false, loadingMore: false });
      wx.showToast({ title: e.message || '沉默商戶載入失敗', icon: 'none' });
    } finally { if (reset) wx.hideLoading(); }
  },
  onReachBottom() {
    this.loadSilent(false);
  },
  onSearch(e) {
    this.setData({ searchText: (e.detail.value || '').trim() });
    if (this._searchTimer) clearTimeout(this._searchTimer);
    this._searchTimer = setTimeout(() => this.loadSilent(true), 300);
  },
  showDefinition(e) {
    wx.showModal({
      title: e.currentTarget.dataset.title,
      content: e.currentTarget.dataset.definition,
      showCancel: false,
      confirmText: '知道了'
    });
  },
  goMerchantDetail(e) {
    const id = e.currentTarget.dataset.id;
    if (id) wx.navigateTo({ url: '/pages/merchant-detail/merchant-detail?id=' + id });
  }
});
