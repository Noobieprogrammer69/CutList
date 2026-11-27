"""Durable project/workspace records. Media remains on disk, outside SQLite."""
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "cutlist.sqlite3"
        self.lock = threading.RLock()
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, owner TEXT NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS project_owner ON projects(owner, updated);
                CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY, owner TEXT NOT NULL, fingerprint TEXT NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS upload_owner ON uploads(owner, fingerprint);
                CREATE TABLE IF NOT EXISTS preferences (owner TEXT PRIMARY KEY, data TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def session(self, token):
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connection() as db:
            row = db.execute("SELECT owner FROM sessions WHERE token=? AND expires>?", (digest, time.time())).fetchone()
            if not row:
                return None
            return {"owner": row["owner"]}

    def new_session(self, owner=None):
        owner, token = owner or secrets.token_hex(16), secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), owner, time.time() + 30 * 86400))
        return owner, token

    def revoke(self, token):
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256((token or '').encode()).hexdigest(),))

    def save(self, project):
        with self.connection() as db:
            db.execute("INSERT INTO projects VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,updated=excluded.updated,data=excluded.data",
                       (project["id"], project["owner"], time.time(), json.dumps(project)))

    def get(self, project):
        with self.connection() as db:
            row = db.execute("SELECT data FROM projects WHERE id=?", (project,)).fetchone()
            return json.loads(row["data"]) if row else None

    def preferences(self, owner, data=None):
        with self.connection() as db:
            if data is not None:
                db.execute("INSERT INTO preferences VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET data=excluded.data", (owner, json.dumps(data)))
            row = db.execute("SELECT data FROM preferences WHERE owner=?", (owner,)).fetchone()
            return json.loads(row["data"]) if row else {}

    def authorized(self, project, owner, now):
        with self.connection() as db:
            return db.execute("SELECT 1 FROM projects WHERE id=? AND owner=? AND json_extract(data,'$.expires')>?", (project, owner, now)).fetchone() is not None

    def summaries(self, owner):
        keys = ("id", "source_name", "status", "message", "created", "expires", "progress")
        columns = ",".join(f"json_extract(data,'$.{k}') AS {k}" for k in keys)
        with self.connection() as db:
            return [dict(row) for row in db.execute(f"SELECT {columns} FROM projects WHERE owner=? AND json_extract(data,'$.expires')>? ORDER BY updated DESC", (owner, time.time())).fetchall()]

    def feedback_rows(self, owner):
        with self.connection() as db:
            rows = db.execute("SELECT projects.id,json_extract(projects.data,'$.source_name') AS source,clips.value FROM projects,json_each(projects.data,'$.result.clips') AS clips WHERE owner=? AND json_extract(projects.data,'$.expires')>?", (owner, time.time())).fetchall()
            return [(row['id'], row['source'], json.loads(row['value'])) for row in rows]

    def expired(self, now):
        with self.connection() as db:
            return [json.loads(row['data']) for row in db.execute("SELECT data FROM projects WHERE json_extract(data,'$.expires')<=?", (now,)).fetchall()]

    def all(self, owner=None):
        with self.connection() as db:
            rows = db.execute("SELECT data FROM projects" + (" WHERE owner=?" if owner else "") + " ORDER BY updated DESC", (owner,) if owner else ()).fetchall()
            return [json.loads(row["data"]) for row in rows]

    def delete(self, project):
        with self.connection() as db:
            db.execute("DELETE FROM projects WHERE id=?", (project,))

    def save_upload(self, upload):
        with self.connection() as db:
            db.execute("INSERT INTO uploads VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,data=excluded.data",
                       (upload["id"], upload["owner"], upload["fingerprint"], time.time(), json.dumps(upload)))

    def upload(self, identifier):
        with self.connection() as db:
            row = db.execute("SELECT data FROM uploads WHERE id=?", (identifier,)).fetchone()
            return json.loads(row["data"]) if row else None

    def uploads(self, owner=None):
        with self.connection() as db:
            rows = db.execute("SELECT data FROM uploads" + (" WHERE owner=?" if owner else "") + " ORDER BY updated DESC", (owner,) if owner else ()).fetchall()
            return [json.loads(row["data"]) for row in rows]

    def delete_upload(self, identifier):
        with self.connection() as db:
            db.execute("DELETE FROM uploads WHERE id=?", (identifier,))

    def adopt(self, previous_owner, user):
        with self.connection() as db:
            for row in db.execute("SELECT id,data FROM projects WHERE owner=?", (previous_owner,)).fetchall():
                data = json.loads(row["data"])
                data["owner"] = user
                db.execute("UPDATE projects SET owner=?,data=? WHERE id=?", (user, json.dumps(data), row["id"]))
            for row in db.execute("SELECT id,data FROM uploads WHERE owner=?", (previous_owner,)).fetchall():
                data = json.loads(row["data"])
                data["owner"] = user
                db.execute("UPDATE uploads SET owner=?,data=? WHERE id=?", (user, json.dumps(data), row["id"]))
