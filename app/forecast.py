"""Cautious sales planning estimates from the restaurant own comparable Toast days."""
from datetime import date
from statistics import median

from .calculations import money


def game_kind(signals):
    if signals.get('bills'):
        return 'Bills game'
    if signals.get('uga'):
        return 'UGA game'
    if signals.get('prominent_college'):
        return 'prominent college game'
    if signals.get('nfl_prime_time'):
        return 'national NFL prime-time game'
    if not signals.get('nfl_schedule_available', True):
        return 'NFL schedule unavailable'
    if date.fromisoformat(signals['date']).weekday() in (3, 4, 5) and not signals.get('college_schedule_available', True):
        return 'college schedule unavailable'
    if signals.get('nfl_games') and date.fromisoformat(signals['date']).weekday() == 6:
        return 'NFL Sunday'
    if signals.get('college_games'):
        return 'college football day'
    return 'no flagged football game'


def estimate_sales(target, history, game, current=None):
    usable = sorted((row for row in history if row['orders'] > 0 and row['net_sales'] > 0), key=lambda row: row['date'])
    if len(usable) < 4:
        return {'estimate': None, 'reason': 'Fewer than four nonzero same-weekday Toast days are available.',
                'comparable_days': len(usable)}
    target_kind = game_kind({'date': target.isoformat(), 'nfl_schedule_available': game['nfl_schedule_available'], **game['signals']})
    matched = [row for row in usable if row.get('game_kind') == target_kind]
    if target_kind != 'no flagged football game' and len(matched) >= 2:
        chosen, game_method = matched, f'{len(matched)} same-weekday {target_kind} analogues'
    elif target_kind == 'no flagged football game' and len(matched) >= 3:
        chosen, game_method = matched, f'{len(matched)} same-weekday days without flagged football games'
    else:
        chosen, game_method = usable, 'game-specific uplift unavailable; all recent same weekdays used'
    baseline = float(median(row['net_sales'] for row in chosen))
    trend = None
    if len(usable) >= 6:
        recent = float(median(row['net_sales'] for row in usable[-3:]))
        previous = float(median(row['net_sales'] for row in usable[-6:-3]))
        if previous > 0:
            trend = max(-0.2, min(0.2, recent / previous - 1))
    estimate = baseline * (1 + 0.25 * trend) if trend is not None else baseline
    pace = None
    if current and current.get('elapsed_hours', 0) >= 7 and current.get('completed_hour_sales', 0) > 0:
        elapsed = current['elapsed_hours']
        fractions = [sum(row['hourly'][:elapsed]) / row['net_sales'] for row in usable if row['net_sales'] > 0]
        typical_fraction = float(median(fractions)) if fractions else 0
        if typical_fraction >= 0.1:
            pace = current['completed_hour_sales'] / typical_fraction
            # Historical pace is an estimate, not a point-in-time historical snapshot.
            estimate = 0.6 * estimate + 0.4 * pace
        estimate = max(estimate, current.get('net_sales', 0))
    values = sorted(float(row['net_sales']) for row in chosen)
    low = min(values[max(0, int((len(values) - 1) * 0.2))], estimate * 0.8)
    high = max(values[min(len(values) - 1, int((len(values) - 1) * 0.8))], estimate * 1.2)
    if current:
        low = max(low, current.get('net_sales', 0))
        high = max(high, estimate, low)
    return {'estimate': money(estimate), 'planning_range_low': money(low), 'planning_range_high': money(high),
            'comparable_days': len(usable), 'game_analogue_days': len(matched),
            'game_method': game_method, 'target_game_kind': target_kind,
            'recent_weekday_trend_used_percent': round(trend * 100, 1) if trend is not None else None,
            'pace_projection': money(pace) if pace is not None else None,
            'history_start': usable[0]['date'], 'history_end': usable[-1]['date'],
            'analogue_dates': [row['date'] for row in chosen],
            'limitations': ['Planning estimate and empirical range, not a guaranteed forecast or confidence interval.',
                            'Only recent same-weekday Toast days are used; year-over-year seasonality cannot be learned without older usable sales history.',
                            'Game-day analogues are observational and do not establish a causal game uplift.',
                            'Unflagged college games and other events may affect demand; prominence is based on ranking, not proven local viewing.']}
