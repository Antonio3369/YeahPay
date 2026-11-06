const { get } = require('../../utils/api');
const { formatMastercardRow } = require('../../utils/preformat');
Page({
  data: { list: [], searchText: '', page: 0, pageSize: 20, total: 0, hasMore: false, loading: true, loadingMore: false },
  onShow() { this.loadList(true); },
  onUnload() {
    if (this._searchTimer) clearTimeout(this._searchTimer);
  },
  buildListUrl(page) {
    let url = '/api/v1/v2/mastercard/rank?page=' + page + '&page_size=' + this.data.pageSize;
    if (this.data.searchText) url += '&keyword=' + encodeURIComponent(this.data.searchText);
    return url;
  },
  async loadList(reset = true) {
    if (!reset && (this.data.loading || this.data.loadingMore || !this.data.hasMore)) return;
    const page = reset ? 1 : this.data.page + 1;
    this.setData(reset
      ? { loading: true, loadingMore: false, list: [], page: 0, hasMore: false }
      : { loadingMore: true });
    try {
      const d = await get(this.buildListUrl(page), { silent: !reset });
      const items = (d?.items || (Array.isArray(d) ? d : [])).map(formatMastercardRow);
      this.setData({
        list: reset ? items : this.data.list.concat(items),
        page,
        total: d?.total || 0,
        hasMore: d?.has_more !== undefined ? !!d.has_more : ((d?.total || 0) > page * this.data.pageSize),
        loading: false,
        loadingMore: false
      });
    } catch (e) {
      console.error('[mastercard]', e);
      this.setData({ loading: false, loadingMore: false });
    }
  },
  onReachBottom() {
    this.loadList(false);
  },
  onSearch(e) {
    this.setData({ searchText: (e.detail.value || '').trim() });
    if (this._searchTimer) clearTimeout(this._searchTimer);
    this._searchTimer = setTimeout(() => this.loadList(true), 300);
  },
  goMerchantDetail(e) {
    const id = e.currentTarget.dataset.id;
    if (id) wx.navigateTo({ url: '/pages/merchant-detail/merchant-detail?id=' + id });
  }
});
