const { get } = require('../../utils/api');
const { API_BASE_URL } = require('../../utils/config');
const { getToken, getRole, getScope, buildScopeParam } = require('../../utils/auth');

Page({
  data: {
    q: '', status: '', touchType: '', startDate: '', endDate: '',
    summary: { cases: 0, resolved: 0, following: 0, avg_touch_count: 0, avg_resolve_days: 0 },
    items: [], page: 0, pageSize: 20, hasMore: false, loading: true, loadingMore: false, exporting: false
  },
  onShow() { this.loadReviews(true); },
  buildQuery() {
    const params = [];
    if (this.data.q.trim()) params.push('q=' + encodeURIComponent(this.data.q.trim()));
    if (this.data.status) params.push('status=' + this.data.status);
    if (this.data.touchType) params.push('touch_type=' + this.data.touchType);
    if (this.data.startDate) params.push('start_date=' + this.data.startDate);
    if (this.data.endDate) params.push('end_date=' + this.data.endDate);
    const scope = buildScopeParam(getRole(), getScope());
    if (scope) params.push(scope.substring(1));
    return params.length ? '&' + params.join('&') : '';
  },
  async loadReviews(reset = true) {
    if (!reset && (this.data.loading || this.data.loadingMore || !this.data.hasMore)) return;
    const page = reset ? 1 : this.data.page + 1;
    this.setData(reset ? { loading: true, items: [], page: 0 } : { loadingMore: true });
    try {
      const result = await get('/api/v1/reviews?page=' + page + '&page_size=' + this.data.pageSize + this.buildQuery(), { silent: !reset });
      const resultItems = (result.items || []).map(item => ({ ...item, case_key: item.source_type + '-' + item.source_id }));
      this.setData({ summary: result.summary || this.data.summary, items: reset ? resultItems : this.data.items.concat(resultItems), page, hasMore: !!result.has_more, loading: false, loadingMore: false });
    } catch (e) {
      console.error('[reviews] load failed', e);
      this.setData({ loading: false, loadingMore: false });
      wx.showToast({ title: e.message || '復盤報告載入失敗', icon: 'none' });
    }
  },
  onSearchInput(e) { this.setData({ q: e.detail.value }); },
  onSearchConfirm() { this.loadReviews(true); },
  clearSearch() { this.setData({ q: '' }, () => this.loadReviews(true)); },
  switchStatus(e) { this.setData({ status: e.currentTarget.dataset.status || '' }, () => this.loadReviews(true)); },
  switchTouch(e) { this.setData({ touchType: e.currentTarget.dataset.touch || '' }, () => this.loadReviews(true)); },
  onStartDateChange(e) { this.setData({ startDate: e.detail.value }, () => this.loadReviews(true)); },
  onEndDateChange(e) { this.setData({ endDate: e.detail.value }, () => this.loadReviews(true)); },
  clearDates() { this.setData({ startDate: '', endDate: '' }, () => this.loadReviews(true)); },
  openDetail(e) {
    const { kind, id } = e.currentTarget.dataset;
    if (kind && id) wx.navigateTo({ url: '/pages/review-detail/review-detail?kind=' + kind + '&id=' + id });
  },
  onReachBottom() { this.loadReviews(false); },
  exportReport() {
    if (this.data.exporting) return;
    this.setData({ exporting: true });
    wx.downloadFile({
      url: API_BASE_URL + '/api/v1/reviews/export' + this.buildQuery(),
      header: { Authorization: 'Bearer ' + getToken() },
      success: res => {
        if (res.statusCode !== 200) { wx.showToast({ title: '匯出失敗', icon: 'none' }); return; }
        wx.openDocument({ filePath: res.tempFilePath, fileType: 'xlsx', showMenu: true, fail: () => wx.showToast({ title: '文件已生成，請在文件菜單中打開', icon: 'none' }) });
      },
      fail: () => wx.showToast({ title: '匯出失敗，請檢查網絡', icon: 'none' }),
      complete: () => this.setData({ exporting: false })
    });
  }
});
