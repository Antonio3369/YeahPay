const { getRole, logout } = require('../../utils/auth');
const { ROLE_MAP, ROLE_AVATAR } = require('../../utils/config');

Page({
  data: { roleLabel: '', roleAvatar: 'A', isAdmin: false },
  onShow() {
    const role = getApp().getRole();
    this.setData({
      roleLabel: ROLE_MAP[role] || '用戶',
      roleAvatar: ROLE_AVATAR[role] || 'U',
      isAdmin: role === 'admin'
    });
  },
  goPage(e) {
    const page = e.currentTarget.dataset.page;
    if (page) wx.navigateTo({ url: '/pages/' + page + '/' + page });
  },
  doLogout() {
    wx.showModal({
      title: '退出登錄',
      content: '確定要退出嗎？',
      success: (res) => {
        if (!res.confirm) return;
        logout();
        wx.reLaunch({ url: '/pages/login/login' });
      }
    });
  }
});
