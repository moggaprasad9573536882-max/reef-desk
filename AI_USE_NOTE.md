# AI-use note: Reef Desk (Part 2)

## Tools
- **Claude (Anthropic) in the Claude app, agent mode**: planned the build, wrote the code, tests and docs, ran the tests in a sandbox.
- **Gemini API, free tier** (OpenAI-compatible endpoint): the runtime AI that triages enquiries when `GEMINI_API_KEY` is set.

## Main prompts and instructions
1. My request: the assessment PDF plus "create everything I need to submit, end to end, don't miss anything."
2. Design rules I kept Claude to (from my audit):
   - AI drafts, a person decides.
   - Health, children, refunds and complaints never go to the AI.
   - Every price and time in a draft must match the approved knowledge base.
   - Seat limits are enforced in code.
   - Synthetic data only; no secrets in the repo.
3. The runtime prompt for Gemini is `SYSTEM_PROMPT` in `app/triage.py`: use only the knowledge base, never promise availability or refunds, return strict JSON.

## What the AI got wrong (caught during the build)
- The first promo-video render came out at 27.9 s, under the 30–45 s rule. Caught on the length check and fixed by adding pauses between lines.
- In earlier drafts of the audit, Claude's layout overflowed the 2-page limit three times. Each was caught by rendering and checking page height, then fixed by cutting text.
- A design risk I made it guard against: a generic phone-number regex would also delete dates like 2026-11-02 from enquiries. The redaction now needs 9+ digits, and a test proves dates survive.
- Generative AI can state wrong prices or promise bookings. So the app doesn't trust the model: `check_draft()` rejects unknown prices, times and links, and any promise. This is tested.

## What I reviewed or changed myself
> **Durga: fill this in honestly before submitting** (what you read, ran, tested or changed by hand). Suggested checks:
> - run `python -m pytest -q` (16 pass)
> - sign in as olivia, sam and kai and try each role
> - send an enquiry that mentions asthma and confirm it skips the AI
> - try to book more seats than are left
