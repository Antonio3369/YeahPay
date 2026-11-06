/**
 * 全局配置常量
 *
 * 产品确认：本小程序不请求后端 API。
 * 页面数据由 utils/local-api.js 在本地处理；FastAPI 可独立部署，但不作为小程序数据源。
 */

const USE_REMOTE_API = false;

// 远端地址保留注释，便于对照历史 B/正式环境；当前不会发起 wx.request。
// const API_BASE_URL = 'https://b-api.seeya.icu';
// const API_BASE_URL = 'https://api.yeahpay.hk';
const API_BASE_URL = '';

const REQUEST_TIMEOUT = 15000;

const TOKEN_KEY = 'yp_token';
const ROLE_KEY = 'yp_role';
const SCOPE_KEY = 'yp_scope';
const LOCAL_DB_KEY = 'yp_local_db';

const ROLE_MAP = {
  admin: '管理員',
  supervisor: '代理商',
  sales: '銷售'
};

const ROLE_AVATAR = {
  admin: 'A',
  supervisor: 'G',
  sales: 'S'
};

module.exports = {
  USE_REMOTE_API,
  API_BASE_URL,
  REQUEST_TIMEOUT,
  TOKEN_KEY,
  ROLE_KEY,
  SCOPE_KEY,
  LOCAL_DB_KEY,
  ROLE_MAP,
  ROLE_AVATAR
};
