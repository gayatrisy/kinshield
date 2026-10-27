# KinShield

**A private care assistant for the family member who manages an older parent's medicines.**
Built for the Nebius x NVIDIA Global AI Hackathon, Personal AI track. Runs on **NVIDIA Nemotron** models through **Nebius Token Factory**.

When a parent comes home from hospital, the family gets a discharge summary that changes half their medicines. Someone has to work out what's new, what changed, what stopped, what the paper forgot to mention, and then make sure the new routine actually happens. KinShield does that work with the caregiver, and never behind their back.

- **Reads the document on your server.** PDF text or local OCR (Tesseract). Names, phone numbers, addresses and ID numbers are removed before any text goes to a model.
- **Shows its source for every field.** Click a field and the exact line lights up on the original page. Medicine name, strength and dose must be confirmed by a person, every time.
- **Compares with what you already have.** New, changed, explicitly stopped, possible duplicate, or *not mentioned*. A medicine the paper doesn't mention stays on the list until you decide.
- **Checks US drug labels in code.** For each new medicine, the openFDA label is searched for the names of the other medicines. Nemotron only explains passages that matched. Three outcomes, never a safety verdict.
- **Remembers and acts.** A background worker sends reminders to the parent's screen and alerts you on Telegram when one gets no response, or when your parent taps "I need help".
- **Prepares the next visit.** A one-page PDF brief whose every sentence cites the record it came from; uncited sentences are dropped in code.
- **Shows its work.** The activity log lists every model call with the exact redacted text sent, tokens, latency and cost, and every decision with who made it.

> KinShield organises what documents say. It does not give medical advice, judge whether medicines are safe, or decide doses.

## Try it

**Hosted demo:** `<DEMO URL>` → *Start the demo*. You get a private workspace with Ramesh, 74, an invented patient. Workspaces are isolated from each other and deleted 7 days after last use. No login needed.

Two-minute path: **Documents → Read the PDF → tick each "Matches the document" → Confirm → Decisions → Medicines** (see the Clopidogrel label passages) **→ Make a reminder due now → Parent's screen** (leave it unanswered for 2 minutes; the alert goes to the demo Telegram channel, or to your own chat if you link it in Settings) **→ Visit brief**.

## How NVIDIA Nemotron and Nebius Token Factory are used

All inference is a runtime call to the Token Factory OpenAI-compatible API (`backend/kinshield/llm.py`). Every call is logged with model ID, tokens, latency and list-price cost.

| Task | Model | Why this one |
| --- | --- | --- |
| Turn redacted document text into structured medicines with line citations | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` (Super for long documents) | Fast and cheap; the job is careful copying, not reasoning |
| Plain-English notes on plan differences, answers in *Ask KinShield*, the visit brief | `nvidia/nemotron-3-super-120b-a12b` | Good instruction following for cited JSON output |
| Explain an openFDA label passage and draft a clinician question | `nvidia/Nemotron-3-Ultra-550b-a55b` | Long, dense label text; rare calls, so the cost stays low |
| Read a page image when OCR fails (opt-in, identifiers masked first) | `nvidia/Nemotron-3-Nano-Omni` | Vision input |

What Token Factory made easier: one OpenAI-compatible endpoint for four Nemotron sizes, so routing a task to a bigger or smaller model is a one-line config change (`KS_MODEL_*` in `.env`), and the per-token pricing let us show the real cost of each workflow in the app.

## Run it yourself

Requirements: Docker, a Nebius Token Factory API key, optionally a Telegram bot from @BotFather.

```bash
git clone <REPO URL> kinshield && cd kinshield
cp .env.example .env          # add NEBIUS_API_KEY (and Telegram values)
docker compose up --build -d  # web app + API on :8000, worker alongside
open http://localhost:8000
```

Check your setup (model IDs, JSON replies, OCR, RxNorm, openFDA, Telegram):

```bash
pip install -r backend/requirements.txt   # plus Tesseract: apt install tesseract-ocr / brew install tesseract
python scripts/check_live.py --telegram
```

Development without Docker:

```bash
cd backend && pip install -r requirements.txt
uvicorn kinshield.api:app --reload --port 8000     # API
python -m kinshield.worker                          # reminders and alerts (second terminal)
cd ../frontend && npm install && npm run dev        # web app on :5173
cd ../backend && pytest -q                          # tests use fake model, drug-API and Telegram transports
```

## Architecture

```
Browser (caregiver + parent screens, React)
   │
FastAPI ── per-workspace SQLite + uploads (owner's disk)
   │   ├─ local OCR + redaction ──► Nebius Token Factory (Nemotron Nano / Super / Ultra / Omni)
   │   ├─ RxNorm (names) + openFDA (labels)
Worker ── reminders, escalation, Telegram "Seen" taps ──► Telegram Bot API ──► caregiver's phone
```

- `backend/kinshield/intake.py`: read → redact → extract → validate each field against its cited lines
- `reconcile.py`: confirmation records and the plan diff (a missing medicine is *not mentioned*, never *stopped*)
- `evidence.py` / `drugs.py`: label lookup and in-code matching
- `worker.py` / `notify.py`: reminders, escalation, honest delivery status (`accepted` ≠ seen)
- `memory.py` / `brief.py`: cited answers and the visit brief

## Privacy and safety choices

- Unconfirmed extractions never change the plan. Every change writes a `confirmations` row: who, what before, what after, when.
- Redaction is pattern-based and can miss identifiers. The exact text sent to the model is visible in the activity log. Use synthetic or redacted documents in the public demo.
- Acknowledgements are self-reported ("I took it"); KinShield never claims a dose was taken or missed.
- Telegram "accepted" means Telegram took the message. It counts as *seen* only when the caregiver taps **Seen**; otherwise it is re-sent once.
- Label results: *mention found*, *label found with no mention*, or *no label available*. The last two are not evidence of safety, and US labels may not cover medicines sold elsewhere.

## Data sources

- [RxNorm API](https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html), U.S. National Library of Medicine
- [openFDA drug label API](https://open.fda.gov/apis/drug/label/), U.S. FDA. openFDA is not intended for clinical decisions.

## License

MIT. See [LICENSE](LICENSE). All patient data in `demo-data/` and the seeded demo is invented.
