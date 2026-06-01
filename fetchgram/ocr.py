"""
fetchgram/ocr.py
----------------
Cross-platform OCR engine with three backends:

  vision     – macOS only; compiles the bundled Swift binary once and caches it
               at ~/.cache/fetchgram/ocr_vision.  Reads paths from stdin, emits
               {"path": ..., "text": ...} JSONL.

  tesseract  – anywhere pytesseract + tesseract-ocr are installed.
               Images are downscaled to ≤1000 px on the longest side before
               recognition (speeds things up, has no accuracy cost on
               typical IG slides).

  none       – no binary available; returns empty text for every path.
               A clear note is printed so the caller knows why.

engine="auto" selects:  vision  (macOS + swiftc)
                         → tesseract  (pytesseract importable)
                         → none

Public API
----------
ocr_images(paths: list[str], engine: str = "auto") -> dict[str, str]
    Returns {absolute_path: recognised_text}.  Keys match the input paths
    exactly (no normalisation), values are "" when recognition fails or the
    engine is "none".
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_DIR = Path.home() / ".cache" / "fetchgram"
_SWIFT_SRC = Path(__file__).parent / "_vision" / "ocr_vision.swift"
_SWIFT_BIN = _CACHE_DIR / "ocr_vision"
_MAX_DIM = 1000  # pixels — longest side cap for tesseract path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _swiftc_available() -> bool:
    return shutil.which("swiftc") is not None


def _ensure_vision_binary() -> Path:
    """Compile the bundled Swift source once; return path to binary.

    Raises RuntimeError if compilation fails.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if _SWIFT_BIN.exists():
        return _SWIFT_BIN

    if not (_is_macos() and _swiftc_available()):
        raise RuntimeError(
            "Vision OCR requires macOS + swiftc; "
            "use --ocr tesseract or --ocr none"
        )

    if not _SWIFT_SRC.exists():
        raise RuntimeError(
            f"fetchgram: Swift source not found at {_SWIFT_SRC}. "
            "The package may be incomplete."
        )

    print(
        f"fetchgram: compiling macOS Vision OCR binary (one-time) → {_SWIFT_BIN}",
        file=sys.stderr,
    )
    result = subprocess.run(
        ["swiftc", str(_SWIFT_SRC), "-o", str(_SWIFT_BIN)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"fetchgram: swiftc compilation failed:\n{result.stderr}"
        )
    return _SWIFT_BIN


def _ocr_vision(paths: List[str]) -> Dict[str, str]:
    """Run the macOS Vision binary on *paths*.  Returns {path: text}."""
    try:
        binary = _ensure_vision_binary()
    except (RuntimeError, OSError) as exc:
        print(f"fetchgram: Vision OCR unavailable — {exc}", file=sys.stderr)
        return {p: "" for p in paths}

    stdin_data = "\n".join(paths) + "\n"
    try:
        proc = subprocess.run(
            [str(binary)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min hard cap
        )
    except subprocess.TimeoutExpired:
        print("fetchgram: Vision OCR timed out", file=sys.stderr)
        return {p: "" for p in paths}
    except Exception as exc:  # noqa: BLE001
        print(f"fetchgram: Vision OCR error — {exc}", file=sys.stderr)
        return {p: "" for p in paths}

    results: Dict[str, str] = {p: "" for p in paths}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            results[obj["path"]] = obj.get("text", "")
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def _resize_for_tesseract(img):
    """Return a (possibly downscaled) PIL Image with longest side ≤ _MAX_DIM."""
    w, h = img.size
    longest = max(w, h)
    if longest <= _MAX_DIM:
        return img
    scale = _MAX_DIM / longest
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    # LANCZOS for downscaling quality; fall back to BICUBIC for older Pillow
    try:
        from PIL.Image import Resampling
        return img.resize(new_size, Resampling.LANCZOS)
    except ImportError:
        return img.resize(new_size)


def _ocr_tesseract(paths: List[str]) -> Dict[str, str]:
    """Run pytesseract on each image.  Returns {path: text}."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        print(
            "fetchgram: pytesseract or Pillow not installed — "
            "run `pip install pytesseract pillow` and install tesseract-ocr.",
            file=sys.stderr,
        )
        return {p: "" for p in paths}

    results: Dict[str, str] = {}
    for path in paths:
        try:
            img = Image.open(path).convert("RGB")
            img = _resize_for_tesseract(img)
            text = pytesseract.image_to_string(img)
            results[path] = text.strip()
        except Exception as exc:  # noqa: BLE001
            print(f"fetchgram: tesseract failed on {path} — {exc}", file=sys.stderr)
            results[path] = ""
    return results


def _resolve_engine(engine: str) -> str:
    """Resolve 'auto' to a concrete engine name."""
    if engine != "auto":
        return engine

    if _is_macos() and _swiftc_available():
        return "vision"

    # Check if pytesseract is importable without actually importing (fast check)
    try:
        import importlib.util
        spec = importlib.util.find_spec("pytesseract")
        if spec is not None:
            return "tesseract"
    except Exception:  # noqa: BLE001
        pass

    return "none"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ocr_images(paths: List[str], engine: str = "auto") -> Dict[str, str]:
    """OCR a list of image file paths.

    Parameters
    ----------
    paths:
        Absolute or relative paths to image files.
    engine:
        One of "auto", "vision", "tesseract", "none".
        "auto" selects the best available engine at runtime.

    Returns
    -------
    dict mapping each input path (unchanged) to its recognised text string.
    Empty string if recognition failed or engine is "none".
    """
    if not paths:
        return {}

    resolved = _resolve_engine(engine)

    if resolved == "vision":
        return _ocr_vision(paths)

    if resolved == "tesseract":
        return _ocr_tesseract(paths)

    # engine == "none" (or unknown value)
    if engine not in ("none", "auto"):
        print(
            f"fetchgram: unknown OCR engine '{engine}'; falling back to none.",
            file=sys.stderr,
        )
    else:
        print(
            "fetchgram: no OCR engine available (install tesseract+pytesseract on "
            "Linux/Windows, or run on macOS with Xcode tools for Vision). "
            "Text fields will be empty — captions still captured.",
            file=sys.stderr,
        )
    return {p: "" for p in paths}
