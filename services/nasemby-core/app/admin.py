from __future__ import annotations

import argparse
import getpass
import json

from app.admin_auth import AdminAuth, AdminCredentialStore
from app.config import AUTH_DB_PATH
from app import discover_runtime
from app.private_rss_repository import PrivateRssRepository
from app.rss_scope_repair import RssScopeRepairError, RssScopeRepairService


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


def repair_rss_scope(mode, fingerprint=""):
    repository = PrivateRssRepository(discover_runtime.subscription_database_path())
    service = RssScopeRepairService(repository)
    try:
        result = service.preview() if mode == "preview" else service.apply(fingerprint)
    except RssScopeRepairError as exc:
        print(json.dumps({"status": "failed", "code": exc.code, "error": exc.message}))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m app.admin")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("reset-password")
    repair = subcommands.add_parser("repair-rss-scope")
    repair_group = repair.add_mutually_exclusive_group(required=True)
    repair_group.add_argument("--preview", action="store_true")
    repair_group.add_argument("--apply", metavar="PREVIEW_FINGERPRINT")
    arguments = parser.parse_args(argv)
    if arguments.command == "reset-password":
        return reset_password()
    if arguments.command == "repair-rss-scope":
        return repair_rss_scope(
            "preview" if arguments.preview else "apply",
            arguments.apply or "",
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
