# fetchgram

**see what _actually_ works on any instagram account.**

`fetchgram` pulls any public profile, reads the text off every image, and scores each post against its own era — so a growing account never gets mistaken for good content. free, local, no api keys.

`free` · `runs locally` · `no api keys` · `mac · linux · windows` · `MIT`

```
$ pipx install git+https://github.com/howwohmm/fetchgram
$ fetchgram analyze nike
```

---

## the 11.5× that wasn't

you've seen the thread: _"carousels get 11× more likes — just post more carousels."_

i ran that exact claim on a real wellness brand (904 posts). here's what the raw numbers said, vs what was actually true once you divide out the account's growth that year:

```
content type     raw lift          era-adjusted        the truth
─────────────────────────────────────────────────────────────────
carousel         11.5×        →    1.01×               dead average
single image      0.09×       →    1.00×               dead average
reel              1.02×       →    0.99×               dead average

confound  (post age ↔ likes):   -0.864   →   -0.017     (removed)
```

the famous "carousel lift" was **~90% just the account tripling its following** that year. measured against its own contemporaries, the format did *nothing*.

**raw engagement is a clock, not a verdict.** almost every "what works on instagram" take is survivorship bias wearing a lab coat. fetchgram is the tool that controls for it — and that table above is real, unedited output.

---

## the fix (it's embarrassingly simple)

compare each post to what that same account posted **±45 days around it** — not its all-time average. growth and recency cancel out, and you start measuring the content instead of the calendar.

that's it. that's the whole trick. nobody ships it.

## what it does

- **reads the words inside the images.** most scrapers grab the caption and stop. fetchgram OCRs every frame — quote graphics, carousel slides, on-image text all become searchable data. Apple Vision on mac, tesseract everywhere else.
- **scores each post against its own era.** the part nobody else does (see above). you see content effects, not calendar effects.
- **free, local, yours.** no api keys, no cloud, no account. runs on your machine; the data never leaves it.
- **clean data, ready for anything.** out comes a text corpus, a training-ready `jsonl`, a `metrics.json`, and a plain-english `SIGNAL.md`. drop any of it into an LLM and ask your own questions.

## 60 seconds to your first teardown

```
pipx install git+https://github.com/howwohmm/fetchgram
fetchgram analyze nike
```

one command runs the whole pipeline: scrape → OCR → corpus → era-adjusted report. open `fetchgram-data/nike/signal/SIGNAL.md` and read.

> **now run it on the account whose advice you've been copying.** if their "secret" survives era-adjustment — great, copy away. if it evaporates like the 11.5× did, you just saved yourself a quarter of wasted posting. either way, tell me what you find.

## how it works

1. **scrape** — `fetchgram analyze <handle>` pulls the profile, images only, rate-limit friendly.
2. **read** — OCRs every image, groups carousel slides, builds the corpus.
3. **signal** — era-adjusts the engagement and writes you the report.

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
| macOS | just works — fetchgram compiles the bundled Apple Vision binary on first run (needs Xcode CLI tools: `xcode-select --install`) |
| Linux / Windows | `pip install pytesseract` + install `tesseract-ocr` from your package manager (`apt install tesseract-ocr` / `choco install tesseract`) |
| anywhere | `--ocr none` to skip OCR and use captions only |

## usage

```
# full pipeline (recommended)
fetchgram analyze <handle> [--login U] [--count N] [--out DIR] [--ocr auto|vision|tesseract|none]

# individual steps
fetchgram scrape  <handle> [--login U] [--count N] [--out DIR] [--force]
fetchgram ocr     <handle> [--out DIR] [--ocr ...]
fetchgram metrics <handle> [--out DIR]
```

```
fetchgram analyze nike                       # public account, no login
fetchgram analyze patagonia --login myuser   # logged in = more posts, less throttling
fetchgram analyze someaccount --count 200    # last 200 posts only
fetchgram analyze brand --ocr none           # captions only (fast)
fetchgram analyze brand --out ~/data/ig      # custom output dir
```

> **login:** run `instaloader -l <your_ig_username>` once to create a session, then pass `--login <your_ig_username>`. anonymous works for small public accounts but rate-limits fast.

## output layout

```
fetchgram-data/<handle>/
├── raw/        the posts (instaloader format)
├── text/
│   ├── corpus.txt        full text corpus
│   ├── training.jsonl    one record per post (training-ready)
│   └── posts/            per-post readable .txt files
└── signal/
    ├── metrics.json      every number (structured)
    └── SIGNAL.md         human-readable report
```

### `training.jsonl` record schema

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

### what `SIGNAL.md` tells you

- **confound correction** — `spearman(age, likes)` before and after era-normalisation. close to 0 after = the growth confound is gone.
- **content type** — reel vs single vs carousel: raw lift vs era-adjusted lift vs p90 ceiling.
- **vocabulary** — Monroe z log-odds, top-tercile vs bottom-tercile era posts. the words that travel with hits (and flops), cleaned of stopwords / calendar / brand noise.
- **CTA, caption length, weekday, opening word** — all era-adjusted, most groups gated at n≥20 (hook-words at n≥12).
- **exemplars** — current-scale hits (era ≥ 1.5× *and* likes ≥ median) and the top era-outliers.
- **raw engagement stats** — gini, p90/p99, virality multiple, hit rate.

## fair use

public profiles only. rate-limited. for research and personal use. respect Instagram's terms of service — don't be weird with it. fetchgram is a measurement tool, not a growth-hack farm.

## contributing

PRs welcome. keep deps minimal (`instaloader` + `pillow` + `pytesseract` + stdlib only).

## license

MIT — see [LICENSE](LICENSE).

---

_if fetchgram changed how you read a feed, a ⭐ helps the next person find it._

**the obvious numbers lie.**
