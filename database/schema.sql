-- schema.sql - Telusko Support Chat database schema (SQLite)
--
-- Create the database by hand:   sqlite3 telusko.db < schema.sql
-- Or reset + seed everything:    python db.py

DROP TABLE IF EXISTS faqs;
DROP TABLE IF EXISTS ticket_messages;
DROP TABLE IF EXISTS tickets;
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS enrollments;
DROP TABLE IF EXISTS courses;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,                          -- bcrypt hash, never the plain password
    role          TEXT NOT NULL CHECK (role IN ('learner', 'support', 'admin')),
    scopes        TEXT                                    -- custom permissions (space-separated), NULL = role defaults
);

CREATE TABLE courses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    description  TEXT,
    price_inr    INTEGER NOT NULL,
    access_days  INTEGER NOT NULL DEFAULT 365
);

CREATE TABLE enrollments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL REFERENCES users(id),
    course_id           INTEGER NOT NULL REFERENCES courses(id),
    enrolled_at         TEXT NOT NULL,                    -- 'YYYY-MM-DD HH:MM:SS'
    access_valid_till   TEXT NOT NULL,
    progress_percent    INTEGER NOT NULL DEFAULT 0 CHECK (progress_percent BETWEEN 0 AND 100),
    certificate_issued  INTEGER NOT NULL DEFAULT 0        -- 0 = no, 1 = yes
);

CREATE TABLE payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    course_id   INTEGER NOT NULL REFERENCES courses(id),
    amount_inr  INTEGER NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('success', 'failed', 'pending')),
    paid_at     TEXT NOT NULL,
    reference   TEXT                                      -- payment gateway transaction id
);

CREATE TABLE tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),    -- learner who raised it
    subject     TEXT NOT NULL,
    category    TEXT NOT NULL CHECK (category IN ('access', 'payment', 'certificate', 'technical', 'other')),
    priority    TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high')),
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'resolved', 'closed')),
    assigned_to INTEGER REFERENCES users(id),             -- support/admin user, NULL = unassigned
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE ticket_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    sender_id   INTEGER NOT NULL REFERENCES users(id),
    message     TEXT NOT NULL,
    is_internal INTEGER NOT NULL DEFAULT 0,               -- 1 = internal note for support/admin, hidden from learners
    created_at  TEXT NOT NULL
);

CREATE TABLE faqs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    question  TEXT NOT NULL,
    answer    TEXT NOT NULL,
    category  TEXT NOT NULL
);

CREATE INDEX idx_enrollments_user ON enrollments(user_id);
CREATE INDEX idx_payments_user    ON payments(user_id);
CREATE INDEX idx_tickets_user     ON tickets(user_id);
CREATE INDEX idx_tickets_status   ON tickets(status);
CREATE INDEX idx_messages_ticket  ON ticket_messages(ticket_id);