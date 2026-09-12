# Savivah backend (Firestore version)

A fork of the Python/FastAPI backend with PostgreSQL replaced by Firestore.
**The entire original Postgres/SQLAlchemy implementation is preserved
untouched in `app_postgres_legacy/`** (with `schema_postgres_legacy.sql`
alongside it) — nothing was deleted, per your request to keep it available
for future changes. `app/` is the new, active Firestore version that
actually runs.

## What was verified, and what wasn't (read this before deploying)

I don't have network access to `storage.googleapis.com` in this sandbox,
which is the only place Google hosts the actual Firestore emulator binary —
so unlike the Postgres version, **I could not run this against a real,
live Firestore instance**. What I *did* verify, the same way as always:

- Every file passes a Python syntax check.
- The full app imports successfully and all 26 routes register correctly
  (confirmed via the OpenAPI schema) — checked using Firestore's official
  "emulator mode" flag, which lets the client construct without real
  credentials or a real network call, so this catches import/wiring bugs
  without needing a live database.
- The server boots cleanly end-to-end (scheduler starts, shuts down
  gracefully).
- Missing required env vars still fail fast with a clear error, same as
  the Postgres version.

**What this does NOT prove**: that the Firestore transactions (checkout's
stock-locking, the register/create-store uniqueness checks) behave
correctly against a real Firestore backend under real concurrent load. The
logic follows Firestore's documented transaction semantics carefully, but
"carefully reasoned" isn't the same as "tested." **Before trusting this
with real money and real stock**, run through register -> create store ->
list product -> checkout -> (in a second browser tab) try to buy the last
unit at the same time, against your real Firebase project, and confirm
only one succeeds.

## Setup

1. Create a Firebase project at console.firebase.google.com if you don't have one, and enable Firestore (Native mode, not Datastore mode).
2. Project Settings -> Service Accounts -> Generate new private key. Save the JSON file.
3. Deploy the required composite indexes: `firebase deploy --only firestore:indexes` (uses `firestore.indexes.json` in this repo — without these, several queries will fail at runtime with a Firestore error containing a link to auto-create the missing index).
4. `firebase deploy --only firestore:rules` to apply `firestore.rules` (locks out any direct client access — the backend uses the Admin SDK, which bypasses these rules anyway, so this is just a safety net).

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill in FIREBASE_PROJECT_ID and FIREBASE_CREDENTIALS_PATH
python scripts/create_admin.py
python scripts/register_ipn.py
uvicorn app.main:app --reload --port 8000
```

## What changed, concretely

| Concern | Postgres version | Firestore version |
|---|---|---|
| Stock-overselling protection | `SELECT ... FOR UPDATE` (pessimistic lock) | Firestore transaction (optimistic retry) — same real-world guarantee, different mechanism |
| Uniqueness (email, phone, store slug) | `UNIQUE` column constraint | A small lookup document per unique value, claimed transactionally alongside the real record (see `services/firestore_helpers.py`) |
| Product search | `ILIKE '%term%'` (substring, anywhere) | Prefix match only (`name_lower >= term`) — a genuine capability loss, not just a syntax change |
| Store name on a product listing | `JOIN stores` | Denormalized onto the product at write time — a store rename won't retroactively update already-listed products |
| Admin stats/sellers aggregation | `SUM`/`COUNT`/`GROUP BY` in SQL | Streamed and summed in Python — fine at small-to-medium order volumes, a real scaling ceiling if the platform grows large (see comments in `routers/admin.py`) |
| Order + line items | Two tables (`orders`, `order_items`) | One document — items embedded as an array field on the order |

## Known gaps carried over unchanged from the Postgres version

Same as before — Fargo's webhook shape is still an assumption, there's no
real M-Pesa B2C payout call, no Pesapal refund call, and Fargo's webhook
signature isn't actually verified yet. See the connection reference
document from earlier in this project for full detail on all of these.

## Deploying to Render

`render.yaml` here provisions Redis and the web service, but **not** a
database — Firebase/Firestore is a separate Google Cloud product, not
something Render hosts. You'll need to:

1. Upload your service account JSON as a Render **Secret File**, at the
   path `/etc/secrets/firebase-service-account.json` (matching
   `FIREBASE_CREDENTIALS_PATH` in `render.yaml`).
2. Set `FIREBASE_PROJECT_ID` when prompted during the Blueprint deploy.
