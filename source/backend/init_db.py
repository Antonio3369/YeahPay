"""
初始化 SQLite 业务数据库
用法: python init_db.py
"""
import sqlite3
import os
import sys
import argparse
from typing import Optional
from pathlib import Path

# bcrypt 用于生成 admin 密码 hash
try:
    import bcrypt
except ImportError:
    print("错误: 缺少 bcrypt，请先运行: pip install bcrypt")
    sys.exit(1)

DB_DIR = Path(__file__).parent / "data"
DB_PATH = DB_DIR / "biz.db"
SCHEMA_PATH = Path(__file__).parent / "db" / "schema.sql"


def init_database(reset: bool = False, admin_password: Optional[str] = None):
    """初始化数据库"""
    DB_DIR.mkdir(parents=True, exist_ok=True)

    # 如果已存在，先备份
    if DB_PATH.exists() and not reset:
        print(f"数据库已存在，未做任何修改: {DB_PATH}")
        print("如需重建，请明确执行: python init_db.py --reset --admin-password '<新密码>'")
        return
    if DB_PATH.exists():
        backup = DB_PATH.with_suffix(f".db.bak.{int(__import__('time').time())}")
        DB_PATH.rename(backup)
        print(f"已备份旧数据库到: {backup}")

    # 读取 schema
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    if not admin_password or len(admin_password) < 8:
        raise SystemExit("重建数据库必须通过 --admin-password 提供至少 8 位管理员密码")
    password_hash = bcrypt.hashpw(admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    schema_sql = schema_sql.replace("__INITIAL_ADMIN_HASH__", password_hash)

    # 执行建表
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(schema_sql)
    conn.commit()

    # 验证
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print(f"\n✓ 数据库初始化完成: {DB_PATH}")
    print(f"  文件大小: {DB_PATH.stat().st_size / 1024:.1f} KB")
    print(f"  表数量: {len(tables)}")
    for t in tables:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
        print(f"    {t[0]}: {cnt} 行")

    # 验证 admin 账号
    admin = conn.execute(
        "SELECT id, username, display_name, role, must_change_pwd FROM users WHERE username='admin'"
    ).fetchone()
    print(f"\n  管理员账号: {admin[1]} / {admin[3]} (首次登录需改密: {bool(admin[4])})")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化或重建 SQLite 业务数据库")
    parser.add_argument("--reset", action="store_true", help="重建已存在的数据库（会先备份旧文件）")
    parser.add_argument("--admin-password", help="初始管理员密码，至少 8 位")
    args = parser.parse_args()
    init_database(reset=args.reset, admin_password=args.admin_password)
