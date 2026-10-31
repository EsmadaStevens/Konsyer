import sqlite3

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS profiles(
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  interests TEXT DEFAULT '[]', home_lat REAL, home_lng REAL,
  autonomy TEXT NOT NULL DEFAULT 'ask' CHECK(autonomy IN ('ask','auto_small','pre_approved')),
  per_purchase_cap INTEGER NOT NULL DEFAULT 50000,
  daily_cap INTEGER NOT NULL DEFAULT 100000,
  auto_buy_under INTEGER NOT NULL DEFAULT 0,
  ride_home_default INTEGER NOT NULL DEFAULT 1,
  pre_approved_merchants TEXT DEFAULT '[]',
  memory TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS merchants(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, owner_user_id INTEGER REFERENCES users(id),
  whatsapp TEXT, area TEXT, verified INTEGER NOT NULL DEFAULT 0, is_sample INTEGER NOT NULL DEFAULT 0,
  social_url TEXT);
CREATE TABLE IF NOT EXISTS venues(id INTEGER PRIMARY KEY, name TEXT NOT NULL, area TEXT, lat REAL, lng REAL);
CREATE TABLE IF NOT EXISTS listings(
  id INTEGER PRIMARY KEY, merchant_id INTEGER REFERENCES merchants(id), venue_id INTEGER REFERENCES venues(id),
  type TEXT NOT NULL CHECK(type IN ('event','activity','dining','service')),
  name TEXT NOT NULL, description TEXT, tags TEXT DEFAULT '[]',
  lat REAL, lng REAL, area TEXT,
  price INTEGER, deposit INTEGER DEFAULT 0, serves_alcohol INTEGER NOT NULL DEFAULT 0,
  starts_at TEXT, ends_at TEXT,
  source TEXT NOT NULL DEFAULT 'merchant' CHECK(source IN ('merchant','flyer','screenshot','web_lead','seed')),
  status TEXT NOT NULL DEFAULT 'unverified' CHECK(status IN ('unverified','claimed','verified')),
  trust_score REAL NOT NULL DEFAULT 0.3,
  contact_whatsapp TEXT, source_url TEXT, fetched_at TEXT,
  is_sample INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS slots(
  id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL REFERENCES listings(id),
  starts_at TEXT NOT NULL, ends_at TEXT, capacity INTEGER NOT NULL DEFAULT 1, booked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS holds(
  id INTEGER PRIMARY KEY, slot_id INTEGER NOT NULL REFERENCES slots(id),
  user_id INTEGER NOT NULL REFERENCES users(id), qty INTEGER NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plans(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), title TEXT,
  budget INTEGER, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS plan_items(
  id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES plans(id),
  kind TEXT NOT NULL CHECK(kind IN ('booking','ride_to','ride_home','saved_event','contact_request')),
  listing_id INTEGER REFERENCES listings(id), slot_id INTEGER REFERENCES slots(id),
  starts_at TEXT, ends_at TEXT, amount INTEGER DEFAULT 0, status TEXT NOT NULL DEFAULT 'planned');
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY, plan_item_id INTEGER REFERENCES plan_items(id),
  user_id INTEGER NOT NULL REFERENCES users(id), listing_id INTEGER NOT NULL REFERENCES listings(id),
  slot_id INTEGER REFERENCES slots(id), qty INTEGER NOT NULL DEFAULT 1, amount INTEGER NOT NULL,
  qr_token TEXT UNIQUE, status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS wallets(user_id INTEGER PRIMARY KEY REFERENCES users(id), balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0));
CREATE TABLE IF NOT EXISTS wallet_txns(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  amount INTEGER NOT NULL, kind TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
  ref TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS pending_actions(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), plan_id INTEGER REFERENCES plans(id),
  kind TEXT NOT NULL, payload TEXT NOT NULL, amount INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','denied','executed','failed','expired')),
  policy_decision TEXT, policy_rule TEXT, policy_reason TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, decided_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, user_id INTEGER, pending_action_id INTEGER, event TEXT NOT NULL,
  why TEXT, rule TEXT, data TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS reminders(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), plan_id INTEGER REFERENCES plans(id),
  fire_at TEXT NOT NULL, text TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS flyers(
  id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), source TEXT, extracted TEXT,
  confidence REAL, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS web_leads(
  id INTEGER PRIMARY KEY AUTOINCREMENT, search_key TEXT NOT NULL, name TEXT NOT NULL, area TEXT, phone TEXT,
  price_text TEXT, price_for TEXT, source_url TEXT NOT NULL, platform TEXT,
  corroborated INTEGER NOT NULL DEFAULT 0, is_sample INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_web_leads_key ON web_leads(search_key);
CREATE TABLE IF NOT EXISTS recent_leads(
  user_id INTEGER PRIMARY KEY, lead_ids TEXT NOT NULL, shown_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vouchers(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK(kind IN ('booking','lead')),
  label TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount > 0),
  state TEXT NOT NULL DEFAULT 'held' CHECK(state IN ('held','released','refunded','forfeited')),
  booking_id INTEGER REFERENCES bookings(id), listing_id INTEGER REFERENCES listings(id),
  merchant_id INTEGER REFERENCES merchants(id), lead_id INTEGER,
  payee_name TEXT, payee_phone TEXT, payee_verified INTEGER NOT NULL DEFAULT 0,
  starts_at TEXT, ends_at TEXT,
  free_cancel_until TEXT, forfeit_pct INTEGER NOT NULL DEFAULT 0,
  auto_release_at TEXT, expires_at TEXT,
  paid_amount INTEGER NOT NULL DEFAULT 0, refunded_amount INTEGER NOT NULL DEFAULT 0,
  resolution TEXT, payout_ref TEXT, idem_key TEXT UNIQUE,
  created_at TEXT NOT NULL, resolved_at TEXT);
CREATE INDEX IF NOT EXISTS idx_vouchers_user ON vouchers(user_id, state);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY, booking_id INTEGER REFERENCES bookings(id), rating INTEGER, note TEXT);
"""


def get_conn(path: str = "concierge.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that older databases lack (CREATE TABLE IF NOT EXISTS never changes an existing table)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(merchants)")}
    if "social_url" not in cols:
        conn.execute("ALTER TABLE merchants ADD COLUMN social_url TEXT")
    lcols = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
    if "cancel_free_hours" not in lcols:  # deposit policy, fixed per listing: free cancellation window
        conn.execute("ALTER TABLE listings ADD COLUMN cancel_free_hours INTEGER NOT NULL DEFAULT 24")
    if "late_forfeit_pct" not in lcols:   # share of the held money the seller keeps after a late cancel
        conn.execute("ALTER TABLE listings ADD COLUMN late_forfeit_pct INTEGER NOT NULL DEFAULT 50")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()