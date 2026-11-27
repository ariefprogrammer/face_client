import json
import sys

import cv2

from poc_common import BASE, create_models, open_camera

DATA_FILE = BASE / "embeddings.json"
SAMPLES = 5


def main():
    if len(sys.argv) < 2:
        print('Pemakaian: python poc_enroll.py "Nama"')
        return

    name = sys.argv[1]
    cap = open_camera()
    ok, frame = cap.read()
    if not ok:
        print("Gagal membaca kamera")
        return

    h, w = frame.shape[:2]
    detector, recognizer = create_models((w, h))
    samples = []
    print(f"Hadap kamera. SPASI = ambil sampel ({SAMPLES} kali), Q = batal.")

    while len(samples) < SAMPLES:
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

        cv2.putText(view, f"Sampel {len(samples)}/{SAMPLES} | wajah: {count}",
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

    if len(samples) == SAMPLES:
        data = json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else {}
        data[name] = samples
        DATA_FILE.write_text(json.dumps(data))
        print(f"Enrollment {name} selesai")


if __name__ == "__main__":
    main()