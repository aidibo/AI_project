# 华为云 OBS Python 操作示例

基于华为云官方 SDK [`esdk-obs-python`](https://github.com/huaweicloud/huaweicloud-sdk-python-obs)
封装的对象存储（OBS）操作模块，覆盖日常使用的核心场景：

| 能力 | 方法 | 说明 |
| --- | --- | --- |
| 查看桶列表 | `list_buckets()` | 列出当前凭证可见的所有桶 |
| 创建/删除桶 | `create_bucket()` / `delete_bucket()` | 幂等创建、删除桶 |
| 判断桶/对象是否存在 | `bucket_exists()` / `object_exists()` | HEAD 请求探测 |
| 上传（内存） | `upload_bytes(key, data)` | 直接上传 bytes / str |
| 上传（文件） | `upload_file(path, key=...)` | 大文件自动分片、断点续传 |
| 下载 | `download_file(key, path)` / `get_object_bytes(key)` | 落盘或读入内存 |
| 查看对象元数据 | `stat_object(key)` | 大小、ETag、修改时间、Content-Type |
| 文件列表 | `list_objects()` / `list_all_objects()` / `list_prefixes()` | 支持前缀、分页、模拟目录 |
| 生成临时访问地址 | `generate_signed_url()` / `generate_upload_url()` | 预签名 URL，GET/PUT/DELETE |
| 复制对象 | `copy_object(src, dst)` | 同桶或跨桶复制 |
| 删除对象 | `delete_object()` / `delete_objects()` | 单个或批量删除 |

## 安装

```bash
pip install -r requirements.txt      # 含 esdk-obs-python
```

## 配置

复制 `.env.example` 为 `.env`，填写华为云凭证：

```ini
OBS_ACCESS_KEY_ID=your_ak
OBS_SECRET_ACCESS_KEY=your_sk
OBS_SERVER=obs.cn-north-4.myhuaweicloud.com
OBS_REGION=cn-north-4
OBS_BUCKET=your_bucket
```

AK/SK 在华为云控制台 **我的凭证 → 访问密钥** 获取。若使用 IAM 委托或 STS 临时凭证，
额外填写 `OBS_SECURITY_TOKEN`。终端节点按桶所在区域选择，例如
`obs.cn-north-4.myhuaweicloud.com`、`obs.cn-east-3.myhuaweicloud.com`。

## 作为库使用

```python
from huawei_obs import ObsConfig, ObsService

config = ObsConfig.from_env()          # 读取 .env / 环境变量
with ObsService(config) as obs:
    # 查看
    print(obs.list_buckets())
    print(obs.stat_object("docs/report.pdf"))

    # 上传
    obs.upload_bytes("docs/hello.txt", "hello obs")
    obs.upload_file("./report.pdf", key="docs/report.pdf")

    # 下载
    obs.download_file("docs/report.pdf", "./report.pdf")

    # 文件列表（自动翻页）
    for info in obs.list_all_objects(prefix="docs/"):
        print(info.key, info.size)

    # 生成临时访问地址（1 小时后失效）
    url = obs.generate_signed_url("docs/report.pdf", method="GET", expires=3600)
    print(url)

    # 预签名上传地址：客户端可直接 PUT，无需暴露 AK/SK
    put_url = obs.generate_upload_url("uploads/new.bin", expires=600)
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
```

可通过 `--bucket` / `--server` 临时覆盖配置。

## 临时访问地址说明

`generate_signed_url()` 使用 SDK 的 V4 签名生成预签名 URL，把有效期内的访问权限
授予任何持有该 URL 的人，无需其拥有 AK/SK：

- `method="GET"` —— 下载链接，可直接给前端或第三方使用；
- `method="PUT"` —— 上传链接，客户端 PUT 文件即可上传；
- `method="DELETE"` —— 删除链接。

`expires` 为有效期秒数。注意：URL 一旦生成，在有效期内任何人拿到都能使用，
请按最小必要原则设置有效期。

## 运行测试

测试使用真实的 SDK 代码路径，可对接任意 S3 兼容对象存储。未设置
`OBS_TEST_SERVER` 时 OBS 测试会自动跳过。

```bash
# 华为云 OBS 或自建 S3 兼容服务
export OBS_TEST_SERVER=http://127.0.0.1:8333
export OBS_TEST_ACCESS_KEY_ID=any
export OBS_TEST_SECRET_ACCESS_KEY=anysecret
python -m pytest tests/test_obs_service.py -v
```

覆盖用例：桶创建与列举、字节/文件上传下载往返、对象元数据、存在性判断、
带前缀与分页的列表、模拟目录前缀、预签名 GET/PUT 的实际可用性、对象复制、
单个与批量删除、缺失对象报错。

## 说明

- 模块对 SDK 返回做了归一化：非 2xx 响应统一抛出 `ObsError`，无需到处判断 `status`。
- 模块默认按华为云 OBS 的 `obs` 签名工作；对接自建 S3 兼容服务（如 Ceph、SeaweedFS）时
  设置 `OBS_SIGNATURE=v4`、`OBS_PATH_STYLE=true`。
- `list_all_objects()` 内部自动翻页，适合中小规模列举；超大桶建议自行按 `marker` 分批。
