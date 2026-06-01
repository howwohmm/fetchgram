"""
fetchgram.corpus
----------------
Build text corpus + training data for one Instagram profile.

Reads:
    <data_dir>/<handle>/raw/          — instaloader dump (*.json, *.jpg, *.txt)
    ocr_dict (optional)               — {basename: text} from fetchgram.ocr

Writes:
    <data_dir>/<handle>/text/corpus.txt
    <data_dir>/<handle>/text/training.jsonl    (one JSON record per line)
    <data_dir>/<handle>/text/posts/<base>.txt  (human-readable per-post file)

Returns:
    list of record dicts (same schema written to training.jsonl)

Record schema
-------------
{
    date:       ISO-8601 str | None,
    shortcode:  str | None,
    url:        str | None,
    likes:      int | None,
    comments:   int | None,
    num_slides: int,
    images:     list[str],      # basenames in slide order
    caption:    str,
    slides:     list[str],      # cleaned OCR text per image
    text:       str,            # merged slides + caption (clean)
}
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

POST_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_UTC")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_ocr_jsonl(path: str) -> dict:
    """Load a legacy {path, text} JSONL file into {basename: text}."""
    result = {}
    if not os.path.exists(path):
        return result
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                result[os.path.basename(obj["path"])] = (obj.get("text") or "").strip()
            except Exception:
                pass
    return result


def _detect_boilerplate(posts: list, threshold: float = 0.15) -> set:
    """
    Lines that appear on >= threshold fraction of all slides are considered
    signature / boilerplate and will be stripped from every post.
    Only lines <= 60 chars are candidates (longer lines are almost always
    unique content).
    """
    line_counter: Counter = Counter()
    total_slides = 0
    for p in posts:
        for slide_text in p["_raw_slides"]:
            total_slides += 1
            for ln in {l.strip() for l in slide_text.splitlines() if l.strip()}:
                line_counter[ln.upper()] += 1

    boiler: set = set()
    if total_slides:
        for ln, count in line_counter.items():
            if count / total_slides >= threshold and len(ln) <= 60:
                boiler.add(ln)
    return boiler


def _clean(text: str, boiler: set) -> str:
    kept = [
        l for l in text.splitlines()
        if l.strip() and l.strip().upper() not in boiler
    ]
    return "\n".join(kept).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_corpus(
    handle: str,
    data_dir: str = "./fetchgram-data",
    ocr_dict: Optional[dict] = None,
) -> list:
    """
    Build corpus, training data, and per-post text files for *handle*.

    Parameters
    ----------
    handle:
        Instagram username (no @).
    data_dir:
        Root output directory; profile lives at <data_dir>/<handle>/.
    ocr_dict:
        Pre-computed {image_basename: ocr_text} mapping.  When None the
        function looks for the sidecar written by the CLI at
        <data_dir>/<handle>/<handle>_ocr.jsonl, then silently proceeds with
        empty OCR (captions only).

    Returns
    -------
    List of record dicts (one per post, newest-first order matching raw/).
    """
    raw_dir = os.path.join(data_dir, handle, "raw")
    text_dir = os.path.join(data_dir, handle, "text")
    posts_dir = os.path.join(text_dir, "posts")
    os.makedirs(posts_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Resolve OCR map
    # ------------------------------------------------------------------
    if ocr_dict is None:
        # cli.py writes the sidecar nested under the handle dir:
        #   <data_dir>/<handle>/<handle>_ocr.jsonl
        sidecar_path = os.path.join(data_dir, handle, f"{handle}_ocr.jsonl")
        ocr_dict = _load_ocr_jsonl(sidecar_path)

    # ------------------------------------------------------------------
    # Enumerate posts from per-post JSON files
    # ------------------------------------------------------------------
    posts = []
    for jf in sorted(glob.glob(os.path.join(raw_dir, "*_UTC.json"))):
        base = os.path.basename(jf)[:-5]   # strip .json
        if not POST_RE.match(base):
            continue
        try:
            with open(jf, encoding="utf-8") as fh:
                node = json.load(fh).get("node", {})
        except Exception:
            node = {}

        # Caption text file (instaloader writes <base>.txt)
        cap_file = os.path.join(raw_dir, base + ".txt")
        caption = ""
        if os.path.exists(cap_file):
            with open(cap_file, encoding="utf-8") as fh:
                caption = fh.read().strip()

        # Slide images — single image or numbered carousel slides
        single = os.path.join(raw_dir, base + ".jpg")
        if os.path.exists(single):
            imgs = [base + ".jpg"]
        else:
            numbered = []
            for f in glob.glob(os.path.join(raw_dir, base + "_*.jpg")):
                m = re.search(r"_(\d+)\.jpg$", f)
                if m:
                    numbered.append((int(m.group(1)), os.path.basename(f)))
            imgs = [bn for _, bn in sorted(numbered)]

        raw_slides = [ocr_dict.get(bn, "") for bn in imgs]

        # Engagement metadata from instaloader node
        likes = (node.get("edge_media_preview_like") or {}).get("count")
        _c = (
            node.get("edge_media_to_comment")
            or node.get("edge_media_to_parent_comment")
            or node.get("comments")
            or {}
        )
        comments = _c.get("count") if isinstance(_c, dict) else _c

        ts = node.get("date") or node.get("taken_at_timestamp")
        dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None

        posts.append({
            # internal (not written to training.jsonl)
            "_raw_slides": raw_slides,
            "_ts": ts,
            # public fields
            "base": base,
            "shortcode": node.get("shortcode"),
            "url": (
                f"https://www.instagram.com/p/{node.get('shortcode')}/"
                if node.get("shortcode") else None
            ),
            "date": dt.isoformat() if dt else None,
            "likes": likes,
            "comments": comments,
            "num_slides": len(imgs),
            "images": imgs,
            "caption": caption,
        })

    # ------------------------------------------------------------------
    # Auto-detect boilerplate / signature lines across all slide OCR
    # ------------------------------------------------------------------
    boiler = _detect_boilerplate(posts)

    # ------------------------------------------------------------------
    # Build cleaned records, per-post .txt, corpus blob, training JSONL
    # ------------------------------------------------------------------
    corpus_parts: list[str] = []
    records: list[dict] = []

    for p in posts:
        clean_slides = [_clean(s, boiler) for s in p["_raw_slides"]]
        clean_slides = [s for s in clean_slides if s]  # drop empty
        clean_caption = p["caption"]

        body = "\n\n".join(clean_slides)
        merged_text = (
            body + ("\n\n" + clean_caption if clean_caption else "")
        ).strip()

        # Per-post readable file
        head = (
            f"# {p['date']}  ·  {p['num_slides']} slide(s)"
            f"  ·  {p['likes']} likes  ·  {p['url']}\n"
        )
        parts = [head]
        if clean_caption:
            parts.append(f"[caption] {clean_caption}")
        for i, slide_text in enumerate(clean_slides, 1):
            parts.append(f"[slide {i}]\n{slide_text}")

        post_file = os.path.join(posts_dir, p["base"] + ".txt")
        with open(post_file, "w", encoding="utf-8") as fh:
            fh.write("\n\n".join(parts) + "\n")

        # Corpus accumulator
        blob = "\n\n".join(
            x for x in [body, f"(caption) {clean_caption}" if clean_caption else ""]
            if x
        )
        if blob.strip():
            corpus_parts.append(
                f"===== {p['date']} | {p['url']} | {p['likes']} likes =====\n{blob}"
            )

        # Training record (public schema — no internal keys)
        record = {
            "date":       p["date"],
            "shortcode":  p["shortcode"],
            "url":        p["url"],
            "likes":      p["likes"],
            "comments":   p["comments"],
            "num_slides": p["num_slides"],
            "images":     p["images"],
            "caption":    clean_caption,
            "slides":     clean_slides,
            "text":       merged_text,
        }
        records.append(record)

    # ------------------------------------------------------------------
    # Write corpus.txt
    # ------------------------------------------------------------------
    corpus_path = os.path.join(text_dir, "corpus.txt")
    with open(corpus_path, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(corpus_parts) + "\n")

    # ------------------------------------------------------------------
    # Write training.jsonl
    # ------------------------------------------------------------------
    jsonl_path = os.path.join(text_dir, "training.jsonl")
    with open(jsonl_path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_slides = sum(p["num_slides"] for p in posts)
    corpus_chars = sum(len(c) for c in corpus_parts)
    print(
        f"[corpus] {handle}: {len(posts)} posts | {n_slides} slides"
        f" | corpus chars={corpus_chars} | boilerplate={sorted(boiler)}",
        file=sys.stderr,
    )

    # Strip internal keys before returning
    for r in records:
        r.pop("_raw_slides", None)
        r.pop("_ts", None)

    # Also clean posts list used internally (not returned)
    for p in posts:
        p.pop("_raw_slides", None)
        p.pop("_ts", None)

    return records
