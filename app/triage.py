"""Enquiry triage: privacy rules first, then AI (or the offline engine), then code checks.
The AI never sends anything. It only drafts; a person approves."""
import json
import re
import urllib.error
import urllib.request

from . import knowledge

# ---- 1. Privacy: what never goes to an AI ------------------------------------
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")

HUMAN_ONLY = {
    "health, medical or mobility details": r"\b(medical|medication|asthma\w*|diabet\w*|heart|epilep\w*|seizure\w*|allerg\w*|anaphyla\w*|pregnan\w*|injur\w*|surgery|disabilit\w*|wheelchair|mobility|seasick\w*|health)\b",
    "children or minors": r"\b(child|children|kid|kids|baby|babies|infant|toddler|minor|\d{1,2}\s*(?:yo|y/o|year[- ]old|years old))\b",
    "refund, cancellation or change": r"\b(refund\w*|cancel\w*|reschedul\w*|money back|chargeback)\b",
    "complaint or legal issue": r"\b(complain\w*|disappoint\w*|unacceptable|terrible|awful|lawyer|legal|sue|accc|ombudsman)\b",
}


def redact(text: str) -> str:
    text = EMAIL_RE.sub("[email removed]", text)
    return PHONE_RE.sub(lambda m: "[phone removed]" if sum(c.isdigit() for c in m.group()) >= 9 else m.group(), text)


def human_only_reasons(text: str) -> list[str]:
    return [label for label, pat in HUMAN_ONLY.items() if re.search(pat, text, re.IGNORECASE)]


# ---- 2. Engines ---------------------------------------------------------------
INTENTS = {"booking_request", "availability", "price", "pickup", "what_to_bring", "weather", "other"}
KEYS = {"intent", "tour_code", "requested_date", "guests", "confidence", "draft_reply"}


class EngineError(Exception):
    pass


class OfflineEngine:
    """Deterministic fallback used when no AI key is set or the AI is switched off.
    It only fills approved templates, so it cannot invent facts."""
    name = "offline-rules-v1 (no AI)"

    def triage(self, text: str, kb: dict) -> dict:
        low = text.lower()
        tour = next((t for t in kb["tours"] if any(k in low for k in t["keywords"])), None)
        m = re.search(r"\b(\d{1,2})\s*(?:people|persons|adults|guests|of us|pax)\b", low)
        guests = int(m.group(1)) if m else (2 if re.search(r"\b(couple|two of us|my partner|my wife|my husband)\b", low) else None)
        d = re.search(r"\b(20\d\d-\d\d-\d\d)\b", text)
        intent = ("pickup" if "pick" in low else "what_to_bring" if "bring" in low or "wear" in low else
                  "weather" if "weather" in low or "rain" in low and not tour else
                  "price" if "price" in low or "cost" in low or "how much" in low else
                  "booking_request" if re.search(r"\b(book|reserve|seats?|spots?|available|availability)\b", low) else "other")
        conf = 0.85 if tour and intent != "other" else 0.5
        return {"intent": intent, "tour_code": tour["code"] if tour else None, "requested_date": d.group(1) if d else None,
                "guests": guests, "confidence": conf, "draft_reply": template_reply(kb, tour, intent)}


def template_reply(kb: dict, tour: dict | None, intent: str) -> str:
    if not tour:
        return ("Thanks for getting in touch with Reef & Ranges Tours. A member of our team will reply shortly with the details you need.\n\n"
                "The Reef & Ranges team")
    lines = [f"Thanks for your interest in our {tour['name']}.",
             f"Prices are ${tour['adult_price']} per adult and ${tour['child_price']} per child (4 to 14). It runs for about {tour['duration']}.",
             tour["pickup"]]
    if intent == "what_to_bring":
        lines.append("Please bring " + ", ".join(tour["what_to_bring"]) + ".")
    if intent == "weather":
        lines.append(kb["policies"]["weather"])
    lines.append("A member of our team will confirm availability for your date. Reply here or call us on " + kb["business"]["phone"] + ".")
    return "\n".join(lines) + "\n\nThe Reef & Ranges team"


SYSTEM_PROMPT = """You are the enquiry assistant for Reef & Ranges Tours in Cairns, Australia.
Use ONLY facts from the KNOWLEDGE BASE. If something is not in it, say a team member will confirm, and lower your confidence.
Never promise availability, a confirmed booking, refunds, discounts or anything free. Never invent prices, times or links.
Reply with ONE JSON object only, with keys:
intent (one of booking_request, availability, price, pickup, what_to_bring, weather, other),
tour_code (REEF, RAIN, TABLE or null), requested_date (YYYY-MM-DD or null), guests (integer or null),
confidence (0 to 1), draft_reply (friendly, under 120 words, Australian English, signed "The Reef & Ranges team").
KNOWLEDGE BASE:
"""


class OpenAICompatibleEngine:
    """Gemini free tier via its OpenAI-compatible endpoint (any compatible API works)."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 15):
        self.base_url, self.api_key, self.model, self.timeout = base_url.rstrip("/"), api_key, model, timeout
        self.name = model

    def triage(self, text: str, kb: dict) -> dict:
        kb_public = {k: v for k, v in kb.items() if not k.startswith("_")}
        body = json.dumps({"model": self.model, "temperature": 0.2, "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": SYSTEM_PROMPT + json.dumps(kb_public)},
                                        {"role": "user", "content": text}]}).encode()
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                content = json.load(resp)["choices"][0]["message"]["content"]
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
            raise EngineError(f"AI service unavailable ({type(exc).__name__})") from exc
        m = re.search(r"\{.*\}", content or "", re.DOTALL)
        if not m:
            raise EngineError("AI reply was not JSON")
        try:
            return json.loads(m.group())
        except ValueError as exc:
            raise EngineError("AI reply was not valid JSON") from exc


def engine_from_config(cfg) -> object:
    if cfg.get("AI_PROVIDER") != "offline" and cfg.get("AI_API_KEY"):
        return OpenAICompatibleEngine(cfg["AI_BASE_URL"], cfg["AI_API_KEY"], cfg["AI_MODEL"], cfg.get("AI_TIMEOUT", 15))
    return OfflineEngine()


# ---- 3. Code checks on whatever the engine returned ---------------------------
PROMISES = re.compile(r"\b(guarantee\w*|refund\w*|discount\w*|free of charge|complimentary|you(?:'re| are) booked|"
                      r"booking (?:is )?confirmed|we have (?:seats|space|availability)|seats? (?:are|is) available|reserved)\b", re.I)


def check_draft(draft: str, kb: dict) -> list[str]:
    problems = []
    for value in re.findall(r"\$\s?(\d+(?:\.\d\d)?)", draft):
        if float(value) not in {float(p) for p in knowledge.allowed_prices(kb)}:
            problems.append(f"price ${value} is not in the approved price list")
    ok_times = knowledge.allowed_times(kb)
    for h, m, ap in knowledge.TIME_RE.findall(draft):
        if knowledge.minutes(h, m, ap) not in ok_times:
            problems.append(f"time {h}:{m or '00'} {ap} is not in the knowledge base")
    for url in re.findall(r"https?://[^\s)]+", draft):
        if "reefandranges.example" not in url:
            problems.append(f"link {url} is not an approved link")
    scrubbed = draft
    for sentence in knowledge.policy_sentences(kb):   # quoting an approved policy word-for-word is fine
        scrubbed = scrubbed.replace(sentence, "")
    if PROMISES.search(scrubbed):
        problems.append("promises something only a person can confirm (" + PROMISES.search(scrubbed).group() + ")")
    if len(draft) > 1500:
        problems.append("draft is too long")
    return problems


def validate(raw: dict, kb: dict) -> tuple[dict, list[str]]:
    errs = []
    if not isinstance(raw, dict) or not KEYS.issubset(raw):
        return {}, ["AI output was missing required fields"]
    if raw["intent"] not in INTENTS:
        errs.append(f"unknown intent {raw['intent']!r}")
    if raw["tour_code"] is not None and raw["tour_code"] not in kb["_by_code"]:
        errs.append(f"unknown tour {raw['tour_code']!r}")
    try:
        conf = float(raw["confidence"])
    except (TypeError, ValueError):
        conf = 0.0
        errs.append("confidence was not a number")
    guests = raw["guests"] if isinstance(raw["guests"], int) else None
    draft = raw["draft_reply"] if isinstance(raw["draft_reply"], str) else ""
    if not draft.strip():
        errs.append("empty draft")
    clean = {"intent": raw["intent"], "tour_code": raw["tour_code"], "requested_date": raw["requested_date"],
             "guests": guests, "confidence": max(0.0, min(conf, 1.0)), "draft_reply": draft.strip()}
    return clean, errs


CONFIDENCE_FLOOR = 0.7
MAX_GROUP = 12


def triage(message: str, kb: dict, engine) -> dict:
    """Returns the fields to store on the enquiry. status is 'draft_ready' only if every check passed."""
    result = {"status": "needs_human", "intent": None, "tour_code": None, "requested_date": None, "guests": None,
              "confidence": None, "ai_draft": None, "drafted_by": None, "handoff_reasons": []}
    reasons = human_only_reasons(message)
    if reasons:  # privacy and policy topics: a person handles these, the AI never sees them
        result["handoff_reasons"] = [f"{r}: handled by a person (not sent to AI)" for r in reasons]
        result["drafted_by"] = "not sent to AI"
        return result
    result["drafted_by"] = engine.name
    try:
        raw = engine.triage(redact(message), kb)
    except EngineError as exc:
        result["handoff_reasons"] = [f"{exc}: a person will reply"]
        return result
    clean, errs = validate(raw, kb)
    if not clean:
        result["handoff_reasons"] = errs
        return result
    problems = errs + check_draft(clean["draft_reply"], kb)
    if clean["confidence"] < CONFIDENCE_FLOOR:
        problems.append(f"low confidence ({clean['confidence']:.2f})")
    if clean["guests"] and clean["guests"] > MAX_GROUP:
        problems.append(f"group of {clean['guests']} (over {MAX_GROUP}): custom request")
    if clean["intent"] == "other":
        problems.append("question type not recognised")
    result.update({k: clean[k] for k in ("intent", "tour_code", "requested_date", "guests", "confidence")})
    result["ai_draft"] = clean["draft_reply"]
    result["handoff_reasons"] = problems
    result["status"] = "needs_human" if problems else "draft_ready"
    return result
