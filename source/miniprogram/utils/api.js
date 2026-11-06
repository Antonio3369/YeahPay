/**
 * 请求封装：默认走本地数据层，不发起 wx.request。
 */

const { USE_REMOTE_API, API_BASE_URL, REQUEST_TIMEOUT } = require('./config');
const { getToken, logout } = require('./auth');
const localApi = require('./local-api');

let isRefreshing = false;

function request(path, options = {}) {
  const {
    method = 'GET',
    data = null,
    silent = false,
    noAuth = false
  } = options;

  if (!noAuth && !getToken()) {
    return Promise.reject(new Error('未登錄'));
  }

  if (!silent) {
    wx.showLoading({ title: '載入中...', mask: true });
  }

  const finish = () => {
    if (!silent) wx.hideLoading();
  };

  if (!USE_REMOTE_API) {
    return Promise.resolve()
      .then(() => localApi.handle(path, { method, data, noAuth }))
      .finally(finish);
  }

  const header = {
    'Content-Type': 'application/json'
  };
  if (!noAuth) {
    const token = getToken();
    if (token) header.Authorization = 'Bearer ' + token;
  }

  return new Promise((resolve, reject) => {
    wx.request({
      url: API_BASE_URL + path,
      method,
      header,
      data,
      timeout: REQUEST_TIMEOUT,
      success(res) {
        if (res.statusCode === 401) {
          logout();
          if (!isRefreshing) {
            isRefreshing = true;
            wx.reLaunch({ url: '/pages/login/login' });
            setTimeout(() => { isRefreshing = false; }, 2000);
          }
          reject(new Error('登錄已過期，請重新登錄'));
          return;
        }
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(new Error(res.data?.detail || '請求失敗'));
          return;
        }
        resolve(res.data);
      },
      fail() {
        reject(new Error('網絡請求失敗，請檢查網絡連接'));
      },
      complete: finish
    });
  });
}

function get(path, options = {}) {
  return request(path, { ...options, method: 'GET' });
}

function post(path, data, options = {}) {
  return request(path, { ...options, method: 'POST', data });
}

function put(path, data, options = {}) {
  return request(path, { ...options, method: 'PUT', data });
}

function login(username, password) {
  return post('/api/v1/auth/login', { username, password }, { noAuth: true });
}

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
