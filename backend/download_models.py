"""Fetch the on-device vision models.

The app works without these — the Gallery simply won't group faces or search by
content — so they are downloaded separately rather than being a hard dependency of
starting the app. About 190 MB in total, needed once:

    face_detection_yunet_2023mar.onnx     0.2 MB  OpenCV Zoo, finds faces
    face_recognition_sface_2021dec.onnx    39 MB  OpenCV Zoo, identity vector
    clip/vision_model_quantized.onnx       85 MB  CLIP ViT-B/32 image encoder
    clip/text_model_quantized.onnx         62 MB  CLIP ViT-B/32 text encoder
    clip/tokenizer*.json, vocab, merges     3 MB  CLIP tokeniser

    python download_models.py            # get whatever is missing
    python download_models.py --force    # re-download everything

Already-present files are skipped, so re-running is cheap and safe.
"""
import sys
import urllib.request
from pathlib import Path

MODELS = Path(__file__).resolve().parent / "models"

# The exact bytes of every model this app runs, recorded from the copies that are
# in use and known to give correct results.
#
# Both sources are moving targets: opencv_zoo is fetched from `main`, and the
# CLIP repo has no pinned revision, so "the same URL" can return different
# content tomorrow. These files are not data — they are executed by onnxruntime.
# Verifying the hash makes what arrives independent of what the server chose to
# send, which is stronger than pinning a revision and covers both sources.
CHECKSUMS = {
    "clip/merges.txt":
        "9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a",
    "clip/preprocessor_config.json":
        "6f638fb9401a6d6296feff533ee7efe657b787c49f954f82f5906b36ef2a1b1f",
    "clip/text_model_quantized.onnx":
        "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a",
    "clip/tokenizer.json":
        "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd",
    "clip/tokenizer_config.json":
        "60ba2912bc6344c94bc16bbdec27fa1209409167b6f2fdf3cfe9e65462ea3967",
    "clip/vision_model_quantized.onnx":
        "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299",
    "clip/vocab.json":
        "5047b556ce86ccaf6aa22b3ffccfc52d391ea4accdab9c2f2407da5b742d4363",
    "face_detection_yunet_2023mar.onnx":
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    "face_recognition_sface_2021dec.onnx":
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
}


def verify(path, key: str) -> bool:
    """Is this file exactly the model we expect? Unknown files are allowed through
    (a new model added later should not brick the download), but a KNOWN file that
    fails its hash is deleted rather than used."""
    import hashlib
    want = CHECKSUMS.get(key)
    if not want:
        return True
    try:
        got = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return False
    if got == want:
        return True
    print(f"    CHECKSUM MISMATCH for {key}")
    print(f"      expected {want}")
    print(f"      got      {got}")
    try:
        path.unlink()
    except OSError:
        pass
    return False


CLIP = MODELS / "clip"

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
FACE_FILES = [
    (MODELS / "face_detection_yunet_2023mar.onnx",
     f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx"),
    (MODELS / "face_recognition_sface_2021dec.onnx",
     f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx"),
]
CLIP_REPO = "Xenova/clip-vit-base-patch32"
CLIP_FILES = [
    "onnx/vision_model_quantized.onnx",
    "onnx/text_model_quantized.onnx",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
]


def _get(url: str, dest: Path, force: bool) -> bool:
    if dest.exists() and not force:
        print(f"  have    {dest.name}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  getting {dest.name} …", end="", flush=True)
    try:
        # .part first, so an interrupted download is never mistaken for a good file.
        part = dest.with_suffix(dest.suffix + ".part")
        if not url.lower().startswith("https://"):
            raise ValueError("refusing a non-HTTPS model URL")
        urllib.request.urlretrieve(url, part)
        part.replace(dest)
        if not verify(dest, dest.name):
            print(" REJECTED (checksum)")
            return False
    except Exception as exc:
        print(f" FAILED ({type(exc).__name__})")
        return False
    print(f" {dest.stat().st_size / 1048576:.1f} MB")
    return True


def main() -> int:
    force = "--force" in sys.argv
    ok = True

    print("\nFace grouping (OpenCV Zoo)")
    for dest, url in FACE_FILES:
        ok &= _get(url, dest, force)

    print("\nSearch by content (CLIP)")
    try:
        from huggingface_hub import hf_hub_download
        import shutil
        for name in CLIP_FILES:
            dest = CLIP / Path(name).name
            if dest.exists() and not force:
                print(f"  have    {dest.name}")
                continue
            print(f"  getting {dest.name} …", end="", flush=True)
            try:
                cached = hf_hub_download(CLIP_REPO, name)
                CLIP.mkdir(parents=True, exist_ok=True)
                shutil.copy(cached, dest)
                if not verify(dest, f"clip/{dest.name}"):
                    print(" REJECTED (checksum)")
                    ok = False
                    continue
                print(f" {dest.stat().st_size / 1048576:.1f} MB")
            except Exception as exc:
                print(f" FAILED ({type(exc).__name__})")
                ok = False
    except ImportError:
        print("  huggingface_hub is not installed — run: pip install -r requirements.txt")
        ok = False

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from app import vision
    state = vision.status()
    print(f"\nface grouping     : {'ready' if state['faces'] else 'NOT available'}")
    print(f"search by content : {'ready' if state['clip'] else 'NOT available'}")
    if not ok:
        print("\nSome downloads failed. The app still runs; those features stay off.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
