/**
 * YeahPay 小程序全局入口
 * 负责：登录态检查、角色管理、全局数据缓存
 */

const { isLoggedIn, getRole, setRole, getScope, setScope } = require('./utils/auth');
const { getMe } = require('./utils/api');

App({
  globalData: {
    role: '',           // 当前角色：admin / supervisor / sales
    scope: 'all',       // 当前视角：all / agent_xxx / sales_xxx
    username: '',       // 当前用户名
    agents: [],         // 代理商列表（缓存）
    sales: []           // 销售列表（缓存）
  },

  onLaunch() {
    // 初始化时恢复缓存数据
    if (isLoggedIn()) {
      this.globalData.role = getRole();
      this.globalData.scope = getScope();
    } else {
      this.globalData.role = '';
      this.globalData.scope = 'all';
      this.globalData.username = '';
    }

    // 如果已登录，异步获取用户信息
    if (isLoggedIn()) {
      this.refreshUserInfo();
    }
  },

  /**
   * 刷新用户信息（异步，不阻塞页面渲染）
   */
  async refreshUserInfo() {
    try {
      const user = await getMe();
      if (user && user.role) {
        this.globalData.role = user.role;
        this.globalData.username = user.username || '';
        setRole(user.role);
      }
    } catch (e) {
      console.warn('[app] 获取用户信息失败', e.message);
      // 不处理：可能是网络问题，继续使用缓存的 role
    }
  },

  /**
   * 设置当前角色
   */
  setRole(role) {
    this.globalData.role = role;
    setRole(role);
  },

  /**
   * 获取当前角色
   */
  getRole() {
    return this.globalData.role || getRole();
  },

  /**
   * 设置视角
   */
  setScope(scope) {
    this.globalData.scope = scope;
    setScope(scope);
  },

  /**
   * 获取视角
   */
  getScope() {
    return this.globalData.scope || 'all';
  }
});
