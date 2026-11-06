const { get } = require('../../utils/api');
const { fHKD, fNum } = require('../../utils/format');

function monthLabel(value) {
  const match = String(value || '').match(/^(\d{4})-(\d{2})/);
  return match ? match[1] + '-' + match[2] : String(value || '-').slice(0, 10);
}

Page({
  data: {
    agentId: '',
    info: null,
    wallet: [],
    status: [],
    trend: [],
    loading: true,
    loadError: ''
  },

  onLoad(options) {
    const agentId = options.id ? decodeURIComponent(options.id) : '';
    if (!agentId) {
      this.setData({ loading: false, loadError: '缺少代理商ID' });
      return;
    }
    this.setData({ agentId });
    this.loadDetail(agentId);
  },

  onShow() {
    if (this._loadedOnce && this.data.agentId) this.loadDetail(this.data.agentId);
  },

  async loadDetail(agentId) {
    if (this._loadingDetail) return;
    this._loadingDetail = true;
    try {
      const encoded = encodeURIComponent(agentId);
      const [info, breakdown, trend] = await Promise.all([
        get('/api/v1/v2/entity/agent/' + encoded),
        get('/api/v1/v2/entity/agent/' + encoded + '/breakdown'),
        get('/api/v1/v2/entity/agent/' + encoded + '/trend')
      ]);
      this.setData({
        info: info ? {
          ...info,
          this_month_volume_fmt: fHKD(info.this_month_volume || 0)
        } : null,
        wallet: (breakdown?.wallet || []).map(row => ({ ...row, volume_fmt: fNum(row.volume || 0) })),
        status: (breakdown?.status || []).map(row => ({ ...row, volume_fmt: fNum(row.volume || 0) })),
        trend: (Array.isArray(trend) ? trend : []).map(row => ({
          ...row,
          month_label: monthLabel(row.month),
          volume_fmt: fHKD(row.new_volume || 0)
        })),
        loading: false,
        loadError: ''
      });
      wx.setNavigationBarTitle({ title: '代理商詳情' });
    } catch (e) {
      console.error('[agent-detail]', e);
      this.setData({ info: null, wallet: [], status: [], trend: [], loading: false, loadError: e.message || '代理商詳情載入失敗' });
    } finally {
      this._loadingDetail = false;
      this._loadedOnce = true;
    }
  }
});
