"""
fetchgram.metrics
~~~~~~~~~~~~~~~~~
Era-adjusted signal engine — the crown jewel.

Public API
----------
era_signal(handle: str, data_dir: str) -> dict

Reads:   <data_dir>/<handle>/text/training.jsonl
Writes:  <data_dir>/<handle>/signal/metrics.json
         <data_dir>/<handle>/signal/SIGNAL.md

Algorithm (ported faithfully from instagram-archive/tools/research_signal.py):
  - era_score(post) = likes / median(likes of posts within ±45 days of that post,
    widening window to 90/180/400 days until at least MIN_WIN=10 neighbours).
  - Posts younger than MIN_AGE=21 days are excluded from the analysis set (still
    accumulating — not flops).
  - Every group dimension is gated at n≥MIN_N=20.
  - Vocabulary: Monroe-style z log-odds comparing top-tercile era posts (hits) vs
    bottom-tercile (flops); cleaned of stopwords, brand-ish tokens, and calendar
    words; minimum combined frequency 10.
  - Supplementary stats from research_metrics.py: gini, spearman of raw features
    vs likes, content-format classifier, best opening words.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_AGE = 21          # days — drop posts still accumulating
WIN = 45              # era window: ±days (base)
MIN_WIN = 10          # widen until this many neighbours
MIN_N = 20            # minimum group size to report

# Generic stopwords (verbatim from research_signal.py)
STOP = set(
    "the a an and or but to of in on for with at by from is are was were be been "
    "being this that these those you your i me my we our it its he she they them "
    "his her their as so if then than too very just not no yes do does did have has "
    "had will would can could should may might must up out off over under again more "
    "most some any all each one two get got go going make made like know see want "
    "need new now here there what when where why how who which while because about "
    "into onto upon per via vs s t re ve ll m d don t isn aren wasn didn doesn "
    "won t i'm it's don't you're that's u ur".split()
)

# Calendar tokens to strip from vocabulary
CAL = set(
    "january february march april may june july august september october november "
    "december jan feb mar apr jun jul aug sep sept oct nov dec monday tuesday "
    "wednesday thursday friday saturday sunday mon tue wed thu fri sat sun".split()
)

# Emoji pattern (for counting / stripping)
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)

# ---------------------------------------------------------------------------
# Pure math helpers (no external deps)
# ---------------------------------------------------------------------------

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _weekday(dt: datetime) -> str:
    """Locale-independent 3-letter weekday name."""
    return _WEEKDAYS[dt.weekday()]


def _med(values: list[float]) -> float:
    """Median of a non-empty list."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _pctl(values: list[float], q: float) -> float:
    """q-quantile (0–1) of a non-empty list."""
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * q))]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((x - m) ** 2 for x in values) / len(values))


def _gini(values: list[float]) -> float | None:
    """Gini coefficient of attention (0=even, 1=winner-take-all)."""
    a = sorted(x for x in values if x >= 0)
    n = len(a)
    if n == 0 or sum(a) == 0:
        return None
    cum = sum((i + 1) * v for i, v in enumerate(a))
    return round((2 * cum) / (n * sum(a)) - (n + 1) / n, 3)


def _spearman(x: list[float], y: list[float]) -> float | None:
    """Spearman rank correlation (handles ties). Returns None if n<3."""
    n = len(x)
    if n < 3:
        return None

    def _rank(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r: list[float] = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = _rank(x), _rank(y)
    mx, my = _mean(rx), _mean(ry)
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = math.sqrt(
        sum((rx[i] - mx) ** 2 for i in range(n))
        * sum((ry[i] - my) ** 2 for i in range(n))
    )
    return round(num / den, 3) if den else None


# ---------------------------------------------------------------------------
# Content-format classifier (from research_metrics.py)
# ---------------------------------------------------------------------------

def _classify(text: str, hook: str, nslides: int, cap_len: int, wc: int) -> str:
    """Classify a post into a content-format bucket."""
    t = text
    if (
        re.search(
            r"\b\d+\s+(things|ways|lessons|sentences|rules|signs|habits|reasons|"
            r"steps|mistakes|tips|quotes|laws|truths|principles|questions|books|"
            r"traits|lies|secrets)\b",
            t,
            re.I,
        )
        or len(re.findall(r"(?m)^\s*\d+[\.\):]", t)) >= 3
    ):
        return "listicle"
    if re.search(
        r"\b(they say|they tell you|everyone (says|thinks)|nobody tells|no one "
        r"tells|stop (doing|being)|unpopular|the myth|the lie|actually,|truth is|"
        r"biggest mistake|wrong about)\b",
        t,
        re.I,
    ):
        return "contrarian"
    h = hook.strip().lower()
    if h.endswith("?") or re.match(
        r"(why|how|what|when|should|are you|do you|did you|can you|would you|"
        r"is it|have you)\b",
        h,
    ):
        return "question"
    if cap_len > 450 and nslides <= 2:
        return "narrative"
    if nslides <= 1 and wc <= 12:
        return "single_line"
    if ('"' in t or "“" in t or "—" in t) and wc <= 40:
        return "quote"
    return "statement"


# ---------------------------------------------------------------------------
# Load + enrich records
# ---------------------------------------------------------------------------

def _load_records(jsonl_path: str) -> list[dict]:
    """Load training.jsonl; attach dt, age, ts fields. Skip malformed rows."""
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    rows: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(r.get("likes"), int):
                continue
            if not r.get("date"):
                continue
            try:
                dt = datetime.fromisoformat(r["date"]).replace(tzinfo=None)
            except (ValueError, TypeError):
                continue
            r["dt"] = dt
            r["age"] = (now - dt).days
            r["ts"] = dt.timestamp()
            rows.append(r)
    return rows


# ---------------------------------------------------------------------------
# Era normalisation (exact port from research_signal.py)
# ---------------------------------------------------------------------------

def _era_normalize(rows: list[dict]) -> None:
    """
    Annotate every post with:
      era_base  — median likes of temporal neighbours
      era       — likes / era_base  (≈1.0 = in-line with contemporaries)

    Window starts at ±WIN days, widens to 90/180/400 if fewer than MIN_WIN
    neighbours are found at each level.
    """
    for i, r in enumerate(rows):
        nb: list[int] = []
        for w in (WIN, 90, 180, 400):
            lo = r["ts"] - w * 86400
            hi = r["ts"] + w * 86400
            nb = [
                rows[j]["likes"]
                for j in range(len(rows))
                if j != i and lo <= rows[j]["ts"] <= hi
            ]
            if len(nb) >= MIN_WIN:
                break
        era_base = _med(nb) if nb else float(r["likes"])
        r["era_base"] = era_base
        r["era"] = r["likes"] / era_base if era_base else 1.0


# ---------------------------------------------------------------------------
# Content-type helpers
# ---------------------------------------------------------------------------

def _ctype(r: dict) -> str:
    n = len(r.get("images") or [])
    return "reel" if n == 0 else "single" if n == 1 else "carousel"


def _slide_bin(r: dict) -> str | None:
    imgs = r.get("images")
    if not imgs:
        return None  # reels excluded
    n = len(imgs)
    if n == 1:
        return "1"
    if n <= 3:
        return "2-3"
    if n <= 5:
        return "4-5"
    if n <= 9:
        return "6-9"
    return "10+"


def _caplen_bin(r: dict) -> str:
    c = len(r.get("caption") or "")
    if c == 0:
        return "0"
    if c <= 80:
        return "1-80"
    if c <= 200:
        return "81-200"
    if c <= 500:
        return "201-500"
    return "500+"


def _has_cta(r: dict) -> bool:
    cap = r.get("caption") or ""
    return bool(
        re.search(
            r"\b(follow|tag|save|share|send|comment|link in bio|dm|drop a)\b",
            cap,
            re.I,
        )
    )


# ---------------------------------------------------------------------------
# Vocabulary cleaning (era-fair)
# ---------------------------------------------------------------------------

def _brand_set(handle: str) -> set[str]:
    """Brand-ish tokens to strip: the handle plus its dot/underscore parts.

    e.g. "guinea_mate_gaki" → {"guinea_mate_gaki", "guinea", "mate", "gaki"}
    """
    h = (handle or "").lower()
    brand = {h} if h else set()
    for part in re.split(r"[._]+", h):
        if part:
            brand.add(part)
    return brand


def _clean_tokens(r: dict, brand: set[str] | None = None) -> set[str]:
    """Return per-post token presence set, stripped of stop/brand/calendar tokens."""
    BRAND = brand or set()
    slides_text = "\n".join(r.get("slides") or [])
    caption = r.get("caption") or ""
    raw = (slides_text + " " + caption).lower()
    tokens = re.findall(r"[a-z']{3,}", raw)
    return {
        w
        for w in tokens
        if w not in STOP
        and w not in CAL
        and w not in BRAND
        and any(c in "aeiou" for c in w)
    }


# ---------------------------------------------------------------------------
# Group-table builder
# ---------------------------------------------------------------------------

def _grp_table(
    analysis_set: list[dict],
    raw_median: float,
    key_fn,
    min_n: int = MIN_N,
) -> dict[str, dict]:
    """
    Build a per-key stats dict over the analysis set.
    Keys with < min_n posts are suppressed.
    Result sorted by era_lift descending.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in analysis_set:
        k = key_fn(r)
        if k is not None:
            groups[str(k)].append(r)

    out: dict[str, dict] = {}
    for k, posts in groups.items():
        if len(posts) < min_n:
            continue
        eras = [p["era"] for p in posts]
        likes = [p["likes"] for p in posts]
        out[k] = {
            "n": len(posts),
            "raw_lift": round(_med(likes) / raw_median, 2) if raw_median else None,
            "era_lift": round(_med(eras), 2),
            "era_p90": round(_pctl(eras, 0.9), 2),
        }

    return dict(sorted(out.items(), key=lambda kv: -kv[1]["era_lift"]))


# ---------------------------------------------------------------------------
# Slim post summary (for exemplars)
# ---------------------------------------------------------------------------

def _slim(r: dict) -> dict:
    slides = r.get("slides") or []
    caption = r.get("caption") or ""
    first = slides[0] if slides else caption
    hook = (first.split("\n")[0] if first else "")[:90]
    return {
        "era": round(r["era"], 1),
        "likes": r["likes"],
        "date": r["date"][:7],
        "type": _ctype(r),
        "hook": hook,
        "shortcode": r.get("shortcode", ""),
    }


# ---------------------------------------------------------------------------
# Monroe z log-odds vocabulary
# ---------------------------------------------------------------------------

def _era_vocab(
    hits: list[dict], flops: list[dict], handle: str = ""
) -> tuple[list[str], list[str]]:
    """
    Era-fair vocabulary using Monroe-style z log-odds.
    hits  = top-tercile era posts
    flops = bottom-tercile era posts
    Returns (hit_words, flop_words).
    """
    Nh, Nf = len(hits), len(flops)
    brand = _brand_set(handle)
    ch: Counter = Counter()
    cf: Counter = Counter()

    for r in hits:
        for w in _clean_tokens(r, brand):
            ch[w] += 1
    for r in flops:
        for w in _clean_tokens(r, brand):
            cf[w] += 1

    vocab: list[tuple[str, float, int, int]] = []
    for w in set(list(ch) + list(cf)):
        if ch[w] + cf[w] < 10:  # require real frequency
            continue
        a = 0.5
        ph = (ch[w] + a) / (Nh + 2 * a)
        pf = (cf[w] + a) / (Nf + 2 * a)
        lo = math.log(ph / (1 - ph)) - math.log(pf / (1 - pf))
        z = lo / math.sqrt(1 / (ch[w] + a) + 1 / (cf[w] + a))
        vocab.append((w, round(z, 2), ch[w], cf[w]))

    hit_words = [w for w, z, _a, _b in sorted(vocab, key=lambda x: -x[1])[:22] if z > 0]
    flop_words = [w for w, z, _a, _b in sorted(vocab, key=lambda x: x[1])[:18] if z < 0]
    return hit_words, flop_words


# ---------------------------------------------------------------------------
# Supplementary per-post enrichment (research_metrics.py style)
# ---------------------------------------------------------------------------

def _enrich_for_metrics(rows: list[dict]) -> list[dict]:
    """Add format, hook, wc, etc. for the raw-metrics layer."""
    enriched = []
    for r in rows:
        slides = [s for s in (r.get("slides") or []) if s.strip()]
        cap = r.get("caption") or ""
        stext = "\n".join(slides)
        full = (stext + "\n" + cap).strip()
        hook = (
            slides[0].split("\n")[0]
            if slides
            else (cap.split("\n")[0] if cap else "")
        )
        imgs = r.get("images") or []
        wc = len(re.findall(r"\w+", stext))
        fmt = _classify(full, hook, len(imgs), len(cap), wc)
        enriched.append(
            dict(
                r,
                hook=hook[:120],
                full=full,
                fmt=fmt,
                nslides=len(imgs),
                is_reel=(len(imgs) == 0),
                cap_len=len(cap),
                cap_words=len(re.findall(r"\w+", cap)),
                text_words=wc,
                emoji=len(_EMOJI_RE.findall(cap)),
                hashtags=cap.count("#"),
                mentions=cap.count("@"),
                has_cta=_has_cta(r),
            )
        )
    return enriched


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def era_signal(handle: str, data_dir: str) -> dict:
    """
    Compute era-adjusted signal for *handle*.

    Parameters
    ----------
    handle   : Instagram username (no @)
    data_dir : root data directory (contains <handle>/ subdirectory)

    Returns
    -------
    dict with all signal fields (also written to signal/metrics.json + signal/SIGNAL.md)
    """
    handle_dir = os.path.join(data_dir, handle)
    jsonl_path = os.path.join(handle_dir, "text", "training.jsonl")

    if not os.path.exists(jsonl_path):
        raise FileNotFoundError(
            f"training.jsonl not found at {jsonl_path}. "
            "Run `fetchgram ocr` (or `analyze`) first."
        )

    # --- load ---
    rows = _load_records(jsonl_path)
    if not rows:
        raise ValueError(f"No valid records found in {jsonl_path}.")

    # --- era normalise (mutates rows in-place) ---
    _era_normalize(rows)

    # --- validation: did era-norm fix the age confound? ---
    ages = [r["age"] for r in rows]
    likes_all = [r["likes"] for r in rows]
    eras_all = [r["era"] for r in rows]
    validation = {
        "spearman_age_likes": _spearman(ages, likes_all),
        "spearman_age_era": _spearman(ages, eras_all),
        "median_era": round(_med(eras_all), 2),
    }

    # --- analysis set: drop still-accumulating young posts ---
    A = [r for r in rows if r["age"] >= MIN_AGE]
    if not A:
        raise ValueError(
            f"No posts older than {MIN_AGE} days found. "
            "Cannot compute era signal yet."
        )

    raw_median = _med([r["likes"] for r in A])

    # --- group tables ---
    ct = _grp_table(A, raw_median, _ctype)
    slides = _grp_table(A, raw_median, _slide_bin)
    caplen = _grp_table(A, raw_median, _caplen_bin)
    cta_t = _grp_table(
        A, raw_median, lambda r: "cta" if _has_cta(r) else "no_cta"
    )
    wd = _grp_table(A, raw_median, lambda r: _weekday(r["dt"]))
    hr = _grp_table(A, raw_median, lambda r: f"{r['dt'].hour:02d}:00")

    def _hook1(r: dict) -> str | None:
        slides_r = r.get("slides") or [r.get("caption") or ""]
        first = (slides_r[0] if slides_r else "").split()
        if not first:
            return None
        return first[0].lower().strip(":.,?!\"'") or None

    hooks = _grp_table(A, raw_median, _hook1, min_n=12)

    # --- era-fair vocabulary ---
    eras_sorted = sorted(r["era"] for r in A)
    n_a = len(eras_sorted)
    hi_thr = eras_sorted[int(n_a * 0.70)]
    lo_thr = eras_sorted[int(n_a * 0.30)]
    hit_posts = [r for r in A if r["era"] >= hi_thr]
    flop_posts = [r for r in A if r["era"] <= lo_thr]
    hit_vocab, flop_vocab = _era_vocab(hit_posts, flop_posts, handle)

    # --- exemplars ---
    current_hits = sorted(
        [r for r in A if r["era"] >= 1.5 and r["likes"] >= raw_median],
        key=lambda r: -r["era"],
    )[:12]
    top_outliers = sorted(A, key=lambda r: -r["era"])[:15]

    # --- supplementary metrics (research_metrics.py layer) ---
    enriched = _enrich_for_metrics(rows)
    likes_e = [p["likes"] for p in enriched]
    med_e = _med(likes_e)

    # engagement stats
    p99 = _pctl(likes_e, 0.99)
    engagement = {
        "n": len(enriched),
        "mean": round(_mean(likes_e)),
        "median": round(med_e),
        "p25": round(_pctl(likes_e, 0.25)),
        "p75": round(_pctl(likes_e, 0.75)),
        "p90": round(_pctl(likes_e, 0.90)),
        "p99": round(p99),
        "max": max(likes_e),
        "min": min(likes_e),
        "std": round(_std(likes_e)),
        "viral_multiple_max_over_median": round(max(likes_e) / med_e, 1) if med_e else None,
        "p99_over_median": round(p99 / med_e, 1) if med_e else None,
        "pct_posts_over_3x_median": round(
            100 * sum(1 for x in likes_e if x > 3 * med_e) / len(likes_e), 1
        ) if med_e else None,
        "gini_attention": _gini(likes_e),
        "avg_comments": round(_mean([p.get("comments") or 0 for p in enriched])),
        "median_comments": round(_med([p.get("comments") or 0 for p in enriched])),
    }

    # format groups (raw lift, gated n≥5)
    def _fmt_grp(key_fn, min_n: int = 5) -> dict:
        g: dict[str, list[int]] = defaultdict(list)
        for p in enriched:
            g[str(key_fn(p))].append(p["likes"])
        out: dict[str, dict] = {}
        for k, v in g.items():
            if k == "None" or len(v) < min_n:
                continue
            m = _med(v)
            out[k] = {
                "n": len(v),
                "median": round(m),
                "mean": round(_mean(v)),
                "lift_vs_median": round(m / med_e, 2) if med_e else None,
            }
        return dict(sorted(out.items(), key=lambda kv: -(kv[1]["lift_vs_median"] or 0)))

    _slide_bin_e = (
        lambda p: (
            "0(reel)" if p["nslides"] == 0
            else "1" if p["nslides"] == 1
            else "2-3" if p["nslides"] <= 3
            else "4-5" if p["nslides"] <= 5
            else "6-9" if p["nslides"] <= 9
            else "10+"
        )
    )
    _caplen_bin_e = (
        lambda p: (
            "0" if p["cap_len"] == 0
            else "1-80" if p["cap_len"] <= 80
            else "81-200" if p["cap_len"] <= 200
            else "201-500" if p["cap_len"] <= 500
            else "500+"
        )
    )

    by_format = _fmt_grp(lambda p: p["fmt"])
    by_slides_raw = _fmt_grp(_slide_bin_e)
    by_caplen_raw = _fmt_grp(_caplen_bin_e)
    by_weekday_raw = _fmt_grp(lambda p: _weekday(p["dt"]) if p.get("dt") else None)
    by_cta_raw = _fmt_grp(lambda p: "cta" if p["has_cta"] else "no_cta")
    by_reel = _fmt_grp(lambda p: "reel" if p["is_reel"] else "image_post")

    # spearman correlations of features vs raw likes
    def _sp(feat_fn) -> float | None:
        fx = [feat_fn(p) for p in enriched]
        return _spearman(fx, likes_e)

    spearman_vs_likes = {
        "nslides": _sp(lambda p: p["nslides"]),
        "cap_len": _sp(lambda p: p["cap_len"]),
        "cap_words": _sp(lambda p: p["cap_words"]),
        "text_words": _sp(lambda p: p["text_words"]),
        "emoji": _sp(lambda p: p["emoji"]),
        "hashtags": _sp(lambda p: p["hashtags"]),
        "mentions": _sp(lambda p: p["mentions"]),
        "hour": _sp(lambda p: p["dt"].hour if p.get("dt") else 0),
        "is_reel": _sp(lambda p: 1 if p["is_reel"] else 0),
        "has_cta": _sp(lambda p: 1 if p["has_cta"] else 0),
    }

    # best opening words (hook first word, min 5 posts)
    hook_first: dict[str, list[int]] = defaultdict(list)
    for p in enriched:
        fw = (p["hook"].split() or [""])[0].lower().strip(":.,?")
        if fw:
            hook_first[fw].append(p["likes"])
    best_opening_words = sorted(
        [
            {"word": w, "n": len(v), "median_likes": round(_med(v))}
            for w, v in hook_first.items()
            if len(v) >= 5
        ],
        key=lambda x: -x["median_likes"],
    )[:18]

    # posting cadence
    months_set = sorted(
        set(p["dt"].strftime("%Y-%m") for p in enriched if p.get("dt"))
    )
    posting = {
        "months_active": len(months_set),
        "avg_posts_per_month": round(len(enriched) / max(len(months_set), 1), 1),
        "first": months_set[0] if months_set else None,
        "last": months_set[-1] if months_set else None,
    }

    # --- assemble result ---
    result: dict[str, Any] = {
        "handle": handle,
        "n_all": len(rows),
        "n_analyzed": len(A),
        "min_age_days": MIN_AGE,
        "min_group_n": MIN_N,
        "era_window_base_days": WIN,
        "min_win_neighbours": MIN_WIN,
        "validation": validation,
        "raw_median_analyzed": int(raw_median),
        "era_hit_thr": round(hi_thr, 2),
        "era_flop_thr": round(lo_thr, 2),
        # era-adjusted group tables
        "by_content_type": ct,
        "by_slides": slides,
        "by_caption_len": caplen,
        "by_cta": cta_t,
        "by_weekday": wd,
        "by_hour": hr,
        "by_hook_word": hooks,
        # era-fair vocabulary
        "hit_vocab": hit_vocab,
        "flop_vocab": flop_vocab,
        # exemplars
        "current_hits": [_slim(r) for r in current_hits],
        "top_outliers": [_slim(r) for r in top_outliers],
        # raw engagement / supplementary
        "engagement": engagement,
        "by_format": by_format,
        "by_slides_raw": by_slides_raw,
        "by_caption_len_raw": by_caplen_raw,
        "by_weekday_raw": by_weekday_raw,
        "by_cta_raw": by_cta_raw,
        "reel_vs_image": by_reel,
        "spearman_vs_likes": spearman_vs_likes,
        "best_opening_words": best_opening_words,
        "posting": posting,
    }

    # --- write outputs ---
    signal_dir = os.path.join(handle_dir, "signal")
    os.makedirs(signal_dir, exist_ok=True)

    metrics_path = os.path.join(signal_dir, "metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    signal_md = _render_signal_md(result)
    md_path = os.path.join(signal_dir, "SIGNAL.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(signal_md)

    return result


# ---------------------------------------------------------------------------
# SIGNAL.md renderer
# ---------------------------------------------------------------------------

def _md_table(rows_dict: dict, col_headers: list[str]) -> str:
    """Render a simple markdown table from a {key: {col: val}} dict."""
    lines = [
        "| " + " | ".join(col_headers) + " |",
        "|" + "|".join(["---"] * len(col_headers)) + "|",
    ]
    for k, v in rows_dict.items():
        cells = [k] + [str(v.get(c, "")) for c in col_headers[1:]]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_signal_md(m: dict) -> str:
    """
    Generate a human-readable SIGNAL.md from the metrics dict.
    Deterministic — all numbers come from m, no hardcoded values.
    """
    handle = m["handle"]
    v = m["validation"]
    e = m["engagement"]
    p = m["posting"]

    # content-type table rows
    ct_rows = "\n".join(
        f"| {k} | {d['n']} | {d['raw_lift']}× | **{d['era_lift']}×** | {d['era_p90']}× |"
        for k, d in m["by_content_type"].items()
    )

    # slide count era table
    slide_era_str = ", ".join(
        f"{k} {d['era_lift']}×"
        for k, d in sorted(
            m["by_slides"].items(), key=lambda kv: -kv[1]["era_lift"]
        )
    )

    # top current hits bullets
    hit_bullets = "\n".join(
        f"- {h['era']}× · {h['likes']:,}♥ · {h['date']} [{h['type']}] — {h['hook']}"
        for h in m["current_hits"][:8]
    )
    if not hit_bullets:
        hit_bullets = "- (none met era≥1.5 + raw≥median threshold)"

    # top era-outliers
    outlier_bullets = "\n".join(
        f"- {h['era']}× · {h['likes']:,}♥ · {h['date']} [{h['type']}] — {h['hook']}"
        for h in m["top_outliers"][:10]
    )

    # by-format table (raw lift, gated n≥5)
    fmt_rows = "\n".join(
        f"| {k} | {d['n']} | {d['median']:,} | {d['lift_vs_median']}× |"
        for k, d in m["by_format"].items()
    )

    # spearman correlations
    corr_lines = "\n".join(
        f"- {k}: {vv}"
        for k, vv in sorted(
            m["spearman_vs_likes"].items(),
            key=lambda x: -(abs(x[1]) if x[1] else 0),
        )
    )

    # opening-word table
    ow_rows = "\n".join(
        f"| {w['word']} | {w['n']} | {w['median_likes']:,} |"
        for w in m["best_opening_words"][:12]
    )

    # weekday era table
    wd_rows = "\n".join(
        f"| {k} | {d['n']} | {d['era_lift']}× |"
        for k, d in m["by_weekday"].items()
    )

    # cta comparison
    cta_lines = "\n".join(
        f"- **{k}**: era_lift {d['era_lift']}× (n={d['n']})"
        for k, d in m["by_cta"].items()
    )

    confound_verdict = (
        "confound largely removed"
        if abs(v["spearman_age_era"] or 0) < abs(v["spearman_age_likes"] or 0) / 2
        else "residual age correlation remains — interpret era scores cautiously"
    )

    md = f"""# SIGNAL — @{handle} — era-adjusted content analysis

*Every number below is **era-adjusted**: each post is scored against the median
of its contemporaries (±{m['era_window_base_days']}d, widening to 90/180/400d if sparse), dividing
out the account's growth curve. We measure content quality, not calendar luck.
Posts younger than {m['min_age_days']} days are dropped (still accumulating).
Most groups are gated at n≥{m['min_group_n']} (hook-word groups at n≥12).*

## confound correction

| metric | value |
|---|---|
| spearman(age, raw likes) | {v['spearman_age_likes']} |
| spearman(age, era score) | **{v['spearman_age_era']}** |
| median era score | {v['median_era']} (should be ≈1.0) |

→ **{confound_verdict}**. Raw correlations above inflate older-post formats; era scores are the reliable signal.

---

## account snapshot

- **{e['n']} posts** · median **{e['median']:,}** likes · mean {e['mean']:,}
- p90 {e['p90']:,} · p99 {e['p99']:,} · max {e['max']:,}
- **virality ceiling:** top post = {e['viral_multiple_max_over_median']}× median; p99 = {e['p99_over_median']}× median
- **hit rate:** {e['pct_posts_over_3x_median']}% of posts exceed 3× median
- **attention Gini:** {e['gini_attention']} (0 = even, 1 = winner-take-all)
- **posting cadence:** {p['avg_posts_per_month']} posts/mo over {p['months_active']} months ({p['first']} → {p['last']})
- analysed {m['n_analyzed']}/{m['n_all']} posts (dropped <{m['min_age_days']}d) · raw median (analysed set) = {m['raw_median_analyzed']:,}♥

---

## content type — era-adjusted

| type | n | raw | era | p90 |
|---|---|---|---|---|
{ct_rows}

*raw = format median ÷ overall median; era = era-normalised lift (the honest number)*

## slide count — era-adjusted

{slide_era_str if slide_era_str else "(insufficient data — groups below n≥" + str(m['min_group_n']) + ")"}

## content format (raw lift — not era-adjusted)

| format | n | median ♥ | lift |
|---|---|---|---|
{fmt_rows if fmt_rows else "| (insufficient data) | | | |"}

---

## vocabulary — era-fair (Monroe z log-odds)

*Top-tercile era posts (era≥{m['era_hit_thr']}) vs bottom-tercile (era≤{m['era_flop_thr']}).
Words ranked by statistical reliability (z-score), min combined frequency 10,
stripped of stopwords + calendar tokens.*

**words that travel with hits:**
{", ".join(m["hit_vocab"][:16]) if m["hit_vocab"] else "(not enough frequency data)"}

**words that travel with flops:**
{", ".join(m["flop_vocab"][:14]) if m["flop_vocab"] else "(not enough frequency data)"}

---

## CTA vs no-CTA (era-adjusted)

{cta_lines if cta_lines else "(insufficient data)"}

## best posting day (era-adjusted)

| day | n | era_lift |
|---|---|---|
{wd_rows if wd_rows else "| (insufficient data) | | |"}

## best opening words (raw median ♥, min 5 posts)

| word | n | median ♥ |
|---|---|---|
{ow_rows if ow_rows else "| (insufficient data) | | |"}

## spearman correlations — features vs raw likes

{corr_lines}

---

## current-scale hits to study

*era≥1.5 AND likes≥raw-median. The posts that beat contemporaries AND have
meaningful absolute reach — the safest exemplars to emulate.*

{hit_bullets}

## top era-outliers (time-fair, all sizes)

{outlier_bullets}

---

## methodology notes

- **era window:** ±{m['era_window_base_days']}d, widens to 90/180/400 if fewer than {m.get('min_win_neighbours', MIN_WIN)} neighbours found
- **young-post cutoff:** {m['min_age_days']} days (likes still accumulating — not flops, just early)
- **group gate:** n≥{m['min_group_n']} (hook-word groups n≥12; prevents small-n headline claims)
- **hit threshold:** top-30% era score = {m['era_hit_thr']}×
- **flop threshold:** bottom-30% era score = {m['era_flop_thr']}×
- **vocab min-count:** 10 combined hits+flops
- judge your own posts era-adjusted (vs your last ~6 weeks), never vs all-time —
  or you'll mistake growth for skill and skill for growth
"""
    return md
