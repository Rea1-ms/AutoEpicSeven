"""Small, best-effort local bridge between task processes and open interfaces."""

import logging
import os
import sqlite3
import time
from contextlib import closing
from functools import wraps
from pathlib import Path

import psutil

CLIENT_TTL = 15
NOTIFICATION_TTL = 30
PROGRESS_STATES = {'running', 'waiting', 'error', 'stopped', 'completed'}


def _best_effort(default_factory):
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            try:
                return method(self, *args, **kwargs)
            except Exception:  # noqa: BLE001
                # Telemetry is optional. A locked/unwritable database must never
                # interrupt automation or expose notification/config contents.
                if not self._warned:
                    logging.getLogger(__name__).warning(
                        'Local runtime status unavailable; automation will continue'
                    )
                    self._warned = True
                return default_factory()
        return wrapped
    return decorate


class RuntimeEvents:
    def __init__(self, path=None, clock=None):
        self.path = Path(path) if path is not None else (
            Path(__file__).resolve().parents[1] / 'log' / 'runtime-events.sqlite3'
        )
        self.clock = clock if clock is not None else time.time
        self._warned = False

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=0.25)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS clients (
                    client_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    heartbeat REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS progress (
                    config_name TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    completed INTEGER NOT NULL,
                    total INTEGER,
                    task TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    pid INTEGER NOT NULL,
                    process_created REAL NOT NULL
                );
            """)
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _prune(connection, now):
        # Once a client misses its lease, its queued messages must not replay
        # after a refresh/reconnect, even if it reuses the same client id.
        connection.execute('DELETE FROM clients WHERE heartbeat < ?', (now - CLIENT_TTL,))
        connection.execute(
            'DELETE FROM notifications WHERE expires_at <= ? '
            'OR client_id NOT IN (SELECT client_id FROM clients)', (now,)
        )

    @_best_effort(lambda: False)
    def register_client(self, client_id, kind):
        if not isinstance(client_id, str) or not client_id or len(client_id) > 128:
            return False
        if kind not in {'electron', 'browser'}:
            return False
        now = self.clock()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            self._prune(connection, now)
            connection.execute(
                'INSERT OR REPLACE INTO clients (client_id, kind, heartbeat) VALUES (?, ?, ?)',
                (client_id, kind, now),
            )
        return True

    @_best_effort(lambda: None)
    def unregister_client(self, client_id):
        with closing(self._connect()) as connection, connection:
            connection.execute('DELETE FROM clients WHERE client_id = ?', (client_id,))
            connection.execute('DELETE FROM notifications WHERE client_id = ?', (client_id,))

    @_best_effort(lambda: False)
    def notify(self, title, content):
        """Return whether queued for one live interface, not whether displayed."""
        now = self.clock()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            self._prune(connection, now)
            recipient = connection.execute(
                "SELECT client_id FROM clients ORDER BY (kind = 'electron') DESC, "
                'heartbeat DESC, client_id LIMIT 1'
            ).fetchone()
            if recipient is None:
                return False
            connection.execute(
                'INSERT INTO notifications (client_id, title, content, expires_at) '
                'VALUES (?, ?, ?, ?)',
                (recipient['client_id'], str(title)[:256], str(content)[:4096],
                 now + NOTIFICATION_TTL),
            )
        return True

    @_best_effort(list)
    def poll_notifications(self, client_id):
        now = self.clock()
        with closing(self._connect()) as connection, connection:
            # Claim and delete in the same write transaction: two concurrent
            # polls cannot display the same desktop notification twice.
            connection.execute('BEGIN IMMEDIATE')
            self._prune(connection, now)
            rows = connection.execute(
                'SELECT id, title, content FROM notifications WHERE client_id = ? ORDER BY id',
                (client_id,),
            ).fetchall()
            connection.execute('DELETE FROM notifications WHERE client_id = ?', (client_id,))
        return [dict(row, id=str(row['id'])) for row in rows]

    @_best_effort(lambda: False)
    def publish_progress(self, config_name, state, completed=0, total=None, task=''):
        if state not in PROGRESS_STATES:
            return False
        completed = max(0, int(completed))
        total = None if total is None else max(completed, int(total), 0)
        process = psutil.Process(os.getpid())
        with closing(self._connect()) as connection, connection:
            connection.execute(
                'INSERT OR REPLACE INTO progress '
                '(config_name, state, completed, total, task, updated_at, pid, process_created) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (str(config_name), state, completed, total, str(task), self.clock(),
                 process.pid, process.create_time()),
            )
        return True

    @staticmethod
    def _process_alive(pid, created):
        try:
            process = psutil.Process(pid)
            return (
                process.is_running()
                and abs(process.create_time() - created) < 0.01
                and process.status() != psutil.STATUS_ZOMBIE
            )
        except psutil.Error:
            return False

    @_best_effort(lambda: None)
    def get_progress(self, config_name):
        with closing(self._connect()) as connection:
            row = connection.execute(
                'SELECT * FROM progress WHERE config_name = ?', (str(config_name),)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        pid = result.pop('pid')
        created = result.pop('process_created')
        # Task durations are unknown: a long battle is not a stale worker.
        # Check process identity instead of expiring progress by wall time.
        # Completed/error are explicit terminal results and survive process exit.
        if result['state'] in {'running', 'waiting'} and not self._process_alive(pid, created):
            result['state'] = 'stopped'
            result['task'] = ''
        return result


runtime_events = RuntimeEvents()
