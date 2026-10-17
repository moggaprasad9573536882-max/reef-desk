# Reef Desk: enquiry and booking assistant for Reef & Ranges Tours

Prototype for Getup Solutions' AI Digital Transformation Specialist assessment (Part 2). It tackles audit opportunities **#1 (instant enquiry response)** and **#2 (one booking record with seat limits)**. Reef & Ranges is fictional and **all data is synthetic**.

## What it does
| Area | What happens |
|---|---|
| **Enquiries** | Public form (consent box, honeypot) plus staff logging for phone, Facebook and email. Every enquiry gets an instant non-AI acknowledgement with a promised reply time (1 h in office hours, else 9 am next day, Cairns time). |
| **AI triage** | Privacy rules run first: health, children, refunds/cancellations and complaints go **straight to a person and are never sent to the AI**. Contact details are redacted. The AI (Gemini free tier via its OpenAI-compatible API) returns JSON; code then checks it: schema, prices and times against the approved knowledge base, links, and no promises (booked, refund, discount). Low confidence, over 12 guests or unknown topics go to a person. |
| **Human control** | AI drafts, a person decides. Nothing is sent without a staff click; edited replies are re-checked, and overriding a warning needs an explicit tick. Every action goes in the audit log. |
| **Bookings** | Seat limits per departure enforced inside a database write lock: two staff can't sell the last seats twice (tested with threads). Duplicate guest on the same departure is flagged. Weather cancel is owner-only. |
| **Paperwork** | Tax invoice with GST (1/11 of the GST-inclusive price) and a confirmation, generated from the booking. |
| **Roles** | owner (everything plus report and audit log), staff (enquiries and bookings), guide (manifests only, **no contact details**). |
| **Owner report** | Weekly enquiries by channel, median first reply, % within 1 h, open over 24 h, bookings and revenue by source. |
| **Security** | Hashed passwords, sign-in throttling (5 tries, then 5-minute lock), CSRF tokens on every form, CSP and anti-framing headers, HttpOnly session cookie. |

If no AI key is set (or the AI is down), the app uses `offline-rules-v1 (no AI)`, which only fills approved templates, so the desk keeps working. The banner always shows which engine drafted the replies.

## Run it
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
flask --app app init-db --demo        # prints the demo password (or set REEF_DEMO_PASSWORD first)
flask --app app run                   # http://127.0.0.1:5000  (users: olivia, sam, kai)
python -m pytest -q                   # 16 tests
```
Optional real AI: `export GEMINI_API_KEY=...` (free key from Google AI Studio) before `flask run`. `AI_MODEL` defaults to `gemini-2.5-flash`. **Keys live only in environment variables, never in the repo.**

## Deploy (free)
Render: New → Blueprint → this repo (`render.yaml`). Set `REEF_DEMO_PASSWORD` (and optionally `GEMINI_API_KEY`) in the dashboard. Free instances lose SQLite data on restart; that's fine for a demo, but use Postgres for real use.

## Structure
`app/triage.py` privacy rules, AI engine and output checks · `app/bookings.py` seat-limit logic and GST · `app/views.py` routes, sign-in and roles · `app/seed.py` synthetic data · `data/knowledge.json` the approved facts (owner signs off changes) · `tests/` pytest suite.

## Known limits (prototype)
Email is simulated (outbox table). There is no payment integration (placeholder link), no Postgres, and the throttle is in-memory, so it resets on restart. Keyword privacy rules are deliberately cautious: they send some harmless family enquiries to a person.
