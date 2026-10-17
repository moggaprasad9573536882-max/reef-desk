import json
import re
import threading

import pytest
from werkzeug.security import generate_password_hash

from app import bookings, create_app, db, knowledge, triage
from app.timeutil import today_local

PW = "test-only-password"


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.sqlite3"), "AI_PROVIDER": "offline", "SECRET_KEY": "test"})
    conn = db.connect(app.config["DATABASE"])
    db.init_schema(conn)
    for u, r in [("olivia", "owner"), ("sam", "staff"), ("kai", "guide")]:
        conn.execute("INSERT INTO users (username, display_name, role, password_hash) VALUES (?,?,?,?)", (u, u, r, generate_password_hash(PW)))
    conn.execute("INSERT INTO tours VALUES ('REEF','Outer Reef Snorkel Day',26900,16900,40)")
    conn.execute("INSERT INTO departures (tour_code, date, capacity) VALUES ('REEF', ?, 4)", (today_local().isoformat(),))
    conn.close()
    return app


@pytest.fixture
def kb(app):
    return knowledge.load(app.config["KNOWLEDGE_FILE"])


def csrf(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def login(client, user="sam", pw=PW):
    return client.post("/login", data={"username": user, "password": pw, "csrf_token": csrf(client)})


# ---- access control ----
def test_staff_pages_need_sign_in(app):
    c = app.test_client()
    assert c.get("/enquiries").status_code == 302
    assert c.get("/report").status_code == 302


def test_roles_are_enforced(app):
    c = app.test_client()
    login(c, "kai")
    assert c.get("/enquiries").status_code == 403      # guides cannot read enquiries
    assert c.get("/departures").status_code == 200
    c2 = app.test_client(); login(c2, "sam")
    assert c2.get("/report").status_code == 403        # only the owner sees the report


def test_wrong_password_is_rejected_and_throttled(app):
    c = app.test_client()
    for _ in range(5):
        assert login(c, "olivia", "wrong").status_code == 401
    assert login(c, "olivia", PW).status_code == 429   # locked even with the right password


def test_post_without_csrf_token_is_refused(app):
    c = app.test_client()
    assert c.post("/login", data={"username": "sam", "password": PW}).status_code == 400


# ---- triage safety ----
def test_health_details_never_reach_the_ai(kb):
    class Spy:
        name = "spy"; called = False
        def triage(self, text, kb): Spy.called = True
    r = triage.triage("I have asthma, is the reef trip ok?", kb, Spy())
    assert r["status"] == "needs_human" and not Spy.called


def test_contact_details_are_redacted_before_ai():
    out = triage.redact("Call me on 0400 123 456 or mail jo@example.com on 2026-11-02")
    assert "0400" not in out and "jo@example.com" not in out and "2026-11-02" in out


def test_invented_price_and_promise_are_caught(kb):
    problems = triage.check_draft("Good news, you're booked! It's only $199 per adult and pickup is 5:10 am.", kb)
    text = " ".join(problems)
    assert "$199" in text and "5:10" in text and "promises" in text


def test_quoting_approved_policy_is_allowed(kb):
    assert triage.check_draft(kb["policies"]["weather"], kb) == []


def test_ai_outage_hands_over_to_a_person(kb):
    class Down:
        name = "gemini"
        def triage(self, text, kb): raise triage.EngineError("AI service unavailable (URLError)")
    r = triage.triage("How much is the reef snorkel trip?", kb, Down())
    assert r["status"] == "needs_human" and "unavailable" in r["handoff_reasons"][0]


def test_bad_ai_output_hands_over(kb):
    class Junk:
        name = "gemini"
        def triage(self, text, kb): return {"intent": "booking_request"}
    assert triage.triage("Book the reef please", kb, Junk())["status"] == "needs_human"


def test_good_answer_becomes_a_draft_for_approval(kb):
    r = triage.triage("How much is the reef snorkel trip for 2 people?", kb, triage.OfflineEngine())
    assert r["status"] == "draft_ready" and "$269" in r["ai_draft"]


# ---- bookings ----
def test_capacity_is_never_exceeded(app):
    conn = db.connect(app.config["DATABASE"])
    dep = conn.execute("SELECT id FROM departures").fetchone()[0]
    bookings.create_booking(conn, departure_id=dep, customer_name="A Sample", email="a@example.com", phone="", adults=3, children=0, source="phone", created_by="sam")
    with pytest.raises(bookings.BookingError, match="Only 1 seat"):
        bookings.create_booking(conn, departure_id=dep, customer_name="B Sample", email="b@example.com", phone="", adults=2, children=0, source="phone", created_by="sam")


def test_simultaneous_bookings_cannot_double_book_the_last_seats(app):
    dep = db.connect(app.config["DATABASE"]).execute("SELECT id FROM departures").fetchone()[0]
    results = []
    def book(i):
        conn = db.connect(app.config["DATABASE"])
        try:
            bookings.create_booking(conn, departure_id=dep, customer_name=f"G{i}", email=f"g{i}@example.com", phone="", adults=3, children=0, source="web", created_by="sam")
            results.append("ok")
        except bookings.BookingError:
            results.append("refused")
    threads = [threading.Thread(target=book, args=(i,)) for i in range(4)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert results.count("ok") == 1 and bookings.seats_booked(db.connect(app.config["DATABASE"]), dep) == 3


def test_same_guest_twice_is_flagged_as_double_booking(app):
    conn = db.connect(app.config["DATABASE"])
    dep = conn.execute("SELECT id FROM departures").fetchone()[0]
    kw = dict(departure_id=dep, customer_name="A", email="a@example.com", phone="", adults=1, children=0, source="web", created_by="sam")
    bookings.create_booking(conn, **kw)
    with pytest.raises(bookings.BookingError, match="double booking"):
        bookings.create_booking(conn, **kw)


def test_gst_is_one_eleventh():
    assert bookings.gst_cents(26900) == 2445 and bookings.gst_cents(1100) == 100


# ---- end to end through the web ----
def test_public_enquiry_to_staff_reply(app):
    c = app.test_client()
    token = csrf(c, "/enquire")
    r = c.post("/enquire", data={"csrf_token": token, "name": "Mia Sample", "email": "mia@example.com",
                                 "message": "What time is pickup for the reef snorkel trip?", "consent": "1"})
    assert r.status_code == 200 and "ENQ-1" in r.get_data(as_text=True)
    conn = db.connect(app.config["DATABASE"])
    assert conn.execute("SELECT kind FROM outbox").fetchone()["kind"] == "acknowledgement"
    staff = app.test_client(); login(staff, "sam")
    page = staff.get("/enquiries/1").get_data(as_text=True)
    t = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    staff.post("/enquiries/1", data={"csrf_token": t, "action": "send", "reply": "Pickup is between 6:45 am and 7:15 am. The Reef & Ranges team"})
    assert conn.execute("SELECT status FROM enquiries WHERE id=1").fetchone()["status"] == "replied"
