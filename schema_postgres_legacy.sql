-- Savivah Global Products — PostgreSQL schema (Python/FastAPI backend)
-- Run against a fresh database: psql -d savivah -f schema.sql

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------- Users (customer / seller only — admin is a separate table) ----------
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name       TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    phone_number    TEXT UNIQUE,
    password_hash   TEXT,                      -- null for Google-only accounts
    role            TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'seller')),
    national_id     TEXT,
    kra_pin         TEXT,
    google_id       TEXT UNIQUE,
    avatar_url      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Admin users — deliberately separate table, no self-registration ----------
-- Provisioned directly by a developer/ops person, e.g.:
--   INSERT INTO admin_users (full_name, email, password_hash)
--   VALUES ('Jane Admin', 'jane@savivah.co.ke', '<bcrypt hash>');
-- Generate a bcrypt hash with: python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('yourpassword'))"
CREATE TABLE admin_users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name       TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    totp_secret     TEXT,                      -- set once 2FA is enabled for this admin
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Stores ----------
CREATE TABLE stores (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    slug                TEXT UNIQUE NOT NULL,
    business_reg_number TEXT,
    verified            BOOLEAN NOT NULL DEFAULT false,
    payout_method       TEXT CHECK (payout_method IN ('mpesa', 'bank')),
    payout_account      TEXT,
    subscription_plan   TEXT NOT NULL DEFAULT 'none' CHECK (subscription_plan IN ('none', 'monthly', 'yearly')),
    subscription_expires_at TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Products ----------
CREATE TABLE products (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id        UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    category        TEXT,
    price           NUMERIC(12,2) NOT NULL CHECK (price >= 0),
    stock           INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    image_url       TEXT,
    is_featured     BOOLEAN NOT NULL DEFAULT false,
    featured_until  TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'hidden', 'out_of_stock')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_products_store ON products(store_id);
CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_created_at ON products(created_at DESC, id DESC);  -- for cursor pagination

-- ---------- Orders ----------
CREATE TABLE orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id         UUID NOT NULL REFERENCES users(id),
    store_id            UUID NOT NULL REFERENCES stores(id),
    subtotal            NUMERIC(12,2) NOT NULL,
    commission_rate     NUMERIC(5,4) NOT NULL DEFAULT 0.10,
    commission_amount   NUMERIC(12,2) NOT NULL,
    payout_amount       NUMERIC(12,2) NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'KES',
    status              TEXT NOT NULL DEFAULT 'pending_payment' CHECK (status IN (
                            'pending_payment', 'escrow_held', 'shipped', 'delivered',
                            'delivery_failed', 'refunded', 'disputed'
                         )),
    delivery_address    TEXT NOT NULL,
    shipped_at          TIMESTAMPTZ,
    delivered_at        TIMESTAMPTZ,
    payout_released_at  TIMESTAMPTZ,
    auto_release_at     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_store ON orders(store_id);
CREATE INDEX idx_orders_status ON orders(status);

CREATE TABLE order_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id      UUID NOT NULL REFERENCES products(id),
    product_name    TEXT NOT NULL,
    unit_price      NUMERIC(12,2) NOT NULL,
    quantity        INTEGER NOT NULL CHECK (quantity > 0)
);

-- ---------- Payments (Pesapal) ----------
CREATE TABLE payments (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id                    UUID NOT NULL REFERENCES orders(id),
    pesapal_order_tracking_id   TEXT UNIQUE,
    pesapal_merchant_reference  TEXT UNIQUE NOT NULL,
    amount                      NUMERIC(12,2) NOT NULL,
    payment_method              TEXT,
    status_code                 SMALLINT,
    status_description          TEXT,
    confirmation_code           TEXT,
    raw_ipn_payload              JSONB,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_payments_order ON payments(order_id);

-- ---------- Deliveries (Fargo) ----------
CREATE TABLE deliveries (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id                UUID NOT NULL REFERENCES orders(id) UNIQUE,
    fargo_tracking_id       TEXT UNIQUE,
    proof_of_shipment_url   TEXT,
    status                  TEXT NOT NULL DEFAULT 'awaiting_pickup' CHECK (status IN (
                                'awaiting_pickup', 'in_transit', 'delivered', 'failed', 'returned'
                             )),
    attempts                SMALLINT NOT NULL DEFAULT 0,
    last_status_at          TIMESTAMPTZ,
    raw_webhook_payload     JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Disputes ----------
CREATE TABLE disputes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL REFERENCES orders(id),
    raised_by       UUID NOT NULL REFERENCES users(id),
    reason          TEXT NOT NULL CHECK (reason IN ('not_delivered', 'item_not_as_described', 'damaged', 'other')),
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved_refund', 'resolved_release', 'rejected')),
    resolved_by     UUID REFERENCES admin_users(id),   -- now points at admin_users, not users
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Store subscriptions ----------
CREATE TABLE subscription_payments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id        UUID NOT NULL REFERENCES stores(id),
    plan            TEXT NOT NULL CHECK (plan IN ('monthly', 'yearly')),
    amount          NUMERIC(12,2) NOT NULL,
    pesapal_merchant_reference TEXT UNIQUE,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'failed')),
    period_start    TIMESTAMPTZ,
    period_end      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Featured listings ----------
CREATE TABLE featured_listings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id      UUID NOT NULL REFERENCES products(id),
    store_id        UUID NOT NULL REFERENCES stores(id),
    amount_paid     NUMERIC(12,2) NOT NULL,
    starts_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ends_at         TIMESTAMPTZ NOT NULL,
    pesapal_merchant_reference TEXT UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Payout ledger — now with an audit trail of which admin dispatched it ----------
CREATE TABLE payouts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id        UUID NOT NULL REFERENCES stores(id),
    order_id        UUID NOT NULL REFERENCES orders(id) UNIQUE,
    amount          NUMERIC(12,2) NOT NULL,
    method          TEXT CHECK (method IN ('mpesa', 'bank')),
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    dispatched_by   UUID REFERENCES admin_users(id),   -- audit trail: which admin clicked "Mark sent"
    sent_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
