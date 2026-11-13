"""针对真实 MySQL/MariaDB 的 t_user CRUD 集成测试。

运行前请确保 .env 中的数据库可连接，且账号具备建表权限：

    pytest -v
"""

import pytest

from mysql_t_user.config import DbConfig
from mysql_t_user.dao import UserDao
from mysql_t_user.db import Database
from mysql_t_user.models import User


@pytest.fixture()
def dao():
    db = Database(DbConfig.from_env(), pool_size=2)
    user_dao = UserDao(db)
    user_dao.create_table()
    user_dao.delete_all()
    yield user_dao
    user_dao.delete_all()
    db.close()


def test_insert_and_get(dao):
    uid = dao.insert(
        User(username="u1", password="p", nickname="N1", email="u1@x.com", age=20, gender=1)
    )
    assert uid > 0
    user = dao.get_by_id(uid)
    assert user is not None
    assert user.username == "u1"
    assert user.age == 20
    assert user.created_at is not None


def test_insert_many_and_count(dao):
    rows = dao.insert_many(
        [User(username=f"m{i}", password="p", email=f"m{i}@x.com") for i in range(5)]
    )
    assert rows == 5
    assert dao.count() == 5


def test_duplicate_username_rejected(dao):
    import pymysql

    dao.insert(User(username="dup", password="p", email="dup@x.com"))
    with pytest.raises(pymysql.err.IntegrityError):
        dao.insert(User(username="dup", password="p", email="dup2@x.com"))


def test_update_partial(dao):
    uid = dao.insert(
        User(username="up", password="p", nickname="old", email="up@x.com", age=18)
    )
    affected = dao.update(uid, nickname="new", age=19)
    assert affected == 1
    user = dao.get_by_id(uid)
    assert user.nickname == "new"
    assert user.age == 19
    assert user.email == "up@x.com"  # 未更新字段保持不变


def test_update_no_writable_field(dao):
    uid = dao.insert(User(username="nw", password="p", email="nw@x.com"))
    with pytest.raises(ValueError):
        dao.update(uid, not_a_column=1)


def test_search_filters(dao):
    dao.insert_many(
        [
            User(username="a1", password="p", email="a1@x.com", age=20, gender=1, status=1),
            User(username="a2", password="p", email="a2@x.com", age=30, gender=2, status=0),
            User(username="a3", password="p", email="a3@x.com", age=40, gender=1, status=1),
        ]
    )
    assert len(dao.search(status=1)) == 2
    assert len(dao.search(min_age=25, max_age=35)) == 1
    assert len(dao.search(keyword="a2")) == 1
    ages = [u.age for u in dao.search(order_by="age", desc=True)]
    assert ages == [40, 30, 20]


def test_delete_variants(dao):
    ids = [
        dao.insert(User(username=f"d{i}", password="p", email=f"d{i}@x.com"))
        for i in range(3)
    ]
    assert dao.delete_by_id(ids[0]) == 1
    assert dao.delete_by_username("d1") == 1
    assert dao.delete_by_ids(ids[2:]) == 1
    assert dao.count() == 0


def test_upsert_keeps_omitted_fields(dao):
    dao.upsert(User(username="ups", password="p", nickname="keep", email="ups@x.com", age=20))
    dao.upsert(User(username="ups", password="p2", nickname="changed", age=21))
    user = dao.get_by_username("ups")
    assert user.password == "p2"
    assert user.nickname == "changed"
    assert user.age == 21
    assert user.email == "ups@x.com"  # 本次未提供的字段应保留


def test_transaction_rollback(dao):
    uid = dao.insert(User(username="tx", password="p", email="tx@x.com", status=1))
    with pytest.raises(ValueError):
        dao.swap_status_in_tx(uid, 999999)
    assert dao.get_by_id(uid).status == 1  # 回滚后状态未变


def test_group_by_gender(dao):
    dao.insert_many(
        [
            User(username="g1", password="p", email="g1@x.com", gender=1),
            User(username="g2", password="p", email="g2@x.com", gender=1),
            User(username="g3", password="p", email="g3@x.com", gender=2),
        ]
    )
    result = {row["gender"]: row["total"] for row in dao.group_by_gender()}
    assert result == {1: 2, 2: 1}


def test_sql_injection_is_parameterized(dao):
    dao.insert(User(username="safe", password="p", email="safe@x.com"))
    assert dao.search(keyword="' OR '1'='1") == []
    assert dao.get_by_username("' OR 1=1 -- ") is None
