"""Offline football dates with separately labeled Clover sales observations."""
from collections import defaultdict
from datetime import date
from statistics import median

from .forecast import game_kind


def football_history(db, football, start, end, focus='all'):
    archive = football.archive
    if not archive:
        return {'error': 'Offline football calendar is unavailable; no game dates were inferred.'}
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        return {'error': 'Use YYYY-MM-DD dates.'}
    if first > last or (last-first).days > 1000:
        return {'error': 'Select a valid interval of at most 1,001 days.'}
    if start < archive['coverage_start'] or end > archive['coverage_end']:
        return {'error': 'Dates are outside the offline football calendar coverage.',
                'coverage': [archive['coverage_start'], archive['coverage_end']]}
    if focus not in {'all', 'bills', 'uga', 'college'}:
        return {'error': 'Focus must be all, bills, uga, or college.'}
    seasons = defaultdict(lambda: {'college_days': 0, 'uga_days': 0, 'bills_days': 0, 'prominent_college_days': 0, 'nfl_days': 0})
    details = []
    for day, row in archive['days'].items():
        if not start <= day <= end:
            continue
        uga, bills, college = bool(row['uga_games']), bool(row['bills_games']), bool(row['college_games'])
        season = str(date.fromisoformat(day).year - (date.fromisoformat(day).month <= 2))
        yearly = seasons[season]
        yearly['college_days'] += int(college)
        yearly['uga_days'] += int(uga)
        yearly['bills_days'] += int(bills)
        yearly['prominent_college_days'] += int(bool(row['prominent_college_games']))
        yearly['nfl_days'] += int(row['nfl_games'] > 0)
        include = (bills or uga or bool(row['prominent_college_games'])) if focus == 'all' else bills if focus == 'bills' else uga if focus == 'uga' else college
        if include:
            details.append({'date': day, 'season': season, 'college_games': row['college_games'], 'nfl_games': row['nfl_games'],
                            'uga': [game['label'] for game in row['uga_games']],
                            'bills': [game['label'] for game in row['bills_games']],
                            'prominent_college': [game['label'] for game in row['prominent_college_games'][:3]]})
    # Gross line-item sales are observations, never presented as Toast net sales
    # or as proof that the games caused any change in demand.
    groups = defaultdict(list)
    for row in db.rows('SELECT day,gross_line_cents,paid_orders FROM clover_days WHERE day>=? AND day<=? AND gross_line_cents>0 AND paid_orders>0', (start, end)):
        day = row['day']
        game = archive['days'].get(day, {})
        kind = game_kind({'date': day, 'nfl_schedule_available': True,
                          'bills': bool(game.get('bills_games')), 'uga': bool(game.get('uga_games')),
                          'prominent_college': bool(game.get('prominent_college_games')),
                          'college_games': game.get('college_games', 0),
                          'nfl_prime_time': bool(game.get('national_nfl_games')),
                          'nfl_games': game.get('nfl_games', 0)})
        groups[kind].append(row['gross_line_cents'])
    gross = {kind: {'days': len(values), 'median_gross_line_sales': round(median(values)/100, 2)} for kind, values in sorted(groups.items())}
    return {'source': archive['sources'], 'snapshot_at': archive['generated_at'],
            'period': [start, end], 'focus': focus, 'days_by_season': dict(sorted(seasons.items())),
            'matching_dates': details[:120], 'omitted_matching_dates': max(0, len(details)-120),
            'clover_gross_by_game_kind': gross,
            'limitations': ['Football dates are a public offline snapshot; future games and kickoffs can change.',
                            'College games are ESPN FBS scoreboard events; prominent games use ranking heuristics.',
                            'Clover medians are approximate gross line-item sales through July 19, 2026, not Toast net sales.',
                            'The game calendar follows Eastern civil dates while POS business dates use the configured closeout; late games can cross that boundary.',
                            'Sales differences are observational and do not establish a causal game effect.']}
