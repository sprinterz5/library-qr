import sqlite3
import os
from app.core.config import DB_PATH

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS return_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT NOT NULL,
            reader_id INTEGER,
            card_barcode TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING/APPROVED/REJECTED
            created_at TEXT NOT NULL,
            created_ip TEXT,
            created_ua TEXT,
            approved_at TEXT,
            approved_by TEXT
        )
        """)
        # Add card_barcode column if it doesn't exist (for existing databases)
        try:
            c.execute("ALTER TABLE return_requests ADD COLUMN card_barcode TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        # Таблица для выданных книг (логирование всех успешных выдач)
        c.execute("""
        CREATE TABLE IF NOT EXISTS issued_books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT NOT NULL,
            reader_id INTEGER,
            card_barcode TEXT,
            loan_days INTEGER NOT NULL,
            issued_at TEXT NOT NULL,
            issued_by_ip TEXT,
            issued_by_ua TEXT
        )
        """)
        
        # Events table for dynamic homepage content
        c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            type TEXT NOT NULL,       -- WORKSHOP, TRAINING, SESSION, etc.
            description TEXT,
            location TEXT,            -- e.g. "Library"
            date_display TEXT,        -- Free text label (optional now)
            event_date TEXT,          -- ISO8601 value for sorting/filtering
            color TEXT,               -- hex or class suffix
            created_at TEXT NOT NULL
        )
        """)
        
        # Migration: Add event_date if missing
        try:
            c.execute("ALTER TABLE events ADD COLUMN event_date TEXT")
        except sqlite3.OperationalError:
            pass

        # Migration: Add registration_link (custom override)
        try:
            c.execute("ALTER TABLE events ADD COLUMN registration_link TEXT")
        except sqlite3.OperationalError:
            pass
