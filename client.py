import sys
import time
import random

import cv2
import mediapipe as mp

import store
from api import ApiError, AuthError, NetworkError, RejectedError, ApiClient
from app_config import load_config
from poc_common import create_models, open_camera
from poc_pipeline import (
    CHALLENGE_TIMEOUT,
    PITCH_THRESHOLD,
    STABLE_FRAMES,
    YAW_THRESHOLD,
    detect_blink,
    estimate_pitch,
    estimate_yaw,
    largest_face,
)
from poc_recognize import best_match
from sync import GalleryHolder, SyncWorker, sync_encodings

VERSION = "0.2.0"
ENROLL_SAMPLES = 5

LOST_GRACE = 10      # toleransi frame tanpa wajah selama tantangan
MAX_SHIFT = 0.6      # batas geser pusat wajah antar frame (relatif ukuran wajah)
WRONG_BLOCK_SECONDS = 60 # durasi blokir jika wajah salah (deteksi liveness gagal)
WRONG_MARGIN = 1.2   # arah salah harus melewati ambang x 1.2 supaya dianggap salah

# fungsi scan effect
SCAN_SPEED = 0.8     # siklus naik-turun per detik (makin besar makin cepat)
SCAN_ALPHA = 0.25    # transparansi pita scan (0.0 - 1.0)

BOX_COLOR = (0, 255, 0)   # hijau (BGR)


def same_face(face, prev_box):
    x, y, bw, bh = face[:4]
    px, py, pbw, pbh = prev_box
    dx = abs((x + bw / 2) - (px + pbw / 2))
    dy = abs((y + bh / 2) - (py + pbh / 2))
    return dx < pbw * MAX_SHIFT and dy < pbh * MAX_SHIFT

# TURN_CHALLENGES = ["kanan", "kiri", "atas", "bawah"]
TURN_CHALLENGES = ["atas", "bawah"]
TURN_PROMPTS = {
    "kanan": "toleh ke KANAN",
    "kiri": "toleh ke KIRI",
    "atas": "angkat wajah ke ATAS",
    "bawah": "tunduk ke BAWAH",
}


def check_turn(target, yaw, pitch_delta):
    if target == "kiri":
        return yaw > YAW_THRESHOLD
    if target == "kanan":
        return yaw < -YAW_THRESHOLD
    if target == "atas":
        return pitch_delta < -PITCH_THRESHOLD
    return pitch_delta > PITCH_THRESHOLD

def is_wrong_direction(target, yaw, pitch_delta):
    m = WRONG_MARGIN
    moves = {
        "kiri": yaw > YAW_THRESHOLD * m,
        "kanan": yaw < -YAW_THRESHOLD * m,
        "atas": pitch_delta < -PITCH_THRESHOLD * m,
        "bawah": pitch_delta > PITCH_THRESHOLD * m,
    }
    return any(done for direction, done in moves.items() if direction != target)

def draw_scan_effect(frame, box, color, now):
    x, y, bw, bh = box
    fh, fw = frame.shape[:2]

    t = (now * SCAN_SPEED) % 2.0
    t = t if t <= 1.0 else 2.0 - t
    line_y = int(y + bh * t)

    band = max(int(bh * 0.12), 6)
    x1, x2 = max(x, 0), min(x + bw, fw)
    y1, y2 = max(line_y - band, y, 0), min(line_y + band, y + bh, fh)

    if x2 > x1 and y2 > y1:
        roi = frame[y1:y2, x1:x2]
        tint = roi.copy()
        tint[:] = color
        frame[y1:y2, x1:x2] = cv2.addWeighted(tint, SCAN_ALPHA, roi, 1 - SCAN_ALPHA, 0)

    cv2.line(frame, (x1, line_y), (x2, line_y), color, 2)

def make_api(cfg):
    return ApiClient(cfg["server_url"], cfg["device_token"])


def run():

    stage, turn_target, snapshot = "blink", "kiri", None
    pitch_base = 0.0

    locked_id, locked_score, locked_box = None, 0.0, None
    lost_frames = 0

    cfg = load_config()
    api = make_api(cfg)
    holder = GalleryHolder()
    settings = {"cooldown_seconds": 60, "sync_interval_seconds": 300}

    try:
        settings.update(api.get_config())
    except ApiError as exc:
        print(f"Pengaturan server tidak dimuat, memakai bawaan ({exc})")

    try:
        sync_encodings(api, holder, full=True)
        print("Sinkron data wajah dari server selesai")
    except ApiError as exc:
        print(f"Sinkron gagal ({exc}), memakai cache lokal")
        holder.set(*store.build_gallery(store.load_gallery_cache()))

    worker = SyncWorker(api, holder, settings["sync_interval_seconds"], VERSION)
    worker.start()

    cap = open_camera(cfg.get("camera_index", 0))
    ok, frame = cap.read()
    if not ok:
        raise SystemExit("Kamera tidak mengirim gambar.")

    h, w = frame.shape[:2]
    detector, recognizer = create_models((w, h))
    face_mesh = mp.solutions.face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    cooldown = settings["cooldown_seconds"]
    last_success = {}
    blocked_until = {}
    candidate, stable, waiting_since, closed_frames = None, 0, None, 0
    retry_after = 0.0
    message, message_until = "", 0.0
    queue_size, queue_checked = 0, 0.0
    print("Q untuk keluar.")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        now = time.time()
        gallery, names = holder.get()
        _, faces = detector.detect(frame)
        status, color, box = "Menunggu wajah", (200, 200, 200), None

        if faces is None:
            if waiting_since is not None and lost_frames < LOST_GRACE:
                lost_frames += 1
                status, color = "Wajah hilang, tetap hadap kamera", (0, 165, 255)
            else:
                candidate, stable, waiting_since, closed_frames = None, 0, None, 0
                locked_id, lost_frames = None, 0
        else:
            lost_frames = 0
            face = largest_face(faces)
            box = face[:4].astype(int)

            challenge_active = waiting_since is not None and locked_id is not None
            if challenge_active and not same_face(face, locked_box):
                candidate, stable, waiting_since, closed_frames = None, 0, None, 0
                locked_id, challenge_active = None, False

            if not gallery:
                status, color = "Belum ada data wajah", (0, 0, 255)
            else:
                if challenge_active:
                    employee_id, score = locked_id, locked_score
                    locked_box = tuple(face[:4])
                else:
                    feature = recognizer.feature(recognizer.alignCrop(frame, face))
                    employee_id, score = best_match(recognizer, feature, gallery)

                if employee_id is None:
                    candidate, stable, waiting_since, closed_frames = None, 0, None, 0
                    status, color = f"Tidak dikenal ({score:.2f})", (0, 0, 255)
                else:
                    name = names.get(employee_id, "?")

                    if now - last_success.get(employee_id, 0.0) < cooldown:
                        status, color = f"{name}: sudah presensi", (255, 200, 0)
                    elif now < blocked_until.get(employee_id, 0.0):
                        sisa = blocked_until[employee_id] - now
                        status, color = f"{name}: diblokir {sisa:.0f}s", (0, 0, 255)
                    elif now < retry_after:
                        status, color = "Coba lagi sebentar", (0, 165, 255)
                    else:
                        if employee_id != candidate:
                            candidate, stable, waiting_since, closed_frames = employee_id, 1, None, 0
                        else:
                            stable += 1

                        if stable < STABLE_FRAMES:
                            status, color = f"{name}: memverifikasi...", (0, 255, 255)
                        else:
                            if waiting_since is None:
                                waiting_since = now
                                closed_frames = 0
                                stage = "blink"
                                turn_target = random.choice(TURN_CHALLENGES)
                                pitch_base = estimate_pitch(face)
                                snapshot = frame.copy()
                                locked_id, locked_score = employee_id, score
                                locked_box = tuple(face[:4])

                            prompt = "silakan berkedip"
                            passed = False
                            wrong = False
                            yaw = estimate_yaw(face)
                            pitch = estimate_pitch(face)

                            if stage == "blink":
                                pitch_base = pitch
                                blinked, closed_frames = detect_blink(face_mesh, frame, face, closed_frames)
                                if blinked:
                                    stage = "turn"

                            if stage == "turn":
                                prompt = TURN_PROMPTS[turn_target]
                                d_pitch = pitch - pitch_base
                                passed = check_turn(turn_target, yaw, d_pitch)
                                wrong = (not passed) and is_wrong_direction(turn_target, yaw, d_pitch)

                            if passed:
                                _, buf = cv2.imencode(".jpg", snapshot, [cv2.IMWRITE_JPEG_QUALITY, 85])
                                store.enqueue_attendance(employee_id, score, buf.tobytes())
                                last_success[employee_id] = now
                                message, message_until = f"PRESENSI BERHASIL: {name}", now + 3
                                candidate, stable, waiting_since = None, 0, None
                                locked_id = None
                            elif wrong:
                                blocked_until[employee_id] = now + WRONG_BLOCK_SECONDS
                                message, message_until = f"Arah salah: diblokir {WRONG_BLOCK_SECONDS} detik", now + 3
                                candidate, stable, waiting_since = None, 0, None
                                locked_id = None
                            elif now - waiting_since > CHALLENGE_TIMEOUT:
                                message, message_until = "Liveness gagal: tantangan tidak selesai", now + 3
                                retry_after = now + 3
                                candidate, stable, waiting_since = None, 0, None
                                locked_id = None
                            else:
                                remaining = CHALLENGE_TIMEOUT - (now - waiting_since)
                                status = f"{name}: {prompt} ({remaining:.0f}s)"
                                color = (0, 255, 255)

        if now - queue_checked > 2:
            queue_size = len(store.pending_entries())
            queue_checked = now

        if box is not None:
            x, y, bw, bh = box
            cv2.rectangle(frame, (x, y), (x + bw, y + bh), BOX_COLOR, 2)
            is_prompt = color == (0, 255, 255)
            if is_prompt:
                draw_scan_effect(frame, (x, y, bw, bh), BOX_COLOR, now)
            text_color = BOX_COLOR if is_prompt else color
            cv2.putText(frame, status, (x, max(y - 10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, text_color, 2)
        else:
            cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        cv2.putText(frame, f"Server: {worker.status} | antrean: {queue_size}",
                    (10, h - 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)

        if now < message_until:
            msg_color = (0, 255, 0) if "BERHASIL" in message else (0, 0, 255)
            cv2.putText(frame, message, (10, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, msg_color, 2)

        cv2.imshow("Presensi", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    worker.stop_event.set()
    cap.release()
    cv2.destroyAllWindows()


def enroll(code):
    cfg = load_config()
    api = make_api(cfg)
    cap = open_camera(cfg.get("camera_index", 0))
    ok, frame = cap.read()
    if not ok:
        raise SystemExit("Kamera tidak mengirim gambar.")

    h, w = frame.shape[:2]
    detector, recognizer = create_models((w, h))
    samples = []
    print(f"Hadap kamera. SPASI = ambil sampel ({ENROLL_SAMPLES} kali), Q = batal.")

    while len(samples) < ENROLL_SAMPLES:
        ok, frame = cap.read()
        if not ok:
            break

        _, faces = detector.detect(frame)
        count = 0 if faces is None else len(faces)
        view = frame.copy()

        if faces is not None:
            for f in faces:
                x, y, bw, bh = f[:4].astype(int)
                cv2.rectangle(view, (x, y), (x + bw, y + bh), (0, 255, 0), 2)

        cv2.putText(view, f"Sampel {len(samples)}/{ENROLL_SAMPLES} | wajah: {count}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.imshow("Enroll", view)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord(" "):
            if count != 1:
                print("Ditolak: harus tepat satu wajah.")
                continue
            aligned = recognizer.alignCrop(frame, faces[0])
            samples.append(recognizer.feature(aligned)[0].tolist())
            print(f"Sampel {len(samples)} tersimpan")

    cap.release()
    cv2.destroyAllWindows()

    if len(samples) < ENROLL_SAMPLES:
        print("Enrollment dibatalkan.")
        return

    try:
        result = api.enroll(code, samples)
        print(f"Enrollment berhasil untuk {result['name']}")
    except RejectedError as exc:
        print(f"Ditolak server: {exc}")
    except AuthError as exc:
        print(f"Autentikasi gagal: {exc}")
    except NetworkError as exc:
        print(f"Gagal terhubung: {exc}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "enroll":
        enroll(sys.argv[2])
    else:
        run()