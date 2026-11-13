# Python 操作 MySQL —— t_user 表增删改查

使用 **PyMySQL**（纯 Python 驱动，无需编译）访问 MySQL / MariaDB 中的 `t_user` 表，
覆盖连接池、参数化查询、单条/批量增删改、条件查询、分页、聚合、事务等基本操作。
不依赖 ORM，所有 SQL 都直接可见，便于理解底层执行过程。

## 目录结构

```
.
├── .env.example                 # 配置模板（复制为 .env 使用）
├── requirements.txt
├── schema.sql                   # 建表语句（在 mysql_t_user/ 下）
├── mysql_t_user/
│   ├── config.py                # 数据库配置（环境变量 / .env）
│   ├── db.py                    # 连接池 + 游标/事务上下文
│   ├── models.py                # User 数据模型
│   ├── dao.py                   # UserDao：所有 CRUD 方法
│   ├── schema.sql               # 建表语句
│   └── main.py                  # 命令行入口 + 完整演示
└── tests/test_user_dao.py       # 针对真实数据库的集成测试
```

## 环境准备

```bash
pip install -r requirements.txt

cp .env.example .env    # 按实际环境修改数据库地址、账号、密码
```

`.env` 内容：

```ini
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=demo
DB_PASSWORD=demo123
DB_NAME=demo_db
DB_CHARSET=utf8mb4
```

建库与授权（用 root 执行一次）：

```sql
CREATE DATABASE IF NOT EXISTS demo_db
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'demo'@'%' IDENTIFIED BY 'demo123';
GRANT ALL PRIVILEGES ON demo_db.* TO 'demo'@'%';
FLUSH PRIVILEGES;
```

## 表结构（t_user）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT UNSIGNED | 主键，自增 |
| username | VARCHAR(50) | 用户名，唯一 |
| password | VARCHAR(255) | 密码（生产环境请存哈希） |
| nickname | VARCHAR(50) | 昵称 |
| email | VARCHAR(100) | 邮箱，唯一 |
| phone | VARCHAR(20) | 手机号，普通索引 |
| age | TINYINT UNSIGNED | 年龄 |
| gender | TINYINT | 0 未知 / 1 男 / 2 女 |
| status | TINYINT | 1 正常 / 0 禁用 |
| created_at | DATETIME | 创建时间，默认当前时间 |
| updated_at | DATETIME | 更新时间，自动刷新 |

## 快速开始

```bash
# 1. 建表（幂等，可重复执行）
python -m mysql_t_user.main init

# 2. 完整 CRUD 演示：增 → 查 → 改 → 删 → 聚合 → 事务回滚
python -m mysql_t_user.main demo
```

`demo` 输出示例（节选）：

```
== 2. 新增（单条） ==
新增 alice，id = 1

== 6. 查询：条件 + 模糊 + 排序 ==
  id  username     nickname   email                  age gender status
   4  dave         戴夫         dave@example.com        40      1      0
   2  bob          鲍勃         bob@example.com         30      1      1

== 11. 事务：原子更新（失败自动回滚） ==
事务已回滚：用户不存在：[999999]
回滚后 alice 状态仍为：0
```

## 命令行用法

```bash
# 新增
python -m mysql_t_user.main add alice pwd123 --nickname 爱丽丝 --email alice@example.com --age 25 --gender 2

# 按 id 查询
python -m mysql_t_user.main get 1

# 查询列表（可选 --status 0/1）
python -m mysql_t_user.main list --status 1

# 条件查询：模糊匹配 + 年龄区间 + 排序 + 分页
python -m mysql_t_user.main search --keyword ali --min-age 18 --max-age 40 --order-by age --desc

# 局部更新（只改传入的字段）
python -m mysql_t_user.main update 1 --nickname 小明 --status 0

# 删除
python -m mysql_t_user.main delete 1
```

## 在代码中调用

```python
from mysql_t_user import Database, DbConfig, User, UserDao

db = Database(DbConfig.from_env())
dao = UserDao(db)
dao.create_table()

# 增
uid = dao.insert(User(username="alice", password="pwd123",
                      nickname="爱丽丝", email="alice@example.com", age=25, gender=2))
dao.insert_many([User(username="bob", password="p", email="bob@example.com")])

# 查
user = dao.get_by_id(uid)                       # 按主键
user = dao.get_by_username("alice")             # 按唯一键
users = dao.list_all(limit=20, offset=0)        # 分页
users = dao.search(keyword="ali", status=1,     # 条件 + 模糊 + 排序
                   min_age=18, max_age=40, order_by="age", desc=True)
total = dao.count()                             # 计数

# 改
dao.update(uid, nickname="小明", age=26)         # 局部更新
dao.update_status(uid, status=0)                # 改状态
dao.update_batch_status([1, 2, 3], status=0)    # 批量改状态
dao.upsert(User(username="bob", password="new"))# 存在则更新

# 删
dao.delete_by_id(uid)
dao.delete_by_username("bob")
dao.delete_by_ids([1, 2, 3])

# 聚合
dao.group_by_gender()                           # [{'gender': 1, 'total': 2}, ...]

# 事务：全部成功才提交，任一异常整体回滚
dao.swap_status_in_tx(from_id=1, to_id=2)

db.close()
```

## 方法一览

| 分类 | 方法 |
| --- | --- |
| 增 | `insert`、`insert_many`、`upsert` |
| 删 | `delete_by_id`、`delete_by_username`、`delete_by_ids`、`delete_all` |
| 改 | `update`、`update_status`、`update_batch_status` |
| 查 | `get_by_id`、`get_by_username`、`list_all`、`list_by_status`、`search`、`count`、`exists`、`group_by_gender` |
| 事务 | `swap_status_in_tx`（`Database.transaction()` 上下文） |

## 关键实现说明

- **参数化查询**：所有值都通过 `%s` 占位符传入，杜绝 SQL 注入；
  仅列名、表名等标识符做白名单校验后拼接。
- **连接池**：`Database` 内部用 `LifoQueue` 维护连接，取用前 `ping` 探活，
  失效连接自动重建，避免 `wait_timeout` 导致的 "MySQL server has gone away"。
- **事务**：`Database.transaction()` 上下文管理器，正常结束提交、异常回滚。
- **rowcount 的坑**：`UPDATE` 影响行数为 0 只代表“值没有变化”，
  不代表记录不存在；判断存在性需显式 `SELECT`。
- **upsert 保留字段**：`ON DUPLICATE KEY UPDATE` 中用 `COALESCE(VALUES(col), col)`，
  未传入的字段不会被写成 NULL。

## 运行测试

测试直接连真实数据库（不使用 mock），覆盖增删改查、唯一约束、事务回滚、
聚合以及 SQL 注入防护：

```bash
pytest -v
```

## 注意事项

- 生产环境请勿明文存储密码，应使用 `bcrypt` / `argon2` 等算法哈希。
- 大数据量查询请使用分页（`limit` / `offset`），避免一次性加载全表。
- 如使用 MySQL 8 的 `caching_sha2_password` 认证，请确保 `cryptography` 已安装
  （`pip install cryptography`）。
