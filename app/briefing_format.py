"""Compact Telegram presentation of the saved briefing; no new data or AI calls."""
from datetime import date,datetime
import re

from .calculations import ET


def plain(text):
    text=re.sub(r'(?m)^\s{0,3}#{1,6}\s*','',text)
    text=re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+)\)',r'\1 (\2)',text)
    return text.replace('**','').replace('__','').replace('`','').strip()


def compact(text,limit):
    text=' '.join(plain(text).split())
    if len(text)<=limit:return text
    prefix=text[:limit-1]
    # Preserve a complete sentence where possible; otherwise mark the excerpt.
    sentence=re.search(r'^(.{60,}[.!?])\s',prefix)
    return sentence.group(1) if sentence else prefix.rsplit(' ',1)[0]+'…'


def local_stamp(value):
    try:
        stamp=datetime.fromisoformat(value.rstrip('.'))
        if stamp.tzinfo is None:return value
        local=stamp.astimezone(ET)
        return local.strftime('%a, %b %d at %I:%M %p').replace(' 0',' ')+ ' ET'
    except ValueError:
        return value


def short_day(value):
    return date.fromisoformat(value).strftime('%a, %b %d').replace(' 0',' ')


def telegram_briefing(report):
    """One bounded plain-text digest; the original full report stays unchanged."""
    full=report['text']; narrative='';source=full
    if full.startswith('AI Interpretation\n') and '\n\nSource report\n' in full:
        narrative,source=full[len('AI Interpretation\n'):].split('\n\nSource report\n',1)
    lines=source.splitlines()
    def find(prefix):return next((line for line in lines if line.startswith(prefix)),'')
    matched=re.search(r'\| Business date (\d{4}-\d{2}-\d{2})',source)
    footer='Full report: dashboard → Daily briefings.'
    if not matched:
        return '☀ Tony’s morning brief · '+short_day(report['day'])+'\n\n'+compact(full,2900)+'\n\n'+footer
    sections=['☀ Tony’s morning brief · '+short_day(report['day']),
              'Sales for '+short_day(matched.group(1))]
    sales=[find('Net sales '),find('Week to date '),find('Last 7 days versus '),find('Discounts ')]
    sales=[line.removesuffix('.') for line in sales if line]
    sales=[re.sub(r'Last 7 days versus preceding 7 days:','7-day change:',line) for line in sales]
    sales=[re.sub(r'Week to date \(since (\d{4}-\d{2}-\d{2})\)',lambda m:'Week to date (since '+short_day(m[1])+')',line) for line in sales]
    sections.append('SALES\n'+'\n'.join(sales))
    watch=[]
    hours=find('Overtime watch: ')
    if hours:
        since=re.search(r'35\+ recorded hours since (\d{4}-\d{2}-\d{2})',source)
        watch.append(hours.replace('Overtime watch: ','Hours watch: ')+((' since '+short_day(since[1])) if since else '')+' — advisory, not payroll')
    labor=find('Recorded labor: ') or find('Estimated labor: ') or find('Labor unavailable: ')
    if labor:
        label=labor.split(';',1)[0].removesuffix('.')
        watch.append('Yesterday’s '+label[0].lower()+label[1:])
    stock=find('Out of stock: ')
    if stock:
        items=list(dict.fromkeys(stock[len('Out of stock: '):].split(', ')))
        watch.append('Current stock: '+compact(', '.join(items),480))
    followups=find('Open follow-ups: ')
    if followups.removeprefix('Open follow-ups: ').strip():watch.append(compact(followups,240))
    if watch:sections.append('WATCH TODAY\n'+'\n'.join('• '+line for line in watch))
    orders=re.findall(r'^• (\$[\d,.]+) at (\S+)$',source,re.M)
    if orders:
        count=re.search(r'^(\d+) upcoming large orders',source,re.M)
        total=int(count[1]) if count else len(orders)
        sections.append('LARGE ORDERS\n'+'\n'.join('• '+amount+' · '+local_stamp(stamp) for amount,stamp in orders[:4])+
                        (f'\n+{total-4} additional orders; review upcoming-order details' if total>4 else ''))
    elif find('0 upcoming large orders'):
        sections.append('LARGE ORDERS\nNone returned in the scanned window.')
    if re.search(r'scan incomplete|coverage incomplete',source,re.I):
        sections.append('Order coverage is incomplete; check the full report.')
    events=re.findall(r'^• (\d{4}-\d{2}-\d{2}): (.+)$',source,re.M)
    if events:
        entries=['• '+short_day(day)+' · '+compact(label,100) for day,label in events[:5]]
        sections.append('STAFF CALENDAR\n'+'\n'.join(entries)+'\nStaff-entered plans; game listings unverified.'+
                        ('\nCalendar refresh unavailable; saved copy.' if 'Calendar refresh unavailable' in source else ''))
    if 'Outlook unavailable:' in source:sections.append('EMAIL\nUnavailable; not covered in this briefing.')
    elif 'Outlook Inbox:' in source:
        messages=re.findall(r'^• (\S+) \| (.+?) \| (.+)$',source,re.M)
        sections.append('EMAIL\n'+('\n'.join('• '+compact(sender+' · '+subject,150) for _,sender,subject in messages[:3])
            if messages else 'No relevant new messages in the scanned interval.')+
            ('\nMore may remain; see the full report.' if len(messages)>3 or 'More messages may remain' in source else ''))
    # Keep interpretation distinct from facts. No attempt to reinterpret numbers.
    bullets=re.findall(r'^\s*[-•]\s+(.+)$',narrative,re.M)
    if narrative:
        selected=bullets[:3] if bullets else [narrative]
        sections.append('TONY’S TAKE · AI interpretation\n'+'\n'.join('• '+compact(value,300) for value in selected))
    else:
        sections.append('AI interpretation unavailable or disabled; facts above are preserved.')
    fresh=find('Retrieved ')
    stamp=re.search(r'^Retrieved (\S+);',fresh)
    note=('Checked '+local_stamp(stamp[1])+'. ' if stamp else '')+'Sales may revise; stock is a current snapshot.'
    # Omitted sections are disclosed. Never split a message or silently clip data.
    result=[]; omitted=False
    for section in sections:
        candidate='\n\n'.join(result+[section,note,footer])
        if len(candidate.encode('utf-16-le'))//2<=3200:result.append(section)
        else:omitted=True
    if omitted:result.append('Additional details omitted from this digest; see the full report.')
    return '\n\n'.join(result+[note,footer])
