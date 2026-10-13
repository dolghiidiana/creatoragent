"""Run from the repository root: python -m app.cli --help."""
import argparse
import json
from datetime import datetime, timezone
from .store import Store
from .adapters.fake_destination import FakeDestination
from .worker import Worker
from .approvals import decide, packages

def display_time(value):
    # Windows may lack the IANA tz database: explicit Toronto DST rule for modern dates.
    from datetime import timedelta
    utc = datetime.fromtimestamp(value, timezone.utc)
    march = datetime(utc.year, 3, 1, tzinfo=timezone.utc)
    november = datetime(utc.year, 11, 1, tzinfo=timezone.utc)
    start = march + timedelta(days=(6-march.weekday()) % 7 + 7, hours=7)
    end = november + timedelta(days=(6-november.weekday()) % 7, hours=6)
    offset = -4 if start <= utc < end else -5
    return utc.astimezone(timezone(timedelta(hours=offset))).isoformat() + ' [America/Toronto]'

def main():
    parser = argparse.ArgumentParser(description='CreatorAgent Milestone 1: OFFLINE SYNTHETIC SIMULATION ONLY')
    parser.add_argument('--db', default='data/state.sqlite')
    parser.add_argument('--destination-db', default='data/fake-destination.sqlite')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('seed','status','review','pause','resume'):
        sub.add_parser(name)
    run = sub.add_parser('run')
    run.add_argument('--watch', action='store_true', help='Keep worker alive for approvals and due tasks; Ctrl+C to stop')
    run.add_argument('--lease-seconds', type=float, default=30)
    run.add_argument('--crash', choices=['after_claim','before_dispatch','after_effect'], help=argparse.SUPPRESS)
    for name in ('approve','reject','revoke'):
        command = sub.add_parser(name)
        command.add_argument('request_id')
    provide = sub.add_parser('provide')
    provide.add_argument('task_id')
    provide.add_argument('--title', required=True)
    args = parser.parse_args()
    store = Store(args.db)
    destination = None
    try:
        if args.command == 'seed':
            store.seed()
        elif args.command == 'run':
            if args.lease_seconds <= 0:
                raise ValueError('Lease must be positive')
            destination = FakeDestination(args.destination_db)
            Worker(store, destination, args.lease_seconds, crash=args.crash).run(args.watch)
        elif args.command in ('approve','reject','revoke'):
            decide(store, args.request_id, {'approve':'approved','reject':'rejected','revoke':'revoked'}[args.command])
        elif args.command == 'review':
            result = packages(store)
            for item in result:
                item['expires_local'] = display_time(item['expires_utc'])
            print(json.dumps(result, indent=2))
        elif args.command == 'status':
            result = {'simulated': True, 'projects': [dict(r) for r in store.db.execute('SELECT * FROM projects')],
                      'tasks': [dict(r) for r in store.db.execute('SELECT * FROM tasks ORDER BY id')],
                      'events': [{**dict(r), 'at_local':display_time(r['at'])} for r in store.db.execute('SELECT * FROM events ORDER BY id')]}
            print(json.dumps(result, indent=2))
        elif args.command in ('pause','resume'):
            store.pause(args.command == 'pause')
        elif args.command == 'provide':
            task = store.task(args.task_id)
            if not task or task['state'] != 'awaiting_information':
                raise ValueError('Only a task waiting for information can receive input')
            payload = json.loads(task['payload'])
            payload['title'] = args.title
            store.update_payload(task['id'], payload)
    except KeyboardInterrupt:
        print('Worker stopped; state preserved.')
    except (ValueError, RuntimeError) as error:
        parser.exit(2, str(error) + '\n')
    finally:
        if destination:
            destination.close()
        store.close()

if __name__ == '__main__':
    main()
