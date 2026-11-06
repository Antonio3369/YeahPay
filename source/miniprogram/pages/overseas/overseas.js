const { get } = require('../../utils/api');
const { formatOverseasRow } = require('../../utils/preformat');
Page({
  data: { list: [], searchText: '', loading: true, loadError: '' },
  onUnload() {
    if (this._searchTimer) clearTimeout(this._searchTimer);
  },
  onShow() {
    this.setData({ loading: true, loadError: '' });
    let url = '/api/v1/v2/overseas/top10';
    if (this.data.searchText) url += '?keyword=' + encodeURIComponent(this.data.searchText);
    get(url).then(d => {
      const rows = d?.items || (Array.isArray(d) ? d : []);
      this.setData({ list: rows.map(formatOverseasRow), loading: false });
    }).catch((e) => {
      console.error('[overseas]', e);
      this.setData({ list: [], loading: false, loadError: '境外卡數據暫時無法載入' });
    });
  },
  onSearch(e) {
    this.setData({ searchText: (e.detail.value || '').trim() });
    if (this._searchTimer) clearTimeout(this._searchTimer);
    this._searchTimer = setTimeout(() => this.onShow(), 300);
  },
  goMerchantDetail(e) {
    const id = e.currentTarget.dataset.id;
    if (id) wx.navigateTo({ url: '/pages/merchant-detail/merchant-detail?id=' + id });
  }
});
