from datetime import datetime, time, timedelta
from .calculations import ET
from .storage import utcnow
from .briefing_format import telegram_briefing

def next_briefing(now=None):
    now = (now or datetime.now(ET)).astimezone(ET)
    target = datetime.combine(now.date(), time(9), ET)
    if target <= now:
        target = datetime.combine(now.date()+timedelta(days=1), time(9), ET)
    return target.isoformat()

def schedule_status(db, enabled):
    latest = db.rows("SELECT day,created,ai,mode FROM briefings ORDER BY id DESC LIMIT 1")
    delivery=db.get('briefing_delivery') or (db.get('delivery:telegram_briefing:'+latest[0]['day']) if latest else None)
    return {**db.get('scheduler', {}), 'enabled': enabled, 'next_briefing': next_briefing(),
            'last_briefing': latest[0] if latest else None, 'last_delivery': db.get('telegram_delivery'),
            'last_briefing_delivery':delivery,
            'policy': '09:00 America/New_York; catch up once after restart while logged in and awake'}

async def deliver_briefing(ops, telegram, manual=False):
    # Both manual and scheduled delivery use the same process and lock.
    async with telegram.briefing_delivery_lock:
        report = await ops.briefing()
        if report['mode'] != 'live':
            raise RuntimeError('Telegram briefing delivery requires live mode.')
        key = 'telegram_briefing:' + report['day']
        if not manual and ops.db.get(key):
            return {'status': 'Already delivered', 'day': report['day']}
        delivery_id = 'manual:' + utcnow() if manual else key
        try:
            await telegram.send(('MANUAL BRIEFING TEST\n' if manual else '') + telegram_briefing(report), delivery_id)
        finally:
            ops.db.set('briefing_delivery',ops.db.get('telegram_delivery'))
        if not manual:
            ops.db.set(key, True)
        return {'status':'Delivered to configured private Telegram chat', 'day':report['day'], 'ai':bool(report['ai'])}
