# 数据文件说明

## 分析运行包：两个文件缺一不可

- `merchant.duckdb`：维表、汇总表，以及名为 `ods_transactions_all` 的只读视图。
- `ods_transactions_all-<内容摘要>.parquet`：该视图实际读取的全时间范围交易明细。

两文件必须放在同一个目录，不能只复制 DuckDB。后端环境变量仍只需设置 `DUCKDB_PATH=.../merchant.duckdb`，外部 Parquet 会由后端按数据库所在目录安全解析。

当前轻量包覆盖 20 家商户、10,000 行交易，日期范围 2026-03-01 至 2026-08-08，只从交易明细移除了小程序全部接口未使用的列；接口路径、字段和统计口径没有变化。精确表、字段、行数和日期覆盖见：

- `../machine-readable/database_dictionary.json`
- `../docs/04_数据库表与字段字典.md`

## `biz.db`

当前 SQLite 业务状态原件，包含账号密码 Hash、账号数据范围、阈值、预警、推送、跟进、复盘和已确认数据日期。它不是可丢弃的缓存，也不能用 `05-database/biz.db` 的历史副本替代。

该文件含公司内部账号与业务记录，应按敏感内部数据存储。交付后建议由技术部门轮换管理员密码和 JWT 密钥。

## 恢复原则

- DuckDB 与它引用的 Parquet 必须作为同一候选版本发布或从同一备份目录回滚。
- SQLite 必须独立备份；恢复时需与对应 DuckDB 发布日期一致。
- 不要直接在运行中的数据库文件上执行 ETL。
- 不要用二进制 SHA 判断本地/云端逻辑数据是否一致；使用 `source/data-sync/verify_duckdb_sync.py`。

完整 ETL 工作库不在交接包中。它仍由 iMac/云端 ETL 保存，更新后通过 `build_runtime_bundle.py` 生成这个小于 100MiB 的查询运行包。
