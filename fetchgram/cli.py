"""
fetchgram.cli
-------------
Entry-point for the ``fetchgram`` command.

Subcommands
-----------
  analyze <handle>   full pipeline: scrape -> ocr -> corpus -> metrics
  scrape  <handle>   download only
  ocr     <handle>   OCR existing raw images (writes sidecar JSONL)
  metrics <handle>   recompute era-adjusted signal from existing corpus

OCR sidecar location (consistent across all sub-commands):
  <data_dir>/<handle>/<handle>_ocr.jsonl

This matches the legacy path that corpus.py's _load_ocr_jsonl() picks up
automatically when ocr_dict is not passed explicitly.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_version() -> str:
    try:
        from fetchgram import __version__
        return __version__
    except Exception:
        return "unknown"


def _ocr_sidecar_path(data_dir: str, handle: str) -> str:
    """Canonical path for the OCR sidecar file consumed by build_corpus."""
    return os.path.join(data_dir, handle, f"{handle}_ocr.jsonl")


def _common_args(p: argparse.ArgumentParser) -> None:
    """Add shared positional + output flag to a subcommand parser."""
    p.add_argument("handle", help="Instagram username (without @)")
    p.add_argument(
        "--out", metavar="DIR", default="./fetchgram-data",
        help="Root data directory (default: ./fetchgram-data)",
    )


def _scrape_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--login", metavar="USER", default=None,
        help="Instagram username to log in as (uses saved instaloader session)",
    )
    p.add_argument(
        "--count", metavar="N", type=int, default=None,
        help="Maximum number of posts to download",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Ignore an existing .download.done marker and re-download",
    )


def _ocr_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--ocr",
        choices=["auto", "vision", "tesseract", "none"],
        default="auto",
        help=(
            "OCR engine: "
            "auto=Vision (macOS+swiftc) → tesseract → none, "
            "vision=force Apple Vision (macOS only), "
            "tesseract=pytesseract, "
            "none=skip OCR  (captions still captured)"
        ),
    )


def _collect_images(raw_dir: str) -> list:
    """Return sorted list of .jpg paths in raw_dir, excluding profile pics."""
    return sorted(
        p for p in glob.glob(os.path.join(raw_dir, "*.jpg"))
        if "profile_pic" not in os.path.basename(p)
    )


def _write_ocr_sidecar(results: dict, sidecar_path: str) -> None:
    """Write {path, text} JSONL sidecar so corpus.py can load it."""
    os.makedirs(os.path.dirname(sidecar_path), exist_ok=True)
    with open(sidecar_path, "w", encoding="utf-8") as fh:
        for path, text in results.items():
            fh.write(json.dumps({"path": path, "text": text}, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _scrape_error(exc: Exception, handle: str) -> None:
    """Print a friendly, actionable message for an instaloader failure."""
    print(
        f"[scrape] couldn't fetch @{handle}: {exc}\n"
        f"  common causes & fixes:\n"
        f"   • rate-limited IP — Instagram throttles heavy use. wait a while, or fetch fewer with --count.\n"
        f"   • expired/missing session — log in once, then pass it:\n"
        f"       instaloader -l <your_ig_username>\n"
        f"       fetchgram scrape {handle} --login <your_ig_username>\n"
        f"   • the profile is private, renamed, or doesn't exist.",
        file=sys.stderr,
    )


def _cmd_scrape(args: argparse.Namespace) -> None:
    from fetchgram.scrape import scrape_profile

    try:
        raw_dir = scrape_profile(
            handle=args.handle,
            data_dir=args.out,
            login=args.login,
            count=args.count,
            force=getattr(args, "force", False),
        )
    except Exception as exc:  # noqa: BLE001 — surface a clean message, not a traceback
        _scrape_error(exc, args.handle)
        sys.exit(1)
    print(f"[scrape] done → {raw_dir}")


def _cmd_ocr(args: argparse.Namespace) -> None:
    """OCR all images in an existing raw directory; write sidecar JSONL."""
    from fetchgram.ocr import ocr_images

    raw_dir = os.path.join(args.out, args.handle, "raw")
    if not os.path.isdir(raw_dir):
        print(
            f"[ocr] error: raw directory not found: {raw_dir}\n"
            f"       run: fetchgram scrape {args.handle} --out {args.out}",
            file=sys.stderr,
        )
        sys.exit(1)

    image_paths = _collect_images(raw_dir)
    if not image_paths:
        print(f"[ocr] no .jpg files found in {raw_dir}", file=sys.stderr)
        sys.exit(1)

    engine = args.ocr
    print(f"[ocr] engine={engine}  images={len(image_paths)}", file=sys.stderr)
    results = ocr_images(image_paths, engine=engine)

    sidecar = _ocr_sidecar_path(args.out, args.handle)
    _write_ocr_sidecar(results, sidecar)

    hits = sum(1 for t in results.values() if t.strip())
    print(f"[ocr] done — {hits}/{len(results)} images had text → {sidecar}")


def _cmd_metrics(args: argparse.Namespace) -> None:
    """Recompute era-adjusted signal from an existing training.jsonl."""
    from fetchgram.metrics import era_signal

    try:
        result = era_signal(handle=args.handle, data_dir=args.out)
    except FileNotFoundError as exc:
        print(
            f"[metrics] error: {exc}\n"
            f"          run: fetchgram analyze {args.handle} --out {args.out}",
            file=sys.stderr,
        )
        sys.exit(1)
    except ValueError as exc:
        print(f"[metrics] error: {exc}", file=sys.stderr)
        sys.exit(1)

    signal_dir = os.path.join(args.out, args.handle, "signal")
    print(f"[metrics] done → {signal_dir}/")
    print(
        f"[metrics] analyzed {result.get('n_analyzed')}/{result.get('n_all')} posts"
        f"  (dropped <{result.get('min_age_days', 21)}d old)"
    )
    val = result.get("validation", {})
    print(
        f"[metrics] confound: spearman(age,likes)={val.get('spearman_age_likes')}  "
        f"→  spearman(age,era)={val.get('spearman_age_era')}  (≈0 = removed)"
    )
    ct = result.get("by_content_type", {})
    if ct:
        print("[metrics] content type (era lift):")
        for k, d in ct.items():
            print(f"  {k:10} n={d['n']:<4}  era {d['era_lift']}×  p90 {d['era_p90']}×")


def _cmd_analyze(args: argparse.Namespace) -> None:
    """Full pipeline: scrape -> OCR -> corpus -> era-signal."""
    import time
    from fetchgram.scrape import scrape_profile
    from fetchgram.ocr import ocr_images
    from fetchgram.corpus import build_corpus
    from fetchgram.metrics import era_signal

    t0 = time.monotonic()
    handle = args.handle
    data_dir = args.out
    engine = getattr(args, "ocr", "auto")

    print(f"fetchgram {_get_version()} — analyzing @{handle}")
    print(f"  data dir  : {os.path.abspath(data_dir)}")
    print(f"  ocr engine: {engine}")

    # ── 1. scrape ────────────────────────────────────────────────────────────
    print("\n[1/4] scraping …", flush=True)
    try:
        raw_dir = scrape_profile(
            handle=handle,
            data_dir=data_dir,
            login=getattr(args, "login", None),
            count=getattr(args, "count", None),
            force=getattr(args, "force", False),
        )
    except Exception as exc:
        _scrape_error(exc, handle)
        sys.exit(1)
    print(f"[1/4] done → {raw_dir}")

    # ── 2. OCR ───────────────────────────────────────────────────────────────
    print("\n[2/4] OCR …", flush=True)
    image_paths = _collect_images(raw_dir)
    print(f"[2/4] {len(image_paths)} image(s) found")

    ocr_results: dict = {}
    if image_paths and engine != "none":
        try:
            ocr_results = ocr_images(image_paths, engine=engine)
        except Exception as exc:
            print(
                f"[2/4] OCR error (continuing without slide text): {exc}",
                file=sys.stderr,
            )
    elif engine == "none":
        print("[2/4] skipped (--ocr none)")
    else:
        print("[2/4] no images — skipping OCR")

    # Write sidecar so `fetchgram metrics` can be re-run later without re-OCR
    sidecar = _ocr_sidecar_path(data_dir, handle)
    _write_ocr_sidecar(ocr_results, sidecar)
    hits = sum(1 for t in ocr_results.values() if t.strip())
    print(f"[2/4] done — {hits}/{len(ocr_results)} images had text")

    # Convert full-path keys to basenames for in-memory corpus pass
    ocr_dict = {os.path.basename(p): t for p, t in ocr_results.items()}

    # ── 3. corpus ────────────────────────────────────────────────────────────
    print("\n[3/4] building corpus …", flush=True)
    try:
        records = build_corpus(handle=handle, data_dir=data_dir, ocr_dict=ocr_dict)
    except Exception as exc:
        print(f"[3/4] corpus build failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"[3/4] done — {len(records)} posts")

    # ── 4. metrics ───────────────────────────────────────────────────────────
    print("\n[4/4] era-adjusted signal …", flush=True)
    result: dict = {}
    try:
        result = era_signal(handle=handle, data_dir=data_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[4/4] metrics skipped — {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"[4/4] metrics error (non-fatal): {exc}", file=sys.stderr)

    # ── summary ──────────────────────────────────────────────────────────────
    elapsed = time.monotonic() - t0
    handle_dir = os.path.abspath(os.path.join(data_dir, handle))
    print(f"\n{'─' * 52}")
    print(f"  @{handle}  ·  {elapsed:.0f}s")
    print(f"  posts          : {len(records)}")

    if result:
        n_a = result.get("n_analyzed", 0)
        n_all = result.get("n_all", 0)
        v = result.get("validation", {})
        print(f"  era-analyzed   : {n_a}/{n_all}")
        print(
            f"  confound       : spearman(age,likes) {v.get('spearman_age_likes')}"
            f"  →  {v.get('spearman_age_era')} after era-norm"
        )
        ct = result.get("by_content_type", {})
        if ct:
            best_k, best_d = max(ct.items(), key=lambda kv: kv[1]["era_lift"])
            print(f"  best format    : {best_k}  {best_d['era_lift']}× era  (n={best_d['n']})")
        hv = result.get("hit_vocab", [])
        if hv:
            print(f"  hit vocab      : {', '.join(hv[:8])}")

    print(f"\n  outputs (all under {handle_dir}/):")
    print(f"    raw/               instaloader dump")
    print(f"    text/corpus.txt    full text corpus")
    print(f"    text/training.jsonl  training-ready JSONL")
    if result:
        print(f"    signal/metrics.json  all numbers")
        print(f"    signal/SIGNAL.md     human-readable report")
    print()


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> None:
    """CLI entry-point registered in pyproject.toml as ``fetchgram``."""
    parser = argparse.ArgumentParser(
        prog="fetchgram",
        description=(
            "fetchgram — era-adjusted Instagram content intelligence.\n\n"
            "Scrape any public profile, OCR every image, measure what actually works.\n"
            "Data lands in <out>/<handle>/{raw/, text/, signal/}."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  fetchgram analyze someaccount\n"
            "  fetchgram analyze someaccount --login myusername --count 200\n"
            "  fetchgram analyze someaccount --ocr tesseract\n"
            "  fetchgram scrape  someaccount --login myusername\n"
            "  fetchgram ocr     someaccount\n"
            "  fetchgram metrics someaccount\n"
        ),
    )
    parser.add_argument(
        "--version", action="version",
        version=f"%(prog)s {_get_version()}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True

    # --- analyze (recommended entry-point) ---
    p_analyze = subparsers.add_parser(
        "analyze",
        help="full pipeline: scrape → OCR → corpus → era-signal  (recommended)",
        description=(
            "Run all four pipeline steps for a profile:\n"
            "  1. scrape   — download posts via instaloader\n"
            "  2. ocr      — extract text from slide images\n"
            "  3. corpus   — build text corpus + training.jsonl\n"
            "  4. metrics  — era-adjusted engagement signal\n\n"
            "All outputs land under <out>/<handle>/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _common_args(p_analyze)
    _scrape_args(p_analyze)
    _ocr_args(p_analyze)
    p_analyze.set_defaults(func=_cmd_analyze)

    # --- scrape ---
    p_scrape = subparsers.add_parser(
        "scrape",
        help="download posts only (no OCR / corpus / metrics)",
        description="Download posts for a profile using instaloader.\nOutput: <out>/<handle>/raw/",
    )
    _common_args(p_scrape)
    _scrape_args(p_scrape)
    p_scrape.set_defaults(func=_cmd_scrape)

    # --- ocr ---
    p_ocr = subparsers.add_parser(
        "ocr",
        help="OCR images in an existing raw directory",
        description=(
            "Run OCR on images already downloaded by `fetchgram scrape`.\n"
            "Requires: <out>/<handle>/raw/ exists.\n"
            "Output  : <out>/<handle>/<handle>_ocr.jsonl"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _common_args(p_ocr)
    _ocr_args(p_ocr)
    p_ocr.set_defaults(func=_cmd_ocr)

    # --- metrics ---
    p_metrics = subparsers.add_parser(
        "metrics",
        help="recompute era-adjusted signal from an existing corpus",
        description=(
            "Recompute era-adjusted engagement metrics from an existing corpus.\n"
            "Requires: <out>/<handle>/text/training.jsonl exists.\n"
            "Output  : <out>/<handle>/signal/metrics.json\n"
            "          <out>/<handle>/signal/SIGNAL.md"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _common_args(p_metrics)
    p_metrics.set_defaults(func=_cmd_metrics)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
