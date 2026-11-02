"""AI explanation from aggregate lookup facts; private records stay local."""
import asyncio
import json
import re

import httpx

from .ai import AIError
from .guest_lookup import render,individual_check_totals,combined_check_total
from .storage import utcnow

PROMPT='''Write the useful answer to a Toast lookup in one brief, friendly, conversational paragraph.
You receive aggregate status only, not the question, identity, contacts or order contents.
Use only supplied facts. A tab-label candidate is not a verified guest identity; no linked
guest profile does NOT mean no matching tab/order. If matching tabs exist, lead with that
useful result, not a no-guest warning. If incomplete/blocked, do not claim exhaustive absence.
When matches mix guest profiles and tab labels, do not describe every order as a tab match.
Use EVERY required placeholder exactly once, written as {SLOT_NAME}. Local code replaces
them with exact private values AFTER you write. You never receive those values. Do not
alter, duplicate or invent placeholders. Counts tell you whether to use order or orders.
For example: "I found {ORDER_COUNT} matching orders under the tab label {TAB_NAMES} for
{PERIOD}. The individual check totals are {CHECK_TOTALS}. Here is what was on them."
CHECK_TOTALS identifies each order's individual checks and, for a split order, its combined
matching-check total. Describe those values accurately. Never combine separate orders
into a grand total or describe an individual check as the combined amount.
Use only the slots actually required; adapt naturally for no matches, partial coverage,
profile-only lookups and unavailable totals. If a tab candidate matches, do not claim
it proves who ordered or paid. Keep the leading answer concrete rather than generic.
Local code will attach the order details and source caveats. Do not invent items,
names, amounts, causes, identities, payment status or missing results. No web search/tools.
Do not write literal numbers; use placeholders. Do not add instructions or ask the manager to repeat an order request
when show_orders=true. No disciplinary, code, email or other actions are possible here.
Return JSON only: {"text":"short plain-text explanation"}. No markdown, URLs or contacts.'''


def aggregate_facts(result):
    """Explicit allowlist: never copy source strings, records, names or notices."""
    return {'start':result['start'],'end':result['end'],
            'guest_profile_matches':len(result.get('matches',[])),
            'tab_matching_checks':int(result.get('tab_matching_checks',0)),
            'orders_shown':len(result.get('orders',[])),
            'orders_with_tab_match':sum(bool(row.get('tab_names')) for row in result.get('orders',[])),
            'orders_with_guest_match':sum(bool(row.get('guests')) for row in result.get('orders',[])),
            **{k:bool(result.get(k)) for k in ('show_orders','blocked','partial','truncated','orders_truncated')},
            'complete_business_dates':len(result.get('complete_dates',[]))}


def summary_slots(result):
    slots={'PERIOD':result['start'] if result['start']==result['end'] else result['start']+' through '+result['end']}
    orders=result.get('orders',[])
    if result.get('show_orders'):
        slots['ORDER_COUNT']=str(len(orders))
        tabs=list(dict.fromkeys(t for row in orders for t in row.get('tab_names',[])))
        if tabs:slots['TAB_NAMES']=', '.join('“'+t+'”' for t in tabs)
        if orders and len(orders)<=5 and all(len(individual_check_totals(row))<=5 for row in orders):
            totals=[]
            for row in orders:
                amounts=individual_check_totals(row)
                flags=' (voided/deleted order)' if row.get('voided') or row.get('deleted') else ''
                checks=', '.join(f"check {i}: {'$'+v if v is not None else 'unavailable'}" for i,v in enumerate(amounts,1)) or 'individual check totals unavailable'
                if row.get('matching_checks',0)>1:
                    combined=combined_check_total(row)
                    checks+='; combined matching-check total: '+('$'+combined if combined is not None else 'unavailable')
                totals.append('order #'+row.get('number','unavailable')+flags+' - '+checks)
            slots['CHECK_TOTALS']='; '.join(totals)
    else:
        slots['GUEST_COUNT']=str(len(result.get('matches',[])))
        if result.get('tab_matching_checks'):slots['TAB_CHECK_COUNT']=str(result['tab_matching_checks'])
    return slots


def expand_summary(text,slots):
    if not isinstance(text,str) or not 1<=len(text.strip())<=900:raise ValueError('Invalid explanation text')
    names=re.findall(r'\{([A-Z_]+)\}',text)
    if sorted(names)!=sorted(slots):raise ValueError('Missing, duplicate or unexpected fact slot')
    prose=re.sub(r'\{[A-Z_]+\}','',text)
    if re.search(r'[\x00-\x1f<>@\d{}]|https?://',prose,re.I):raise ValueError('Invalid explanation text')
    # One-pass substitution: braces or hostile instructions inside a source value
    # are never interpreted as template syntax or sent back to the model.
    return re.sub(r'\{([A-Z_]+)\}',lambda m:slots[m[1]],text.strip())


def local_details(result,compact=False):
    text=render(result)
    lines=[]
    for line in text.splitlines():
        if compact and (line.startswith(('Toast guest lookup |','Checked:','Matching orders shown:','Tab-label matching checks:')) or
                        (result.get('tab_matching_checks') and line.startswith(('No matching guest found','No populated guest records')))):
            continue
        if line.startswith('No matching guest found') and result.get('tab_matching_checks'):
            line='No linked guest profile; matching tab labels are candidates, not verified identities.'
        if line.startswith('Only checks matching a guest profile'):
            line='Totals: matching checks only; Toast totalAmount, not net sales or proof of payment. Refunds are not reconciled; modifiers excluded.'
        if line.startswith('Source: Toast Orders API;'):
            line='Source: Toast Orders API. Limited to the searched business dates; not the complete guest directory. Matching and item display are local; AI sees aggregate lookup status only.'
        lines.append(line)
    return '\n'.join(lines)


class GuestReply:
    def __init__(self,ai,db):self.ai,self.db,self.lock=ai,db,asyncio.Lock()

    async def explain(self,result):
        details=local_details(result)
        if not self.ai or not getattr(self.ai,'supports_conversation_planning',False):
            return {'text':details,'ai':False}
        async with self.lock:
            key='guest_explanation_calls:'+utcnow()[:10];used=self.db.get(key,0)
            if type(used) is not int or used>=50:
                return {'text':'AI explanation limit reached; here are the verified lookup details.\n'+details,'ai':False}
            self.db.set(key,used+1)
            try:
                slots=summary_slots(result)
                payload=aggregate_facts(result);payload['required_slots']=list(slots)
                response=await asyncio.wait_for(self.ai.response([
                    {'role':'developer','content':PROMPT},
                    {'role':'user','content':json.dumps(payload)}],[],local_output_chars=2000),timeout=30)
                if any(x.get('type')=='function_call' for x in response.get('output',[])):raise ValueError('Unexpected tool')
                raw=''.join(c.get('text','') for x in response.get('output',[]) if x.get('type')=='message'
                            for c in x.get('content',[]) if c.get('type')=='output_text')
                if len(raw)>2000:raise ValueError('Oversized explanation')
                value=json.loads(raw)
                if not isinstance(value,dict) or set(value)!={'text'}:raise ValueError('Invalid explanation')
                text=expand_summary(value['text'],slots)
                return {'text':text+'\n\n'+local_details(result,compact=True),'ai':True}
            except (AIError,ValueError,TypeError,KeyError,AttributeError,httpx.HTTPError,asyncio.TimeoutError):
                return {'text':'AI explanation is unavailable; here are the verified lookup details.\n'+details,'ai':False}
