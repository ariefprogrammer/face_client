import time

import cv2
import mediapipe as mp
import numpy as np

from poc_common import open_camera

RIGHT_EYE = [33, 160, 158, 133, 153, 144]
LEFT_EYE = [362, 385, 387, 263, 373, 380]
EAR_THRESHOLD = 0.21
CONSEC_FRAMES = 2


def ear(p):
    a = np.linalg.norm(p[1] - p[5])
    b = np.linalg.norm(p[2] - p[4])
    c = np.linalg.norm(p[0] - p[3])
    return (a + b) / (2.0 * c)


def to_points(landmarks, idx, w, h):
    return np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in idx])


def main():
    face_mesh = mp.solutions.face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    cap = open_camera()
    blinks = 0
    closed_frames = 0
    started = time.time()
    print("Q = keluar, R = reset hitungan kedipan.")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        h, w = frame.shape[:2]
        t0 = time.perf_counter()
        result = face_mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        ms = (time.perf_counter() - t0) * 1000

        ear_text = "EAR: -"
        if result.multi_face_landmarks:
            lm = result.multi_face_landmarks[0].landmark
            value = (ear(to_points(lm, RIGHT_EYE, w, h)) +
                     ear(to_points(lm, LEFT_EYE, w, h))) / 2
            ear_text = f"EAR: {value:.3f}"

            if value < EAR_THRESHOLD:
                closed_frames += 1
            else:
                if closed_frames >= CONSEC_FRAMES:
                    blinks += 1
                closed_frames = 0

        elapsed = int(time.time() - started)
        cv2.putText(frame, f"{ear_text} | kedipan: {blinks} | {elapsed}s | {ms:.0f} ms",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)
        cv2.imshow("Liveness", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("r"):
            blinks, closed_frames, started = 0, 0, time.time()

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()