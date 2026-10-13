INSERT OR IGNORE INTO schema_version VALUES(2);
CREATE TABLE IF NOT EXISTS launch_plans(
 project_id TEXT PRIMARY KEY REFERENCES projects(id), document TEXT NOT NULL,
 hash TEXT NOT NULL, context_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS launch_costs(
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), requests INTEGER NOT NULL CHECK(requests=0),
 credit_usd TEXT NOT NULL CHECK(credit_usd='0'), out_of_pocket_usd TEXT NOT NULL CHECK(out_of_pocket_usd='0'));
CREATE TRIGGER IF NOT EXISTS launch_plans_no_update BEFORE UPDATE ON launch_plans
 BEGIN SELECT RAISE(ABORT,'launch plans are immutable; create a new workflow version'); END;
CREATE TRIGGER IF NOT EXISTS launch_plans_no_delete BEFORE DELETE ON launch_plans
 BEGIN SELECT RAISE(ABORT,'launch plans are immutable'); END;
