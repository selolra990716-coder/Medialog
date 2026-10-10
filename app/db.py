import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def db_path():
    return Path(os.getenv('MEDIALOG_DB', 'data/medialog.sqlite3'))


@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS feeds (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
            last_checked TEXT, last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY, feed_id INTEGER REFERENCES feeds(id) ON DELETE SET NULL,
            publisher TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
            summary TEXT NOT NULL, published_at TEXT NOT NULL, collected_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, width INTEGER NOT NULL,
            height INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, payload TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS issue_projects (
            article_id INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE
        );
        ''')
