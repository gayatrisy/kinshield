# KinShield

**A private care assistant for the family member who manages an older parent's medicines.**

Built for the **Nebius × NVIDIA Global AI Hackathon** (Personal AI track). Every AI step runs on **NVIDIA Nemotron** models through **Nebius Token Factory**.

**Live demo:** https://kinshield-lii3.onrender.com · **License:** MIT

When a parent comes home from hospital, the family gets a discharge summary that changes half their medicines. Someone has to work out what is new, what changed, what stopped and what the paper forgot to mention, and then make sure the new routine actually happens. KinShield does that work *with* the caregiver, never behind their back.

> KinShield organises what documents say. It does not give medical advice, judge whether medicines are safe, or decide doses.

---

## Features

| | What it does |
| --- | --- |
| **Reads documents privately** | Upload a PDF or take a phone photo. The page is read on the server (PDF text or local Tesseract OCR). Names, phone numbers, addresses and ID numbers are removed before any text goes to a model. |
| **Shows its source** | Nemotron lists every medicine with the line it came from. Click a field and that line lights up on the original page. Name, strength and dose must be confirmed by a person every time. Lines that look like a medicine but were not listed are flagged. |
| **Compares with the current plan** | New, changed, explicitly stopped, possible duplicate, or *not mentioned*. A medicine the paper doesn't mention stays on the list until the caregiver decides. Every decision is recorded with who made it and when. |
| **Checks drug labels** | For each new medicine, its US label (openFDA) is searched *in code* for the names of the other medicines. Nemotron only explains passages that matched and drafts a question for the doctor. Three outcomes, never a safety verdict. |
| **Reminds and escalates** | The parent gets a big-button screen: *I took it*, *Skip*, *I need help*. No answer within the window (2 minutes in the demo), or a help request, sends the caregiver a **Telegram** alert with a **Seen** button. |
| **Remembers** | Confirmed medicines, decisions, symptoms, alerts and label passages form the assistant's memory. *Ask KinShield* answers from it, and every sentence cites its record. |
| **Prepares the visit** | A one-page PDF brief for the doctor: current medicines, changes, self-reported acknowledgements, symptoms and questions, all cited. Uncited sentences are removed in code. |
| **Shows its work** | The activity log lists every model call with the exact redacted text sent, tokens, latency and cost, and every confirmation with who made it. |
| **Works on a phone** | Installable web app (Add to Home Screen), camera upload, phone-sized screens with a bottom tab bar. |

## Try it in five minutes

1. Open **https://kinshield-lii3.onrender.com** and click **Start the demo**. You get a private workspace with *Ramesh, 74*, an invented patient. No login is needed, and workspaces are isolated from each other.
   *Free hosting: the first visit after 15 idle minutes takes about a minute to wake the server, and a workspace is reset if the server restarts.*
2. Upload the sample documents from [`demo-data/`](demo-data) in order (see the [one-page guide](demo-data/0-README-demo-guide.pdf)):

   | File | What it shows |
   | --- | --- |
   | `1-discharge-summary.pdf` | Redaction, line-linked fields, confirmation. Then **Decide**: 3 new medicines, 2 dose changes, Ibuprofen stopped, Metformin *not mentioned*. **Medicines**: the Clopidogrel label mentions Omeprazole. |
   | `2-follow-up-prescription.pdf` | A later visit: Metoprolol increased, Omeprazole replaced by Pantoprazole, Metformin continued. KinShield remembers the plan and shows only what changed. |
   | `3-clinic-prescription-photo.jpg` | A phone photo read with local OCR; an "as needed" medicine gets no reminder times. |
   | `1-discharge-summary-photo.jpg` | Optional: the discharge as a tilted, noisy photo. |

   Reading a document takes about 35 seconds on the free server.
3. **Medicines → Make a reminder due now**, then **More → Parent's screen** and tap **I need help** (or wait 2 minutes). A Telegram alert is sent; the app shows it as *seen* only after **Seen** is tapped.
4. **Today → Note a symptom**, **More → Ask KinShield** ("What changed this week?"), **More → Visit brief → Prepare**, and **More → Activity log → What was sent**.

**Telegram:** demo alerts go to the team's chat. To get them yourself, open **Settings → Telegram alerts → Open Telegram and press Start**; this links @kinshieldbot to your own chat.

**On a phone:** open the link in Chrome and choose **Install** (Android), or **Share → Add to Home Screen** (iPhone).

## How NVIDIA Nemotron and Nebius Token Factory are used

All inference is a runtime call to the Token Factory OpenAI-compatible API ([`backend/kinshield/llm.py`](backend/kinshield/llm.py)). Every call is logged with model ID, tokens, latency and list-price cost, and is visible in the app.

| Task | Model | Why |
| --- | --- | --- |
| Turn redacted document text into structured medicines with line citations | `nvidia/nemotron-3-super-120b-a12b` | In live tests on the sample discharge, Super listed all 7 medicines in about 14 s; Nano missed one and took 30 s. |
| Answers in *Ask KinShield* and the visit brief | `nvidia/nemotron-3-super-120b-a12b` | Reliable cited JSON output |
| Plain-English notes on plan differences | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Short, simple wording; about 1.5 s |
| Explain an openFDA label passage and draft a question for the doctor | `nvidia/Nemotron-3-Ultra-550b-a55b` | Dense label text; rare calls, so cost stays low |
| Read a page image when OCR fails (opt-in, identifier regions masked first) | `nvidia/Nemotron-3.5-Super-VL` | Vision input |

Token Factory gives one endpoint for every Nemotron size, so moving a task to a bigger or smaller model is a one-line change (`KS_MODEL_*`), and per-token pricing let us show the real cost of each workflow in the app.

## Architecture

```
Phone / browser  (caregiver screens + parent's big-button screen, React PWA)
        │
FastAPI ─── one SQLite database + upload folder per workspace
   │  ├── PDF text / Tesseract OCR ──► redaction ──► Nebius Token Factory (Nemotron Super · Nano · Ultra · Super-VL)
   │  └── RxNorm (names) · openFDA (drug labels)
Worker ─── reminders · escalation · "Seen" taps ──► Telegram Bot API ──► caregiver's phone
```

| File | Responsibility |
| --- | --- |
| `backend/kinshield/intake.py` | Read → redact → extract → check every field against its cited lines; flag uncited medicine lines |
| `backend/kinshield/reconcile.py` | Confirmation records and the plan diff (a missing medicine is *not mentioned*, never *stopped*) |
| `backend/kinshield/evidence.py`, `drugs.py` | Label lookup and in-code matching |
| `backend/kinshield/worker.py`, `notify.py` | Reminders, escalation, honest delivery status (Telegram *accepted* ≠ *seen*) |
| `backend/kinshield/memory.py`, `brief.py` | Cited answers and the visit brief PDF |
| `backend/kinshield/redact.py` | Pattern-based removal of identifiers |
| `frontend/src/` | React + TypeScript web app, installable on phones |

## Run it yourself

You need a [Nebius Token Factory](https://tokenfactory.nebius.com) API key, and optionally a Telegram bot from [@BotFather](https://t.me/BotFather).

**With Docker (one container, worker included):**

```bash
git clone https://github.com/gayatrisy/kinshield && cd kinshield
cp .env.example .env            # add NEBIUS_API_KEY and, optionally, the Telegram values
docker build -t kinshield .
docker run -p 8000:8000 --env-file .env -v kinshield-data:/data kinshield
# open http://localhost:8000
```

**On Windows without Docker:** `powershell -ExecutionPolicy Bypass -File .\start-windows.ps1` (needs Python 3.10+; installs packages on first run).

**For development:**

```bash
cd backend && pip install -r requirements.txt     # plus Tesseract: apt install tesseract-ocr / brew install tesseract
uvicorn kinshield.api:app --reload --port 8000     # API
python -m kinshield.worker                         # reminders and alerts (second terminal)
cd ../frontend && npm install && npm run dev       # web app on :5173
cd ../backend && pytest -q                         # tests use fake model, drug-API and Telegram transports
python ../scripts/check_live.py --telegram         # checks model IDs, OCR, RxNorm, openFDA, Telegram with real keys
```

**Hosting:**
- **Render (free):** New Web Service → this repo → Docker → Free, health check `/api/health`, and the variables from `.env.example`.
- **Any VM with https:** `deploy/` has a Caddy compose file and cloud-init for a Nebius AI Cloud VM.

### Environment variables

| Name | Required | Purpose |
| --- | --- | --- |
| `NEBIUS_API_KEY` | yes | Nebius Token Factory key |
| `KS_MODEL_FAST`, `KS_MODEL_MAIN`, `KS_MODEL_DEEP`, `KS_MODEL_VISION` | no | Override the Nemotron model IDs |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` | for alerts | Your bot from @BotFather |
| `TELEGRAM_DEMO_CHAT_ID` | for alerts | Chat that receives alerts until a caregiver links their own |
| `KINSHIELD_PUBLIC_URL` | yes, when hosted | Public address, used in alert links |
| `KINSHIELD_SECRET` | yes | Long random string for signing |
| `KINSHIELD_DEMO_MODE` | no | `1` = 2-minute response window and demo helpers |

## Privacy and safety choices

- Unconfirmed extractions never change the plan. Every change writes a confirmation record: who, before, after, when.
- Identifiers are removed before any cloud call. Redaction is pattern-based and can miss things, so the exact text sent is visible in the activity log. Use synthetic or redacted documents in the public demo.
- Acknowledgements are self-reported. KinShield never claims a dose was taken or missed.
- A Telegram alert that Telegram *accepted* is shown as "sent, not yet seen" until **Seen** is tapped; it is re-sent once otherwise.
- Label results are *mention found*, *label found with no mention*, or *no label available*. The last two are not evidence of safety, and US labels may not cover medicines sold elsewhere.
- Each demo workspace has its own database and expires 7 days after last use. **Settings** has *Export my data* and *Delete everything*.

## Known limitations

- The free demo server sleeps when idle, so reminders run only while it is awake, and workspaces reset on restart. Self-hosting keeps everything.
- Drug-name normalisation uses RxNorm; when it is unreachable, the cleaned-up name as written is used instead.
- openFDA covers US labels; many medicines sold in India have no US label.

## Data sources

- [RxNorm API](https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html), U.S. National Library of Medicine
- [openFDA drug label API](https://open.fda.gov/apis/drug/label/), U.S. FDA. openFDA is not intended for clinical decisions.

## License

MIT. See [LICENSE](LICENSE). All patient data in `demo-data/` and in the seeded demo is invented.
