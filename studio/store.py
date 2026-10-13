import json
import sqlite3
import time
import uuid
from pathlib import Path


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'files').mkdir(exist_ok=True)
        (self.root / 'workflows').mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / 'studio.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, params TEXT NOT NULL,
                status TEXT NOT NULL, stage TEXT NOT NULL, progress INTEGER,
                prompt_id TEXT, created REAL NOT NULL, updated REAL NOT NULL, error TEXT);
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY, job_id TEXT, name TEXT NOT NULL,
                mime TEXT NOT NULL, created REAL NOT NULL);
        ''')
        self.db.commit()

    def jobs(self):
        return [self.job(r['id']) for r in self.db.execute('SELECT id FROM jobs ORDER BY created DESC')]

    def job(self, id):
        row = self.db.execute('SELECT * FROM jobs WHERE id=?', (id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d['params'] = json.loads(d['params'])
        d['files'] = [dict(r) for r in self.db.execute('SELECT * FROM files WHERE job_id=?', (id,))]
        return d

    def create(self, params, request_key):
        previous = self.db.execute('SELECT id,params FROM jobs WHERE request_key=?', (request_key,)).fetchone()
        if previous:
            # Client repeats the identical seed-resolved request after an uncertain response.
            return self.job(previous['id'])
        id = uuid.uuid4().hex
        now = time.time()
        with self.db:
            self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)',
                (id, request_key, json.dumps(params), 'queued', 'В очереди', None, None, now, now, None))
        return self.job(id)

    def update(self, id, **fields):
        allowed = {'status','stage','progress','prompt_id','error'}
        if not fields.keys() <= allowed:
            raise ValueError('Invalid database fields')
        fields['updated'] = time.time()
        with self.db:
            self.db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',
                            (*fields.values(), id))

    def next_job(self):
        row = self.db.execute("SELECT id FROM jobs WHERE status IN ('queued','submitting','running') ORDER BY created LIMIT 1").fetchone()
        return self.job(row['id']) if row else None

    def add_file(self, name, mime, job_id=None, file_id=None):
        id = file_id or uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO files VALUES (?,?,?,?,?)', (id, job_id, name, mime, time.time()))
        return id

    def file(self, id):
        row = self.db.execute('SELECT * FROM files WHERE id=?', (id,)).fetchone()
        return dict(row) if row else None

    def file_path(self, file):
        path = (self.root / 'files' / file['name']).resolve()
        if path.parent != self.root / 'files':
            raise ValueError('Invalid file path')
        return path
