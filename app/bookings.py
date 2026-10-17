"""Bookings with hard seat limits. The check and the insert happen inside one write lock."""
import secrets
from decimal import ROUND_HALF_UP, Decimal

from .db import audit, transaction
from .timeutil import iso, today_local, utcnow


class BookingError(Exception):
    pass


def seats_booked(conn, departure_id: int) -> int:
    row = conn.execute("SELECT COALESCE(SUM(adults + children), 0) FROM bookings WHERE departure_id = ? AND status = 'confirmed'",
                       (departure_id,)).fetchone()
    return int(row[0])


def gst_cents(total_cents: int) -> int:
    """GST included in a GST-inclusive price = 1/11 of the total, rounded to the cent."""
    return int((Decimal(total_cents) / Decimal(11)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def create_booking(conn, *, departure_id: int, customer_name: str, email: str, phone: str, adults: int, children: int,
                   source: str, created_by: str, notes: str = "", enquiry_id=None, allow_duplicate=False) -> str:
    if adults < 0 or children < 0 or adults + children < 1:
        raise BookingError("A booking needs at least one guest.")
    if adults + children > 12:
        raise BookingError("Groups over 12 are custom requests: please ask the owner.")
    if not customer_name.strip() or "@" not in email:
        raise BookingError("Name and a valid email are required.")
    with transaction(conn):
        dep = conn.execute("SELECT d.*, t.adult_cents, t.child_cents, t.name AS tour_name FROM departures d "
                           "JOIN tours t ON t.code = d.tour_code WHERE d.id = ?", (departure_id,)).fetchone()
        if dep is None:
            raise BookingError("Departure not found.")
        if dep["status"] != "scheduled":
            raise BookingError("This departure is cancelled (weather). Choose another date.")
        if dep["date"] < today_local().isoformat():
            raise BookingError("This departure is in the past.")
        left = dep["capacity"] - seats_booked(conn, departure_id)
        if adults + children > left:
            raise BookingError(f"Only {max(left, 0)} seat(s) left on this departure. Nothing was booked.")
        dup = conn.execute("SELECT ref FROM bookings WHERE departure_id = ? AND lower(email) = lower(?) AND status = 'confirmed'",
                           (departure_id, email)).fetchone()
        if dup and not allow_duplicate:
            raise BookingError(f"{email} already has booking {dup['ref']} on this departure (possible double booking).")
        total = adults * dep["adult_cents"] + children * dep["child_cents"]
        ref = "RR-" + secrets.token_hex(3).upper()
        conn.execute("INSERT INTO bookings (ref, departure_id, customer_name, email, phone, adults, children, total_cents, source, "
                     "enquiry_id, notes, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (ref, departure_id, customer_name.strip(), email.strip(), phone.strip(), adults, children, total, source,
                      enquiry_id, notes.strip(), created_by, iso(utcnow())))
        audit(conn, created_by, "booking_created", ref, {"departure": departure_id, "guests": adults + children})
    return ref
