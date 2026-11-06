/**
 * 商户列表页逻辑
 */

const { get } = require('../../utils/api');
const { getRole, getScope, buildScopeParam } = require('../../utils/auth');
const { formatMerchantList } = require('../../utils/preformat');

Page({
  data: {
    merchants: [],      // 当前已加载商户（含格式化字段）
    searchText: '',
    totalCount: 0,
    page: 0,
    pageSize: 20,
    hasMore: false,
    loading: true,
    loadingMore: false
  },

  onShow() {
    this.loadMerchants(true);
  },

  onUnload() {
    if (this._searchTimer) clearTimeout(this._searchTimer);
  },

  buildListUrl(page) {
    const role = getApp().getRole();
    const scope = getScope();
    const scopeQuery = buildScopeParam(role, scope);
    const query = [];
    if (scopeQuery) query.push(scopeQuery.substring(1));
    if (this.data.searchText) query.push('keyword=' + encodeURIComponent(this.data.searchText));
    query.push('page=' + page);
    query.push('page_size=' + this.data.pageSize);
    return '/api/v1/v2/merchants/list?' + query.join('&');
  },

  async loadMerchants(reset = false) {
    if (!reset && (this.data.loading || this.data.loadingMore || !this.data.hasMore)) return;
    const page = reset ? 1 : this.data.page + 1;
    const requestId = (this._requestId || 0) + 1;
    this._requestId = requestId;

    this.setData(reset
      ? { loading: true, loadingMore: false, merchants: [], page: 0, hasMore: false }
      : { loadingMore: true });

    try {
      const result = await get(this.buildListUrl(page), { silent: !reset });
      if (requestId !== this._requestId) return;

      const raw = result?.items || [];
      const items = formatMerchantList(raw);
      this.setData({
        merchants: reset ? items : this.data.merchants.concat(items),
        totalCount: result?.total || (reset ? items.length : this.data.totalCount),
        page,
        hasMore: result?.has_more !== undefined
          ? !!result.has_more
          : ((result?.total || 0) > page * this.data.pageSize),
        loading: false,
        loadingMore: false
      });
    } catch (e) {
      console.error('[merchants] 加载失败', e);
      this.setData({ loading: false, loadingMore: false });
      wx.showToast({ title: '加載失敗', icon: 'none' });
    }
  },

  onSearch(e) {
    const searchText = (e.detail.value || '').trim();
    this.setData({ searchText });
    if (this._searchTimer) clearTimeout(this._searchTimer);
    this._searchTimer = setTimeout(() => this.loadMerchants(true), 300);
  },

  onReachBottom() {
    this.loadMerchants(false);
  },

  goDetail(e) {
    const id = e.currentTarget.dataset.id;
    if (!id) return;
    wx.navigateTo({ url: '/pages/merchant-detail/merchant-detail?id=' + id });
  }
});
