"""cc-feishu 子命令分发。stdin/env 处理集中于此，notify.py 保持无 CLI 代码。"""

import argparse
import os
import sys

from cc_feishu import notify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cc-feishu", description="Claude Code ↔ 飞书桥")
    sub = parser.add_subparsers(dest="command")

    p_notify = sub.add_parser("notify", help="Notification hook 入口")
    p_notify.add_argument("--dry-run", action="store_true", help="只打印分片与字节数，不发送")

    args = parser.parse_args(argv)

    if args.command == "notify":
        dry_run = args.dry_run or os.environ.get("DRY_RUN") == "1"
        return notify.notify(sys.stdin.read(), dry_run)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
