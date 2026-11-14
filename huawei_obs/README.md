# Python 操作华为云 OBS —— 查看 / 上传 / 下载 / 列表 / 临时访问地址

基于华为云官方 SDK **esdk-obs-python** 封装的对象存储（OBS）操作示例，
覆盖日常开发中最常用的场景，不引入额外抽象层，所有调用都直接落到真实 SDK。

## 目录结构

```
huawei_obs/
├── config.py     # 配置层：从 .env / 环境变量读取 AK/SK、终端节点、桶等
├── client.py     # 操作层：ObsService，封装全部 OBS 操作
├── main.py       # 命令行入口 + 一键演示
└── README.md
tests/test_obs_service.py   # 对接真实对象存储的集成测试
```

## 能力一览

| 能力 | 方法 |
| --- | --- |
| 查看桶列表 | `list_buckets()` |
| 创建 / 删除桶 | `create_bucket()` / `delete_bucket()` |
| 桶 / 对象是否存在 | `bucket_exists()` / `object_exists()` |
| 桶区域 | `get_bucket_location()` |
| 上传（内存） | `upload_bytes(key, data)` |
| 上传（文件） | `upload_file(path, key=...)` |
| 下载 | `download_file(key, path)` / `get_object_bytes()` / `get_object_text()` |
| 流式下载 | `get_object_stream(key)` |
| 查看对象元数据 | `stat_object(key)` |
| 修改对象元数据 | `set_object_metadata(key, metadata)` |
| 查看对象 ACL | `get_object_acl(key)` |
| 文件列表 | `list_objects()` / `iter_objects()` / `list_all_objects()` / `list_prefixes()` |
| 复制对象 | `copy_object(src, dst)` |
| 删除对象 | `delete_object()` / `delete_objects()` / `delete_prefix()` |
| 临时访问地址 | `generate_signed_url()` / `generate_download_url()` / `generate_upload_url()` |

## 环境准备

```bash
pip install -r requirements.txt      # 含 esdk-obs-python

cp .env.example .env                 # 按实际环境修改 OBS_* 配置
```

AK/SK 在华为云控制台 **我的凭证 → 访问密钥** 获取。终端节点按桶所在区域选择，
例如 `obs.cn-north-4.myhuaweicloud.com`。若使用 IAM 委托 / STS 临时凭证，
额外填写 `OBS_SECURITY_TOKEN`。

## 在代码中调用

```python
from huawei_obs import ObsConfig, ObsService

with ObsService(ObsConfig.from_env()) as obs:
    # 查看
    print(obs.list_buckets())
    print(obs.stat_object("docs/report.pdf"))

    # 上传
    obs.upload_bytes("docs/hello.txt", "hello obs")
    obs.upload_file("./report.pdf", key="docs/report.pdf")

    # 下载
    obs.download_file("docs/report.pdf", "./report.pdf")
    print(obs.get_object_text("docs/hello.txt"))

    # 文件列表（自动翻页）
    for info in obs.iter_objects(prefix="docs/"):
        print(info.key, info.size)

    # 临时访问地址（1 小时后失效）
    print(obs.generate_download_url("docs/report.pdf", expires=3600))

    # 预签名上传地址：客户端可直接 PUT，无需暴露 AK/SK
    print(obs.generate_upload_url("uploads/new.bin", expires=600))

    # 复制 / 删除
    obs.copy_object("docs/report.pdf", "backup/report.pdf")
    obs.delete_object("docs/report.pdf")
```

## 命令行用法

```bash
python -m huawei_obs.main list-buckets                 # 查看桶列表
python -m huawei_obs.main mb                           # 创建默认桶
python -m huawei_obs.main ls --prefix docs/            # 查看文件列表
python -m huawei_obs.main upload ./report.pdf --key docs/report.pdf
python -m huawei_obs.main download docs/report.pdf --dest ./report.pdf
python -m huawei_obs.main stat docs/report.pdf         # 查看对象信息
python -m huawei_obs.main url docs/report.pdf --expires 600   # 临时访问地址
python -m huawei_obs.main cp docs/report.pdf docs/report.bak
python -m huawei_obs.main rm docs/report.pdf
python -m huawei_obs.main rm a.txt b.txt c.txt         # 批量删除
python -m huawei_obs.main demo                         # 一键演示全部操作
```

`--bucket` / `--server` 可临时覆盖配置。

## 关于临时访问地址

`generate_signed_url()` 用 SDK 的 V4 签名生成预签名 URL，把有效期内的访问权限授予
任何持有该 URL 的人，无需其拥有 AK/SK：

- `method="GET"` —— 下载链接，可交给前端或第三方使用；
- `method="PUT"` —— 上传链接，客户端直接 PUT 文件即可上传；
- `method="DELETE"` —— 删除链接。

`expires` 为有效期秒数。URL 一旦生成，有效期内任何人拿到都能使用，
请按最小必要原则设置有效期。

## 运行测试

测试直接对接真实对象存储（不使用 mock），可指向任意 S3 兼容端点。
未设置 `OBS_TEST_SERVER` 时 OBS 测试会自动跳过。

```bash
export OBS_TEST_SERVER=http://127.0.0.1:8333
export OBS_TEST_ACCESS_KEY_ID=any
export OBS_TEST_SECRET_ACCESS_KEY=anysecret
python -m pytest tests/test_obs_service.py -v
```

覆盖用例：桶创建 / 列举 / 区域、字节与文件上传下载往返、流式读取、对象元数据与
ACL、存在性判断、带前缀与分页的列表、模拟目录前缀、预签名 GET/PUT 的实际可用性、
对象复制、单个 / 批量 / 按前缀删除、缺失对象报错。

## 关键实现说明

- **统一错误处理**：非 2xx 响应统一抛出 `ObsError`，保留 `status`、`errorCode`、
  `requestId` 等字段，调用方无需到处判断 `result.status`。
- **响应字段命名不一致**：SDK 中列表接口的 `is_truncated` / `next_marker` 为
  snake_case，而对象条目字段为 camelCase，且公共前缀字段拼写为 `commonPrefixs`
  （SDK 自身拼写错误）。模块内部已做兼容，`iter_objects()` 可正确自动翻页。
- **签名与寻址**：华为云 OBS 用 `signature=obs`；对接自建 S3 兼容服务
  （如 Ceph、SeaweedFS）时设置 `OBS_SIGNATURE=v4`、`OBS_PATH_STYLE=true`。
- **大文件**：`upload_file()` / `download_file()` 走 SDK 的分片与断点续传能力，
  并支持 `progress_callback` 回调。
