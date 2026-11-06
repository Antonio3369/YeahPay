/**
 * 登录页逻辑
 * 流程：输入账号密码 → 登录 → 读取账号权限 → 进入工作台
 */

const { login, getMe } = require('../../utils/api');
const { setToken, setRole, setScope, isLoggedIn } = require('../../utils/auth');

Page({
  data: {
    username: '',
    password: '',
    loading: false,
    errorMsg: '',
    showRoles: false,
    role: ''
  },

  onLoad() {
    // 如果已有 token，尝试直接进入
    const app = getApp();
    const cachedRole = app.getRole();
    if (cachedRole && isLoggedIn()) {
      this.goToDashboard();
    }
  },

  /** 输入框绑定 */
  onUsernameInput(e) {
    this.setData({ username: e.detail.value, errorMsg: '' });
  },

  onPasswordInput(e) {
    this.setData({ password: e.detail.value, errorMsg: '' });
  },

  /**
   * 执行登录
   */
  async doLogin() {
    const { username, password } = this.data;

    // 表单校验
    if (!username.trim()) {
      this.setData({ errorMsg: '請輸入賬號' });
      return;
    }
    if (!password) {
      this.setData({ errorMsg: '請輸入密碼' });
      return;
    }

    this.setData({ loading: true, errorMsg: '' });

    try {
      // 1. 登录获取 token
      const result = await login(username.trim(), password);
      if (!result.access_token) {
        throw new Error('登錄失敗，未獲取到令牌');
      }

      // 2. 保存 token
      setToken(result.access_token);

      // 3. 角色由服务端账号决定，不允许用户在前端手动选择
      let role = result.user?.role || '';
      try {
        const user = await getMe();
        role = user?.role || role;
        const app = getApp();
        app.globalData.username = user?.username || result.user?.username || '';
      } catch (e) {
        console.warn('[login] 获取用户信息失败，使用登录返回的角色', e.message);
      }

      if (!role) {
        throw new Error('賬號未配置角色，請聯絡管理員');
      }
      setRole(role);
      setScope('all');
      const app = getApp();
      app.setRole(role);
      app.setScope('all');
      this.goToDashboard();

    } catch (e) {
      console.error('[login] 登录失败', e);
      this.setData({ errorMsg: e.message || '登錄失敗' });
    } finally {
      this.setData({ loading: false });
    }
  },

  /**
   * 跳转到工作台
   */
  goToDashboard() {
    wx.switchTab({ url: '/pages/dashboard/dashboard' });
  }
});
