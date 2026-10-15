"""cc-feishu 子命令分发。M1 只有 notify/install 的注册框架，子命令随后续任务加入。"""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cc-feishu", description="Claude Code ↔ 飞书桥")
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
