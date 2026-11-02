"""Public, time-sensitive query routing and citation extraction.

Only a purpose-built public query is sent to hosted search. Internal reports,
saved memories, and conversation history never enter that request.
"""
import re
from .restaurant_profile import profile
from urllib.parse import urlsplit


_TIME = re.compile(r"\b(today|tonight|tomorrow|this week|next week|weekend|upcoming|latest|current|now|yesterday)\b", re.I)
_PRIVATE = re.compile(r"\b(our|my|we|us|galla'?s?|toast|clover|r365|sales|revenue|labor|staff|inventory|recipe|orders?|sold|sell|wings?|fingers?|pizzas?|guests?|checks?|pmix)\b", re.I)


def public_query(question, day):
    """Return a public-only search query, or None for internal/stable questions."""
    q = question.lower()
    location=profile()['public_location']
    when = next((term for term in ('tomorrow', 'tonight', 'next week', 'this week', 'weekend', 'today', 'yesterday') if term in q), 'upcoming')
    if re.search(r'\b(weather|forecast|rain|temperature|storm)\b', q):
        return f'{location} weather forecast {when} {day}' if location else None
    if re.search(r'\b(news|headlines|breaking)\b', q):
        if 'bill' in q:
            topic = 'Buffalo Bills'
        elif 'uga' in q:
            topic = 'Georgia Bulldogs'
        elif _PRIVATE.search(q):
            topic = location
            if not topic:return None
        else:
            topic = question[:200]
        return f'{topic} news {when} {day}'
    if re.search(r'\b(nfl|bills|football|uga|bulldogs|game|kickoff)\b', q) and not (_PRIVATE.search(q) and not re.search(r'\b(when|who|play|playing|kickoff|schedule)\b', q)):
        team = 'Buffalo Bills' if 'bill' in q else 'Georgia Bulldogs' if re.search(r'\b(uga|bulldogs)\b', q) else 'NFL college football'
        return f'{team} game schedule kickoff opponent {when} {day}'
    if re.search(r'\b(events?|concerts?|festivals?|happenings?)\b', q):
        return f'{location} local events {when} {day}' if location else None
    # Broader current public facts can be searched when the question carries no
    # business context; otherwise fail closed instead of leaking private text.
    if re.search(r'\b(current|latest|live|right now|as of today)\b', q) and not _PRIVATE.search(q) and re.search(r'\b(what|who|when|where|how|which)\b', q):
        return question[:300]
    return None


def cited_result(response):
    calls = [x for x in response.get('output', []) if x.get('type') == 'web_search_call' and x.get('status') == 'completed']
    if not calls:
        return None
    chunks, sources, seen = [], [], set()
    for item in response.get('output', []):
        if item.get('type') != 'message':
            continue
        for part in item.get('content', []):
            if part.get('type') != 'output_text':
                continue
            chunks.append(part.get('text', ''))
            for annotation in part.get('annotations', []):
                citation = annotation.get('url_citation', annotation)
                url = citation.get('url', '')
                if annotation.get('type') != 'url_citation' or url in seen or urlsplit(url).scheme not in {'https', 'http'}:
                    continue
                seen.add(url)
                sources.append({'title': citation.get('title') or urlsplit(url).netloc, 'url': url})
    answer = '\n'.join(chunks).strip()
    return {'summary': answer, 'sources': sources, 'search_calls': len(calls)} if answer and sources else None
