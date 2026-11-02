"""Deterministic disclosure limits; model instructions cannot expand them."""
import copy
import re

EXTERNAL_TOOLS = {'get_email', 'get_monday_items', 'get_business_calendar', 'get_shift_desk',
                  'get_approved_tasks', 'get_corner', 'get_changes', 'get_exceptions'}
STOP = {'what','when','where','which','about','there','their','have','does','with','from','that',
        'this','your','tony','restaurant','tonyops','pizza','tell','please','would','could','should','know',
        'today','yesterday','tomorrow','will','were','they','them','some','many','more','need'}


def terms(text):
    return {w for w in re.findall(r'[a-z0-9]+', str(text).casefold()) if len(w) >= 3 and w not in STOP}


def wants_history(question):
    return bool(re.search(r'\b(that|those|same|again|previous answer|you said|follow.?up)\b', question, re.I))


def report_needed(question):
    q = question.casefold()
    return (any(x in q for x in ('sales looking', 'sales today', 'sales yesterday', 'overview', 'sales pace', 'how are we doing'))
            and not any(x in q for x in ('how many', 'clover', '2025', '2024', 'week', 'month')))


def personal_details_requested(question):
    return bool(re.search(r'\b(who|employee|staff names|clocked in|overtime|sender|email address)\b', question, re.I))


def minimize(value, include_people=False):
    """Retain aggregate facts while excluding employee/customer identities by default."""
    if isinstance(value, list):
        return [minimize(x, include_people) for x in value]
    if isinstance(value, str) and not include_people:
        if value.endswith(' is no longer listed clocked in.'):
            return 'An employee is no longer listed clocked in; identities withheld for this aggregate request.'
        if value.endswith(' clocked in.'):
            return 'An employee clocked in; identities withheld for this aggregate request.'
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {'customers', 'customer', 'customerName', 'customerEmail', 'phone', 'phoneNumber',
                   'deliveryAddress', 'billingAddress', 'access_token', 'refresh_token', 'bot_token', 'password'}:
            continue
        if not include_people and key in {'employee', 'employee_id', 'employee_guid', 'employee_hours',
                                         'employees', 'firstName', 'lastName', 'address'}:
            continue
        if not include_people and key in {'clocked_in', 'overtime'} and isinstance(item, list):
            result[key+'_count'] = len(item)
            continue
        result[key] = minimize(item, include_people)
    return result


def model_result(value, question):
    return minimize(copy.deepcopy(value), personal_details_requested(question))
