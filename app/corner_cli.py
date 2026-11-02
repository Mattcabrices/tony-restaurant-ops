"""Local owner commands for Tony's Corner. Never exposed as an AI tool."""
import argparse
import asyncio
from datetime import datetime, time
import json
from pathlib import Path

from .storage import ROOT
from .calculations import ET
from .corner.collect import RequestBudget, collect_toast, backfill_toast, import_clover, import_existing_events, import_toast_snapshot
from .corner.connectors import public_url, verified_calendar_csv, nws_forecast, noaa_daily, fetch_public, calendar_ics, save_events
from .corner.service import CornerService, DEFAULT_CONFIG
from .corner.store import now


def main():
    parser = argparse.ArgumentParser(description="Tony's Corner: no changes to restaurant systems.")
    parser.add_argument('command', choices=['status', 'dry-run', 'improvements', 'refresh-inbox', 'consult-inbox', 'import-history', 'collect-toast', 'backfill-toast', 'setup-isolation',
        'run', 'experiments', 'import-features', 'reflect', 'research-web', 'approve-schedule', 'disable-schedule', 'import-calendar', 'fetch-nws', 'fetch-noaa', 'fetch-calendar'])
    parser.add_argument('--workspace', type=Path, default=ROOT / 'Tonys-Corner')
    parser.add_argument('--target')
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--at')
    parser.add_argument('--confirm', action='store_true')
    parser.add_argument('--file', type=Path)
    parser.add_argument('--url')
    parser.add_argument('--host')
    parser.add_argument('--name')
    parser.add_argument('--category', choices=['school', 'holiday', 'local', 'sports'])
    parser.add_argument('--latitude', type=float)
    parser.add_argument('--longitude', type=float)
    parser.add_argument('--station')
    parser.add_argument('--backfill-start')
    parser.add_argument('--backfill-end')
    parser.add_argument('--refresh-nws', action='store_true')
    args = parser.parse_args()
    service = CornerService(args.workspace)
    config = service.config()
    budget = RequestBudget(config['http_requests_per_utc_day'], service.store)
    try:
        if args.command == 'status':
            result = service.status()
        elif args.command in {'improvements', 'refresh-inbox', 'consult-inbox'}:
            from .corner.inbox import listing, refresh, consult
            if args.command == 'improvements':
                result = listing(service.store)
            elif args.command == 'refresh-inbox':
                if not args.confirm:
                    raise ValueError('Updating the local task ledger requires --confirm.')
                result = refresh(service)
            else:
                if not args.confirm or not args.name:
                    raise ValueError('Provide --confirm and a stable --name for one bounded AI consultation.')
                from .storage import DB, Vault
                from .ai import ChatGPT
                async def consultation():
                    ai = ChatGPT(Vault(ROOT/'data'), DB(ROOT/'data'))
                    try:
                        return await consult(service, ai, args.name, confirmed=True)
                    finally:
                        await ai.http.aclose()
                result = asyncio.run(consultation())
        elif args.command == 'dry-run':
            result = {'mode': 'dry-run', 'production_writes': 0, 'http_requests': 0, 'model_calls': 0,
                      'schedule_changes': 0, 'readiness': service.status()}
        elif args.command == 'import-history':
            result = {'clover': import_clover(service.store, ROOT / 'data' / 'operations.sqlite3'),
                      'toast_snapshot': import_toast_snapshot(service.store, ROOT / 'data' / 'operations.sqlite3'),
                      'events': import_existing_events(service.store, ROOT / 'data' / 'operations.sqlite3', ROOT / 'app' / 'football_calendar.json')}
            day = datetime.now(ET).date().isoformat()
            service.store.journal(day, 'data_import', json.dumps(result))
            service.export_journal(day)
        elif args.command == 'setup-isolation':
            result = service.worker.setup()
        elif args.command == 'run':
            result = service.run(args.target)
        elif args.command == 'experiments':
            from .corner.plans import run_plans
            result = run_plans(service)
        elif args.command == 'import-features':
            from .corner.features import import_saved_sources
            result = import_saved_sources(service,ROOT/'app'/'football_calendar.json')
        elif args.command in {'reflect', 'research-web'}:
            from .storage import DB, Vault
            from .ai import ChatGPT
            from .operations import DEFAULTS
            from .corner.reflection import reflect, search_public
            db = DB(ROOT/'data')
            async def research():
                ai = ChatGPT(Vault(ROOT/'data'), db)
                try:
                    operation = search_public if args.command == 'research-web' else reflect
                    value = await operation(service, ai, DEFAULTS | db.get('settings', {}))
                    return {'cited_public_results': len(value)} if args.command == 'research-web' else value
                finally:
                    await ai.http.aclose()
            result = asyncio.run(research())
        elif args.command in {'collect-toast', 'backfill-toast'}:
            if not args.start or not args.end:
                raise ValueError('Provide --start and --end, limited to seven closed business dates.')
            from .storage import Vault
            collector = backfill_toast if args.command == 'backfill-toast' else collect_toast
            result = asyncio.run(collector(service.store, Vault(ROOT / 'data'), args.start, args.end,
                channel_mapping=config['channel_mapping'], budget=budget))
        elif args.command in {'approve-schedule', 'disable-schedule'}:
            if not args.confirm:
                raise ValueError('This changes the research schedule. Explicit local-owner --confirm is required.')
            if args.command == 'approve-schedule':
                if not args.at or time.fromisoformat(args.at) < time(4, 15):
                    raise ValueError('Provide --at HH:MM at or after 04:15 Eastern; verify restaurant closeout first.')
                recent = service.store.rows("SELECT metadata FROM datasets WHERE source='Toast' ORDER BY id DESC")
                cutoff = next((json.loads(r['metadata']).get('closeout_hour') for r in recent if json.loads(r['metadata']).get('closeout_hour') is not None), None)
                if cutoff is None or time.fromisoformat(args.at) < time(int(cutoff), 15):
                    raise ValueError('Approval requires a saved verified Toast cutoff and a schedule at least 15 minutes after it.')
                if not service.worker.status()['ready']:
                    raise ValueError('Schedule activation requires successful OS isolation setup first.')
                config.update(enabled=True, schedule_et=args.at, schedule_approved_at=now())
                config.update(backfill_start=args.backfill_start, backfill_end=args.backfill_end)
                config.update(refresh_nws=args.refresh_nws)
                if args.refresh_nws:
                    sources = json.loads((service.workspace/'sources.json').read_text(encoding='utf-8'))
                    coordinates = sources.get('weather_forecast', {}).get('coordinates')
                    if not isinstance(coordinates, dict) or not all(isinstance(coordinates.get(k), (int, float)) for k in ('latitude', 'longitude')):
                        raise ValueError('NWS schedule approval requires saved verified restaurant coordinates.')
                # Validate the proposed scope before saving or scheduling anything.
                if bool(args.backfill_start) != bool(args.backfill_end):
                    raise ValueError('Backfill approval requires both --backfill-start and --backfill-end.')
                if args.backfill_start:
                    from .corner.collect import backfill_window
                    backfill_window(service.store, args.backfill_start, args.backfill_end)
            else:
                config.update(enabled=False)
            (service.workspace / 'config.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
            result = {'enabled': config['enabled'], 'schedule_et': config['schedule_et'], 'restart_required': True,
                      'model_calls': 0, 'paid_search_calls': 0}
        elif args.command == 'import-calendar':
            if not all((args.file, args.url, args.host, args.name)):
                raise ValueError('Provide --file, --url, --host and --name for a verified official calendar CSV.')
            public_url(args.url, {args.host})
            result = {'imported': verified_calendar_csv(service.store, args.file.read_text(encoding='utf-8-sig'), args.name, args.url)}
        elif args.command == 'fetch-nws':
            if args.latitude is None or args.longitude is None:
                raise ValueError('Provide verified restaurant --latitude and --longitude.')
            snapshot = asyncio.run(nws_forecast(args.latitude, args.longitude, budget))
            result = service.store.ingest('NWS', [], {'kind': 'weather_forecast_snapshot', **snapshot}, snapshot['retrieved_at'])
        elif args.command == 'fetch-noaa':
            if not all((args.station, args.start, args.end)):
                raise ValueError('Provide verified --station, --start and --end.')
            rows, metadata = asyncio.run(noaa_daily(args.station, args.start, args.end, budget))
            result = service.store.ingest('NOAA', rows, metadata)
        elif args.command == 'fetch-calendar':
            if not all((args.url, args.host, args.name, args.category)):
                raise ValueError('Provide --url, --host, --name and --category for an approved public all-day ICS source.')
            content = asyncio.run(fetch_public(args.url, {args.host}, budget)).decode('utf-8-sig')
            events, skipped = calendar_ics(content, args.category)
            result = {'imported': save_events(service.store, args.name, args.url, events), 'limitations': skipped}
        print(json.dumps(result, indent=2, ensure_ascii=True))
    except Exception as error:
        raise SystemExit(type(error).__name__ + ': ' + str(error)[:500]) from None


if __name__ == '__main__':
    main()
