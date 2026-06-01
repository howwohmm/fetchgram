# fetchgram — see what *actually* works on any instagram account.

fetchgram pulls any public profile, reads the text off every image, and scores each post against its own era — so a growing account never gets mistaken for good content.

```
$ pipx install git+https://github.com/howwohmm/fetchgram
$ fetchgram analyze nike
```

free · runs locally · no api keys · mac · linux · windows

---

## the problem: raw engagement is a clock, not a verdict.

most "what works on instagram" takes are survivorship bias. *"carousels get 11× more likes!"* — except the account tripled its following that year. compare a post to its all-time average and you're mostly measuring **time**, not **talent**.

fetchgram fixes the measurement.

built by running it on **7 accounts and 5,060 posts.** it found that one wellness brand's "11.5× carousel lift" was ~90% just the account growing — era-adjusted, carousels were dead average. the tool exists because the obvious numbers lie.

---

## what it does

**reads the words inside the images**
most scrapers grab the caption and stop. fetchgram OCRs every frame — so quote graphics, carousels, and on-image text become searchable data. Apple Vision on mac, tesseract everywhere else.

**scores each post against its own era**
the part nobody else does. every post is measured against what that account posted around the same time (±45 days) — so growth and recency cancel out. you see content effects, not calendar effects.

**free, local, yours**
no api keys, no cloud, no account. it runs on your machine and the data never leaves it.

**clean data, ready for anything**
out comes a text corpus, a training-ready JSONL, a metrics.json, and a plain-english SIGNAL.md. drop it into any LLM and ask your own questions.

---

## how it works

1. **scrape** — `fetchgram analyze <handle>` pulls the profile, images only, rate-limit friendly.
2. **read** — OCRs every image, groups carousel slides, builds the corpus.
3. **signal** — era-adjusts the engagement and writes you the report.

---

## install

```
pipx install git+https://github.com/howwohmm/fetchgram
```

or with pip:

```
pip install git+https://github.com/howwohmm/fetchgram
```

> _PyPI release (`pipx install fetchgram`) coming soon._

**OCR dependencies:**

| platform | what to do |
|---|---|
| macOS | just works — fetchgram compiles the bundled Apple Vision binary on first run (requires Xcode command-line tools: `xcode-select --install`) |
| Linux / Windows | `pip install pytesseract` + install `tesseract-ocr` from your package manager (`apt install tesseract-ocr` / choco) |
| anywhere | `--ocr none` to skip OCR and use captions only |

---

## usage

### full pipeline (recommended)

```
fetchgram analyze <handle> [--login U] [--count N] [--out DIR] [--ocr auto|vision|tesseract|none]
```

scrape → OCR → corpus → era-signal, all in one shot.

### individual steps

```
fetchgram scrape  <handle> [--login U] [--count N] [--out DIR]
fetchgram ocr     <handle> [--out DIR] [--ocr auto|vision|tesseract|none]
fetchgram metrics <handle> [--out DIR]
```

### examples

```bash
# analyse a public account (no login needed)
fetchgram analyze nike

# analyse with login (more posts, less rate limiting)
fetchgram analyze patagonia --login myusername

# limit to last 200 posts
fetchgram analyze @someaccount --count 200

# force tesseract (useful on mac if you prefer it)
fetchgram analyze brand --ocr tesseract

# skip OCR, captions only (fast, useful for text-heavy accounts)
fetchgram analyze brand --ocr none

# custom output directory
fetchgram analyze brand --out ~/data/ig
```

---

## output layout

```
fetchgram-data/<handle>/
├── raw/        the posts (instaloader format)
├── text/
│   ├── corpus.txt        full text corpus
│   ├── training.jsonl    one record per post (training-ready)
│   └── posts/            per-post readable .txt files
└── signal/
    ├── metrics.json      all numbers (structured)
    └── SIGNAL.md         human-readable report
```

### training.jsonl record schema

```json
{
  "date":       "2024-03-15T12:30:00+00:00",
  "shortcode":  "ABC123",
  "url":        "https://www.instagram.com/p/ABC123/",
  "likes":      4821,
  "comments":   63,
  "num_slides": 5,
  "images":     ["2024-03-15_12-30-00_UTC_1.jpg", "..."],
  "caption":    "the caption text",
  "slides":     ["slide 1 OCR text", "slide 2 OCR text", "..."],
  "text":       "merged slides + caption (clean, boilerplate stripped)"
}
```

---

## era signal — what the report tells you

the SIGNAL.md covers:

- **confound correction** — spearman(age, likes) before and after era-normalisation. close to 0 after = confound removed.
- **content type** — reel vs single vs carousel, raw lift vs era-adjusted lift vs p90 ceiling.
- **vocabulary** — Monroe z log-odds comparing top-tercile vs bottom-tercile era posts. words that travel with hits and flops, cleaned of stopwords/calendar noise.
- **CTA, caption length, weekday, opening word** — era-adjusted, gated at n≥20.
- **exemplars** — current-scale hits (era≥1.5× AND likes≥median) and top era-outliers.
- **raw engagement stats** — gini, p90/p99, virality multiple, hit rate.

---

## fair use

public profiles only. rate-limited. for research and personal use. respect instagram's terms of service — don't be weird with it.

---

## contributing

PRs welcome. keep deps minimal (instaloader + pillow + pytesseract + stdlib only).

---

## license

MIT — see [LICENSE](LICENSE).

---

*fetchgram · the obvious numbers lie*
