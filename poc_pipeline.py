import json
import time
import uuid
from datetime import datetime

import random

import cv2
import mediapipe as mp

from poc_common import BASE, create_models, open_camera
from poc_liveness import CONSEC_FRAMES, EAR_THRESHOLD, LEFT_EYE, RIGHT_EYE, ear, to_points
from poc_recognize import best_match, load_gallery

QUEUE_FILE = BASE / "attendance_queue.jsonl"
COOLDOWN_SECONDS = 60
STABLE_FRAMES = 3

CHALLENGE_TIMEOUT = 8.0
YAW_THRESHOLD = 0.25
PITCH_THRESHOLD = 0.08


def largest_face(faces):
    return max(faces, key=lambda f: f[2] * f[3])


def crop_with_margin(frame, face, margin=0.25):
    h, w = frame.shape[:2]
    x, y, bw, bh = face[:4]
    mx, my = bw * margin, bh * margin
    x1, y1 = max(int(x - mx), 0), max(int(y - my), 0)
    x2, y2 = min(int(x + bw + mx), w), min(int(y + bh + my), h)
    return frame[y1:y2, x1:x2]

def estimate_yaw(face):
    right_eye_x, left_eye_x, nose_x = face[4], face[6], face[8]
    eye_dist = left_eye_x - right_eye_x
    if eye_dist <= 0:
        return 0.0
    return float((nose_x - (right_eye_x + left_eye_x) / 2) / eye_dist)

def estimate_pitch(face):
    eye_y = (face[5] + face[7]) / 2
    mouth_y = (face[11] + face[13]) / 2
    nose_y = face[9]
    span = mouth_y - eye_y
    if span <= 0:
        return 0.0
    return float((nose_y - eye_y) / span)

def detect_blink(face_mesh, frame, face, closed_frames):
    crop = crop_with_margin(frame, face)
    if crop.size == 0:
        return False, closed_frames

    result = face_mesh.process(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
    if not result.multi_face_landmarks:
        return False, 0

    ch, cw = crop.shape[:2]
    lm = result.multi_face_landmarks[0].landmark
    value = (ear(to_points(lm, RIGHT_EYE, cw, ch)) +
             ear(to_points(lm, LEFT_EYE, cw, ch))) / 2

    if value < EAR_THRESHOLD:
        return False, closed_frames + 1
    return closed_frames >= CONSEC_FRAMES, 0


def save_log(name, score):
    entry = {
        "client_event_id": str(uuid.uuid4()),
        "name": name,
        "score": round(float(score), 3),
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
    }
    with QUEUE_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    gallery = load_gallery()
    cap = open_camera()
    ok, frame = cap.read()
    h, w = frame.shape[:2]
    detector, recognizer = create_models((w, h))
    face_mesh = mp.solutions.face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    last_success = {}
    stage, turn_target = "blink", "left"
    candidate, stable, waiting_since, closed_frames = None, 0, None, 0
    retry_after = 0.0
    message, message_until = "", 0.0
    print("Q untuk keluar.")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        now = time.time()
        _, faces = detector.detect(frame)
        status, color, box = "Menunggu wajah", (200, 200, 200), None

        if faces is None:
            candidate, stable, waiting_since, closed_frames = None, 0, None, 0
        else:
            face = largest_face(faces)
            box = face[:4].astype(int)
            feature = recognizer.feature(recognizer.alignCrop(frame, face))
            name, score = best_match(recognizer, feature, gallery)

            if name is None:
                candidate, stable, waiting_since, closed_frames = None, 0, None, 0
                status, color = f"Tidak dikenal ({score:.2f})", (0, 0, 255)
            elif now - last_success.get(name, 0.0) < COOLDOWN_SECONDS:
                status, color = f"{name}: sudah presensi", (255, 200, 0)
            elif now < retry_after:
                status, color = "Coba lagi sebentar", (0, 165, 255)
            else:
                if name != candidate:
                    candidate, stable, waiting_since, closed_frames = name, 1, None, 0
                else:
                    stable += 1

                if stable < STABLE_FRAMES:
                    status, color = f"{name}: memverifikasi...", (0, 255, 255)
                else:
                    if waiting_since is None:
                        waiting_since = now
                        closed_frames = 0
                        stage = "blink"
                        turn_target = random.choice(["left", "right"])

                    prompt = "silakan berkedip"
                    passed = False
                    yaw = estimate_yaw(face)

                    if stage == "blink":
                        blinked, closed_frames = detect_blink(face_mesh, frame, face, closed_frames)
                        if blinked:
                            stage = "turn"

                    if stage == "turn":
                        prompt = f"toleh ke {turn_target.upper()} Anda"
                        passed = yaw > YAW_THRESHOLD if turn_target == "left" else yaw < -YAW_THRESHOLD

                    if passed:
                        save_log(name, score)
                        last_success[name] = now
                        message, message_until = f"PRESENSI BERHASIL: {name}", now + 3
                        candidate, stable, waiting_since = None, 0, None
                    elif now - waiting_since > CHALLENGE_TIMEOUT:
                        message, message_until = "Liveness gagal: tantangan tidak selesai", now + 3
                        retry_after = now + 3
                        candidate, stable, waiting_since = None, 0, None
                    else:
                        remaining = CHALLENGE_TIMEOUT - (now - waiting_since)
                        status = f"{name}: {prompt} ({remaining:.0f}s) yaw={yaw:.2f}"
                        color = (0, 255, 255)

        if box is not None:
            x, y, bw, bh = box
            cv2.rectangle(frame, (x, y), (x + bw, y + bh), color, 2)
            cv2.putText(frame, status, (x, max(y - 10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        else:
            cv2.putText(frame, status, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        if now < message_until:
            msg_color = (0, 255, 0) if "BERHASIL" in message else (0, 0, 255)
            cv2.putText(frame, message, (10, h - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, msg_color, 2)

        cv2.imshow("Presensi", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()