import os
from pathlib import Path
from tempfile import TemporaryDirectory

from app import config as app_config


_test_runtime_directory = TemporaryDirectory()
_test_root = Path(_test_runtime_directory.name)
_test_data_dir = _test_root / "data"
_test_db_dir = _test_root / "db"

# unittest discovery imports this package before the test modules. Redirect every
# default runtime path here so importing app.main (and its create_app defaults)
# cannot inspect or initialize the live workspace databases.
os.environ.pop("MCC_DATABASE_PATH", None)
app_config.ROOT_DIR = _test_root
app_config.WORKSPACE_ENV_PATH = _test_root / "workspace.env"
app_config.DATA_DIR = _test_data_dir
app_config.USER_ENV_PATH = _test_data_dir / "user.env"
app_config.LEGACY_DB_DIR = _test_db_dir
app_config.AUTH_DB_PATH = _test_db_dir / "auth.sqlite3"
app_config.LEGACY_USER_ENV_PATH = _test_db_dir / "user.env"
app_config.SYS_ENV_PATH = _test_root / "sys.env"

from app import activity_log, discover_runtime


_subscription_config_path = _test_db_dir / "discover_subscriptions.json"
_subscription_items_path = _test_db_dir / "discover_subscription_items.json"
discover_runtime.SUBSCRIPTION_CONFIG_PATH = str(_subscription_config_path)
discover_runtime.SUBSCRIPTION_ITEMS_PATH = str(_subscription_items_path)
discover_runtime.SUBSCRIPTION_DETAIL_CACHE_PATH = str(
    _test_db_dir / "discover_subscription_detail_cache.json"
)
discover_runtime.DISCOVER_CACHE_DB_PATH = str(_test_db_dir / "discover_cache.db")
discover_runtime.TMDB_MATCH_CACHE_PATH = str(_test_db_dir / "tmdb_match_cache.json")

_runtime_subscription_database_path = discover_runtime.subscription_database_path


def _test_subscription_database_path():
    if str(os.environ.get("MCC_DATABASE_PATH") or "").strip():
        return _runtime_subscription_database_path()

    config_path = Path(discover_runtime.SUBSCRIPTION_CONFIG_PATH)
    items_path = Path(discover_runtime.SUBSCRIPTION_ITEMS_PATH)
    config_was_patched = config_path != _subscription_config_path
    items_was_patched = items_path != _subscription_items_path
    if config_was_patched and not items_was_patched:
        return config_path.resolve().parent / "media_control_center.sqlite3"
    if items_was_patched and not config_was_patched:
        return items_path.resolve().parent / "media_control_center.sqlite3"
    return _runtime_subscription_database_path()


discover_runtime.subscription_database_path = _test_subscription_database_path
discover_runtime._SUBSCRIPTION_REPOSITORIES.clear()


activity_log.LOG_PATH = _test_data_dir / "activity_log.jsonl"
