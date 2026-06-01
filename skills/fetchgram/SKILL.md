---
name: fetchgram
description: Analyze any public Instagram account with era-adjusted engagement metrics, then build a clean report. Use when the user wants to scrape, audit, decode, or understand what actually works on an Instagram profile. Triggers on "analyze @<handle>", "instagram teardown", "what works on X's instagram", "audit this ig account", "decode @<handle>", "/fetchgram". Wraps the fetchgram CLI (scrape → OCR → era-adjusted signal) and interprets the results.
---

# fetchgram — instagram content intelligence

Turn any public Instagram handle into an **era-adjusted** read of what actually drives its engagement — then present it.

The whole point: raw engagement is a clock, not a verdict. A growing account makes everything look like it "works." fetchgram scores each post against its **±45-day contemporaries**, so account growth never gets mistaken for good content.

## when to use
The user gives an Instagram handle and wants to understand/audit/decode/reverse-engineer it ("analyze @nike", "what's working for @someaccount", "ig teardown of X"). One handle at a time.

## workflow

1. **Ensure the engine is installed.**
   ```
   which fetchgram || pipx install git+https://github.com/howwohmm/fetchgram
   ```
   (cross-platform: macOS uses Apple Vision for OCR, Linux/Windows use tesseract — `pip install pytesseract` + the system `tesseract-ocr` binary.)

2. **Handle login (required — Instagram 403s anonymous requests).**
   Ask the user for the IG username whose session to use. If they have no session yet, tell them to run once:
   ```
   instaloader -l <their_ig_username>
   ```
   Use **their own account, at a sane volume** — heavy scraping gets the session throttled/burned.

3. **Run the pipeline.** Default to a capped run first (faster, gentler on rate limits); offer a full run after.
   ```
   fetchgram analyze <handle> --login <user> --count 300
   ```
   Flags: `--count N` (cap), `--out DIR`, `--ocr auto|tesseract|none`, `--force`. If scrape 403s, it's almost always rate-limit or an expired session — surface fetchgram's own error message and suggest waiting / re-login, don't retry-spam.

4. **Read the output** at `<out>/<handle>/`:
   - `signal/SIGNAL.md` — the human-readable era-adjusted report (read this first)
   - `signal/metrics.json` — all numbers (content-type/slide/timing lifts, viral-vs-flop vocab, gini, confound check)
   - `text/corpus.txt` + `text/training.jsonl` — every post's caption + on-image OCR text

5. **Interpret, don't just dump.** Lead with the de-noised truth:
   - the **confound** (spearman age↔likes before→after era-normalisation; near 0 after = clean)
   - **raw vs era-adjusted** lifts — call out anything that looked huge raw but collapsed era-adjusted (that's the gold: a fake "rule" exposed)
   - the **viral vs flop vocabulary** (era-fair), top era-outliers, posting patterns
   - what the data says to actually DO vs STOP

6. **Offer a clean HTML report.** ohm prefers deliverables as a beautiful, simple, self-contained HTML page (NOT raw markdown) — editorial style, opens in Chrome. Build one summarising the teardown if the finding is worth keeping. See his existing audits in `~/instagram-archive/research/*_audit.html` for the house style.

## guardrails
- public profiles only · rate-limited · research/personal use · respect Instagram's ToS.
- one account per run; don't loop-scrape many accounts back-to-back (burns the session + IP).
- era-adjusted numbers are the truth; never quote raw all-time lifts as "what works."

## repo
https://github.com/howwohmm/fetchgram (MIT). The CLI is the engine; this skill is the conversational layer + interpretation + report.
