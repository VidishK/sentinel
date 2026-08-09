-- Evaluation tasks used to A/B-test rules before they enter the trusted library.
-- Expected values were computed against the deterministic seed in 002_company_seed.sql.

DELETE FROM sentinel.rule_trials;
DELETE FROM sentinel.rule_decisions;
DELETE FROM sentinel.tasks;

INSERT INTO sentinel.tasks (task_family, kind, prompt, expected, checker) VALUES
(
  'revenue',
  'read',
  'What is total completed-order revenue after subtracting refunds? Return a single number.',
  '{"value": 3305555.50}'::JSONB,
  $$SELECT (COALESCE((SELECT sum(total_amount) FROM company.orders WHERE status = 'completed'), 0)
           - COALESCE((SELECT sum(r.amount) FROM company.refunds r
                       JOIN company.orders o ON o.id = r.order_id
                       WHERE o.status = 'completed'), 0))::DECIMAL(12,2) AS value$$
),
(
  'revenue',
  'read',
  'How many completed orders have at least one refund?',
  '{"value": 607}'::JSONB,
  $$SELECT count(DISTINCT r.order_id)::INT AS value
    FROM company.refunds r
    JOIN company.orders o ON o.id = r.order_id
    WHERE o.status = 'completed'$$
),
(
  'revenue',
  'read',
  'What is gross completed-order revenue before refunds?',
  '{"value": 3449445.00}'::JSONB,
  $$SELECT COALESCE(sum(total_amount), 0)::DECIMAL(12,2) AS value
    FROM company.orders WHERE status = 'completed'$$
),
(
  'test_accounts',
  'read',
  'How many real (non-test) customers are there?',
  '{"value": 1800}'::JSONB,
  $$SELECT count(*)::INT AS value FROM company.customers WHERE is_test = false$$
),
(
  'test_accounts',
  'read',
  'How many customers are test accounts?',
  '{"value": 200}'::JSONB,
  $$SELECT count(*)::INT AS value FROM company.customers WHERE is_test = true$$
),
(
  'test_accounts',
  'read',
  'How many orders were placed by non-test customers?',
  '{"value": 10935}'::JSONB,
  $$SELECT count(*)::INT AS value
    FROM company.orders o
    JOIN company.customers c ON c.id = o.customer_id
    WHERE c.is_test = false$$
),
(
  'duplicates',
  'read',
  'How many duplicate order rows exist (ids >= 900000)?',
  '{"value": 150}'::JSONB,
  $$SELECT count(*)::INT AS value FROM company.orders WHERE id >= 900000$$
),
(
  'duplicates',
  'read',
  'How many unique logical orders are there if we ignore duplicate ids >= 900000?',
  '{"value": 12000}'::JSONB,
  $$SELECT count(*)::INT AS value FROM company.orders WHERE id < 900000$$
),
(
  'customers',
  'read',
  'How many customers have been soft-deleted (deleted_at is not null)?',
  '{"value": 0}'::JSONB,
  $$SELECT count(*)::INT AS value FROM company.customers WHERE deleted_at IS NOT NULL$$
),
(
  'customers',
  'read',
  'How many customers are in country US among non-test accounts?',
  '{"value": 200}'::JSONB,
  $$SELECT count(*)::INT AS value
    FROM company.customers
    WHERE is_test = false AND country = 'US'$$
),
(
  'pricing',
  'read',
  'How many products currently have a price row?',
  '{"value": 40}'::JSONB,
  $$SELECT count(*)::INT AS value FROM company.prices$$
),
(
  'pricing',
  'read',
  'How many rows are in price_history right now?',
  '{"value": 0}'::JSONB,
  $$SELECT count(*)::INT AS value FROM company.price_history$$
);
