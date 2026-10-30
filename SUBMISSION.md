# KinShield: Devpost submission text

## Project name
KinShield

## Tagline (under 200 characters)
A private care assistant that turns a parent's discharge papers into a confirmed medicine plan, reminders, Telegram alerts and a cited doctor brief. Runs on NVIDIA Nemotron via Nebius Token Factory.

## Track
Personal AI

## Inspiration
When an older parent comes home from hospital, a family member is handed a discharge summary that changes half their medicines. Someone has to work out what is new, what changed, what stopped, and what the paper forgot to mention, then make sure the new routine actually happens. That person is usually juggling work, and a mistake here is easy to make and hard to notice. We wanted an assistant that does this work with the caregiver, never behind their back, and keeps the family's health data under their control.

## What it does
- Reads a discharge summary or prescription (PDF or phone photo) on the server: PDF text or local OCR. Names, phone numbers, addresses and ID numbers are removed before any text reaches a model.
- Nemotron lists every medicine with line citations. Click a field and the exact line lights up on the original page. Medicine name, strength and dose must be confirmed by a person every time; lines that look like a medicine but weren't listed are flagged.
- Compares the confirmed document with the current medicine list: new, changed, explicitly stopped, possible duplicate, or not mentioned. A medicine the paper doesn't mention stays on the list until the caregiver decides. Every decision is recorded with who made it and when.
- Searches the US drug label (openFDA) of each new medicine for the names of the patient's other medicines, in code. Nemotron only explains passages that matched and drafts a question for the doctor. It never gives a safety verdict.
- Reminds the parent on a simple big-button screen ("I took it", "Skip", "I need help"). If a reminder gets no response, or the parent asks for help, the caregiver gets a real Telegram alert with a "Seen" button. The app reports delivery honestly: "sent to Telegram, not yet seen" until it is tapped.
- Answers the caregiver's questions and prepares a one-page visit brief (PDF). Every sentence cites the record it came from; sentences without a valid citation are removed in code.
- Installable on a phone as a web app (Add to Home Screen, camera upload).
- An activity log shows every model call with the exact redacted text sent, tokens, latency and cost.

## How we built it
- Backend: Python, FastAPI, SQLite (one database per workspace), a background worker for reminders and escalation, Tesseract OCR, PyMuPDF, ReportLab.
- Frontend: React + TypeScript, built as an installable web app.
- AI: four NVIDIA Nemotron models on Nebius Token Factory through its OpenAI-compatible API:
  - nemotron-3-super-120b-a12b: document extraction, answers, visit brief
  - NVIDIA-Nemotron-3-Nano-30B-A3B: plain-English notes on plan changes
  - Nemotron-3-Ultra-550b-a55b: explaining drug-label passages
  - Nemotron-3.5-Super-VL: opt-in reading of a page image when OCR fails (identifier regions masked first)
- Data: RxNorm (NLM) for name normalisation, openFDA for label text, Telegram Bot API for alerts.
- Hosting: Docker on Render (demo); the same image runs self-hosted on any VM with Caddy for https.

## Challenges we ran into
- In live tests on our sample discharge, Nemotron Nano missed one of seven medicines, while Super listed all seven. We moved extraction to Super and added a code check that flags any line that looks like a medicine but isn't cited, so a miss can't pass silently.
- Nemotron models reason before answering and can use thousands of tokens doing it, so we raised token budgets and handle "ran out of tokens" explicitly.
- Being honest in the interface: "accepted by Telegram" is not "seen"; a missing medicine is "not mentioned", not "stopped"; "no label found" is not "safe".

## Accomplishments that we're proud of
- Nothing changes in the care plan without a person confirming it, and the audit trail shows who confirmed what.
- Every model statement shown to the caregiver links back to its source line or record.
- Identifiers are removed before cloud calls, and the exact prompt is visible to the user.

## What we learned
Bigger is not always slower: Nemotron Super was both more accurate and faster than Nano on our extraction task. Grounding (line citations checked in code) mattered more than prompt wording.

## What's next for KinShield
A chat front end in the family's messaging app, voice check-ins for the parent, refill planning, Indian drug databases alongside openFDA, and a Hermes Agent or OpenShell-sandboxed deployment for fully self-hosted families.

## Built with
python, fastapi, sqlite, react, typescript, vite, nvidia-nemotron, nebius-token-factory, tesseract, pymupdf, reportlab, rxnorm, openfda, telegram-bot-api, docker, render

## Testing instructions for judges
1. Open the demo link and click "Start the demo". You get a private workspace with an invented patient (Ramesh, 74). The free server sleeps after 15 minutes without visitors; the first visit can take about a minute to wake it, and a workspace is reset if the server restarts.
2. Documents → "Read the PDF" (about 15 seconds). Tick each "Matches the document", then "Confirm and compare".
3. Decisions: apply the changes. Note Metformin: the document doesn't mention it, so it stays until you choose.
4. Medicines: the Clopidogrel card shows the label passages that mention Omeprazole and Aspirin.
5. Medicines → "Make a reminder due now", then More → "Parent's screen". Leave it unanswered for 2 minutes (or tap "I need help"). The alert is recorded in Activity log; Telegram alerts go to the team's demo chat unless you link your own Telegram in Settings.
6. More → Visit brief → "Prepare the brief", then open the PDF.
7. More → Activity log → "What was sent" shows the exact redacted text each Nemotron call received.

On a phone: open the link in Chrome and choose "Install" (Android) or Share → "Add to Home Screen" (iPhone).

## Links
- Demo: https://kinshield-lii3.onrender.com
- Code: https://github.com/gayatrisy/kinshield (MIT)
- Video: <YOUTUBE URL>
