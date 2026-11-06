const { get, post, login } = require('../../utils/api');

Page({
  data: {
    accessGranted: false,
    password: '',
    verifying: false,
    loading: false,
    status: {
      detailDate: '',
      summaryDate: '',
      publishDate: '',
      consistencyText: '',
      consistent: false,
      canPublish: false,
      checkedAt: '',
      candidateExists: false,
      validationOk: false,
      validationIssues: [],
      validationWarnings: [],
      entityChanges: {}
    }
  },

  onPasswordInput(e) {
    this.setData({ password: e.detail.value });
  },

  async verifyPassword() {
    if (!this.data.password || this.data.verifying) return;
    this.setData({ verifying: true });
    try {
      // 使用当前登录账号重新验证管理密码，不在小程序端保存密码。
      const me = await get('/api/v1/auth/me', { silent: true });
      const result = await login(me.username, this.data.password);
      if (result?.user?.role !== 'admin') throw new Error('只有管理員可以確認數據更新');
      this.setData({ accessGranted: true, password: '' });
      await this.loadStatus();
    } catch (e) {
      wx.showToast({ title: e.message || '驗證失敗', icon: 'none' });
    } finally {
      this.setData({ verifying: false });
    }
  },

  async loadStatus() {
    if (this.data.loading) return;
    this.setData({ loading: true });
    try {
      const result = await get('/api/v1/v2/admin/data-refresh-status', { silent: true });
      const detailDate = result?.b_table?.latest_date || '';
      const summaryDate = result?.a_table?.latest_date || '';
      const publishDate = result?.publish_date || '';
      const sync = result?.local_cloud_consistency || {};
      const validation = result?.candidate_validation || {};
      this.setData({
        status: {
          detailDate,
          summaryDate,
          publishDate,
          consistencyText: sync.text || '待本地校驗',
          consistent: sync.consistent === true,
          canPublish: result?.can_publish === true,
          checkedAt: sync.checked_at || '剛剛',
          candidateExists: result?.candidate_exists === true,
          validationOk: validation?.ok === true,
          validationIssues: validation?.issues || [],
          validationWarnings: validation?.warnings || [],
          entityChanges: result?.entity_changes || {}
        }
      });
    } catch (e) {
      wx.showToast({ title: e.message || '狀態讀取失敗', icon: 'none' });
    } finally {
      this.setData({ loading: false });
    }
  },

  async confirmPublish() {
    if (!this.data.status.canPublish) return;
    const modal = await new Promise(resolve => wx.showModal({
      title: '確認更新',
      content: '確認讓小程序所有用戶看到 ' + this.data.status.publishDate + ' 的數據嗎？',
      success: resolve
    }));
    if (!modal.confirm) return;
    try {
      await post('/api/v1/v2/admin/confirm-data-refresh', null, { silent: true });
      wx.showToast({ title: '已確認更新', icon: 'success' });
      await this.loadStatus();
    } catch (e) {
      wx.showToast({ title: e.message || '發布失敗', icon: 'none' });
    }
  }
});
