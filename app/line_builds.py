"""Search the privately imported serving-build catalog; never expose batch recipes."""
import json
import re
from pathlib import Path

from .storage import ROOT


CATALOG = ROOT / 'data' / 'line_builds.json'
STOP = {'a', 'an', 'and', 'for', 'in', 'is', 'it', 'many', 'of', 'on', 'order', 'our',
        'per', 'pizza', 'pizzas', 'recipe', 'serving', 'the', 'there', 'to', 'what',
        'with', 'build', 'how', 'do', 'we', 'get', 'does'}


def words(value):
    return {w[:-1] if w.endswith('s') and len(w) > 4 else w
            for w in re.findall(r'[a-z0-9]+', value.casefold()) if w not in STOP}


def requested_size(query):
    text = query.casefold()
    if re.search(r'\b(?:large|lrg)\b', text):
        return 'large'
    if re.search(r'\b(?:medium|med)\b', text):
        return 'medium'
    if re.search(r'\b(?:gluten[- ]?free|gf)\b', text):
        return 'gluten free'
    if re.search(r'\b(?:9[- ]?inch|mini|personal)\b', text):
        return '9 inch'
    return None


def search_builds(query, catalog_path=CATALOG):
    path = Path(catalog_path)
    if not path.is_file():
        return {'available': False, 'reason': 'No serving-build document has been imported.'}
    data = json.loads(path.read_text(encoding='utf-8'))
    size = requested_size(query)
    terms = words(query) - {'large', 'lrg', 'medium', 'med', 'gluten', 'free', 'gf', '9', 'inch', 'mini', 'personal'}
    if not terms:
        return {'available': True, 'matches': [], 'reason': 'Specify a menu item or build name.',
                'source': data['source_title']}
    scored = []
    for build in data['builds']:
        candidates = [build['name'], *build.get('aliases', [])]
        overlap = max((len(terms & words(candidate)) for candidate in candidates), default=0)
        if overlap:
            scored.append((overlap == len(terms), overlap, build))
    scored.sort(key=lambda row: (-row[0], -row[1], row[2]['name']))
    matches = []
    for _, _, build in scored[:6]:
        item = {key: build[key] for key in ('name', 'source_section', 'notes')}
        if 'components' in build:
            item['components'] = build['components']
        if 'sizes' in build:
            item['size'] = size
            item['components'] = build['sizes'].get(size) if size else None
            if not size:
                item['available_sizes'] = list(build['sizes'])
                item['note'] = 'Ask for a pizza size before quoting ingredient quantities.'
        matches.append(item)
    return {'available': True, 'source': data['source_title'],
            'source_sha256_prefix': data['source_sha256'][:12], 'imported_at': data['imported_at'],
            'scope': data['scope'], 'query': query, 'matches': matches,
            'more_matches': len(scored) > len(matches),
            'limitations': ['Documented standard builds, not a count of actual ingredient consumption.',
                            'Toast modifiers, substitutions, waste, and later build changes can alter actual usage.',
                            'Conflicting or draft source notes must be verified before using those quantities.']}
