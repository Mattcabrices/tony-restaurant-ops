"""Citation extraction for hosted public web search."""
from urllib.parse import urlsplit


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
