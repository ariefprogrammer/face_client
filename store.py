import json
import os
import shutil
import uuid
from datetime import datetime

import numpy as np

from poc_common import BASE

DATA_DIR = BASE / "data"
QUEUE_DIR = DATA_DIR / "queue"
REJECTED_DIR = DATA_DIR / "rejected"
GALLERY_FILE = DATA_DIR / "gallery.json"

QUEUE_DIR.mkdir(parents=True, exist_ok=True)
REJECTED_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def load_gallery_cache():
    if not GALLERY_FILE.exists():
        return {"synced_at": None, "employees": {}}
    return json.loads(GALLERY_FILE.read_text())


def save_gallery_cache(cache):
    _atomic_write(GALLERY_FILE, json.dumps(cache))


def apply_sync(cache, payload, replace):
    employees = {} if replace else dict(cache["employees"])

    grouped = {}
    for item in payload["encodings"]:
        entry = grouped.setdefault(item["employee_id"], {"name": item["name"], "samples": []})
        entry["samples"].append(item["encoding"])

    employees.update(grouped)

    for employee_id in payload["inactive_employee_ids"]:
        employees.pop(employee_id, None)

    return {"synced_at": payload["server_time"], "employees": employees}


def build_gallery(cache):
    gallery, names = {}, {}
    for employee_id, info in cache["employees"].items():
        gallery[employee_id] = [
            np.array(sample, dtype=np.float32).reshape(1, -1) for sample in info["samples"]
        ]
        names[employee_id] = info["name"]
    return gallery, names


def enqueue_attendance(employee_id, score, jpg_bytes):
    event_id = str(uuid.uuid4())
    (QUEUE_DIR / f"{event_id}.jpg").write_bytes(jpg_bytes)

    entry = {
        "client_event_id": event_id,
        "employee_id": employee_id,
        "score": round(float(score), 3),
        "scanned_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _atomic_write(QUEUE_DIR / f"{event_id}.json", json.dumps(entry))
    return event_id


def pending_entries():
    return sorted(QUEUE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)


def read_entry(path):
    return json.loads(path.read_text())


def remove_entry(path):
    path.with_suffix(".jpg").unlink(missing_ok=True)
    path.unlink(missing_ok=True)


def reject_entry(path, reason):
    entry = read_entry(path)
    entry["rejected_reason"] = reason
    _atomic_write(REJECTED_DIR / path.name, json.dumps(entry))

    photo = path.with_suffix(".jpg")
    if photo.exists():
        shutil.move(str(photo), str(REJECTED_DIR / photo.name))
    path.unlink(missing_ok=True)