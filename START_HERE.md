# YeahPay 微信小程序项目交接入口

交接源码快照：2026-08-31（包含轻量运行包与双文件发布机制）  
历史 Git 基线：`handover-20260825`（提交 `590f7fd`）  
项目形态：微信原生小程序 + FastAPI + DuckDB + SQLite

## 先读什么

1. `docs/01_系统架构与技术栈.md`：理解系统边界和目录。
2. `docs/05_业务口径权限与页面逻辑.md`：理解不能随意改变的统计口径。
3. `docs/06_数据同步发布与回滚.md`：理解 DuckDB 更新为什么分候选库和正式库。
4. `docs/02_部署配置与微信发布.md`：建立后端与微信小程序部署环境。
5. `docs/08_接管验收清单.md`：逐项签字验收。

精确接口和数据库 Schema 不靠人工抄写，分别在：

- `machine-readable/openapi.json`
- `machine-readable/api_catalog.csv`
- `machine-readable/database_dictionary.json`
- `machine-readable/page_api_field_matrix.csv`

## 文件夹内容

```text
source/miniprogram/       微信原生小程序源码
source/backend/           FastAPI 后端源码（不含 venv、.env、运行数据库）
source/data-sync/         ETL、同步与一致性验证脚本
data/merchant.duckdb      轻量只读运行库（维表、汇总表及交易明细视图）
data/ods_transactions_all-*.parquet  全时间范围交易明细列存文件；必须与 DuckDB 同目录
data/biz.db               当前账号、权限、预警、推送、跟进与发布状态
docs/                     接管说明
machine-readable/         自动生成的精确接口与字段资料
tools/                    验证、冒烟测试和资料再生成工具
git/yeahpay-handover.bundle  可离线恢复 2026-08-25 历史基线；不能代替本包 source/ 最新快照
```

## 首次接管的最短路径

```bash
cd source/backend
python3.11 -m venv venv
./venv/bin/pip install -r requirements-lock.txt
cp .env.example .env
```

修改 `.env` 中的路径和随机 `JWT_SECRET` 后：

```bash
./venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health
```

然后在微信开发者工具导入 `source/miniprogram`，填写正式小程序 AppID。小程序已确认不请求后端 API（`utils/config.js` 中 `USE_REMOTE_API = false`），无需配置 request 合法域名。

## 重要警告

- 不要把 `.env`、服务器密码、Cloudflare 密钥或微信密钥提交到仓库。
- `merchant.duckdb` 与同目录的 `ods_transactions_all-*.parquet` 合称分析运行包，缺一不可；`biz.db` 是独立业务状态，更新分析运行包时必须保留业务库。
- 所有“本月、上月、近一周、近一月”都以 `biz.db.settings.data_refresh_date` 为业务截止日，不以服务器今天为准。
- 候选运行包通过校验并由管理员在小程序确认后，才可一起替换正式运行包。
- 交易金额展示单位由前端格式化，数据库与 API 内部保持 HKD 原值。

## 完整性验证

交付方生成校验文件后，接收方执行：

```bash
./tools/verify_package.sh
```

`manifest.sha256` 不一致时，不应部署该副本。
