/**
 * 全局配置常量
 * 上线前将 API_BASE_URL 改为正式服务器地址
 */

// 后端 API 地址（B 联调环境）。
// B 使用独立 HTTPS 子域名，不影响仍在线的 A 小程序。
const API_BASE_URL = 'https://b-api.seeya.icu';

// 生产环境地址（上线时切换）
// const API_BASE_URL = 'https://api.yeahpay.hk';

// 请求超时时间（毫秒）
const REQUEST_TIMEOUT = 15000;

// token 存储 key
const TOKEN_KEY = 'yp_token';

// 角色存储 key
const ROLE_KEY = 'yp_role';

// 视角缓存 key
const SCOPE_KEY = 'yp_scope';

// 角色映射（后端返回 -> 前端显示）
const ROLE_MAP = {
  admin: '管理員',
  supervisor: '代理商',
  sales: '銷售'
};

// 角色头像首字母
const ROLE_AVATAR = {
  admin: 'A',
  supervisor: 'G',
  sales: 'S'
};

module.exports = {
  API_BASE_URL,
  REQUEST_TIMEOUT,
  TOKEN_KEY,
  ROLE_KEY,
  SCOPE_KEY,
  ROLE_MAP,
  ROLE_AVATAR
};
