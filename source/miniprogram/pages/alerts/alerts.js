/**
 * 预警管理页逻辑
 */

const { get, post, put } = require('../../utils/api');
const { getRole, getScope, buildScopeParam } = require('../../utils/auth');
const { formatAlertList, formatPush } = require('../../utils/preformat');

Page({
  _loadVersion: 0,
  _keywordTimer: null,

  data: {
    summary: { total: 0, unfollowed: 0, following: 0, resolved: 0 },
    alerts: [],
    pushedInbox: [],
    pushes: [],
    pushBadgeCount: 0,
    pushBadgeLabel: '',
    emptyVisible: false,
    activeTab: 'unfollowed',
    loading: true,
    loadingMore: false,
    page: 0,
    pageSize: 20,
    total: 0,
    hasMore: false,
    hasTagFilter: false,
    loadError: '',
    searchKeyword: '',
    tagOptions: [
      { key: 'inactive_days', label: '未激活', selected: false },
      { key: 'dormant_days', label: '已沉默', selected: false },
      { key: 'weekly_decline_pct', label: '交易周环比下降', selected: false },
      { key: 'monthly_decline_pct', label: '交易月环比下降', selected: false },
      { key: 'failure_ratio_pct', label: '交易失败', selected: false },
      { key: 'weekly_spike_pct', label: '交易异动', selected: false }
    ],
    resolveVisible: false,
    resolveTarget: null,
    resolveTouchOptions: ['線上觸達', '線下觸達'],
    resolveTouchIndex: 1,
    resolveSolved: true,
    resolveNote: ''
  },

  onShow() {
    this.loadAlerts();
  },

  onHide() {
    // 离开页面后，旧请求返回时不能覆盖下一次进入页面的结果。
    this._loadVersion += 1;
  },

  /**
   * 加载预警数据
   */
  async loadAlerts(reset = true) {
    if (!reset && (this.data.loading || this.data.loadingMore || !this.data.hasMore)) return;
    const loadVersion = ++this._loadVersion;
    const nextPage = reset ? 1 : this.data.page + 1;
    const activeTab = this.data.activeTab;
    this.setData(reset
      ? {
          loading: true,
          loadingMore: false,
          alerts: [],
          pushedInbox: [],
          pushes: [],
          page: 0,
          total: 0,
          hasMore: false,
          hasTagFilter: false,
          loadError: ''
        }
      : { loadingMore: true });

    const role = getApp().getRole();
    const scope = getScope();
    const sp = buildScopeParam(role, scope);

    try {
      const alertScope = sp ? '&' + sp.substring(1) : '';
      const selectedTags = this.data.tagOptions.filter(item => item.selected).map(item => item.key);
      const hasTagFilter = selectedTags.length > 0;
      const keywordQuery = this.data.searchKeyword.trim()
        ? '&keyword=' + encodeURIComponent(this.data.searchKeyword.trim()) : '';
      const tagQuery = selectedTags.length ? '&tags=' + encodeURIComponent(selectedTags.join(',')) : '';
      const alertPromise = hasTagFilter
        ? get('/api/v1/alerts?page=' + nextPage + '&page_size=' + this.data.pageSize
          + '&status=' + encodeURIComponent(this.data.activeTab) + keywordQuery + tagQuery + alertScope, { silent: !reset })
          .catch(e => ({ __error: e.message }))
        : Promise.resolve({ items: [], total: 0, has_more: false });
      const inboxPromise = reset
        ? get('/api/v1/pushes/inbox', { silent: true }).catch(e => ({ __error: e.message }))
        : Promise.resolve({ items: this.data.pushedInbox });
      const pushStatus = activeTab === 'resolved' ? 'resolved' : 'following';
      // 跟進中/已解決的人工推送也必须经过同一组标签和搜索条件；
      // 没有选择标签时保持空列表，由页面提示先选预警类型。
      const pushPromise = reset && hasTagFilter && (activeTab === 'following' || activeTab === 'resolved')
        ? get('/api/v1/pushes?status=' + pushStatus + keywordQuery + tagQuery, { silent: true }).catch(e => ({ __error: e.message }))
        : Promise.resolve({ items: reset ? [] : this.data.pushes });
      const pushBadgePromise = reset
        ? get('/api/v1/pushes?status=following', { silent: true }).catch(e => ({ __error: e.message }))
        : Promise.resolve({ items: this.data.pushes });
      const [alertListResult, inboxResult, pushListResult, pushBadgeResult] = await Promise.all([
        alertPromise, inboxPromise, pushPromise, pushBadgePromise
      ]);
      if (loadVersion !== this._loadVersion) return;
      const hasError = !!alertListResult.__error;
      const alertList = alertListResult.__error ? { items: [] } : alertListResult;
      const items = formatAlertList(alertList?.items || []);
      const inboxItems = (inboxResult?.items || []).map(formatPush);
      const pushItems = (pushListResult?.items || []).map(formatPush);
      const badgeItems = (pushBadgeResult?.items || []).map(formatPush);
      const pushBadgeCount = pushBadgeResult?.__error
        ? this.data.pushBadgeCount
        : badgeItems.filter(item => item.is_recipient && item.status === 'following' && item.needs_acceptance).length;

      const allAlertItems = reset ? items : this.data.alerts.concat(items);
      const allInboxItems = reset ? inboxItems : this.data.pushedInbox;
      const allPushItems = reset ? pushItems : this.data.pushes;
      const visibleItems = activeTab === 'unfollowed'
        ? allInboxItems.concat(allAlertItems)
        : allAlertItems.concat(allPushItems);
      // total 必须使用接口返回的全量统计，不能使用当前已加载数组长度。
      // alerts/pushes 接口都在后端完成了全量筛选和计数，翻页只负责展示。
      const alertTotal = Number(alertList?.total || 0);
      const inboxTotal = Number(inboxResult?.merchant_total ?? inboxResult?.total ?? inboxItems.length);
      const pushTotal = Number(pushListResult?.merchant_total ?? pushListResult?.total ?? pushItems.length);
      const visibleTotal = activeTab === 'unfollowed'
        ? alertTotal + inboxTotal
        : alertTotal + pushTotal;

      this.setData({
        alerts: reset ? items : this.data.alerts.concat(items),
        pushedInbox: reset ? inboxItems : this.data.pushedInbox,
        pushes: reset ? pushItems : this.data.pushes,
        pushBadgeCount,
        pushBadgeLabel: pushBadgeCount > 99 ? '99+' : String(pushBadgeCount),
        hasTagFilter,
        emptyVisible: hasTagFilter && visibleItems.length === 0,
        page: nextPage,
        total: visibleTotal,
        hasMore: alertList?.has_more !== undefined
          ? !!alertList.has_more
          : ((alertList?.total || 0) > nextPage * this.data.pageSize),
        loading: false,
        loadingMore: false,
        loadError: hasError ? '預警接口暫時無法連接，請稍後重試' : ''
      });
    } catch (e) {
      console.error('[alerts] 加载失败', e);
      this.setData({ loading: false, loadingMore: false, loadError: '預警接口暫時無法連接，請稍後重試' });
    }
  },

  /**
   * Tab 切换
   */
  switchTab(e) {
    const tab = e.currentTarget.dataset.tab;
    if (tab === this.data.activeTab) return;
    this.setData({ activeTab: tab }, () => this.loadAlerts(true));
  },

  onKeywordInput(e) {
    this.setData({ searchKeyword: e.detail.value });
    clearTimeout(this._keywordTimer);
    this._keywordTimer = setTimeout(() => this.loadAlerts(true), 350);
  },

  onKeywordConfirm(e) {
    clearTimeout(this._keywordTimer);
    this.setData({ searchKeyword: e.detail.value }, () => this.loadAlerts(true));
  },

  toggleTag(e) {
    const index = Number(e.currentTarget.dataset.index);
    const selected = !this.data.tagOptions[index].selected;
    this.setData({ ['tagOptions[' + index + '].selected']: selected }, () => this.loadAlerts(true));
  },

  clearAlertFilters() {
    this.setData({
      searchKeyword: '',
      tagOptions: this.data.tagOptions.map(item => ({ ...item, selected: false }))
    }, () => this.loadAlerts(true));
  },

  onReachBottom() {
    this.loadAlerts(false);
  },

  goMerchantDetail(e) {
    const id = e.currentTarget.dataset.id;
    if (id) wx.navigateTo({ url: '/pages/merchant-detail/merchant-detail?id=' + id });
  },

  goReview(e) {
    const kind = e.currentTarget.dataset.kind;
    const id = e.currentTarget.dataset.id;
    if (kind && id) wx.navigateTo({ url: '/pages/review-detail/review-detail?kind=' + kind + '&id=' + id });
  },

  /**
   * 开始跟进
   */
  async doFollowup(e) {
    const id = e.currentTarget.dataset.id;
    try {
      await post('/api/v1/alerts/' + id + '/followup', { content: '跟進中' });
      wx.showToast({ title: '已開始跟進', icon: 'success' });
      this.loadAlerts();
    } catch (err) {
      wx.showToast({ title: '操作失敗', icon: 'none' });
    }
  },

  async startPush(e) {
    const id = e.currentTarget.dataset.id;
    try {
      await post('/api/v1/pushes/' + id + '/start', null);
      wx.showToast({ title: '已開始跟進', icon: 'success' });
      this.loadAlerts(true);
    } catch (err) {
      wx.showToast({ title: err.message || '操作失敗', icon: 'none' });
    }
  },

  /**
   * 标记解决
   */
  async doResolve(e) {
    const id = e.currentTarget.dataset.id;
    this.openResolveModal('alert', id);
  },

  doPushResolve(e) {
    this.openResolveModal('push', e.currentTarget.dataset.id);
  },

  openResolveModal(kind, id) {
    this.setData({
      resolveVisible: true,
      resolveTarget: { kind, id },
      resolveTouchIndex: 1,
      resolveSolved: true,
      resolveNote: ''
    });
  },

  closeResolveModal() {
    this.setData({ resolveVisible: false, resolveTarget: null });
  },

  noop() {},

  onResolveTouchChange(e) {
    this.setData({ resolveTouchIndex: Number(e.detail.value) });
  },

  onResolveSolvedChange(e) {
    this.setData({ resolveSolved: !!e.detail.value });
  },

  onResolveNoteInput(e) {
    this.setData({ resolveNote: e.detail.value });
  },

  async confirmResolve() {
    const target = this.data.resolveTarget;
    if (!target) return;
    const touchType = this.data.resolveTouchIndex === 0 ? 'online' : 'offline';
    try {
      const path = target.kind === 'push'
        ? '/api/v1/pushes/' + target.id + '/resolve'
        : '/api/v1/alerts/' + target.id + '/resolve';
      const result = await post(path, {
        touch_type: touchType,
        resolved: this.data.resolveSolved,
        note: this.data.resolveNote || ''
      });
      this.closeResolveModal();
      wx.showToast({ title: result.status === 'resolved' ? '已標記解決' : '已儲存，仍在跟進中', icon: 'success' });
      this.loadAlerts(true);
    } catch (err) {
      wx.showToast({ title: err.message || '操作失敗', icon: 'none' });
    }
  },

  });
