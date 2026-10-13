CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS projects(
 id TEXT PRIMARY KEY, identity TEXT NOT NULL, workflow_version INTEGER NOT NULL,
 fixture_hash TEXT NOT NULL, allowed_operations TEXT NOT NULL,
 allowed_destination TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)));
CREATE TABLE IF NOT EXISTS tasks(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 operation TEXT NOT NULL, destination TEXT NOT NULL, payload TEXT NOT NULL,
 artifact_version INTEGER NOT NULL DEFAULT 1, action_id TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('pending','ready','running','completed','awaiting_approval',
 'awaiting_information','scheduled','blocked','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
 due REAL NOT NULL DEFAULT 0, owner TEXT, lease_expiry REAL,
 generation INTEGER NOT NULL DEFAULT 0, reason TEXT, output_ref TEXT);
CREATE TABLE IF NOT EXISTS dependencies(
 task_id TEXT NOT NULL REFERENCES tasks(id), predecessor TEXT NOT NULL REFERENCES tasks(id),
 PRIMARY KEY(task_id,predecessor));
CREATE TABLE IF NOT EXISTS artifacts(
 task_id TEXT NOT NULL REFERENCES tasks(id), version INTEGER NOT NULL,
 content TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(task_id,version));
CREATE TABLE IF NOT EXISTS approvals(
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), binding TEXT NOT NULL, content TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','approved','rejected','expired','revoked','invalidated')),
 approver TEXT, decided REAL, expires REAL NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS attempts(
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id),
 action_id TEXT NOT NULL, generation INTEGER NOT NULL, started REAL NOT NULL,
 ended REAL, outcome TEXT NOT NULL, receipt TEXT, error TEXT,
 UNIQUE(task_id,generation));
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, task_id TEXT,
 kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
 BEGIN SELECT RAISE(ABORT,'events are append only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
 BEGIN SELECT RAISE(ABORT,'events are append only'); END;
