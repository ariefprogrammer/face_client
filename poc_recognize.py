import json
import time

import cv2
import numpy as np

from poc_common import BASE, create_models, open_camera

DATA_FILE = BASE / "embeddings.json"
COSINE_THRESHOLD = 0.363


def load_gallery():
    raw = json.loads(DATA_FILE.read_text())
    return {
        name: [np.array(s, dtype=np.float32).reshape(1, -1) for s in samples]
        for name, samples in raw.items()
    }


def best_match(recognizer, feature, gallery):
    best_name, best_score = None, -1.0
    for name, samples in gallery.items():
        for s in samples:
            score = recognizer.match(feature, s, cv2.FaceRecognizerSF_FR_COSINE)
            if score > best_score:
                best_name, best_score = name, score
    if best_score >= COSINE_THRESHOLD:
        return best_name, best_score
    return None, best_score


def main():
    gallery = load_gallery()
    cap = open_camera()
    ok, frame = cap.read()
    h, w = frame.shape[:2]
    detector, recognizer = create_models((w, h))
    latencies = []
    print("Q untuk keluar.")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        t0 = time.perf_counter()
        _, faces = detector.detect(frame)
        label, color, ms = "", (0, 255, 0), None

        if faces is not None:
            for f in faces:
                aligned = recognizer.alignCrop(frame, f)
                feature = recognizer.feature(aligned)
                name, score = best_match(recognizer, feature, gallery)
                ms = (time.perf_counter() - t0) * 1000
                latencies.append(ms)

                x, y, bw, bh = f[:4].astype(int)
                color = (0, 255, 0) if name else (0, 0, 255)
                label = f"{name or 'Tidak dikenal'} ({score:.2f}) {ms:.0f} ms"
                cv2.rectangle(frame, (x, y), (x + bw, y + bh), color, 2)
                cv2.putText(frame, label, (x, max(y - 10, 20)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        cv2.imshow("Recognize", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()

    if latencies:
        arr = np.array(latencies)
        print(f"Latensi deteksi+pengenalan: rata-rata {arr.mean():.0f} ms, "
              f"p95 {np.percentile(arr, 95):.0f} ms, maks {arr.max():.0f} ms")


if __name__ == "__main__":
    main()