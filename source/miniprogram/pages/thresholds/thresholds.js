const { get, put, post } = require('../../utils/api');
const { getRole } = require('../../utils/auth');

Page({
  data: { role: '', thresholds: [], loading: true, savingKey: '', regenerating: false },

  onLoad() {
    const role = getRole();
    if (role !== 'admin') {
      wx.showToast({ title: '只有管理員可以進入', icon: 'none' });
      setTimeout(() => wx.navigateBack(), 500);
      return;
    }
    this.setData({ role });
    this.loadThresholds();
  },

  goBack() { wx.navigateBack(); },

  async loadThresholds() {
    this.setData({ loading: true });
    try {
      const rows = await get('/api/v1/admin/thresholds', { silent: true });
      this.setData({ thresholds: (rows || []).map(row => ({ ...row, draft: String(row.value) })) });
    } catch (e) {
      wx.showToast({ title: e.message || '閾值讀取失敗', icon: 'none' });
    } finally { this.setData({ loading: false }); }
  },

  onValueInput(e) {
    const index = Number(e.currentTarget.dataset.index);
    this.setData({ ['thresholds[' + index + '].draft']: e.detail.value });
  },

  async saveThreshold(e) {
    const index = Number(e.currentTarget.dataset.index);
    const row = this.data.thresholds[index];
    const value = Number(row?.draft);
    if (!row || !Number.isFinite(value) || value < 0) {
      wx.showToast({ title: '請輸入有效的非負數字', icon: 'none' });
      return;
    }
    this.setData({ savingKey: row.threshold_key });
    try {
      await put('/api/v1/admin/thresholds/' + encodeURIComponent(row.threshold_key), { value });
      await this.loadThresholds();

      // 六类规则都直接决定预警页标签，保存后立即按新阈值重算。
      if (['inactive_days', 'dormant_days', 'weekly_decline_pct', 'monthly_decline_pct', 'failure_ratio_pct', 'weekly_spike_pct'].includes(row.threshold_key)) {
        await this.runRegeneration();
        wx.showToast({ title: '已儲存並更新預警', icon: 'success' });
      } else {
        wx.showToast({ title: '已儲存', icon: 'success' });
      }
    } catch (err) {
      wx.showToast({ title: err.message || '儲存失敗', icon: 'none' });
    } finally { this.setData({ savingKey: '' }); }
  },

  async regenerateAlerts() {
    if (this.data.regenerating) return;
    const modal = await new Promise(resolve => wx.showModal({
      title: '重新生成預警',
      content: '將按最新閾值重新檢查交易數據，並更新今日預警。確定繼續嗎？',
      success: resolve
    }));
    if (!modal.confirm) return;
    await this.runRegeneration();
  },

  async runRegeneration() {
    if (this.data.regenerating) return;
    this.setData({ regenerating: true });
    try {
      const result = await post('/api/v1/v2/admin/regenerate-alerts', null);
      wx.showModal({
        title: '預警已更新',
        content: '掃描 ' + (result.scanned || 0) + ' 個商戶，命中 ' + (result.matched || 0) + ' 個，新增 ' + (result.inserted || 0) + ' 條，關閉 ' + (result.stale_closed || 0) + ' 條不再符合規則的預警。',
        showCancel: false
      });
    } catch (e) {
      wx.showToast({ title: e.message || '重新生成失敗', icon: 'none' });
    } finally { this.setData({ regenerating: false }); }
  }
});
