"""Durable, offline evaluation decisions and conservative credit reservations."""
from decimal import Decimal
from contextlib import nullcontext
from .models import canonical

class EvaluationGate:
    def __init__(self, store, cap='0.05'):
        self.store = store
        self.cap = Decimal(cap)
        if not self.cap.is_finite() or self.cap < 0:
            raise ValueError('Invalid cap')
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS evaluation_budget(id INTEGER PRIMARY KEY CHECK(id=1),cap TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evaluation_reservations(
          id TEXT PRIMARY KEY, maximum TEXT NOT NULL, actual TEXT, state TEXT NOT NULL
          CHECK(state IN ('reserved','settled','uncertain')));
        CREATE TABLE IF NOT EXISTS model_evaluations(
          id TEXT PRIMARY KEY, configuration TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS model_evaluations_no_update BEFORE UPDATE ON model_evaluations
          BEGIN SELECT RAISE(ABORT,'model evaluations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS model_evaluations_no_delete BEFORE DELETE ON model_evaluations
          BEGIN SELECT RAISE(ABORT,'model evaluations are immutable'); END;
        ''')
        store.db.execute('INSERT OR IGNORE INTO evaluation_budget VALUES(1,?)', (str(self.cap),))
        if Decimal(store.db.execute('SELECT cap FROM evaluation_budget').fetchone()[0]) != self.cap:
            raise ValueError('Cannot silently change persisted spending cap')

    def reserve(self, request_id, input_tokens, completion_tokens, input_rate, output_rate):
        if not all(type(n) is int and 0 < n <= 4000 for n in (input_tokens, completion_tokens)):
            raise ValueError('Token bounds exceeded')
        rates = [Decimal(str(r)) for r in (input_rate, output_rate)]
        if any(not r.is_finite() or r < 0 for r in rates):
            raise ValueError('Uncertain rate')
        maximum = (input_tokens * rates[0] + completion_tokens * rates[1]) / Decimal(1000000)
        with self.store.tx():
            if self.store.db.execute("SELECT 1 FROM evaluation_reservations WHERE state='uncertain'").fetchone():
                raise ValueError('Uncertain accounting blocks additional work')
            rows = list(self.store.db.execute('SELECT * FROM evaluation_reservations'))
            if any(r['id'] == request_id for r in rows):
                raise ValueError('Request already reserved; do not dispatch again')
            used = sum((Decimal(r['actual'] if r['state'] == 'settled' else r['maximum']) for r in rows), Decimal(0))
            if used + maximum > self.cap:
                raise ValueError('Credit ceiling exceeded')
            self.store.db.execute('INSERT INTO evaluation_reservations VALUES(?,?,NULL,?)',
                                 (request_id, str(maximum), 'reserved'))
        return maximum

    def settle(self, request_id, actual=None):
        with self.store.tx():
            row = self.store.db.execute('SELECT * FROM evaluation_reservations WHERE id=?', (request_id,)).fetchone()
            if not row or row['state'] != 'reserved':
                raise ValueError('Reservation is not pending')
            amount = Decimal(str(actual)) if actual is not None else None
            uncertain = amount is None or not amount.is_finite() or amount < 0 or amount > Decimal(row['maximum'])
            self.store.db.execute('UPDATE evaluation_reservations SET actual=?,state=? WHERE id=?',
                                 (None if uncertain else str(amount), 'uncertain' if uncertain else 'settled', request_id))

    def freeze_result(self, evaluation_id, configuration, result, *, _within_transaction=False):
        required = {'model_id', 'endpoint', 'prompt_version', 'schema_version', 'parameters', 'source_scope'}
        if set(configuration) != required or not all(configuration.values()):
            raise ValueError('Full configuration provenance required')
        with nullcontext() if _within_transaction else self.store.tx():
            self.store.db.execute('INSERT INTO model_evaluations VALUES(?,?,?)',
                                 (evaluation_id, canonical(configuration), canonical(result)))
            self.store.event(None, 'model_evaluation_frozen', {'evaluation_id': evaluation_id})
