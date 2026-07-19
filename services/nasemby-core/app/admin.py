from __future__ import annotations

import argparse
import getpass

from app.admin_auth import AdminAuth, AdminCredentialStore
from app.config import AUTH_DB_PATH


def reset_password():
    username = input("管理员账号: ").strip()
    password = getpass.getpass("新密码: ")
    confirmation = getpass.getpass("确认新密码: ")
    if password != confirmation:
        print("两次输入的密码不一致")
        return 1
    try:
        updated = AdminAuth(AdminCredentialStore(AUTH_DB_PATH)).reset_password(username, password)
    except ValueError as exc:
        print(str(exc))
        return 1
    if updated is None:
        print("管理员账号不存在")
        return 1
    print("管理员密码已重置，全部旧会话已失效")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m app.admin")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("reset-password")
    arguments = parser.parse_args(argv)
    if arguments.command == "reset-password":
        return reset_password()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
