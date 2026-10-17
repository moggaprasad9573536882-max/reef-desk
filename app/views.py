"""Routes: sign-in, public enquiry form, staff inbox, bookings, invoices, manifests, weekly report."""
import hmac
import json
import secrets
import statistics
import time
from datetime import date, timedelta
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for)
from werkzeug.security import check_password_hash

from . import bookings, knowledge, triage
from .db import audit, get_db, transaction
from .timeutil import age_text, fmt_local, iso, parse, reply_by, today_local, utcnow

bp = Blueprint("main", __name__)
FAILED = {}                 # (username, ip) -> (count, locked_until): simple login throttling
MAX_FAILS, LOCK_SECONDS = 5, 300


def kb():
    return knowledge.load(current_app.config["KNOWLEDGE_FILE"])


# ---- access control -----------------------------------------------------------
@bp.before_app_request
def load_user_and_check_csrf():
    g.user = None
    if session.get("uid"):
        g.user = get_db().execute("SELECT id, username, display_name, role FROM users WHERE id=? AND active=1",
                                  (session["uid"],)).fetchone()
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not session.get("csrf") or not hmac.compare_digest(session["csrf"], sent):
            abort(400, "Form expired. Go back, refresh and try again.")


@bp.app_context_processor
def helpers():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return {"csrf_token": session["csrf"], "fmt": fmt_local, "age": age_text, "money": lambda c: f"${c / 100:,.2f}",
            "ai_name": triage.engine_from_config(current_app.config).name}


def roles(*allowed):
    def deco(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if g.user is None:
                return redirect(url_for("main.login", next=request.path))
            if g.user["role"] not in allowed:
                abort(403)
            return view(*a, **kw)
        return wrapper
    return deco


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        key = (username, request.remote_addr)
        count, locked = FAILED.get(key, (0, 0))
        if locked > time.time():
            flash("Too many attempts. Try again in 5 minutes.", "error")
            return render_template("login.html"), 429
        user = get_db().execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            FAILED.pop(key, None)
            session.clear()
            session.update(uid=user["id"], csrf=secrets.token_urlsafe(32))
            session.permanent = True
            audit(get_db(), username, "login")
            target = request.args.get("next", "")
            return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("main.home"))
        count += 1
        FAILED[key] = (count, time.time() + LOCK_SECONDS if count >= MAX_FAILS else 0)
        flash("Username or password is incorrect.", "error")
        return render_template("login.html"), 401
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@bp.route("/")
def home():
    if g.user is None:
        return redirect(url_for("main.enquire"))
    return redirect(url_for("main.departures" if g.user["role"] == "guide" else "main.inbox"))


# ---- enquiries ----------------------------------------------------------------
def intake(conn, *, source, name, email, phone, message, consent_marketing, actor):
    """Store, triage, and send an instant (non-AI) acknowledgement with a promised reply time."""
    now = utcnow()
    result = triage.triage(message, kb(), triage.engine_from_config(current_app.config))
    due = reply_by(now)
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO enquiries (received_at, source, name, email, phone, message, consent_marketing, status, intent, tour_code, "
            "requested_date, guests, confidence, handoff_reasons, ai_draft, drafted_by, reply_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iso(now), source, name, email, phone, message, int(consent_marketing), result["status"], result["intent"],
             result["tour_code"], result["requested_date"], result["guests"], result["confidence"],
             json.dumps(result["handoff_reasons"]), result["ai_draft"], result["drafted_by"], iso(due)))
        eid = cur.lastrowid
        if email:
            conn.execute("INSERT INTO outbox (created_at, to_addr, subject, body, kind, enquiry_id, sent_by) VALUES (?,?,?,?,?,?,?)",
                         (iso(now), email, f"We've got your enquiry (ENQ-{eid})",
                          f"Hi {name.split()[0]},\n\nThanks for contacting Reef & Ranges Tours. A member of our team will reply by "
                          f"{fmt_local(due)} (Cairns time). If it's urgent, call {kb()['business']['phone']}.\n\nReference: ENQ-{eid}",
                          "acknowledgement", eid, "system"))
        audit(conn, actor, "enquiry_received", f"ENQ-{eid}", {"status": result["status"], "engine": result["drafted_by"]})
    return eid, due


@bp.route("/enquire", methods=["GET", "POST"])
def enquire():
    """Public website form (also used by staff to log phone and Facebook enquiries)."""
    if request.method == "POST":
        f = request.form
        if f.get("website_url"):            # honeypot: bots fill hidden fields
            return render_template("thanks.html", ref="ENQ-0", due=None)
        name, email, phone, message = (f.get(k, "").strip() for k in ("name", "email", "phone", "message"))
        source = f.get("source", "website") if g.user else "website"
        errors = []
        if not 1 <= len(name) <= 80: errors.append("Please enter your name.")
        if email and "@" not in email: errors.append("Please check your email address.")
        if not email and not phone: errors.append("Please give an email or phone number so we can reply.")
        if not 5 <= len(message) <= 2000: errors.append("Please write a message (5 to 2000 characters).")
        if not g.user and not f.get("consent"): errors.append("Please tick the box so we can store your enquiry to reply.")
        if source not in ("website", "email", "phone", "facebook"): errors.append("Unknown source.")
        if errors:
            for e in errors: flash(e, "error")
            return render_template("enquire.html", form=f, tours=kb()["tours"]), 422
        eid, due = intake(get_db(), source=source, name=name, email=email, phone=phone, message=message,
                          consent_marketing=bool(f.get("marketing")), actor=g.user["username"] if g.user else "public-form")
        if g.user:
            return redirect(url_for("main.enquiry", eid=eid))
        return render_template("thanks.html", ref=f"ENQ-{eid}", due=fmt_local(due))
    return render_template("enquire.html", form={}, tours=kb()["tours"])


@bp.route("/enquiries")
@roles("owner", "staff")
def inbox():
    tab = request.args.get("tab", "open")
    where = {"open": "status IN ('needs_human','draft_ready')", "replied": "status='replied'", "all": "1=1"}.get(tab, "1=1")
    rows = get_db().execute(f"SELECT * FROM enquiries WHERE {where} ORDER BY received_at {'ASC' if tab == 'open' else 'DESC'}").fetchall()
    now = utcnow()
    return render_template("inbox.html", rows=rows, tab=tab, now=now, overdue=lambda r: r["reply_by"] and parse(r["reply_by"]) < now,
                           reasons=lambda r: json.loads(r["handoff_reasons"] or "[]"))


@bp.route("/enquiries/<int:eid>", methods=["GET", "POST"])
@roles("owner", "staff")
def enquiry(eid):
    conn = get_db()
    e = conn.execute("SELECT * FROM enquiries WHERE id=?", (eid,)).fetchone() or abort(404)
    warnings = []
    if request.method == "POST":
        action = request.form.get("action")
        if action == "close":
            conn.execute("UPDATE enquiries SET status='closed' WHERE id=?", (eid,))
            audit(conn, g.user["username"], "enquiry_closed", f"ENQ-{eid}")
            return redirect(url_for("main.inbox"))
        text = request.form.get("reply", "").replace("\r\n", "\n").strip()   # browsers send CRLF line endings
        warnings = triage.check_draft(text, kb()) if text else ["reply is empty"]
        if text and (not warnings or request.form.get("override") == "yes"):
            with transaction(conn):
                conn.execute("INSERT INTO outbox (created_at, to_addr, subject, body, kind, enquiry_id, sent_by) VALUES (?,?,?,?,?,?,?)",
                             (iso(utcnow()), e["email"] or e["phone"] or "-", f"Re: your enquiry (ENQ-{eid})", text, "reply", eid, g.user["username"]))
                conn.execute("UPDATE enquiries SET status='replied', first_reply_at=COALESCE(first_reply_at, ?), assigned_to=? WHERE id=?",
                             (iso(utcnow()), g.user["username"], eid))
                audit(conn, g.user["username"], "reply_sent", f"ENQ-{eid}",
                      {"edited_by_person": text != (e["ai_draft"] or "").strip(), "override": bool(warnings)})
            flash("Reply sent (prototype: stored in the outbox, not emailed).", "ok")
            return redirect(url_for("main.inbox"))
    sent = conn.execute("SELECT * FROM outbox WHERE enquiry_id=? ORDER BY id", (eid,)).fetchall()
    return render_template("enquiry.html", e=e, reasons=json.loads(e["handoff_reasons"] or "[]"), warnings=warnings,
                           draft=request.form.get("reply", e["ai_draft"] or ""), sent=sent, tour=knowledge.tour(kb(), e["tour_code"]))


# ---- bookings -----------------------------------------------------------------
@bp.route("/departures", methods=["GET", "POST"])
@roles("owner", "staff", "guide")
def departures():
    conn = get_db()
    if request.method == "POST":
        if g.user["role"] == "guide":
            abort(403)
        f = request.form
        try:
            ref = bookings.create_booking(conn, departure_id=int(f["departure_id"]), customer_name=f.get("name", ""),
                                          email=f.get("email", ""), phone=f.get("phone", ""), adults=int(f.get("adults") or 0),
                                          children=int(f.get("children") or 0), source=f.get("source", "phone"),
                                          created_by=g.user["username"], notes=f.get("notes", ""))
            return redirect(url_for("main.booking", ref=ref))
        except (bookings.BookingError, ValueError, KeyError) as exc:
            flash(str(exc) if isinstance(exc, bookings.BookingError) else "Please check the booking form.", "error")
    rows = conn.execute("SELECT d.*, t.name AS tour_name, (SELECT COALESCE(SUM(adults+children),0) FROM bookings b "
                        "WHERE b.departure_id=d.id AND b.status='confirmed') AS booked FROM departures d JOIN tours t ON t.code=d.tour_code "
                        "WHERE d.date BETWEEN ? AND ? ORDER BY d.date, t.code",
                        (today_local().isoformat(), (today_local() + timedelta(days=13)).isoformat())).fetchall()
    return render_template("departures.html", rows=rows)


@bp.route("/departures/<int:did>/weather-cancel", methods=["POST"])
@roles("owner")
def weather_cancel(did):
    conn = get_db()
    conn.execute("UPDATE departures SET status='weather_cancelled', cancel_reason=?, cancelled_by=? WHERE id=?",
                 (request.form.get("reason", "Weather")[:200], g.user["username"], did))
    audit(conn, g.user["username"], "weather_cancelled", f"departure {did}", request.form.get("reason", ""))
    flash("Departure cancelled. Each guest on the manifest must be contacted by a person (refund or new date).", "ok")
    return redirect(url_for("main.manifest", did=did))


@bp.route("/departures/<int:did>/manifest")
@roles("owner", "staff", "guide")
def manifest(did):
    conn = get_db()
    dep = conn.execute("SELECT d.*, t.name AS tour_name FROM departures d JOIN tours t ON t.code=d.tour_code WHERE d.id=?", (did,)).fetchone() or abort(404)
    rows = conn.execute("SELECT * FROM bookings WHERE departure_id=? AND status='confirmed' ORDER BY customer_name", (did,)).fetchall()
    return render_template("manifest.html", dep=dep, rows=rows, show_contact=g.user["role"] != "guide")


@bp.route("/bookings/<ref>")
@roles("owner", "staff")
def booking(ref):
    b = get_db().execute("SELECT b.*, d.date, d.tour_code, t.name AS tour_name, t.adult_cents, t.child_cents FROM bookings b "
                         "JOIN departures d ON d.id=b.departure_id JOIN tours t ON t.code=d.tour_code WHERE b.ref=?", (ref,)).fetchone() or abort(404)
    return render_template("booking.html", b=b, gst=bookings.gst_cents(b["total_cents"]), tour=knowledge.tour(kb(), b["tour_code"]), biz=kb()["business"])


# ---- weekly owner report ------------------------------------------------------
def weekly_report(conn, start: date) -> dict:
    s, e = start.isoformat(), (start + timedelta(days=7)).isoformat()
    enq = conn.execute("SELECT * FROM enquiries WHERE date(received_at) >= ? AND date(received_at) < ?", (s, e)).fetchall()
    waits = [(parse(r["first_reply_at"]) - parse(r["received_at"])).total_seconds() / 3600 for r in enq if r["first_reply_at"]]
    bk = conn.execute("SELECT b.*, d.tour_code FROM bookings b JOIN departures d ON d.id=b.departure_id "
                      "WHERE b.status='confirmed' AND date(b.created_at) >= ? AND date(b.created_at) < ?", (s, e)).fetchall()
    by = lambda rows, key: {k: sum(1 for r in rows if r[key] == k) for k in sorted({r[key] for r in rows})}
    return {"week": s, "enquiries": len(enq), "by_source": by(enq, "source"),
            "median_reply_h": round(statistics.median(waits), 1) if waits else None,
            "within_1h": round(100 * sum(w <= 1 for w in waits) / len(waits)) if waits else None,
            "open_over_24h": sum(1 for r in enq if not r["first_reply_at"] and r["status"] != "closed"
                                 and (utcnow() - parse(r["received_at"])).total_seconds() > 86400),
            "needs_human": sum(1 for r in enq if r["status"] == "needs_human"),
            "bookings": len(bk), "revenue_cents": sum(r["total_cents"] for r in bk), "bookings_by_source": by(bk, "source"),
            "guests": sum(r["adults"] + r["children"] for r in bk)}


@bp.route("/report")
@roles("owner")
def report():
    today = today_local()
    start = today - timedelta(days=today.weekday())
    if request.args.get("week"):
        try:
            start = date.fromisoformat(request.args["week"])
        except ValueError:
            abort(400)
    return render_template("report.html", r=weekly_report(get_db(), start), prev=(start - timedelta(days=7)).isoformat())


@bp.route("/audit")
@roles("owner")
def audit_log():
    return render_template("audit.html", rows=get_db().execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 200").fetchall())
