-- Schema for ph-legislative-graph. Idempotent: CREATE TABLE IF NOT EXISTS.
-- Loader applies this on every run, so edits here only need a re-run,
-- not a docker volume wipe.

CREATE TABLE IF NOT EXISTS legislators (
    legislator_id  TEXT PRIMARY KEY,
    full_name      TEXT NOT NULL,
    last_name      TEXT,
    first_name     TEXT,
    middle_name    TEXT,
    aliases        TEXT[],
    memberships    JSONB
);

CREATE TABLE IF NOT EXISTS bills (
    bill_id     TEXT PRIMARY KEY,
    chamber     TEXT NOT NULL,
    congress    INTEGER NOT NULL,
    bill_number INTEGER NOT NULL,
    title       TEXT NOT NULL,
    long_title  TEXT,
    filed_date  DATE,
    status      TEXT,
    became_ra   BOOLEAN DEFAULT FALSE,
    UNIQUE (chamber, congress, bill_number)
);

CREATE TABLE IF NOT EXISTS bill_authors (
    bill_id           TEXT NOT NULL REFERENCES bills(bill_id),
    legislator_id     TEXT NOT NULL REFERENCES legislators(legislator_id),
    role              TEXT NOT NULL,
    resolution_method TEXT,
    PRIMARY KEY (bill_id, legislator_id, role)
);

CREATE TABLE IF NOT EXISTS bill_committees (
    bill_id        TEXT NOT NULL REFERENCES bills(bill_id),
    committee_name TEXT NOT NULL,
    committee_type TEXT,
    PRIMARY KEY (bill_id, committee_name)
);