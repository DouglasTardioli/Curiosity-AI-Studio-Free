import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "studio.sqlite3"
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, updated TEXT DEFAULT CURRENT_TIMESTAMP, data TEXT NOT NULL
            )""")

    def new(self, topic, category, **fields):
        project = {"id": uuid.uuid4().hex, "topic": topic, "category": category,
                   "sources": [], "scenes": [], "status": "Rascunho", **fields}
        self.save(project)
        return project

    def folder(self, project):
        folder = self.root / project["id"]
        folder.mkdir(exist_ok=True)
        return folder

    def save(self, project):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("""INSERT INTO projects(id,data) VALUES(?,?)
                ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated=CURRENT_TIMESTAMP""",
                (project["id"], json.dumps(project, ensure_ascii=False)))

    def history(self):
        with closing(sqlite3.connect(self.db)) as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT data FROM projects ORDER BY updated DESC, rowid DESC")]
