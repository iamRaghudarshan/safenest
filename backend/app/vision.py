"""Local image understanding: face detection/recognition and CLIP embeddings.

Everything runs in-process on the CPU. Nothing is uploaded anywhere — the whole
point of FinMate is that the photos stay on the machine that owns them, and an
image-understanding feature that phoned home would quietly undo that.

Models live in backend/models and are loaded lazily on first use, because most
requests never touch them and they cost ~190 MB of RAM once resident:

    face_detection_yunet_2023mar.onnx     0.2 MB   finds faces
    face_recognition_sface_2021dec.onnx    39 MB   128-d identity vector
    clip/vision_model_quantized.onnx       85 MB   512-d image vector
    clip/text_model_quantized.onnx         62 MB   512-d text vector

If a model file is absent the corresponding feature reports itself unavailable
rather than raising — a fresh checkout without the models must still run.
"""
import threading

import numpy as np

from .config import BACKEND_DIR

MODELS = BACKEND_DIR / "models"
CLIP_DIR = MODELS / "clip"

YUNET = MODELS / "face_detection_yunet_2023mar.onnx"
SFACE = MODELS / "face_recognition_sface_2021dec.onnx"
CLIP_VISION = CLIP_DIR / "vision_model_quantized.onnx"
CLIP_TEXT = CLIP_DIR / "text_model_quantized.onnx"

# SFace produces 128 floats; CLIP ViT-B/32 produces 512.
FACE_DIM, CLIP_DIM = 128, 512

# CLIP's published preprocessing constants — the model is only accurate on input
# normalised exactly the way it was trained.
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
_SIDE = 224

# Reentrant on purpose: the lazy loaders nest. Building the prompt matrix holds
# this lock and then calls embed_text(), which loads the text model and takes it
# again — a plain Lock deadlocks there, freezing the indexer on the first photo
# that contains a face.
_lock = threading.RLock()
_state: dict = {}


def faces_available() -> bool:
    return YUNET.is_file() and SFACE.is_file()


def clip_available() -> bool:
    return CLIP_VISION.is_file() and CLIP_TEXT.is_file() and (CLIP_DIR / "tokenizer.json").is_file()


def status() -> dict:
    return {"faces": faces_available(), "clip": clip_available()}


# --------------------------------------------------------------------- faces
def _face_models():
    """(detector, recogniser), created once. Guarded because FastAPI serves from a
    thread pool and two concurrent uploads would otherwise both build them."""
    if "face" not in _state:
        with _lock:
            if "face" not in _state:
                import cv2
                det = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.6, 0.3, 5000)
                rec = cv2.FaceRecognizerSF.create(str(SFACE), "")
                _state["face"] = (det, rec)
    return _state["face"]


def detect_faces(bgr: "np.ndarray") -> list[dict]:
    """Every face in one image, as {embedding, bbox, score}.

    `bgr` is an OpenCV-order array (what cv2.imread returns). Returns [] when the
    models are missing or the image has no faces — callers treat faces as a bonus,
    never a requirement for an upload to succeed.
    """
    if not faces_available() or bgr is None or bgr.size == 0:
        return []
    det, rec = _face_models()
    height, width = bgr.shape[:2]
    # YuNet needs the exact frame size before every detect() call.
    det.setInputSize((width, height))
    try:
        _, found = det.detect(bgr)
    except Exception:
        return []
    if found is None:
        return []

    out = []
    for row in found:
        try:
            aligned = rec.alignCrop(bgr, row)
            vec = rec.feature(aligned).ravel().astype(np.float32)
        except Exception:
            continue  # one unreadable face must not lose the others
        x, y, w, h = (int(v) for v in row[:4])
        out.append({
            "embedding": vec,
            "bbox": [x, y, w, h],
            "score": float(row[14]) if len(row) > 14 else None,
        })
    return out


def cosine(a, b) -> float:
    """Similarity of two vectors; 0 when either is empty or degenerate."""
    a = np.asarray(a, np.float32).ravel()
    b = np.asarray(b, np.float32).ravel()
    if a.size == 0 or a.size != b.size:
        return 0.0
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


# ---------------------------------------------------------------------- CLIP
def _clip_vision():
    if "clip_v" not in _state:
        with _lock:
            if "clip_v" not in _state:
                import onnxruntime as ort
                _state["clip_v"] = ort.InferenceSession(
                    str(CLIP_VISION), providers=["CPUExecutionProvider"])
    return _state["clip_v"]


def _clip_text():
    if "clip_t" not in _state:
        with _lock:
            if "clip_t" not in _state:
                import onnxruntime as ort
                from transformers import CLIPTokenizerFast
                _state["clip_t"] = (
                    ort.InferenceSession(str(CLIP_TEXT), providers=["CPUExecutionProvider"]),
                    CLIPTokenizerFast.from_pretrained(str(CLIP_DIR)),
                )
    return _state["clip_t"]


def _preprocess(pil) -> "np.ndarray":
    """Resize short side to 224, centre-crop, normalise — CLIP's exact recipe."""
    image = pil.convert("RGB")
    w, h = image.size
    scale = _SIDE / min(w, h)
    image = image.resize((max(_SIDE, round(w * scale)), max(_SIDE, round(h * scale))))
    w, h = image.size
    left, top = (w - _SIDE) // 2, (h - _SIDE) // 2
    arr = np.asarray(image.crop((left, top, left + _SIDE, top + _SIDE)), np.float32) / 255.0
    return ((arr - _MEAN) / _STD).transpose(2, 0, 1)[None]


def embed_image(pil) -> "np.ndarray | None":
    """512-d unit vector describing what is in the picture, or None."""
    if not clip_available():
        return None
    try:
        raw = _clip_vision().run(None, {"pixel_values": _preprocess(pil)})[0]
    except Exception:
        return None
    return _unit(raw.ravel().astype(np.float32))


def embed_text(query: str) -> "np.ndarray | None":
    """512-d unit vector in the SAME space as embed_image, so a dot product between
    them is a relevance score."""
    if not clip_available() or not (query or "").strip():
        return None
    session, tokeniser = _clip_text()
    try:
        ids = tokeniser([query.strip()], padding="max_length", max_length=77,
                        truncation=True, return_tensors="np")["input_ids"].astype(np.int64)
        raw = session.run(None, {"input_ids": ids})[0]
    except Exception:
        return None
    return _unit(raw.ravel().astype(np.float32))


# Zero-shot check for "is this a picture OF someone, or a picture of a thing that
# happens to contain a face?" — a scanned ID card, a screenshot of a chat, a poster.
# Those faces must not become People: the ID photo on a driving licence is not a
# person you took a photo of.
_PERSON_PROMPTS = [
    "a photo of a person", "a portrait photograph of a person",
    "a candid snapshot of people", "a selfie",
]
_DOCUMENT_PROMPTS = [
    "a screenshot of a phone or computer screen", "a scanned paper document",
    "a page of printed text", "an identity card or licence card",
    "a receipt or an invoice", "a chart or a diagram",
]
# Measured, not guessed: over 40 real portraits the person-minus-document margin
# never fell below -0.011, while synthetic documents, ID cards, screenshots and
# receipts all sat at -0.024 or lower. -0.02 is the gap between those two, and it
# errs towards keeping people — wrongly dropping a real face is worse than letting
# the odd document through.
DOCUMENT_MARGIN = -0.02


def _prompt_matrix():
    if "prompts" not in _state:
        with _lock:
            if "prompts" not in _state:
                _state["prompts"] = (
                    np.array([embed_text(t) for t in _PERSON_PROMPTS]),
                    np.array([embed_text(t) for t in _DOCUMENT_PROMPTS]),
                )
    return _state["prompts"]


def looks_like_document(image_vec: "np.ndarray | None") -> bool:
    """True when the picture is a document/screenshot rather than a photo of people.

    Takes the CLIP vector the indexer already computed, so this costs one small
    matrix multiply rather than another model run.
    """
    if image_vec is None or not clip_available():
        return False
    try:
        person, document = _prompt_matrix()
        margin = float((person @ image_vec).max()) - float((document @ image_vec).max())
    except Exception:
        return False
    return margin < DOCUMENT_MARGIN


def _unit(vec: "np.ndarray") -> "np.ndarray":
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


# Vectors are stored as float16: half the bytes for a similarity difference far
# below what any ranking would notice (5,000 photos ≈ 5 MB instead of 10 MB).
def pack(vec: "np.ndarray") -> bytes:
    return np.asarray(vec, np.float16).tobytes()


def unpack(blob: bytes, dim: int = CLIP_DIM) -> "np.ndarray":
    arr = np.frombuffer(blob, np.float16).astype(np.float32)
    return arr if arr.size == dim else np.zeros(dim, np.float32)
