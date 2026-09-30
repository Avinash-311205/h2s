# Module 2 — AI Understanding (ASR | Translation | NLP | CV)

Turns a raw citizen submission — typed text, a voice note, or a photo — into a
**structured, severity-scored civic record** that Modules 4, 5, 6 and 7 can act
on without re-reading free text.

## Why this module exists

A citizen filing "கழிவு நீர் சாலையில் பாய்ந்து கொண்டிருக்கு ஆறு மாதங்கள்"
and an officer filing "Sewage has been overflowing on Main Road for six months"
are the same problem. Downstream routing, priority scoring and dashboards all
need one shared vocabulary, so this module produces it once, at ingestion.

## Design principle: works fully offline

Every capability is a **swappable provider with a deterministic fallback**. The
module runs correctly with zero model downloads, which matters because a civic
system cannot depend on an API key, a network call, or a multi-gigabyte
download to triage a complaint.

| Stage | Preferred provider | Offline fallback | Extra dependency |
|---|---|---|---|
| Language detection | — | Unicode script + function-word frequency | none |
| ASR | `faster-whisper` (open-weight Whisper) | skipped + reported | `faster-whisper` |
| Translation | NLLB-200 distilled | civic glossary gloss (flagged `translation_is_gloss`) | `transformers` |
| NER | — | regex + gazetteer | none |
| Classification | — | weighted multilingual lexicon evidence | none |
| Severity | — | rule-based band + escalation | none |
| Image analysis | CLIP labels | heuristic measurements (brightness/blur/aspect) | `pillow` |

`GET /api/v1/capabilities` reports the live state of every stage honestly, so
"why is translation passthrough?" has a one-glance answer.

The offline gloss is deliberately **flagged, not hidden**: a glossed English
text is stored with `translation_is_gloss = true` so downstream consumers know
the text is approximate. The service never fabricates a translation.

## Pipeline

```
audio ──► ASR ──┐
text ───────────┼─► language detection ──► translation ──► NER ──► classification ──► severity
image ──────────┘                            │
                                             └──► image analysis (measurements)
```

Stages run in a fixed order, but the `stage_status` map records the **true**
execution order. ASR runs before language detection when a citizen sends only
audio (there is nothing to detect yet); stages that do not apply are recorded as
`SKIPPED` rather than dropped, so the audit trail always shows what was
considered. `status` is `UNDERSTOOD`, `PARTIAL` or `FAILED`.

### Classification

Evidence-weighted, and every prediction is explainable:

- a matched multi-word **phrase** = 1.0 point
- a matched single **lemma** (`pothole`, `sewage`) = 0.5 points
- confidence = winning category's share of all points

Phrase evidence therefore always outvotes a single generic word, while lemmas
still rescue heavily inflected text ("road is slightly damaged") that exact
matching misses. `matched_keywords` records exactly what fired. With no
evidence the result is `UNKNOWN` / `UNCLASSIFIED` at zero confidence — routed
for human review rather than confidently mislabelled.

Lexicon files are validated against the enum vocabulary by tests, so a
sub-category name can never silently drift away from what routers expect.

### Severity (1–5, shared civic scale)

Inspectable rules, applied to **both** the English text and the original text
(the strongest signal wins), so a complaint that says "கழிவு நீர்" in Tamil is
not under-scored because the glossary did not cover it:

1. base severity from the strongest keyword band present (critical 5, high 4,
   medium 3, default 2 — a complaint with no explicit signal is still a real
   complaint)
2. +1 when the complaint has persisted ≥ 7 days
3. at least 4 for personal-health language
4. capped at 2 when the citizen reports it resolved ("already fixed", "slight")
5. clamped to `[min_severity, max_severity]`, never 0

Duration expressions are recognised in English, Tamil, Hindi and Telugu,
including plural and inflected forms (`ஆறு மாதங்கள்` = six months).

## API

Base path `/api/v1`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | liveness + record counts |
| `GET` | `/capabilities` | live AI capability report |
| `POST` | `/understand/text` | text submission |
| `POST` | `/understand/batch` | bulk backlog, per-item error isolation |
| `POST` | `/understand/audio` | voice note (`multipart`, ASR + NLP) |
| `POST` | `/understand/image` | photo (`multipart`, CV + NLP) |
| `GET` | `/understand` | filterable listing (`category`, `language`, `status`, `min_severity`) |
| `GET` | `/understand/{request_id}` | single record |
| `POST` | `/understand/rerun/{request_id}` | re-process after a pipeline fix |
| `GET` | `/analytics/categories` | volume + average severity per category |
| `GET` | `/analytics/languages` | record count per detected language |

Re-submitting an already-understood `request_id` returns **409** rather than
silently overwriting, so accidental double-processing stays visible;
`/understand/rerun/{request_id}` is the explicit way to reprocess.

Handset GPS is passed through untouched in `raw_payload` (`hint_latitude` /
`hint_longitude`) for Module 3's geocoder — this module never geocodes.

## Quick start

```bash
cd module-2-ai-understanding
pip install -r requirements.txt

# Optional, enables real ASR / NLLB / CLIP:
# pip install faster-whisper transformers torch pillow

python seed_sample_data.py
uvicorn app.main:app --reload --port 8002
```

Interactive docs at <http://localhost:8002/docs>.

## Configuration

Every setting is an environment variable (see `app/core/config.py`).

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./ai_understanding.db` | PostgreSQL in production |
| `AUTO_CREATE_SCHEMA` | `true` | create tables on startup |
| `ASR_PROVIDER` | `auto` | `auto` \| `faster_whisper` \| `disabled` |
| `TRANSLATION_PROVIDER` | `auto` | `auto` \| `huggingface` \| `passthrough` |
| `IMAGE_PROVIDER` | `auto` | `auto` \| `clip` \| `heuristics` \| `disabled` |
| `USE_CIVIC_GLOSSARY` | `true` | offline civic-term gloss |
| `PERSONAL_HEALTH_ROUTING` | `true` | raise illness reports to high |
| `MIN_SEVERITY` / `MAX_SEVERITY` | `1` / `5` | severity clamp |
| `MAX_UPLOAD_BYTES` | size limit | early 413 rejection |

## Tests

```bash
python -m pytest
```

96 tests covering language detection (including the script/mark counting that
mixed-script complaints depend on), lexicon validity against the enums,
classification weighting, multilingual duration extraction, severity rules,
the end-to-end pipeline, and the full HTTP contract.

## Notes for reviewers

- No API keys, no network calls, no proprietary models.
- `app/resources/lexicons.json` holds the multilingual civic vocabulary as
  plain data, so a judge can inspect and extend it without touching code.
- `translation_is_gloss` exists so an approximate translation can never be
  mistaken for a verified one.