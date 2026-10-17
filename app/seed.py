"""Synthetic demo data only: invented names, example.com emails, no real people."""
import random
from datetime import timedelta

from werkzeug.security import generate_password_hash

from . import knowledge, triage
from .timeutil import iso, reply_by, today_local, utcnow

FIRST = ["Mia", "Noah", "Aria", "Leo", "Isla", "Kenji", "Priya", "Lucas", "Freya", "Mateo", "Hana", "Oscar", "Zara", "Theo"]
LAST = ["Sample", "Fictional", "Testa", "Demo", "Placeholder", "Example", "Mock", "Synthetic"]
MESSAGES = [
    ("website", "Hi, is there space on the reef snorkel trip on {d} for 2 adults? How much is it?"),
    ("email", "What time is pickup for the Daintree rainforest tour? We are staying in the city."),
    ("facebook", "What should we bring for the waterfalls tablelands day?"),
    ("website", "Can we book the outer reef for 4 people on {d}?"),
    ("email", "My son is 3, can he come on the reef boat?"),
    ("phone", "I want a refund for my booking last week, the guide was late."),
    ("facebook", "Do you do private charters for a wedding group of 30?"),
    ("website", "hello do you have tours"),
    ("email", "I have asthma, is the Daintree walk ok for me?"),
    ("website", "How much is the Daintree tour for a couple on {d}?"),
]


def seed(conn, kb_path: str, password: str):
    kb = knowledge.load(kb_path)
    rnd = random.Random(42)
    pw = generate_password_hash(password)
    conn.executemany("INSERT OR IGNORE INTO users (username, display_name, role, password_hash) VALUES (?,?,?,?)",
                     [("olivia", "Olivia (Owner)", "owner", pw), ("sam", "Sam (Office)", "staff", pw), ("kai", "Kai (Guide)", "guide", pw)])
    for t in kb["tours"]:
        conn.execute("INSERT OR IGNORE INTO tours VALUES (?,?,?,?,?)", (t["code"], t["name"], t["adult_price"] * 100, t["child_price"] * 100, t["capacity"]))
        for i in range(-7, 21):
            conn.execute("INSERT OR IGNORE INTO departures (tour_code, date, capacity) VALUES (?,?,?)",
                         (t["code"], (today_local() + timedelta(days=i)).isoformat(), t["capacity"]))
    now = utcnow()
    engine = triage.OfflineEngine()
    for n in range(24):
        src, msg = MESSAGES[n % len(MESSAGES)]
        msg = msg.format(d=(today_local() + timedelta(days=rnd.randint(3, 15))).isoformat())
        received = now - timedelta(hours=rnd.randint(1, 150), minutes=rnd.randint(0, 59))
        r = triage.triage(msg, kb, engine)
        name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
        replied = n % 3 != 0 and received < now - timedelta(hours=3)
        conn.execute(
            "INSERT INTO enquiries (received_at, source, name, email, message, status, intent, tour_code, requested_date, guests, "
            "confidence, handoff_reasons, ai_draft, drafted_by, reply_by, first_reply_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iso(received), src, name, f"{name.split()[0].lower()}{n}@example.com", msg, "replied" if replied else r["status"],
             r["intent"], r["tour_code"], r["requested_date"], r["guests"], r["confidence"], __import__("json").dumps(r["handoff_reasons"]),
             r["ai_draft"], r["drafted_by"], iso(reply_by(received)),
             iso(received + timedelta(minutes=rnd.randint(8, 300))) if replied else None))
    deps = conn.execute("SELECT d.id, d.capacity, t.adult_cents, t.child_cents FROM departures d JOIN tours t ON t.code=d.tour_code "
                        "WHERE d.date >= ?", (today_local().isoformat(),)).fetchall()
    for n in range(60):
        dep = rnd.choice(deps)
        a, c = rnd.randint(1, 3), rnd.choice([0, 0, 1, 2])
        name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
        booked = conn.execute("SELECT COALESCE(SUM(adults+children),0) FROM bookings WHERE departure_id=?", (dep["id"],)).fetchone()[0]
        if booked + a + c > dep["capacity"]:
            continue
        conn.execute("INSERT INTO bookings (ref, departure_id, customer_name, email, phone, adults, children, total_cents, source, created_by, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (f"RR-D{n:04d}", dep["id"], name, f"guest{n}@example.com", "0400 000 000", a, c, a * dep["adult_cents"] + c * dep["child_cents"],
                      rnd.choice(["website", "email", "facebook", "phone", "walk-in"]), "sam", iso(now - timedelta(hours=rnd.randint(1, 160)))))
