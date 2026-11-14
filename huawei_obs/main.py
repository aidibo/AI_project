"""Command line interface for the OBS helper.

Examples
--------
    python -m huawei_obs.main list-buckets
    python -m huawei_obs.main ls --prefix logs/
    python -m huawei_obs.main upload ./report.pdf --key reports/report.pdf
    python -m huawei_obs.main download reports/report.pdf --dest ./report.pdf
    python -m huawei_obs.main stat reports/report.pdf
    python -m huawei_obs.main url reports/report.pdf --expires 600
    python -m huawei_obs.main rm reports/report.pdf
"""

from __future__ import annotations

import argparse
import sys

from .config import ObsConfig
from .obs_service import ObsError, ObsService


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{size}B"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="obs", description="Huawei Cloud OBS helper")
    parser.add_argument("--bucket", help="override the default bucket")
    parser.add_argument("--server", help="override the OBS endpoint")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-buckets", help="list all buckets")
    sub.add_parser("mb", help="create the configured bucket")

    p_ls = sub.add_parser("ls", help="list objects")
    p_ls.add_argument("--prefix", default=None)
    p_ls.add_argument("--delimiter", default=None)
    p_ls.add_argument("--max-keys", type=int, default=None)

    p_up = sub.add_parser("upload", help="upload a local file")
    p_up.add_argument("path")
    p_up.add_argument("--key", default=None)

    p_down = sub.add_parser("download", help="download an object")
    p_down.add_argument("key")
    p_down.add_argument("--dest", default=None)

    p_stat = sub.add_parser("stat", help="show object metadata")
    p_stat.add_argument("key")

    p_url = sub.add_parser("url", help="generate a temporary URL")
    p_url.add_argument("key")
    p_url.add_argument("--method", default="GET")
    p_url.add_argument("--expires", type=int, default=3600)

    p_rm = sub.add_parser("rm", help="delete one or more objects")
    p_rm.add_argument("keys", nargs="+")

    p_cp = sub.add_parser("cp", help="copy an object within the bucket")
    p_cp.add_argument("source")
    p_cp.add_argument("dest")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = ObsConfig.from_env()
    if args.server:
        config.server = args.server
    if args.bucket:
        config.bucket = args.bucket

    require_bucket = args.command not in ("list-buckets",)
    try:
        config.validate(require_bucket=require_bucket)
    except ValueError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    try:
        with ObsService(config) as service:
            if args.command == "list-buckets":
                for name in service.list_buckets():
                    print(name)

            elif args.command == "mb":
                created = service.create_bucket()
                print("created" if created else "already exists")

            elif args.command == "ls":
                page = service.list_objects(prefix=args.prefix, delimiter=args.delimiter,
                                            max_keys=args.max_keys)
                for info in page.objects:
                    print(f"{_human(info.size):>10}  {info.last_modified or '':<24}  {info.key}")
                for prefix in page.prefixes:
                    print(f"{'DIR':>10}  {'':<24}  {prefix}")

            elif args.command == "upload":
                info = service.upload_file(args.path, key=args.key)
                print(f"uploaded {info.key} ({_human(info.size)})")

            elif args.command == "download":
                dest = args.dest or args.key.rsplit("/", 1)[-1]
                path = service.download_file(args.key, dest)
                print(f"downloaded to {path}")

            elif args.command == "stat":
                info = service.stat_object(args.key)
                print(f"key:          {info.key}")
                print(f"size:         {info.size} ({_human(info.size)})")
                print(f"etag:         {info.etag}")
                print(f"lastModified: {info.last_modified}")
                print(f"contentType:  {info.content_type}")

            elif args.command == "url":
                url = service.generate_signed_url(args.key, method=args.method,
                                                  expires=args.expires)
                print(url)

            elif args.command == "rm":
                if len(args.keys) == 1:
                    service.delete_object(args.keys[0])
                    print(f"deleted {args.keys[0]}")
                else:
                    deleted = service.delete_objects(args.keys)
                    for key in deleted:
                        print(f"deleted {key}")

            elif args.command == "cp":
                service.copy_object(args.source, args.dest)
                print(f"copied {args.source} -> {args.dest}")

    except ObsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
