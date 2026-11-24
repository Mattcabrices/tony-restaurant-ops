"""Small text helpers for owner notes and the AI-free sales overview."""
import re

STOP = {'what','when','where','which','about','there','their','have','does','with','from','that',
        'this','your','tony','restaurant','tonyops','pizza','tell','please','would','could','should','know',
        'today','yesterday','tomorrow','will','were','they','them','some','many','more','need'}


def terms(text):
    return {w for w in re.findall(r'[a-z0-9]+', str(text).casefold()) if len(w) >= 3 and w not in STOP}


def report_needed(question):
    q = question.casefold()
    return (any(x in q for x in ('sales looking', 'sales today', 'sales yesterday', 'overview', 'sales pace', 'how are we doing'))
            and not any(x in q for x in ('how many', 'clover', '2025', '2024', 'week', 'month')))
