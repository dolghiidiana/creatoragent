"""Local project memory and review contracts. No external action executor."""
import json
from contextlib import nullcontext
from .models import canonical, digest

LABELS = {'manuscript_fact', 'direct_quote', 'interpretation', 'reader_hypothesis',
          'external_research', 'unknown', 'creative_decision'}
BOUNDARIES = {'public_posting', 'spending', 'outreach', 'private_disclosure',
              'destructive_action', 'creative_decision'}

class CreatorContext:
    def __init__(self, store):
        self.store = store
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS creator_context(
          project_id TEXT NOT NULL REFERENCES projects(id), revision INTEGER NOT NULL,
          document TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(project_id,revision));
        CREATE TABLE IF NOT EXISTS creator_reviews(
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
          context_hash TEXT NOT NULL, content TEXT NOT NULL, state TEXT NOT NULL
          CHECK(state IN ('pending','approved','rejected','invalidated')));
        CREATE TRIGGER IF NOT EXISTS context_no_update BEFORE UPDATE ON creator_context
          BEGIN SELECT RAISE(ABORT,'context revisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS context_no_delete BEFORE DELETE ON creator_context
          BEGIN SELECT RAISE(ABORT,'context revisions are immutable'); END;
        ''')

    def current(self, project_id):
        row = self.store.db.execute('SELECT * FROM creator_context WHERE project_id=? '
                                    'ORDER BY revision DESC LIMIT 1', (project_id,)).fetchone()
        if not row:
            raise ValueError('Project context missing')
        return {'revision': row['revision'], 'hash': row['hash'],
                'document': json.loads(row['document'])}

    def save(self, project_id, document, *, _within_transaction=False):
        required = {'goal', 'purpose', 'priorities', 'identity', 'voice', 'source_scope',
                    'boundaries', 'model_policy'}
        if set(document) != required or not all(document[k] for k in required):
            raise ValueError('Incomplete project context')
        if document['identity'] != self.store.project(project_id)['identity']:
            raise ValueError('Creator identity does not match project')
        if set(document['boundaries']) != BOUNDARIES:
            raise ValueError('All approval boundaries must be preserved')
        encoded = canonical(document)
        fingerprint = digest(document)
        with nullcontext() if _within_transaction else self.store.tx():
            row = self.store.db.execute('SELECT * FROM creator_context WHERE project_id=? '
                                       'ORDER BY revision DESC LIMIT 1', (project_id,)).fetchone()
            if row and row['hash'] == fingerprint:
                return row['revision']
            revision = row['revision'] + 1 if row else 1
            self.store.db.execute('INSERT INTO creator_context VALUES(?,?,?,?)',
                                 (project_id, revision, encoded, fingerprint))
            self.store.db.execute("UPDATE creator_reviews SET state='invalidated' WHERE project_id=? "
                                  "AND state IN ('pending','approved')", (project_id,))
            self.store.db.execute("UPDATE approvals SET state='invalidated',reason='Creator context changed' "
                                  "WHERE task_id IN (SELECT id FROM tasks WHERE project_id=? AND operation LIKE 'launch_%') "
                                  "AND state IN ('pending','approved')",(project_id,))
            self.store.event(None, 'creator_context_saved',
                             {'project_id': project_id, 'revision': revision, 'hash': fingerprint})
        return revision

    def prepare_review(self, project_id, action, destination, exact_content):
        if action not in BOUNDARIES or not destination or not exact_content:
            raise ValueError('Explicit action, destination and content required')
        context = self.current(project_id)
        content = {'action': action, 'destination': destination, 'content': exact_content}
        review_id = digest({'project_id': project_id, 'context_hash': context['hash'], **content})
        with self.store.tx():
            self.store.db.execute('INSERT OR IGNORE INTO creator_reviews VALUES(?,?,?,?,?)',
                                 (review_id, project_id, context['hash'], canonical(content), 'pending'))
            self.store.event(None, 'creator_review_prepared', {'review_id': review_id})
        return review_id

    def decide(self, review_id, decision):
        if decision not in {'approved', 'rejected'}:
            raise ValueError('Unsupported review decision')
        with self.store.tx():
            row = self.store.db.execute('SELECT * FROM creator_reviews WHERE id=?', (review_id,)).fetchone()
            if not row or row['state'] != 'pending':
                raise ValueError('Review is not pending')
            if row['context_hash'] != self.current(row['project_id'])['hash']:
                raise ValueError('Review context changed')
            self.store.db.execute('UPDATE creator_reviews SET state=? WHERE id=?', (decision, review_id))
            self.store.event(None, 'creator_review_decided', {'review_id': review_id, 'decision': decision})

    def checkpoint(self, project_id):
        context = self.current(project_id)
        return {'context': context, 'tasks': [dict(r) for r in self.store.db.execute(
                'SELECT id,operation,state,reason FROM tasks WHERE project_id=? ORDER BY id', (project_id,))],
                'decisions_needed': [{'id': r['id'], **json.loads(r['content'])} for r in
                self.store.db.execute("SELECT * FROM creator_reviews WHERE project_id=? AND state='pending' ORDER BY rowid",
                                      (project_id,))],
                'external_execution_enabled': False}


def validate_claims(claims, sources):
    """Structural/evidence checks; does not prove semantic entailment."""
    errors = []
    if not isinstance(claims,list) or not isinstance(sources,dict):
        return ['invalid_claim_collection']
    for index, claim in enumerate(claims):
        prefix = str(index) + ':'
        if not isinstance(claim,dict) or set(claim) != {'label', 'text', 'source_refs', 'attribution'}:
            errors.append(prefix + 'schema'); continue
        if claim['attribution'] is not None and (not isinstance(claim['attribution'],str) or not claim['attribution'].strip()):
            errors.append(prefix+'invalid_attribution'); continue
        label, refs = claim['label'], claim['source_refs']
        if label not in LABELS or not isinstance(refs, list) or any(not isinstance(r, str) for r in refs):
            errors.append(prefix + 'label_or_refs'); continue
        if len(refs) != len(set(refs)) or any(r not in sources for r in refs):
            errors.append(prefix + 'unknown_or_duplicate_reference'); continue
        if label == 'unknown':
            if claim['text'] is not None or refs:
                errors.append(prefix + 'unknown_requires_null_and_no_refs')
            continue
        if not isinstance(claim['text'], str) or not claim['text'].strip():
            errors.append(prefix + 'empty_claim'); continue
        if label != 'creative_decision' and not refs:
            errors.append(prefix + 'evidence_required')
        if label == 'direct_quote' and (len(refs) != 1 or claim['text'] not in sources[refs[0]]['text']):
            errors.append(prefix + 'quote_mismatch')
        if label == 'external_research' and any(sources[r].get('kind') != 'external' or
                not sources[r].get('url') or not sources[r].get('accessed_at') for r in refs):
            errors.append(prefix + 'external_provenance_required')
        if label == 'manuscript_fact' and any(sources[r].get('kind') != 'manuscript' for r in refs):
            errors.append(prefix + 'manuscript_source_required')
        if any(sources[r].get('personal_experience') for r in refs) and not claim['attribution']:
            errors.append(prefix + 'personal_experience_requires_attribution')
    return errors
