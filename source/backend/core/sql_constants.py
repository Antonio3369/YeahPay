"""
SQL 常量 — 统一商户筛选条件。

业务视图只展示：未注销、已绑定硬件、非测试商户。
测试商户仍保留在数据层，避免交易历史断链；只是不会进入小程序业务统计、
商户列表、预警和排名。等 iMac ETL 增加正式的 is_test 字段后，可将下面的
名称规则替换为字段判断。
"""

TEST_MARKERS = ("test", "demo", "pretest", "sample", "测试", "測試", "样本", "樣本")


def test_marker_sql(expressions: list[str]) -> str:
    """返回字段表达式中是否出现测试标记的 SQL 条件。"""
    checks = []
    for expression in expressions:
        normalized = f"LOWER(COALESCE(CAST({expression} AS VARCHAR), ''))"
        checks.extend(f"{normalized} LIKE '%{marker}%'" for marker in TEST_MARKERS)
    return "(" + " OR ".join(checks) + ")"


def merchant_is_test_sql(alias: str = "m") -> str:
    """返回商户或代理商名称带测试标记的 SQL 条件。"""
    if alias and not alias.replace("_", "").isalnum():
        raise ValueError("非法 SQL 表别名")
    prefix = f"{alias}." if alias else ""
    return test_marker_sql([f"{prefix}name", f"{prefix}agent_name"])


def merchant_not_test_sql(alias: str = "m") -> str:
    return f"NOT {merchant_is_test_sql(alias)}"


MERCHANT_BINDING_WHERE = (
    "status != '注销' AND ((bound_sn IS NOT NULL AND bound_sn != '') "
    "OR (bound_qr_code IS NOT NULL AND bound_qr_code != '')) "
    f"AND {merchant_not_test_sql('')}"
)

MERCHANT_BINDING_WHERE_M = (
    "m.status != '注销' AND ((m.bound_sn IS NOT NULL AND m.bound_sn != '') "
    "OR (m.bound_qr_code IS NOT NULL AND m.bound_qr_code != '')) "
    f"AND {merchant_not_test_sql('m')}"
)


def merchant_binding_where(alias: str = "m") -> str:
    """返回带表别名的有效商户条件：未注销、已绑定硬件且非测试。"""
    if alias and not alias.replace("_", "").isalnum():
        raise ValueError("非法 SQL 表别名")
    prefix = f"{alias}." if alias else ""
    return (
        f"{prefix}status != '注销' AND "
        f"(({prefix}bound_sn IS NOT NULL AND {prefix}bound_sn != '') OR "
        f"({prefix}bound_qr_code IS NOT NULL AND {prefix}bound_qr_code != '')) AND "
        f"{merchant_not_test_sql(alias)}"
    )


def successful_transaction_where(alias: str = "t") -> str:
    """统一金额口径：只统计交易成功且金额为正的记录。"""
    if not alias.replace("_", "").isalnum():
        raise ValueError("非法 SQL 表别名")
    return f"{alias}.trans_status = '交易成功' AND COALESCE({alias}.amount, 0) > 0"
