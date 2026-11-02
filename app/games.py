"""Read-only football schedule context for the restaurant sales planning."""
import csv
import io
import json
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .storage import utcnow


NFL_SCHEDULE_URL = 'https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'
COLLEGE_SCOREBOARD_URL = 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard'
UGA_SCHEDULE_URL = 'https://georgiadogs.com/sports/football/schedule?path=football'
# Dates from Georgia Athletics' published 2026 schedule. Times are deliberately omitted;
# kickoff windows can change and only the official schedule should be used for timing.
UGA_2026 = {
    '2026-09-05': 'Tennessee State', '2026-09-12': 'Western Kentucky',
    '2026-09-19': 'Arkansas', '2026-09-26': 'Oklahoma',
    '2026-10-03': 'Vanderbilt', '2026-10-10': 'Alabama',
    '2026-10-17': 'Auburn', '2026-10-31': 'Florida',
    '2026-11-07': 'Ole Miss', '2026-11-14': 'Missouri',
    '2026-11-21': 'South Carolina', '2026-11-28': 'Georgia Tech',
}


def parse_nfl_schedule(raw, year):
    games = {}
    for row in csv.DictReader(io.StringIO(raw)):
        if row.get('season') != str(year) or row.get('game_type') not in {'REG', 'WC', 'DIV', 'CON', 'SB'}:
            continue
        try:
            day = date.fromisoformat(row['gameday']).isoformat()
        except (KeyError, ValueError):
            continue
        away, home = row.get('away_team'), row.get('home_team')
        if not away or not home:
            continue
        clock = row.get('gametime') or ''
        try:
            hour = int(clock.split(':', 1)[0])
        except ValueError:
            hour = -1
        bills = 'BUF' in {away, home}
        prime_time = hour >= 19
        games.setdefault(day, []).append({'label': f'{away} at {home}', 'time_et': clock or None,
                                           'bills': bills, 'national_prime_time': prime_time,
                                           'source': NFL_SCHEDULE_URL})
    return games


def prominent_college_games(payload, day):
    """Flag ranked matchups and top-ten teams; ESPN uses rank 99 for unranked."""
    games = []
    for event in payload.get('events', []):
        try:
            event_day = datetime.fromisoformat(event['date'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York')).date()
            competitors = event['competitions'][0]['competitors']
            ranks = [int(team.get('curatedRank', {}).get('current') or 99) for team in competitors]
            if event_day != day or len(ranks) != 2 or not (max(ranks) <= 25 or min(ranks) <= 10):
                continue
            games.append({'label': event['name'], 'time_et': None, 'ranked_matchup': max(ranks) <= 25,
                          'top_ten_team': min(ranks) <= 10, 'source': COLLEGE_SCOREBOARD_URL})
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return games[:8]


class FootballCalendar:
    def __init__(self, http=None, archive_path=None):
        self.http = http or httpx.AsyncClient(trust_env=False, timeout=8, follow_redirects=False)
        self.cache = {}
        self.fetched = {}
        self.fetched_at = {}
        self.college_cache = {}
        path = Path(archive_path) if archive_path is not None else Path(__file__).with_name('football_calendar.json') if http is None else None
        try:
            self.archive = json.loads(path.read_text(encoding='utf-8')) if path else None
        except (OSError, ValueError):
            self.archive = None

    def archived(self, day):
        archive = self.archive
        day_text = day.isoformat()
        if not archive or not archive['coverage_start'] <= day_text <= archive['coverage_end']:
            return None
        row = archive['days'].get(day_text, {})
        uga = [{**game, 'uga': True} for game in row.get('uga_games', [])]
        college = row.get('prominent_college_games', [])[:8]
        bills = row.get('bills_games', [])
        national = row.get('national_nfl_games', [])
        college_count = row.get('college_games', 0)
        nfl_count = row.get('nfl_games', 0)
        college_available = day_text <= archive['college_last_scheduled']
        nfl_available = day_text <= archive['nfl_last_scheduled']
        return {'date': day_text, 'nfl_schedule_retrieved_at': archive['generated_at'],
                'college_schedule_retrieved_at': archive['generated_at'], 'college_schedule_available': college_available,
                'nfl_schedule_available': nfl_available, 'uga_2026_schedule_available': day.year == 2026 or day.year == 2027 and day.month <= 1,
                'schedule_snapshot_at': archive['generated_at'],
                'signals': {'bills': bool(bills), 'uga': bool(uga), 'prominent_college': bool(college),
                            'college_games': college_count, 'college_schedule_available': college_available,
                            'nfl_prime_time': bool(national) or any(game.get('national_prime_time') for game in bills),
                            'nfl_games': nfl_count},
                'featured_games': uga + college + bills + national[:4],
                'limitations': ['Offline schedule snapshot; future dates, kickoffs, rankings, and postseason games can change. Refresh with python -m scripts.football_import.',
                                'College days cover the ESPN FBS scoreboard, not every college division or proof that the restaurant televised a game.',
                                'Prominent college games are a ranking heuristic, not proven local viewing or a causal sales uplift.',
                                'NFL games come from the nflverse schedule feed.']}

    async def college(self, day):
        key = day.isoformat()
        cached = self.college_cache.get(key)
        if cached and time.monotonic() - cached[0] < 4 * 3600:
            return cached[1], cached[2]
        try:
            response = await self.http.get(COLLEGE_SCOREBOARD_URL, params={'dates': day.strftime('%Y%m%d'), 'groups': '80', 'limit': '150'})
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ValueError('College schedule response too large')
            payload = response.json()
            if not isinstance(payload.get('events'), list):
                raise ValueError('College schedule malformed')
            games = prominent_college_games(payload, day)
            fetched_at = utcnow()
            self.college_cache[key] = (time.monotonic(), games, fetched_at)
            return games, fetched_at
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return (cached[1], cached[2]) if cached else (None, None)

    async def nfl(self, year):
        cached = self.cache.get(year)
        if cached is not None and time.monotonic() - self.fetched[year] < 12 * 3600:
            return cached
        try:
            response = await self.http.get(NFL_SCHEDULE_URL)
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                raise ValueError('Schedule response too large')
            games = parse_nfl_schedule(response.text, year)
            if not games:
                raise ValueError('No valid NFL games found')
            self.cache[year] = games
            self.fetched[year] = time.monotonic()
            self.fetched_at[year] = utcnow()
            return games
        except (httpx.HTTPError, ValueError):
            return cached

    async def on(self, day):
        archived = self.archived(day)
        if archived is not None:
            return archived
        day_text = day.isoformat()
        nfl = await self.nfl(day.year - (day.month <= 2))
        nfl_games = (nfl or {}).get(day_text, [])
        uga = [{'label': 'Georgia vs ' + UGA_2026[day_text], 'time_et': None,
                'uga': True, 'source': UGA_SCHEDULE_URL}] if day_text in UGA_2026 else []
        college, college_at = await self.college(day) if 3 <= day.weekday() <= 5 else ([], None)
        college_featured = [game for game in (college or []) if 'Georgia Bulldogs' not in game['label']]
        featured = [game for game in nfl_games if game['bills'] or game['national_prime_time']]
        return {'date': day_text, 'nfl_schedule_retrieved_at': self.fetched_at.get(day.year),
                'college_schedule_retrieved_at': college_at, 'college_schedule_available': college is not None,
                'nfl_schedule_available': nfl is not None, 'uga_2026_schedule_available': day.year == 2026,
                'signals': {'bills': any(game['bills'] for game in nfl_games),
                            'uga': bool(uga), 'prominent_college': bool(college_featured),
                            'college_schedule_available': college is not None,
                            'nfl_prime_time': any(game['national_prime_time'] for game in nfl_games),
                            'nfl_games': len(nfl_games)},
                'featured_games': uga + college_featured + featured,
                'limitations': ['UGA dates come from the published 2026 schedule; kickoff times and changes require checking the current official schedule.',
                                'Other prominent college games are selected from a third-party ESPN scoreboard when available: ranked-vs-ranked matchups or any game with a top-ten team. This is a heuristic, not a complete measure of local TV interest.',
                                'NFL schedule data is a third-party nflverse feed; unavailable feed data is never treated as proof of no game.']}

    async def close(self):
        await self.http.aclose()
