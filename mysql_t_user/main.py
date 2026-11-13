"""命令行入口：演示 t_user 表的增删改查。

示例::

    python -m mysql_t_user.main init          # 建表
    python -m mysql_t_user.main demo          # 完整 CRUD 演示
    python -m mysql_t_user.main add alice pwd123 --email a@x.com --age 25
    python -m mysql_t_user.main list --status 1
    python -m mysql_t_user.main search --keyword ali --min-age 18
    python -m mysql_t_user.main get 1
    python -m mysql_t_user.main update 1 --nickname 小明 --status 0
    python -m mysql_t_user.main delete 1
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import DbConfig
from .dao import UserDao
from .db import Database
from .models import User


def build_dao() -> tuple[Database, UserDao]:
    db = Database(DbConfig.from_env())
    return db, UserDao(db)


def _print_users(users: list[User]) -> None:
    if not users:
        print("(无数据)")
        return
    print(f"{'id':>4}  {'username':<12} {'nickname':<10} {'email':<22} {'age':>3} {'gender':>6} {'status':>6}")
    print("-" * 78)
    for u in users:
        print(
            f"{u.id:>4}  {u.username:<12} {str(u.nickname or ''):<10} "
            f"{str(u.email or ''):<22} {str(u.age or ''):>3} {u.gender:>6} {u.status:>6}"
        )


# ------------------------------------------------------------------ 子命令
def cmd_init(args: argparse.Namespace) -> None:
    db, dao = build_dao()
    try:
        dao.create_table()
        print("t_user 表已就绪。")
    finally:
        db.close()


def cmd_add(args: argparse.Namespace) -> None:
    db, dao = build_dao()
    try:
        user = User(
            username=args.username,
            password=args.password,
            nickname=args.nickname,
            email=args.email,
            phone=args.phone,
            age=args.age,
            gender=args.gender,
            status=args.status,
        )
        new_id = dao.insert(user)
        print(f"新增成功，id = {new_id}")
    finally:
        db.close()


def cmd_get(args: argparse.Namespace) -> None:
    db, dao = build_dao()
    try:
        user = dao.get_by_id(args.id)
        print(user if user else f"未找到 id = {args.id} 的用户")
    finally:
        db.close()


def cmd_list(args: argparse.Namespace) -> None:
    db, dao = build_dao()
    try:
        _print_users(dao.list_by_status(args.status) if args.status is not None else dao.list_all(args.limit))
        print(f"合计：{dao.count()} 条")
    finally:
        db.close()


def cmd_search(args: argparse.Namespace) -> None:
    db, dao = build_dao()
    try:
        users = dao.search(
            keyword=args.keyword,
            status=args.status,
            min_age=args.min_age,
            max_age=args.max_age,
            order_by=args.order_by,
            desc=args.desc,
            limit=args.limit,
        )
        _print_users(users)
    finally:
        db.close()


def cmd_update(args: argparse.Namespace) -> None:
    db, dao = build_dao()
    try:
        changes = {
            k: getattr(args, k)
            for k in ("nickname", "email", "phone", "age", "gender", "status", "password")
            if getattr(args, k) is not None
        }
        if not changes:
            print("没有提供任何要更新的字段。")
            return
        rows = dao.update(args.id, **changes)
        print(f"更新成功，影响 {rows} 行")
    finally:
        db.close()


def cmd_delete(args: argparse.Namespace) -> None:
    db, dao = build_dao()
    try:
        rows = dao.delete_by_id(args.id)
        print(f"删除成功，影响 {rows} 行")
    finally:
        db.close()


def cmd_demo(args: argparse.Namespace) -> None:
    """一次性演示：增 -> 查 -> 改 -> 删 -> 聚合 -> 事务。"""
    db, dao = build_dao()
    try:
        dao.create_table()
        print("== 1. 清空旧数据 ==")
        dao.delete_all()
        print(f"当前记录数：{dao.count()}")

        print("\n== 2. 新增（单条） ==")
        uid = dao.insert(
            User(username="alice", password="pwd123", nickname="爱丽丝",
                 email="alice@example.com", phone="13800000001", age=25, gender=2)
        )
        print(f"新增 alice，id = {uid}")

        print("\n== 3. 新增（批量） ==")
        dao.insert_many([
            User(username="bob", password="pwd123", nickname="鲍勃",
                 email="bob@example.com", phone="13800000002", age=30, gender=1),
            User(username="carol", password="pwd123", nickname="卡罗尔",
                 email="carol@example.com", phone="13800000003", age=22, gender=2),
            User(username="dave", password="pwd123", nickname="戴夫",
                 email="dave@example.com", phone="13800000004", age=40, gender=1, status=0),
        ])
        print(f"批量新增 3 条，当前记录数：{dao.count()}")

        print("\n== 4. 查询：全部 ==")
        _print_users(dao.list_all())

        print("\n== 5. 查询：按 id ==")
        print(dao.get_by_id(uid))

        print("\n== 6. 查询：条件 + 模糊 + 排序 ==")
        _print_users(dao.search(keyword="o", min_age=20, order_by="age", desc=True))

        print("\n== 7. 修改：局部更新 ==")
        rows = dao.update(uid, nickname="Alice-新", age=26)
        print(f"更新 alice，影响 {rows} 行 -> {dao.get_by_id(uid)}")

        print("\n== 8. 修改：批量改状态 ==")
        rows = dao.update_batch_status([uid, uid + 1], status=0)
        print(f"批量禁用，影响 {rows} 行")

        print("\n== 9. upsert：按唯一键插入或更新 ==")
        dao.upsert(User(username="bob", password="newpwd", nickname="Bob-更新", age=31, gender=1))
        print(f"upsert bob -> {dao.get_by_username('bob')}")

        print("\n== 10. 聚合：按性别统计 ==")
        for row in dao.group_by_gender():
            print(f"  gender={row['gender']} 共 {row['total']} 人")

        print("\n== 11. 事务：原子更新（失败自动回滚） ==")
        try:
            dao.swap_status_in_tx(uid, 999999)  # 第二个 id 不存在 -> 回滚
        except ValueError as exc:
            print(f"事务已回滚：{exc}")
        print(f"回滚后 alice 状态仍为：{dao.get_by_id(uid).status}")

        print("\n== 12. 删除 ==")
        rows = dao.delete_by_username("carol")
        print(f"删除 carol，影响 {rows} 行")
        print(f"当前记录数：{dao.count()}")

        print("\n== 完成 ==")
    finally:
        db.close()


# ------------------------------------------------------------------ 参数解析
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="t_user 表增删改查示例")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="建表").set_defaults(func=cmd_init)
    sub.add_parser("demo", help="完整 CRUD 演示").set_defaults(func=cmd_demo)

    p = sub.add_parser("add", help="新增用户")
    p.add_argument("username")
    p.add_argument("password")
    p.add_argument("--nickname")
    p.add_argument("--email")
    p.add_argument("--phone")
    p.add_argument("--age", type=int)
    p.add_argument("--gender", type=int, default=0, choices=[0, 1, 2])
    p.add_argument("--status", type=int, default=1, choices=[0, 1])
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("get", help="按 id 查询")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("list", help="查询列表")
    p.add_argument("--status", type=int, choices=[0, 1])
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("search", help="条件查询")
    p.add_argument("--keyword")
    p.add_argument("--status", type=int, choices=[0, 1])
    p.add_argument("--min-age", type=int, dest="min_age")
    p.add_argument("--max-age", type=int, dest="max_age")
    p.add_argument("--order-by", default="id", choices=["id", "age", "created_at", "username"])
    p.add_argument("--desc", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("update", help="按 id 局部更新")
    p.add_argument("id", type=int)
    p.add_argument("--nickname")
    p.add_argument("--email")
    p.add_argument("--phone")
    p.add_argument("--age", type=int)
    p.add_argument("--gender", type=int, choices=[0, 1, 2])
    p.add_argument("--status", type=int, choices=[0, 1])
    p.add_argument("--password")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("delete", help="按 id 删除")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:  # 统一错误出口，避免堆栈噪音
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
