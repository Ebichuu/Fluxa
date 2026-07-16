from pathlib import Path
import os


def dotenv_values(path):
    values = {}
    path = Path(path)
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_dotenv(dotenv_path=None, override=False, **_kwargs):
    if not dotenv_path:
        dotenv_path = ".env"
    for key, value in dotenv_values(dotenv_path).items():
        if override or key not in os.environ:
            os.environ[key] = value
    return True
