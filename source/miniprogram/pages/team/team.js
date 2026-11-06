/**
 * 飞虎队页面逻辑
 */

const { get } = require('../../utils/api');
const { getRole, getScope, buildScopeParam } = require('../../utils/auth');
const { formatTeamList } = require('../../utils/preformat');

Page({
  data: {
    teamList: [],
    loading: true,
    role: '',
    accessDenied: false
  },

  onShow() {
    const role = getApp().getRole();
    if (role === 'sales') {
      this.setData({ role, accessDenied: true, teamList: [], loading: false });
      return;
    }
    this.setData({ role, accessDenied: false });
    this.loadTeam();
  },

  async loadTeam() {
    this.setData({ loading: true });

    const role = getApp().getRole();
    const scope = getScope();
    const sp = buildScopeParam(role, scope);

    try {
      const result = await get('/api/v1/v2/team/rank' + (sp ? '?' + sp.substring(1) : ''));
      this.setData({
        teamList: formatTeamList(result),
        loading: false
      });
    } catch (e) {
      console.error('[team] 加载失败', e);
      this.setData({ loading: false });
    }
  },

  goSalesPerformance() {
    wx.navigateTo({ url: '/pages/sales-performance/sales-performance' });
  },

  goSalesDetail(e) {
    const name = e.currentTarget.dataset.name;
    if (!name) return;
    wx.navigateTo({ url: '/pages/sales-detail/sales-detail?name=' + encodeURIComponent(name) });
  }
});
