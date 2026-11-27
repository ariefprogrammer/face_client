import json

from poc_common import BASE

CONFIG_FILE = BASE / "config.json"


def load_config():
    if not CONFIG_FILE.exists():
        raise SystemExit("config.json tidak ditemukan di folder face_client.")

    cfg = json.loads(CONFIG_FILE.read_text())
    cfg["server_url"] = cfg["server_url"].rstrip("/")
    return cfg