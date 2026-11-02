"""Snapshot public 2024–2026 FBS and NFL schedules for offline sales context."""
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.games import COLLEGE_SCOREBOARD_URL, NFL_SCHEDULE_URL, parse_nfl_schedule


OUTPUT = Path(__file__).resolve().parents[1] / 'app' / 'football_calendar.json'
ET = ZoneInfo('America/New_York')


def parse_college(events):
    days = defaultdict(lambda: {'college_games': 0, 'uga_games': [], 'prominent_college_games': [],
                                'nfl_games': 0, 'bills_games': [], 'national_nfl_games': []})
    seen = set()
    for event in events:
        try:
            event_id = event['id']
            if event_id in seen:
                continue
            seen.add(event_id)
            day = datetime.fromisoformat(event['date'].replace('Z', '+00:00')).astimezone(ET).date().isoformat()
            if not '2024-08-24' <= day <= '2027-01-31':
                continue
            teams = event['competitions'][0]['competitors']
            if len(teams) != 2:
                continue
            ranks = [int(team.get('curatedRank', {}).get('current') or 99) for team in teams]
            uga = any(str(team.get('team', {}).get('id')) == '61' for team in teams)
            title = event['name']
            page = next((link.get('href') for link in event.get('links', []) if link.get('href', '').startswith('https://')), None)
            game = {'label': title, 'time_et': datetime.fromisoformat(event['date'].replace('Z', '+00:00')).astimezone(ET).strftime('%H:%M'),
                    'source': page or 'https://www.espn.com/college-football/scoreboard',
                    'season': int(event.get('season', {}).get('year') or int(day[:4]) - (day[5:7] == '01'))}
            row = days[day]
            row['college_games'] += 1
            if uga:
                row['uga_games'].append(game)
            if max(ranks) <= 25 or min(ranks) <= 10:
                game['ranked_matchup'] = max(ranks) <= 25
                game['top_ten_team'] = min(ranks) <= 10
                if not uga:
                    row['prominent_college_games'].append(game)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return days


def add_nfl(days, raw):
    for year in (2024, 2025, 2026):
        for day, games in parse_nfl_schedule(raw, year).items():
            row = days[day]
            row['nfl_games'] += len(games)
            for game in games:
                if game['bills']:
                    row['bills_games'].append({**game, 'season': year})
                elif game['national_prime_time']:
                    row['national_nfl_games'].append({**game, 'season': year})


def import_calendar(http=None, output=OUTPUT):
    own_client = http is None
    http = http or httpx.Client(trust_env=False, timeout=30, follow_redirects=False)
    try:
        events = []
        counts = {}
        for year in (2024, 2025, 2026):
            response = http.get(COLLEGE_SCOREBOARD_URL, params={'dates': str(year), 'groups': '80', 'limit': '500'})
            response.raise_for_status()
            payload = response.json()
            year_events = payload.get('events')
            if not isinstance(year_events, list) or len(year_events) < 800:
                raise ValueError(f'Incomplete college-football schedule for {year}; prior snapshot preserved.')
            counts[str(year)] = len(year_events)
            events.extend(year_events)
        days = parse_college(events)
        nfl_response = http.get(NFL_SCHEDULE_URL)
        nfl_response.raise_for_status()
        add_nfl(days, nfl_response.text)
        for year in (2024, 2025, 2026):
            count = sum(len(row['bills_games']) for day, row in days.items() if day.startswith(str(year)))
            if count < 12:
                raise ValueError(f'Incomplete Bills schedule for {year}; prior snapshot preserved.')
        snapshot = {'generated_at': datetime.now(ET).isoformat(), 'coverage_start': '2024-08-24',
                    'coverage_end': '2027-01-31', 'college_events_by_calendar_year': counts,
                    'college_last_scheduled': max(day for day, row in days.items() if row['college_games']),
                    'nfl_last_scheduled': max(day for day, row in days.items() if row['nfl_games']),
                    'sources': {'college': COLLEGE_SCOREBOARD_URL, 'nfl': NFL_SCHEDULE_URL},
                    'days': dict(sorted(days.items()))}
        output = Path(output)
        pending = output.with_suffix('.tmp')
        pending.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        pending.replace(output)
        return {'days_with_games': len(days), 'college_events': counts,
                'uga_games': sum(len(row['uga_games']) for row in days.values()),
                'bills_games': sum(len(row['bills_games']) for row in days.values()), 'output': str(output)}
    finally:
        if own_client:
            http.close()


if __name__ == '__main__':
    print(json.dumps(import_calendar(), indent=2))
