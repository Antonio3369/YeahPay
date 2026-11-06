const { get } = require('../../utils/api');
const { formatFailRow } = require('../../utils/preformat');

Page({
  data: {
    summaryList: [], detailList: [], detailPage: 0, detailPageSize: 30,
    detailTotal: 0, detailHasMore: false, loading: true, loadingMore: false
  },
  onShow() { this.loadFails(); },
  async loadFails(reset = true) {
    if (!reset && (this.data.loading || this.data.loadingMore || !this.data.detailHasMore)) return;
    const nextPage = reset ? 1 : this.data.detailPage + 1;
    this.setData(reset
      ? { loading: true, loadingMore: false, detailList: [], detailPage: 0, detailHasMore: false }
      : { loadingMore: true });
    if (reset) wx.showLoading({ title: '載入中...', mask: true });
    try {
      const requests = [
        reset ? get('/api/v1/fails/summary?days=30') : Promise.resolve(this.data.summaryList),
        get('/api/v1/fails/detail?days=30&page=' + nextPage + '&page_size=' + this.data.detailPageSize, { silent: !reset })
      ];
      const [summary, detail] = await Promise.all(requests);
      const detailItems = (detail?.items || []).map(formatFailRow);
      this.setData({
        summaryList: reset
          ? (Array.isArray(summary) ? summary : []).map(formatFailRow)
          : this.data.summaryList,
        detailList: reset ? detailItems : this.data.detailList.concat(detailItems),
        detailPage: nextPage,
        detailTotal: detail?.total || 0,
        detailHasMore: detail?.has_more !== undefined
          ? !!detail.has_more
          : ((detail?.total || 0) > nextPage * this.data.detailPageSize),
        loading: false,
        loadingMore: false
      });
    } catch (e) {
      console.error('[fails]', e);
      this.setData({ loading: false, loadingMore: false });
      wx.showToast({ title: e.message || '交易失敗數據載入失敗', icon: 'none' });
    } finally {
      wx.hideLoading();
    }
  },
  onReachBottom() {
    this.loadFails(false);
  },
  goMerchantDetail(e) {
    const id = e.currentTarget.dataset.id;
    if (id) wx.navigateTo({ url: '/pages/merchant-detail/merchant-detail?id=' + id });
  }
});
