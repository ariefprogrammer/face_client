import threading
import time
from datetime import datetime, timedelta

import store
from api import ApiError, AuthError, NetworkError, RejectedError


class GalleryHolder:
    def __init__(self):
        self._lock = threading.Lock()
        self._gallery, self._names = {}, {}

    def set(self, gallery, names):
        with self._lock:
            self._gallery, self._names = gallery, names

    def get(self):
        with self._lock:
            return self._gallery, self._names


def sync_encodings(api, holder, full=False):
    cache = store.load_gallery_cache()
    since = None

    if not full and cache["synced_at"]:
        since = (datetime.fromisoformat(cache["synced_at"]) - timedelta(seconds=10)).isoformat()

    payload = api.get_encodings(since)
    new_cache = store.apply_sync(cache, payload, replace=since is None)
    store.save_gallery_cache(new_cache)
    holder.set(*store.build_gallery(new_cache))


def flush_queue(api):
    for path in store.pending_entries():
        entry = store.read_entry(path)
        try:
            api.send_attendance(entry, path.with_suffix(".jpg"))
            store.remove_entry(path)
        except RejectedError as exc:
            store.reject_entry(path, str(exc))


class SyncWorker(threading.Thread):
    TICK = 10

    def __init__(self, api, holder, sync_interval, version):
        super().__init__(daemon=True)
        self.api = api
        self.holder = holder
        self.sync_interval = sync_interval
        self.version = version
        self.status = "memulai"
        self.stop_event = threading.Event()
        self.last_sync = 0.0
        self.last_heartbeat = 0.0

    def run(self):
        while not self.stop_event.is_set():
            self._cycle()
            self.stop_event.wait(self.TICK)

    def _try(self, fn):
        try:
            fn()
            return None
        except AuthError:
            return "token ditolak"
        except NetworkError:
            return "offline"
        except ApiError as exc:
            return f"error: {exc}"
        except Exception as exc:
            return f"error: {exc.__class__.__name__}"

    def _cycle(self):
        now = time.time()
        errors = []

        if now - self.last_sync >= self.sync_interval:
            err = self._try(lambda: sync_encodings(self.api, self.holder))
            if err:
                errors.append(err)
            else:
                self.last_sync = now

        if now - self.last_heartbeat >= 60:
            err = self._try(lambda: self.api.heartbeat(self.version))
            if err:
                errors.append(err)
            else:
                self.last_heartbeat = now

        err = self._try(lambda: flush_queue(self.api))
        if err:
            errors.append(err)

        self.status = errors[0] if errors else "online"