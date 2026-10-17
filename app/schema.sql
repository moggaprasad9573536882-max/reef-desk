-- Reef Desk schema (SQLite). All data in this prototype is synthetic.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY,
  username      TEXT UNIQUE NOT NULL,
  display_name  TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('owner', 'staff', 'guide')),
  password_hash TEXT NOT NULL,
  active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tours (
  code             TEXT PRIMARY KEY,
  name             TEXT NOT NULL,
  adult_cents      INTEGER NOT NULL CHECK (adult_cents > 0),
  child_cents      INTEGER NOT NULL CHECK (child_cents > 0),
  default_capacity INTEGER NOT NULL CHECK (default_capacity > 0)
);

-- One row per tour per day. Capacity is the hard seat limit (boat or bus).
CREATE TABLE IF NOT EXISTS departures (
  id            INTEGER PRIMARY KEY,
  tour_code     TEXT NOT NULL REFERENCES tours(code),
  date          TEXT NOT NULL,                       -- YYYY-MM-DD, Cairns time
  capacity      INTEGER NOT NULL CHECK (capacity > 0),
  status        TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'weather_cancelled')),
  cancel_reason TEXT,
  cancelled_by  TEXT,
  UNIQUE (tour_code, date)
);

CREATE TABLE IF NOT EXISTS enquiries (
  id              INTEGER PRIMARY KEY,
  external_id     TEXT UNIQUE,                       -- idempotency key from integrations (n8n)
  received_at     TEXT NOT NULL,                     -- ISO 8601 UTC
  source          TEXT NOT NULL CHECK (source IN ('website', 'email', 'phone', 'facebook')),
  name            TEXT NOT NULL,
  email           TEXT,
  phone           TEXT,
  message         TEXT NOT NULL,
  consent_marketing INTEGER NOT NULL DEFAULT 0,
  status          TEXT NOT NULL CHECK (status IN ('needs_human', 'draft_ready', 'replied', 'closed')),
  intent          TEXT,
  tour_code       TEXT,
  requested_date  TEXT,
  guests          INTEGER,
  confidence      REAL,
  handoff_reasons TEXT NOT NULL DEFAULT '[]',        -- JSON list of strings
  ai_draft        TEXT,
  drafted_by      TEXT,                              -- which engine/model produced the draft
  reply_by        TEXT,                              -- promised reply time (ISO UTC)
  first_reply_at  TEXT,
  assigned_to     TEXT
);

CREATE TABLE IF NOT EXISTS bookings (
  id            INTEGER PRIMARY KEY,
  ref           TEXT UNIQUE NOT NULL,
  departure_id  INTEGER NOT NULL REFERENCES departures(id),
  customer_name TEXT NOT NULL,
  email         TEXT NOT NULL,
  phone         TEXT,
  adults        INTEGER NOT NULL CHECK (adults >= 0),
  children      INTEGER NOT NULL CHECK (children >= 0),
  total_cents   INTEGER NOT NULL CHECK (total_cents >= 0),
  source        TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'cancelled')),
  enquiry_id    INTEGER REFERENCES enquiries(id),
  notes         TEXT,
  created_by    TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  CHECK (adults + children > 0)
);
CREATE INDEX IF NOT EXISTS idx_bookings_departure ON bookings(departure_id, status);

-- Prototype "sent mail": nothing leaves the app; messages are stored here.
CREATE TABLE IF NOT EXISTS outbox (
  id          INTEGER PRIMARY KEY,
  created_at  TEXT NOT NULL,
  to_addr     TEXT NOT NULL,
  subject     TEXT NOT NULL,
  body        TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('acknowledgement', 'reply', 'confirmation')),
  enquiry_id  INTEGER REFERENCES enquiries(id),
  booking_id  INTEGER REFERENCES bookings(id),
  sent_by     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id      INTEGER PRIMARY KEY,
  at      TEXT NOT NULL,
  actor   TEXT NOT NULL,
  action  TEXT NOT NULL,
  entity  TEXT,
  detail  TEXT
);
