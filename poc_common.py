from pathlib import Path

import cv2

BASE = Path(__file__).parent
MODEL_DIR = BASE / "models"
DETECTOR_PATH = str(MODEL_DIR / "face_detection_yunet_2023mar.onnx")
RECOGNIZER_PATH = str(MODEL_DIR / "face_recognition_sface_2021dec.onnx")


def create_models(frame_size, score_threshold=0.8):
    detector = cv2.FaceDetectorYN.create(
        DETECTOR_PATH, "", frame_size, score_threshold, 0.3, 5000
    )
    recognizer = cv2.FaceRecognizerSF.create(RECOGNIZER_PATH, "")
    return detector, recognizer


def open_camera(index=0, width=640, height=480):
    cap = cv2.VideoCapture(index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if not cap.isOpened():
        raise RuntimeError("Kamera tidak bisa dibuka")
    return cap