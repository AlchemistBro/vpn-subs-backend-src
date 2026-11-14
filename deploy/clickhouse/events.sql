CREATE TABLE IF NOT EXISTS default.product_events
(
    name    LowCardinality(String),
    user_id UInt64,
    ts      DateTime64(3, 'UTC'),
    props   String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (name, ts)
TTL toDateTime(ts) + INTERVAL 1 YEAR;
