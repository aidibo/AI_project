"""命令行入口。

常用示例::

    python -m huawei_obs.main list-buckets            # 查看桶列表
    python -m huawei_obs.main ls --prefix docs/       # 查看文件列表
    python -m huawei_obs.main upload ./report.pdf --key docs/report.pdf
    python -m huawei_obs.main download docs/report.pdf --dest ./report.pdf
    python -m huawei_obs.main stat docs/report.pdf    # 查看对象信息
    python -m huawei_obs.main url docs/report.pdf --expires 600   # 临时访问地址
    python -m huawei_obs.main demo                    # 一键演示全部操作
"""

from __future__ import annotations

import argparse
import os
import sys

from .client import ObsError, ObsService
from .config import ObsConfig


def _human_size(size: int) -> str:
    """把字节数格式化成便于阅读的字符串，如 ``1.5MB``。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        # 小于 1024 或已到最大单位时停止换算。
        if value < 1024 or unit == "TB":
            return f"{int(value)}B" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器（每个子命令对应一类操作）。"""
    parser = argparse.ArgumentParser(prog="obs", description="华为云 OBS 操作工具")
    # 全局选项：可临时覆盖配置里的桶与终端节点。
    parser.add_argument("--bucket", help="覆盖默认桶")
    parser.add_argument("--server", help="覆盖终端节点")
    sub = parser.add_subparsers(dest="command", required=True)

    # 无参数子命令。
    sub.add_parser("list-buckets", help="查看桶列表")
    sub.add_parser("mb", help="创建默认桶")
    sub.add_parser("demo", help="在默认桶上运行一次完整演示")

    # 带参数子命令。
    p_ls = sub.add_parser("ls", help="查看文件列表")
    p_ls.add_argument("--prefix", default=None)
    p_ls.add_argument("--delimiter", default=None)
    p_ls.add_argument("--max-keys", type=int, default=None)

    p_up = sub.add_parser("upload", help="上传本地文件")
    p_up.add_argument("path")
    p_up.add_argument("--key", default=None)

    p_down = sub.add_parser("download", help="下载对象")
    p_down.add_argument("key")
    p_down.add_argument("--dest", default=None)

    p_stat = sub.add_parser("stat", help="查看对象元数据")
    p_stat.add_argument("key")

    p_url = sub.add_parser("url", help="生成临时访问地址")
    p_url.add_argument("key")
    p_url.add_argument("--method", default="GET")
    p_url.add_argument("--expires", type=int, default=3600)

    p_rm = sub.add_parser("rm", help="删除对象（可多个）")
    p_rm.add_argument("keys", nargs="+")

    p_cp = sub.add_parser("cp", help="复制对象")
    p_cp.add_argument("source")
    p_cp.add_argument("dest")

    return parser


def _run_demo(service: ObsService) -> None:
    """在默认桶上依次演示：上传 → 查看 → 列表 → 下载 → 临时地址 → 复制 → 删除。"""
    key = "demo/hello.txt"
    print(f"[1] 默认桶: {service.bucket}")

    # 上传一段文本，指定内容类型便于浏览器直接查看。
    print("[2] 上传文本")
    service.upload_bytes(key, "hello obs 你好", content_type="text/plain")
    print(f"    -> {key}")

    # 查看对象的元数据（大小 / 类型 / ETag）。
    print("[3] 查看对象元数据")
    info = service.stat_object(key)
    print(f"    size={info.size} type={info.content_type} etag={info.etag}")

    # 列出 demo/ 前缀下的对象（内部自动翻页）。
    print("[4] 文件列表（前缀 demo/）")
    for item in service.list_all_objects(prefix="demo/"):
        print(f"    {_human_size(item.size):>8}  {item.key}")

    # 直接读回内容（小文件适合一次性读入内存）。
    print("[5] 下载内容")
    print(f"    -> {service.get_object_text(key)!r}")

    # 生成 60 秒有效的下载链接。
    print("[6] 生成临时访问地址（GET，60 秒）")
    print(f"    {service.generate_download_url(key, expires=60)}")

    # 复制一份，验证复制能力。
    print("[7] 复制对象")
    service.copy_object(key, "demo/hello-copy.txt")
    print("    demo/hello.txt -> demo/hello-copy.txt")

    # 批量删除本次演示产生的对象，保持桶干净。
    print("[8] 删除对象")
    service.delete_objects([key, "demo/hello-copy.txt"])
    print("    done")


def main(argv: list[str] | None = None) -> int:
    """命令行主入口，返回进程退出码（0 成功、1 运行错误、2 配置错误）。"""
    args = _build_parser().parse_args(argv)

    # 读取配置，并用命令行参数覆盖桶 / 终端节点。
    config = ObsConfig.from_env()
    if args.server:
        config.server = args.server
    if args.bucket:
        config.bucket = args.bucket

    # list-buckets 不需要桶，其余命令都要求配置了默认桶。
    try:
        config.validate(require_bucket=args.command != "list-buckets")
    except ValueError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    # with 语句确保退出时关闭客户端连接。
    try:
        with ObsService(config) as service:
            if args.command == "list-buckets":
                for name in service.list_buckets():
                    print(name)

            elif args.command == "mb":
                print("已创建" if service.create_bucket() else "已存在")

            elif args.command == "demo":
                _run_demo(service)

            elif args.command == "ls":
                page = service.list_objects(prefix=args.prefix, delimiter=args.delimiter,
                                            max_keys=args.max_keys)
                for info in page.objects:
                    print(f"{_human_size(info.size):>10}  {info.last_modified or '':<24}  {info.key}")
                # 公共前缀（“目录”）单独打印一行。
                for prefix in page.prefixes:
                    print(f"{'DIR':>10}  {'':<24}  {prefix}")

            elif args.command == "upload":
                info = service.upload_file(args.path, key=args.key)
                print(f"已上传 {info.key}（{_human_size(info.size)}）")

            elif args.command == "download":
                # 未指定目标路径时，用对象名的最后一段作为本地文件名。
                dest = args.dest or os.path.basename(args.key)
                print(f"已下载到 {service.download_file(args.key, dest)}")

            elif args.command == "stat":
                info = service.stat_object(args.key)
                print(f"key:          {info.key}")
                print(f"size:         {info.size}（{_human_size(info.size)}）")
                print(f"etag:         {info.etag}")
                print(f"lastModified: {info.last_modified}")
                print(f"contentType:  {info.content_type}")

            elif args.command == "url":
                print(service.generate_signed_url(args.key, method=args.method,
                                                  expires=args.expires))

            elif args.command == "rm":
                # 单个对象走单删，多个走批量删除。
                if len(args.keys) == 1:
                    service.delete_object(args.keys[0])
                    print(f"已删除 {args.keys[0]}")
                else:
                    for key in service.delete_objects(args.keys):
                        print(f"已删除 {key}")

            elif args.command == "cp":
                service.copy_object(args.source, args.dest)
                print(f"已复制 {args.source} -> {args.dest}")

    except ObsError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
