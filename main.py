import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from api import ApiClient, ApiError, AuthError, NetworkError
from app_config import CONFIG_FILE
from poc_common import BASE

CLIENT_SCRIPT = BASE / "client.py"
MAX_LOG_LINES = 500


# ---------------------------------------------------------------- helper

def python_exe():
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        candidate = exe.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return sys.executable


def read_config():
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except ValueError:
        return {}


def write_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def config_ready(cfg=None):
    cfg = read_config() if cfg is None else cfg
    return bool(cfg.get("server_url")) and bool(cfg.get("device_token"))


def make_client():
    cfg = read_config()
    if not config_ready(cfg):
        return None
    return ApiClient(cfg["server_url"].rstrip("/"), cfg["device_token"])


def make_btn(parent, text, command, big=False, **kwargs):
    opts = {
        "text": text,
        "command": command,
        "font": ("Helvetica", 13, "bold") if big else ("Helvetica", 11),
        "padx": 16 if big else 12,
        "pady": 14 if big else 6,
        "relief": "raised",
        "bd": 1,
        "cursor": "pointinghand",
    }
    opts.update(kwargs)
    return tk.Button(parent, **opts)


def show_dialog(parent, title, message, kind="info"):
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.resizable(False, False)

    colors = {"info": "#1d4ed8", "warning": "#b45309", "error": "#b91c1c"}
    labels = {"info": "Info", "warning": "Peringatan", "error": "Error"}

    body = ttk.Frame(win, padding=20)
    body.pack(fill="both", expand=True)

    ttk.Label(
        body,
        text=labels.get(kind, "Info"),
        font=("Helvetica", 13, "bold"),
        foreground=colors.get(kind, "#1d4ed8"),
    ).pack(anchor="w")

    ttk.Label(body, text=message, wraplength=420, justify="left").pack(
        anchor="w", pady=(8, 18)
    )

    def close(_e=None):
        win.grab_release()
        win.destroy()
        parent.focus_force()

    btn = make_btn(body, "OK", close, width=10)
    btn.pack(anchor="e")
    win.bind("<Return>", close)
    win.bind("<Escape>", close)
    win.protocol("WM_DELETE_WINDOW", close)

    win.update_idletasks()
    px = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_width()) // 2
    py = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_height()) // 2
    win.geometry(f"+{max(0, px)}+{max(0, py)}")

    win.grab_set()
    btn.focus_set()
    parent.wait_window(win)


def ask_yes_no(parent, title, message):
    result = {"value": False}
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.resizable(False, False)

    body = ttk.Frame(win, padding=20)
    body.pack(fill="both", expand=True)

    ttk.Label(body, text=message, wraplength=420, justify="left").pack(
        anchor="w", pady=(0, 18)
    )

    def choose(val):
        result["value"] = val
        win.grab_release()
        win.destroy()
        parent.focus_force()

    btns = ttk.Frame(body)
    btns.pack(anchor="e")
    make_btn(btns, "Batal", lambda: choose(False), width=9).pack(side="left", padx=(0, 8))
    btn_yes = make_btn(btns, "Ya", lambda: choose(True), width=9)
    btn_yes.pack(side="left")

    win.bind("<Return>", lambda _e: choose(True))
    win.bind("<Escape>", lambda _e: choose(False))
    win.protocol("WM_DELETE_WINDOW", lambda: choose(False))

    win.update_idletasks()
    px = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_width()) // 2
    py = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_height()) // 2
    win.geometry(f"+{max(0, px)}+{max(0, py)}")

    win.grab_set()
    btn_yes.focus_set()
    parent.wait_window(win)
    return result["value"]


# ----------------------------------------------------------------- halaman

class Page(ttk.Frame):
    def __init__(self, parent, app, title, back=True):
        super().__init__(parent)
        self.app = app
        head = ttk.Frame(self)
        head.pack(fill="x")
        if back:
            make_btn(head, "\u2190 Kembali", lambda: app.show("home")).pack(side="left")
        ttk.Label(head, text=title, style="Title.TLabel").pack(side="left", padx=12)
        self.body = ttk.Frame(self, padding=(0, 16))
        self.body.pack(fill="both", expand=True)

    def on_show(self):
        pass


class HomePage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Presensi Wajah", back=False)

        ttk.Label(self.body, text="Pilih menu untuk memulai.").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )

        items = [
            ("Mulai Presensi", app.start_presensi, True),
            ("Daftarkan Wajah", lambda: app.show("enroll"), True),
            ("Status & Antrean", lambda: app.show("status"), False),
            ("Pengaturan", lambda: app.show("settings"), False),
        ]
        for i, (text, command, uses_camera) in enumerate(items):
            btn = make_btn(self.body, text, command, big=True)
            btn.grid(row=1 + i // 2, column=i % 2, sticky="nsew", padx=6, pady=6)
            if uses_camera:
                app.lock_when_busy.append(btn)

        self.body.columnconfigure((0, 1), weight=1)
        self.body.rowconfigure((1, 2), weight=1)

        banner = ttk.Frame(self.body)
        banner.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Label(banner, textvariable=app.run_info, wraplength=500,
                  foreground="#1a7f37").pack(side="left")
        make_btn(banner, "Keluar", app.on_close).pack(side="right")
        self.stop_btn = make_btn(banner, "Hentikan", app.stop_job)
        self.stop_btn.pack(side="right", padx=(0, 8))
        self.set_running(False)

    def set_running(self, running):
        self.stop_btn.configure(state="normal" if running else "disabled")


class EnrollPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Daftarkan Wajah")

        ttk.Label(
            self.body, wraplength=640, justify="left",
            text=(
                "1. Masukkan kode pendaftaran.\n"
                "2. Klik Mulai, lalu hadap kamera.\n"
                "3. Klik jendela kamera, lalu tekan SPASI untuk mengambil sampel "
                "(ikuti hitungan di layar). Tekan Q untuk batal.\n"
                "Pastikan hanya ada satu wajah yang terlihat kamera."
            ),
        ).pack(anchor="w")

        row = ttk.Frame(self.body)
        row.pack(anchor="w", pady=16)
        ttk.Label(row, text="Kode pendaftaran:").pack(side="left")
        self.var_code = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.var_code, width=28)
        entry.pack(side="left", padx=8)
        entry.bind("<Return>", lambda _e: self.start())
        self.btn = make_btn(row, "Mulai", self.start)
        self.btn.pack(side="left")
        app.lock_when_busy.append(self.btn)

    def on_show(self):
        self.var_code.set("")

    def start(self):
        code = self.var_code.get().strip()
        if not code:
            show_dialog(self.app, "Daftarkan Wajah", "Kode pendaftaran belum diisi.", kind="warning")
            return
        self.app.start_enroll(code)


class StatusPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Status & Antrean")

        self.var_server = tk.StringVar()
        self.var_faces = tk.StringVar()
        self.var_queue = tk.StringVar()
        self.var_rejected = tk.StringVar()

        rows = [
            ("Server", self.var_server),
            ("Data wajah", self.var_faces),
            ("Antrean menunggu kirim", self.var_queue),
            ("Ditolak server", self.var_rejected),
        ]
        for i, (label, var) in enumerate(rows):
            ttk.Label(self.body, text=label + ":").grid(row=i, column=0, sticky="w", pady=4)
            ttk.Label(self.body, textvariable=var).grid(row=i, column=1, sticky="w", padx=12)

        actions = ttk.Frame(self.body)
        actions.grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(20, 0))
        make_btn(actions, "Periksa Ulang", self.refresh).pack(side="left")
        self.btn_sync = make_btn(actions, "Kirim Antrean & Sinkron", self.sync_now)
        self.btn_sync.pack(side="left", padx=8)
        app.lock_when_busy.append(self.btn_sync)

    def on_show(self):
        self.refresh()

    def refresh(self):
        import store

        try:
            cache = store.load_gallery_cache()
            total = len(cache["employees"])
            synced = cache["synced_at"] or "belum pernah"
            self.var_faces.set(f"{total} orang (sinkron terakhir: {synced})")
        except Exception:
            self.var_faces.set("cache tidak terbaca")

        self.var_queue.set(str(len(store.pending_entries())))
        self.var_rejected.set(str(len(list(store.REJECTED_DIR.glob("*.json")))))

        self.var_server.set("memeriksa...")
        self.app.run_bg(self.app.check_server, self.var_server.set)

    def sync_now(self):
        if self.app.busy:
            return
        self.var_server.set("menyinkronkan...")

        def done(message):
            self.app.log(f"[launcher] {message}")
            show_dialog(self.app, "Sinkron", message, kind="info")
            self.refresh()

        self.app.run_bg(self.app.sync_job, done)


class SettingsPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Pengaturan")

        self.var_url = tk.StringVar()
        self.var_token = tk.StringVar()
        self.var_cam = tk.StringVar(value="0")
        self.var_show = tk.BooleanVar(value=False)
        self.var_hint = tk.StringVar()

        ttk.Label(self.body, textvariable=self.var_hint, foreground="#b45309").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        ttk.Label(self.body, text="Alamat server:").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(self.body, textvariable=self.var_url, width=44).grid(row=1, column=1, sticky="w", padx=12)

        ttk.Label(self.body, text="Token perangkat:").grid(row=2, column=0, sticky="w", pady=6)
        self.entry_token = ttk.Entry(self.body, textvariable=self.var_token, width=44, show="\u2022")
        self.entry_token.grid(row=2, column=1, sticky="w", padx=12)
        ttk.Checkbutton(self.body, text="Tampilkan token", variable=self.var_show,
                        command=self.toggle_token).grid(row=3, column=1, sticky="w", padx=12)

        ttk.Label(self.body, text="Nomor kamera:").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Spinbox(self.body, from_=0, to=9, textvariable=self.var_cam, width=5).grid(
            row=4, column=1, sticky="w", padx=12
        )

        make_btn(self.body, "Simpan", self.save).grid(
            row=5, column=1, sticky="w", padx=12, pady=(16, 0)
        )

    def toggle_token(self):
        self.entry_token.configure(show="" if self.var_show.get() else "\u2022")

    def on_show(self):
        cfg = read_config()
        self.var_url.set(cfg.get("server_url", ""))
        self.var_token.set(cfg.get("device_token", ""))
        self.var_cam.set(str(cfg.get("camera_index", 0)))
        self.var_hint.set("" if config_ready(cfg) else
                          "Pengaturan belum lengkap. Isi alamat server dan token perangkat.")

    def save(self):
        url = self.var_url.get().strip().rstrip("/")
        token = self.var_token.get().strip()

        if not url.startswith(("http://", "https://")):
            show_dialog(self.app, "Pengaturan", "Alamat server harus diawali http:// atau https://", kind="error")
            return
        if not token:
            show_dialog(self.app, "Pengaturan", "Token perangkat belum diisi.", kind="error")
            return
        try:
            cam = int(self.var_cam.get())
        except ValueError:
            show_dialog(self.app, "Pengaturan", "Nomor kamera harus berupa angka.", kind="error")
            return

        cfg = read_config()
        cfg.update({"server_url": url, "device_token": token, "camera_index": cam})
        write_config(cfg)
        self.var_hint.set("")
        show_dialog(self.app, "Pengaturan", "Pengaturan tersimpan.", kind="info")


# --------------------------------------------------------------- aplikasi

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Presensi Wajah")
        self.geometry("760x580")
        self.minsize(640, 500)

        self.proc = None
        self.job = None
        self.job_output = []
        self.stopping = False
        self.out_queue = queue.Queue()
        self.ui_calls = queue.Queue()
        self.run_info = tk.StringVar()
        self.lock_when_busy = []

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Helvetica", 18, "bold"))

        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        container = ttk.Frame(root)
        container.pack(fill="both", expand=True)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.pages = {}
        for name, cls in (("home", HomePage), ("enroll", EnrollPage),
                          ("status", StatusPage), ("settings", SettingsPage)):
            page = cls(container, self)
            self.pages[name] = page

        log_box = ttk.LabelFrame(root, text="Log", padding=6)
        log_box.pack(fill="x", pady=(10, 0))
        self.log_text = tk.Text(log_box, height=7, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(log_box, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="x", expand=True)
        scroll.pack(side="right", fill="y")

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.show("home" if config_ready() else "settings")
        self.refresh_state()

        self.after(100, self._focus_once)
        self.after(100, self._poll)

    def _focus_once(self):
        self.lift()
        self.focus_force()
        if sys.platform == "darwin":
            subprocess.Popen(
                [
                    "osascript",
                    "-e",
                    f'tell application "System Events" to set frontmost of the first process whose unix id is {os.getpid()} to true',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    # ---- navigasi & log

    def show(self, name):
        self.log(f"[launcher] buka halaman: {name}")
        for p in self.pages.values():
            p.grid_remove()
        page = self.pages[name]
        page.grid(row=0, column=0, sticky="nsew")
        page.on_show()

    def log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > MAX_LOG_LINES:
            self.log_text.delete("1.0", "101.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ---- proses klien (client.py)

    @property
    def busy(self):
        return self.proc is not None

    def refresh_state(self):
        busy = self.busy
        for widget in self.lock_when_busy:
            widget.configure(state="disabled" if busy else "normal")
        self.pages["home"].set_running(busy)
        if not busy:
            self.run_info.set("")

    def ensure_ready(self):
        if config_ready():
            return True
        show_dialog(self, "Pengaturan", "Alamat server dan token perangkat belum diisi.", kind="warning")
        self.show("settings")
        return False

    def start_presensi(self):
        if self.busy or not self.ensure_ready():
            return
        self.start_job(
            "presensi", [],
            "Presensi berjalan. Tutup dengan tombol Q di jendela kamera, atau klik Hentikan.",
        )

    def start_enroll(self, code):
        if self.busy or not self.ensure_ready():
            return
        if self.start_job("enroll", ["enroll", code],
                          "Pendaftaran wajah berjalan. Ikuti petunjuk di jendela kamera."):
            self.show("home")

    def start_job(self, job, args, info):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            self.proc = subprocess.Popen(
                [python_exe(), "-u", str(CLIENT_SCRIPT), *args],
                cwd=str(BASE),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.proc = None
            show_dialog(self, "Gagal memulai", str(exc), kind="error")
            return False

        self.job, self.job_output, self.stopping = job, [], False
        threading.Thread(target=self._read_output, args=(self.proc,), daemon=True).start()
        self.run_info.set(info)
        self.log(f"[launcher] {job} dimulai")
        self.refresh_state()
        return True

    def _read_output(self, proc):
        for line in proc.stdout:
            self.out_queue.put(("line", line.rstrip()))
        self.out_queue.put(("exit", proc.wait()))

    def stop_job(self):
        if self.proc is not None:
            self.stopping = True
            self.proc.terminate()

    def _on_exit(self, code):
        job, lines, stopping = self.job, self.job_output, self.stopping
        self.proc, self.job = None, None
        self.refresh_state()
        self.log(f"[launcher] {job} selesai (kode {code})")
        if stopping:
            return

        text = "\n".join(lines)
        tail = "\n".join(lines[-6:]) or "(tidak ada keterangan)"

        if job == "enroll":
            if "Enrollment berhasil" in text:
                done = next(l for l in lines if "Enrollment berhasil" in l)
                show_dialog(self, "Daftarkan Wajah", done, kind="info")
            elif code == 0:
                show_dialog(self, "Daftarkan Wajah", tail, kind="warning")
            else:
                show_dialog(self, "Daftarkan Wajah", tail, kind="error")
        elif code != 0:
            show_dialog(self, "Presensi", tail, kind="error")

    # ---- tugas latar (tidak membekukan jendela)

    def run_bg(self, fn, done):
        def work():
            try:
                result = fn()
            except Exception as exc:
                result = f"Error: {exc.__class__.__name__}: {exc}"
            self.ui_calls.put(lambda: done(result))

        threading.Thread(target=work, daemon=True).start()

    def check_server(self):
        api = make_client()
        if api is None:
            return "Belum diatur (buka Pengaturan)"
        try:
            api.get_config()
            return "Online"
        except AuthError:
            return "Token ditolak server"
        except NetworkError:
            return "Offline / server tidak terjangkau"
        except ApiError as exc:
            return f"Error: {exc}"

    def sync_job(self):
        from sync import GalleryHolder, flush_queue, sync_encodings

        api = make_client()
        if api is None:
            return "Pengaturan belum lengkap."
        try:
            flush_queue(api)
            sync_encodings(api, GalleryHolder(), full=True)
        except AuthError:
            return "Token perangkat ditolak server."
        except NetworkError:
            return "Server tidak terjangkau."
        except ApiError as exc:
            return f"Gagal: {exc}"
        return "Antrean terkirim dan data wajah tersinkron."

    # ---- loop pemeriksa antrean

    def _poll(self):
        try:
            while True:
                kind, payload = self.out_queue.get_nowait()
                if kind == "line":
                    self.job_output.append(payload)
                    self.log(payload)
                else:
                    self._on_exit(payload)
        except queue.Empty:
            pass

        try:
            while True:
                self.ui_calls.get_nowait()()
        except queue.Empty:
            pass

        self.after(100, self._poll)

    def report_callback_exception(self, exc, val, tb):
        import traceback
        detail = "".join(traceback.format_exception(exc, val, tb))
        self.log(detail)
        show_dialog(self, "Error", detail[-800:], kind="error")

    def on_close(self):
        if self.busy:
            if not ask_yes_no(self, "Keluar", "Proses kamera masih berjalan. Hentikan dan keluar?"):
                return
            self.stop_job()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()