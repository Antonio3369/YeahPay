"""预警/人工推送复盘报告接口。

复盘填写保持轻量，但每次提交都会保存为独立记录，支持时间线、筛选和 Excel 导出。
"""

from datetime import date, datetime, timedelta
from html import escape
from io import BytesIO
from typing import Optional
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.alerts import ensure_alert_access
from api.scope import scope_merchant_ids as get_scope_merchant_ids
from core import sqlite_client
from core.auth import get_current_user
from core.pushes import merge_scope_with_pushes
from core import duckdb_client

router = APIRouter(prefix="/reviews", tags=["复盘报告"])


def _scope_ids(current_user, agent_id=None, sales_person=None):
    return merge_scope_with_pushes(
        get_scope_merchant_ids(current_user, agent_id, sales_person), current_user
    )


def _source_name_expr():
    return "COALESCE(NULLIF(r.merchant_name, ''), a.merchant_name, p.merchant_name, r.merchant_id)"


def _current_status_expr():
    return "CASE WHEN r.source_type = 'alert' THEN COALESCE(a.status, 'resolved') ELSE COALESCE(p.status, 'resolved') END"


def _ranked_cte(current_user, q, status, touch_type, start_date, end_date, agent_id, sales_person):
    conditions = ["1 = 1"]
    params = []
    name_expr = _source_name_expr()
    if q:
        conditions.append(f"({name_expr} LIKE ? OR CAST(r.merchant_id AS TEXT) LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if touch_type in ("online", "offline"):
        conditions.append("r.touch_type = ?")
        params.append(touch_type)
    if start_date:
        conditions.append("date(r.created_at) >= ?")
        params.append(str(start_date))
    if end_date:
        conditions.append("date(r.created_at) <= ?")
        params.append(str(end_date))

    scope_ids = _scope_ids(current_user, agent_id, sales_person)
    if scope_ids is not None:
        if not scope_ids:
            conditions.append("1 = 0")
        else:
            placeholders = ",".join("?" for _ in scope_ids)
            conditions.append(f"CAST(r.merchant_id AS TEXT) IN ({placeholders})")
            params.extend(scope_ids)

    status_expr = _current_status_expr()
    cte = f"""
        WITH ranked AS (
            SELECT
                r.id,
                r.source_type,
                r.source_id,
                r.merchant_id,
                {name_expr} AS merchant_name,
                CASE WHEN r.source_type = 'alert' THEN COALESCE(a.title, a.alert_type, '系统预警') ELSE '人工推送' END AS source_title,
                {status_expr} AS current_status,
                r.touch_type AS latest_touch_type,
                r.result AS latest_result,
                r.note AS latest_note,
                u.display_name AS latest_user_name,
                r.created_at AS last_followup_at,
                MIN(r.created_at) OVER (PARTITION BY r.source_type, r.source_id) AS first_followup_at,
                COUNT(*) OVER (PARTITION BY r.source_type, r.source_id) AS touch_count,
                CASE WHEN r.source_type = 'alert' THEN a.updated_at ELSE p.resolved_at END AS resolved_at,
                ROW_NUMBER() OVER (
                    PARTITION BY r.source_type, r.source_id
                    ORDER BY r.created_at DESC, r.id DESC
                ) AS rn
            FROM followup_records r
            LEFT JOIN alerts a ON r.source_type = 'alert' AND a.id = r.source_id
            LEFT JOIN merchant_pushes p ON r.source_type = 'push' AND p.id = r.source_id
            LEFT JOIN users u ON u.id = r.user_id
            WHERE {' AND '.join(conditions)}
        )
    """
    outer = ["rn = 1"]
    if status in ("following", "resolved"):
        outer.append("current_status = ?")
        params.append(status)
    return cte, outer, params


def _case_rows(current_user, q, status, touch_type, start_date, end_date, agent_id, sales_person, limit=None, offset=0):
    cte, outer, params = _ranked_cte(
        current_user, q, status, touch_type, start_date, end_date, agent_id, sales_person
    )
    sql = cte + " SELECT * FROM ranked WHERE " + " AND ".join(outer)
    sql += " ORDER BY last_followup_at DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    return sqlite_client.query_all(sql, params)


def _case_summary(current_user, q, status, touch_type, start_date, end_date, agent_id, sales_person):
    cte, outer, params = _ranked_cte(
        current_user, q, status, touch_type, start_date, end_date, agent_id, sales_person
    )
    rows = sqlite_client.query_all(
        cte
        + " SELECT current_status, COUNT(*) AS case_count, AVG(touch_count) AS avg_touch_count, "
          "AVG(CASE WHEN current_status = 'resolved' AND resolved_at IS NOT NULL "
          "THEN julianday(resolved_at) - julianday(first_followup_at) END) AS avg_resolve_days "
          "FROM ranked WHERE "
        + " AND ".join(outer)
        + " GROUP BY current_status",
        params,
    )
    summary = {
        "cases": 0,
        "resolved": 0,
        "following": 0,
        "avg_touch_count": 0,
        "avg_resolve_days": 0,
    }
    for row in rows:
        count = int(row["case_count"] or 0)
        summary["cases"] += count
        summary[row["current_status"]] = count
        summary["avg_touch_count"] += float(row["avg_touch_count"] or 0) * count
        summary["avg_resolve_days"] += float(row["avg_resolve_days"] or 0) * count
    if summary["cases"]:
        summary["avg_touch_count"] = round(summary["avg_touch_count"] / summary["cases"], 1)
        resolved_count = summary["resolved"]
        summary["avg_resolve_days"] = round(
            summary["avg_resolve_days"] / resolved_count, 1
        ) if resolved_count else 0
    return summary


@router.get("")
def list_reviews(
    q: Optional[str] = Query(None, max_length=100),
    status: Optional[str] = Query(None),
    touch_type: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    agent_id: Optional[str] = Query(None),
    sales_person: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user=Depends(get_current_user),
):
    offset = (page - 1) * page_size
    items = _case_rows(
        current_user, q, status, touch_type, start_date, end_date,
        agent_id, sales_person, page_size, offset,
    )
    summary = _case_summary(
        current_user, q, status, touch_type, start_date, end_date,
        agent_id, sales_person,
    )
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": summary["cases"],
        "has_more": page * page_size < summary["cases"],
        "summary": summary,
    }


@router.get("/{source_type}/{source_id}")
def get_review_detail(source_type: str, source_id: int, current_user=Depends(get_current_user)):
    if source_type not in ("alert", "push"):
        raise HTTPException(status_code=404, detail="复盘类型不存在")

    if source_type == "alert":
        ensure_alert_access(source_id, current_user)
        source = sqlite_client.query_one("SELECT * FROM alerts WHERE id = ?", [source_id])
    else:
        source = sqlite_client.query_one("SELECT * FROM merchant_pushes WHERE id = ?", [source_id])
        if not source:
            raise HTTPException(status_code=404, detail="推送记录不存在")
        if current_user["role"] != "admin" and current_user["id"] not in (source["pushed_by"], source["pushed_to"]):
            raise HTTPException(status_code=403, detail="无权查看该复盘")

    records = sqlite_client.query_all(
        """
        SELECT r.*, u.display_name AS user_name
        FROM followup_records r
        LEFT JOIN users u ON u.id = r.user_id
        WHERE r.source_type = ? AND r.source_id = ?
        ORDER BY r.created_at ASC, r.id ASC
        """,
        [source_type, source_id],
    )
    return {
        "source_type": source_type,
        "source": source,
        "records": records,
        "touch_count": len(records),
    }


def _parse_review_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).replace("Z", "")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _effect_for_review(source_type, source, records):
    """按解决日期计算前30天、后7天和后30天的成功交易变化。"""
    resolved_record = next((r for r in reversed(records) if r.get("result") == "resolved"), None)
    resolved_date = _parse_review_date(
        (source.get("resolved_at") if source_type == "push" else None)
        or (resolved_record or {}).get("created_at")
        or (source.get("updated_at") if source.get("status") == "resolved" else None)
    )
    if not resolved_date:
        return {"available": False, "reason": "尚未标记解决，暂时没有解决后的对比窗口"}

    # 只允许使用已发布交易数据，不能让数据库里预先上传的未来日期影响复盘窗口。
    latest_row = duckdb_client.query_one(
        "SELECT MAX(CAST(created_at AS DATE)) AS latest "
        "FROM ods_transactions_all WHERE CAST(created_at AS DATE) <= ?",
        [duckdb_client.get_reporting_date().isoformat()],
    )
    latest_date = _parse_review_date(latest_row.get("latest"))
    if not latest_date or latest_date < resolved_date:
        return {
            "available": True,
            "resolved_date": resolved_date.isoformat(),
            "data_ready": False,
            "reason": "解决日期晚于当前交易数据，等待后续数据上传",
        }

    merchant_id = str(source.get("merchant_id") or "")
    before_start = resolved_date - timedelta(days=30)
    after7_end = min(resolved_date + timedelta(days=7), latest_date + timedelta(days=1))
    after30_end = min(resolved_date + timedelta(days=30), latest_date + timedelta(days=1))
    rows = duckdb_client.query_one(
        """
        SELECT
            COALESCE(SUM(CASE WHEN CAST(created_at AS DATE) >= CAST(? AS DATE)
                              AND CAST(created_at AS DATE) < CAST(? AS DATE) THEN amount ELSE 0 END), 0) AS before_amount,
            COUNT(CASE WHEN CAST(created_at AS DATE) >= CAST(? AS DATE)
                       AND CAST(created_at AS DATE) < CAST(? AS DATE) THEN 1 END) AS before_count,
            COALESCE(SUM(CASE WHEN CAST(created_at AS DATE) >= CAST(? AS DATE)
                              AND CAST(created_at AS DATE) < CAST(? AS DATE) THEN amount ELSE 0 END), 0) AS after7_amount,
            COUNT(CASE WHEN CAST(created_at AS DATE) >= CAST(? AS DATE)
                       AND CAST(created_at AS DATE) < CAST(? AS DATE) THEN 1 END) AS after7_count,
            COALESCE(SUM(CASE WHEN CAST(created_at AS DATE) >= CAST(? AS DATE)
                              AND CAST(created_at AS DATE) < CAST(? AS DATE) THEN amount ELSE 0 END), 0) AS after30_amount,
            COUNT(CASE WHEN CAST(created_at AS DATE) >= CAST(? AS DATE)
                       AND CAST(created_at AS DATE) < CAST(? AS DATE) THEN 1 END) AS after30_count
        FROM ods_transactions_all
        WHERE CAST(merchant_id AS VARCHAR) = ?
          AND trans_status = '交易成功'
          AND amount > 0
        """,
        [
            before_start.isoformat(), resolved_date.isoformat(),
            before_start.isoformat(), resolved_date.isoformat(),
            resolved_date.isoformat(), after7_end.isoformat(),
            resolved_date.isoformat(), after7_end.isoformat(),
            resolved_date.isoformat(), after30_end.isoformat(),
            resolved_date.isoformat(), after30_end.isoformat(),
            merchant_id,
        ],
    )
    trend = duckdb_client.query_all(
        """
        SELECT CAST(created_at AS DATE) AS period, COALESCE(SUM(amount), 0) AS amount, COUNT(*) AS count
        FROM ods_transactions_all
        WHERE CAST(merchant_id AS VARCHAR) = ?
          AND trans_status = '交易成功'
          AND amount > 0
          AND CAST(created_at AS DATE) >= CAST(? AS DATE)
          AND CAST(created_at AS DATE) < CAST(? AS DATE)
        GROUP BY 1 ORDER BY 1
        """,
        [merchant_id, resolved_date.isoformat(), after30_end.isoformat()],
    )

    def daily(value, days):
        return round(float(value or 0) / days, 2) if days else 0

    def change(after_value, before_value, days):
        before_daily = daily(before_value, 30)
        after_daily = daily(after_value, days)
        return round((after_daily - before_daily) / before_daily * 100, 1) if before_daily else None

    after7_days = max((after7_end - resolved_date).days, 0)
    after30_days = max((after30_end - resolved_date).days, 0)
    after30_change = change(rows.get("after30_amount"), rows.get("before_amount"), after30_days)
    if after30_days == 0:
        effect_label = "等待数据"
    elif after30_change is None:
        effect_label = "暂无基准"
    elif after30_change > 10:
        effect_label = "交易恢复"
    elif after30_change >= -10:
        effect_label = "基本持平"
    else:
        effect_label = "仍需关注"

    return {
        "available": True,
        "data_ready": True,
        "resolved_date": resolved_date.isoformat(),
        "latest_data_date": latest_date.isoformat(),
        "before_days": 30,
        "after7_days": after7_days,
        "after30_days": after30_days,
        "before_amount": float(rows.get("before_amount") or 0),
        "before_count": int(rows.get("before_count") or 0),
        "after7_amount": float(rows.get("after7_amount") or 0),
        "after7_count": int(rows.get("after7_count") or 0),
        "after30_amount": float(rows.get("after30_amount") or 0),
        "after30_count": int(rows.get("after30_count") or 0),
        "before_daily_amount": daily(rows.get("before_amount"), 30),
        "after7_daily_amount": daily(rows.get("after7_amount"), after7_days),
        "after30_daily_amount": daily(rows.get("after30_amount"), after30_days),
        "after7_amount_change_pct": change(rows.get("after7_amount"), rows.get("before_amount"), after7_days),
        "after30_amount_change_pct": after30_change,
        "effect_label": effect_label,
        "trend": trend,
    }


@router.get("/{source_type}/{source_id}/effect")
def get_review_effect(source_type: str, source_id: int, current_user=Depends(get_current_user)):
    """返回解决后的成功交易变化；交易数据只在详情页按需查询。"""
    detail = get_review_detail(source_type, source_id, current_user)
    return _effect_for_review(source_type, detail["source"], detail["records"])


def _xml_cell(value):
    text = "" if value is None else str(value)
    return '<c t="inlineStr"><is><t xml:space="preserve">' + escape(text) + "</t></is></c>"


def _sheet_xml(rows):
    row_xml = []
    for index, row in enumerate(rows, start=1):
        row_xml.append('<row r="%s">%s</row>' % (index, "".join(_xml_cell(v) for v in row)))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>' + "".join(row_xml) + "</sheetData></worksheet>"
    )


def _xlsx_bytes(sheets):
    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
    ]
    workbook_rels = []
    workbook_sheets = []
    for index, (name, _) in enumerate(sheets, start=1):
        content_types.append(
            '<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % index
        )
        workbook_rels.append(
            '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (index, index)
        )
        workbook_sheets.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(name), index, index))
    content_types.append('</Types>')
    files = {
        "[Content_Types].xml": "".join(content_types),
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>%s</sheets></workbook>' % "".join(workbook_sheets),
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % "".join(workbook_rels),
    }
    for index, (_, rows) in enumerate(sheets, start=1):
        files["xl/worksheets/sheet%d.xml" % index] = _sheet_xml(rows)
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content.encode("utf-8"))
    return output.getvalue()


@router.get("/export")
def export_reviews(
    q: Optional[str] = Query(None, max_length=100),
    status: Optional[str] = Query(None),
    touch_type: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    agent_id: Optional[str] = Query(None),
    sales_person: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
):
    cases = _case_rows(
        current_user, q, status, touch_type, start_date, end_date,
        agent_id, sales_person,
    )
    summary = _case_summary(
        current_user, q, status, touch_type, start_date, end_date,
        agent_id, sales_person,
    )
    keys = {(row["source_type"], row["source_id"]) for row in cases}
    records = sqlite_client.query_all(
        """
        SELECT r.*, u.display_name AS user_name
        FROM followup_records r
        LEFT JOIN users u ON u.id = r.user_id
        ORDER BY r.created_at ASC, r.id ASC
        """
    )
    records = [r for r in records if (r["source_type"], r["source_id"]) in keys]
    case_sheet = [["商户", "来源", "状态", "跟进次数", "最近触达", "最近结果", "最近备注", "最后跟进时间"]]
    for row in cases:
        case_sheet.append([
            row["merchant_name"], row["source_title"], row["current_status"], row["touch_count"],
            row["latest_touch_type"] or "", row["latest_result"], row["latest_note"], row["last_followup_at"],
        ])
    record_sheet = [["商户", "来源", "跟进人", "触达方式", "结果", "备注", "时间"]]
    names = {(row["source_type"], row["source_id"]): row["merchant_name"] for row in cases}
    for row in records:
        record_sheet.append([
            names.get((row["source_type"], row["source_id"]), row["merchant_name"]),
            row["source_type"], row["user_name"] or "", row["touch_type"] or "",
            row["result"], row["note"], row["created_at"],
        ])
    summary_sheet = [["指标", "数值"], ["复盘商户数", summary["cases"]], ["已解决", summary["resolved"]], ["跟进中", summary["following"]], ["平均跟进次数", summary["avg_touch_count"]], ["平均解决天数", summary["avg_resolve_days"]]]
    data = _xlsx_bytes([("汇总", summary_sheet), ("复盘清单", case_sheet), ("跟进记录", record_sheet)])
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=yeahpay-review-report-%s.xlsx" % date.today().isoformat()},
    )
