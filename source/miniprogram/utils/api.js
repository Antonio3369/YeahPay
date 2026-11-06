/**
 * API 请求封装模块
 * 统一处理：JWT 注入、401 自动重登录、错误提示
 */

const { API_BASE_URL, REQUEST_TIMEOUT } = require('./config');
const { getToken, setToken, logout } = require('./auth');

// 标记是否正在重登录（避免并发重登录）
let isRefreshing = false;
let refreshQueue = [];

/**
 * 通用请求方法
 * @param {string} path   API 路径（不含 base，如 '/api/v1/v2/dashboard/kpi'）
 * @param {object} options 可选参数：method, data, silent（静默模式不显示 loading）
 * @returns {Promise<any>}
 */
function request(path, options = {}) {
  const {
    method = 'GET',
    data = null,
    silent = false,     // 静默模式：不显示 loading 和 toast
    noAuth = false      // 无需认证的请求（如 login）
  } = options;

  // 未登录且不是认证接口，直接返回（避免无意义的 401）
  if (!noAuth && !getToken()) {
    return Promise.reject(new Error('未登錄'));
  }

  // 非静默模式显示 loading
  if (!silent) {
    wx.showLoading({ title: '載入中...', mask: true });
  }

  const header = {
    'Content-Type': 'application/json'
  };

  if (!noAuth) {
    const token = getToken();
    if (token) {
      header['Authorization'] = 'Bearer ' + token;
    }
  }

  const url = API_BASE_URL + path;

  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method,
      header,
      data,
      timeout: REQUEST_TIMEOUT,
      success(res) {
        // 401 未授权 → 清除 token 并跳转登录
        if (res.statusCode === 401) {
          console.warn('[api] 401 未授权，清除登录态');
          logout();

          // 防止并发 401 多次跳转
          if (!isRefreshing) {
            isRefreshing = true;
            wx.reLaunch({ url: '/pages/login/login' });
            // 重置标记
            setTimeout(() => { isRefreshing = false; }, 2000);
          }
          reject(new Error('登錄已過期，請重新登錄'));
          return;
        }

        // 其他非 2xx 状态码
        if (res.statusCode < 200 || res.statusCode >= 300) {
          const detail = res.data?.detail || '請求失敗';
          reject(new Error(detail));
          return;
        }

        resolve(res.data);
      },
      fail(err) {
        console.error('[api] 网络请求失败', path, err);
        reject(new Error('網絡請求失敗，請檢查網絡連接'));
      },
      complete() {
        if (!silent) {
          wx.hideLoading();
        }
      }
    });
  });
}

/**
 * 快捷方法
 */
function get(path, options = {}) {
  return request(path, { ...options, method: 'GET' });
}

function post(path, data, options = {}) {
  return request(path, { ...options, method: 'POST', data });
}

function put(path, data, options = {}) {
  return request(path, { ...options, method: 'PUT', data });
}

/**
 * 登录（无需认证）
 */
function login(username, password) {
  return post('/api/v1/auth/login', { username, password }, { noAuth: true });
}

/**
 * 获取当前用户信息
 */
function getMe() {
  return get('/api/v1/auth/me');
}

module.exports = {
  request,
  get,
  post,
  put,
  login,
  getMe
};
