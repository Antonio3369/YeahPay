/**
 * 认证工具模块
 * 负责 token、角色、视角的存取和登录态管理
 */

const { TOKEN_KEY, ROLE_KEY, SCOPE_KEY } = require('./config');

/**
 * 保存 JWT token
 */
function setToken(token) {
  try {
    wx.setStorageSync(TOKEN_KEY, token);
  } catch (e) {
    console.error('[auth] setToken 失败', e);
  }
}

/**
 * 获取 JWT token
 */
function getToken() {
  try {
    return wx.getStorageSync(TOKEN_KEY) || '';
  } catch (e) {
    return '';
  }
}

/**
 * 是否已登录（有 token）
 */
function isLoggedIn() {
  const token = getToken();
  return token && token.length > 10;
}

/**
 * 保存当前角色
 */
function setRole(role) {
  try {
    wx.setStorageSync(ROLE_KEY, role);
  } catch (e) {
    console.error('[auth] setRole 失败', e);
  }
}

/**
 * 获取当前角色
 */
function getRole() {
  try {
    return wx.getStorageSync(ROLE_KEY) || '';
  } catch (e) {
    return '';
  }
}

/**
 * 保存视角选择
 */
function setScope(scope) {
  try {
    wx.setStorageSync(SCOPE_KEY, scope);
  } catch (e) {
    console.error('[auth] setScope 失败', e);
  }
}

/**
 * 获取视角选择
 */
function getScope() {
  try {
    return wx.getStorageSync(SCOPE_KEY) || 'all';
  } catch (e) {
    return 'all';
  }
}

/**
 * 清除所有登录态（登出）
 */
function logout() {
  try {
    wx.removeStorageSync(TOKEN_KEY);
    wx.removeStorageSync(ROLE_KEY);
    wx.removeStorageSync(SCOPE_KEY);
    // 页面跳转不会自动刷新 App.globalData；同步清空，避免登录页因旧角色再次跳回工作台。
    if (typeof getApp === 'function') {
      const app = getApp();
      if (app && app.globalData) {
        app.globalData.role = '';
        app.globalData.scope = 'all';
        app.globalData.username = '';
      }
    }
  } catch (e) {
    console.error('[auth] logout 失败', e);
  }
}

/**
 * 构建视角查询参数
 * 仅 admin 角色且有非 all 视角时才返回参数
 */
function buildScopeParam(role, scope) {
  if (role !== 'admin' || scope === 'all') return '';
  if (scope.startsWith('agent_')) return '&agent_id=' + encodeURIComponent(scope.substring(6));
  if (scope.startsWith('sales_')) return '&sales_person=' + encodeURIComponent(scope.substring(6));
  return '';
}

module.exports = {
  setToken,
  getToken,
  isLoggedIn,
  setRole,
  getRole,
  setScope,
  getScope,
  logout,
  buildScopeParam
};
