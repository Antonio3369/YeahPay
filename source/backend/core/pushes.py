"""商户人工推送的共享查询逻辑。"""

from core import sqlite_client


def visible_push_merchant_ids(user_id: int, status: str = "following"):
    """返回账号作为推送者或接收者可见的商户 ID。"""
    rows = sqlite_client.query_all(
        """
        SELECT DISTINCT merchant_id
        FROM merchant_pushes
        WHERE (pushed_by = ? OR pushed_to = ?) AND status = ?
        """,
        [user_id, user_id, status],
    )
    return [str(row["merchant_id"]) for row in rows]


def visible_push_counts(user_id: int):
    rows = sqlite_client.query_all(
        """
        SELECT status, COUNT(*) AS cnt
        FROM merchant_pushes
        WHERE pushed_by = ? OR pushed_to = ?
        GROUP BY status
        """,
        [user_id, user_id],
    )
    return {row["status"]: row["cnt"] for row in rows}


def merge_scope_with_pushes(scope_merchant_ids, current_user):
    """把推送商户并入受限视角，但不收窄管理员的全部机构视角。"""
    pushed_ids = visible_push_merchant_ids(current_user["id"])
    if not pushed_ids:
        return scope_merchant_ids

    # 管理员未选择代理商/销售时，None 代表全部机构，不能被推送记录改成
    # “仅显示被推送商户”，否则推送一次就会把原有预警列表过滤掉。
    if current_user.get("role") == "admin" and scope_merchant_ids is None:
        return None

    if scope_merchant_ids is None:
        return pushed_ids
    return list(dict.fromkeys(scope_merchant_ids + pushed_ids))
