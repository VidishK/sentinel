-- The demo company database: the data Sentinel's agent is allowed to touch.
--
-- Values are derived deterministically from row ids rather than random(), and
-- all timestamps are anchored to 2026-08-01 rather than now(), so the answers
-- to evaluation tasks stay stable every time the database is rebuilt. A rule
-- can only be A/B tested fairly if ground truth does not drift.
--
-- Three deliberate landmines are seeded here, because they are what the
-- agent's rules end up being about:
--   1. ~200 test accounts that inflate every naive count
--   2. duplicate order rows left behind by a "migration"
--   3. a price_history table nothing enforces writes to

CREATE SCHEMA IF NOT EXISTS company;

DROP TABLE IF EXISTS company.refunds;
DROP TABLE IF EXISTS company.order_items;
DROP TABLE IF EXISTS company.orders;
DROP TABLE IF EXISTS company.price_history;
DROP TABLE IF EXISTS company.prices;
DROP TABLE IF EXISTS company.products;
DROP TABLE IF EXISTS company.customers;

CREATE TABLE company.customers (
    id         INT PRIMARY KEY,
    name       STRING NOT NULL,
    email      STRING NOT NULL,
    country    STRING NOT NULL,
    is_test    BOOL NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE company.products (
    id       INT PRIMARY KEY,
    name     STRING NOT NULL,
    category STRING NOT NULL,
    active   BOOL NOT NULL DEFAULT true
);

CREATE TABLE company.prices (
    product_id INT PRIMARY KEY REFERENCES company.products (id),
    amount     DECIMAL(10, 2) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- Nothing in the schema forces a row in here when prices.amount changes.
-- That gap is the point: it is a convention, and conventions are what rules encode.
CREATE TABLE company.price_history (
    id         INT PRIMARY KEY DEFAULT unique_rowid(),
    product_id INT NOT NULL REFERENCES company.products (id),
    old_amount DECIMAL(10, 2) NOT NULL,
    new_amount DECIMAL(10, 2) NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE company.orders (
    id           INT PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES company.customers (id),
    status       STRING NOT NULL,
    total_amount DECIMAL(10, 2) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    INDEX orders_by_customer (customer_id),
    INDEX orders_by_date (created_at)
);

CREATE TABLE company.order_items (
    id         INT PRIMARY KEY DEFAULT unique_rowid(),
    order_id   INT NOT NULL REFERENCES company.orders (id),
    product_id INT NOT NULL REFERENCES company.products (id),
    qty        INT NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    INDEX items_by_order (order_id)
);

CREATE TABLE company.refunds (
    id         INT PRIMARY KEY DEFAULT unique_rowid(),
    order_id   INT NOT NULL REFERENCES company.orders (id),
    amount     DECIMAL(10, 2) NOT NULL,
    reason     STRING NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    INDEX refunds_by_order (order_id)
);

-- ---------------------------------------------------------------------------
-- Seed
-- ---------------------------------------------------------------------------

-- 2,000 customers. Every 10th is a test account (200 total).
INSERT INTO company.customers (id, name, email, country, is_test, created_at)
SELECT
    i,
    'Customer ' || i::STRING,
    CASE WHEN i % 10 = 0
         THEN 'qa+' || i::STRING || '@internal.test'
         ELSE 'user' || i::STRING || '@example.com'
    END,
    (ARRAY['US', 'GB', 'DE', 'IN', 'BR'])[1 + (i % 5)],
    i % 10 = 0,
    TIMESTAMPTZ '2026-08-01' - ((i % 540) || ' days')::INTERVAL
FROM generate_series(1, 2000) AS g (i);

INSERT INTO company.products (id, name, category, active)
SELECT
    i,
    'Product ' || i::STRING,
    (ARRAY['hardware', 'software', 'services', 'accessories'])[1 + (i % 4)],
    i % 17 <> 0
FROM generate_series(1, 40) AS g (i);

INSERT INTO company.prices (product_id, amount, updated_at)
SELECT
    i,
    (19.99 + (i * 7) % 480)::DECIMAL(10, 2),
    TIMESTAMPTZ '2026-08-01' - ((i % 90) || ' days')::INTERVAL
FROM generate_series(1, 40) AS g (i);

-- 12,000 orders spread over the last 18 months.
INSERT INTO company.orders (id, customer_id, status, total_amount, created_at)
SELECT
    i,
    1 + (i * 7) % 2000,
    (ARRAY['completed', 'completed', 'completed', 'pending', 'cancelled'])[1 + (i % 5)],
    (25 + (i * 13) % 900)::DECIMAL(10, 2),
    TIMESTAMPTZ '2026-08-01' - ((i % 540) || ' days')::INTERVAL
FROM generate_series(1, 12000) AS g (i);

-- Duplicate orders: 150 rows that repeat an existing order exactly except for
-- the primary key. Left behind by a migration, as these things always are.
INSERT INTO company.orders (id, customer_id, status, total_amount, created_at)
SELECT
    900000 + i,
    o.customer_id,
    o.status,
    o.total_amount,
    o.created_at
FROM generate_series(1, 150) AS g (i)
JOIN company.orders AS o ON o.id = i * 11;

INSERT INTO company.order_items (order_id, product_id, qty, unit_price)
SELECT
    o.id,
    1 + (o.id * 3 + k) % 40,
    1 + (o.id + k) % 3,
    (19.99 + ((1 + (o.id * 3 + k) % 40) * 7) % 480)::DECIMAL(10, 2)
FROM company.orders AS o
CROSS JOIN generate_series(0, 1) AS s (k)
WHERE (o.id + k) % 3 <> 2;

-- Refunds on roughly 8% of completed orders. Any revenue figure that ignores
-- this table is wrong, which is the single most common mistake on this schema.
INSERT INTO company.refunds (order_id, amount, reason, created_at)
SELECT
    o.id,
    (o.total_amount * 0.5)::DECIMAL(10, 2),
    (ARRAY['damaged', 'late delivery', 'changed mind', 'duplicate charge'])[1 + (o.id % 4)],
    o.created_at + INTERVAL '9 days'
FROM company.orders AS o
WHERE o.status = 'completed' AND o.id % 12 = 0;
