# 数据同步源码

| 文件 | 用途 |
|---|---|
| `cloud_etl.py` | 在云端把 Excel 导入候选 DuckDB |
| `sync_hk_db.sh` | OpenClaw/iMac 编排入口模板 |
| `verify_duckdb_sync.py` | 比较本地与云端核心表逻辑指纹 |
| `build_runtime_bundle.py` | 从完整 ETL 库生成小于 100MiB 的 DuckDB + Parquet 运行包 |
| `mark_sync_verified.py` | 将双文件候选包指纹和批次写入 `biz.db` |

`sync_hk_db.sh` 已移除个人路径，运行前至少设置：

```bash
export CLOUD_HOST='ubuntu@your-server'
export LOCAL_PROJECT='/path/to/local-data-warehouse'
export CLOUD_FULL_DUCKDB='/opt/yeahpay/etl/data/full/merchant.duckdb'
export CLOUD_DUCKDB='/opt/yeahpay/data/candidate/merchant.duckdb'
```

`CLOUD_FULL_DUCKDB` 是 ETL 可写的完整工作库；`CLOUD_DUCKDB` 是生成后的小程序候选运行库。二者不能指向同一路径。脚本只把候选运行包交给发布流程，正式 API 不直接读取完整工作库。

该目录是现有同步实现的交接起点，不是免配置安装包。生产部署和发布安全门见 `../../docs/06_数据同步发布与回滚.md`。
