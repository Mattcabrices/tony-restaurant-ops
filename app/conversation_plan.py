"""AI-first bounded read/draft plans; no generated execution or write authority."""
import asyncio
import json
import re
from .ai import AIError
from .context_policy import report_needed
from .guest_lookup import natural_period,parse
from .intent_router import masked_request,date_candidates,validated_request,LIMIT
from .storage import utcnow

FIELDS={
 'action':{'type':'string','enum':['guest','writeup','ordinary']},
 'workflow_action':{'type':'string','enum':['none','draft','note','revise','status','cancel','resume','retry','pdf','new']},
 'mode':{'type':'string','enum':['unspecified','factual','fictional']},
 'reuse_guest':{'type':'boolean'},'show_orders':{'type':'boolean'},
 **{k:{'type':'integer'} for k in ('clause_start','clause_end','selector_start','selector_end','period_start','period_end')}}
TOOL={'type':'function','name':'plan_conversation','strict':True,
 'description':'Plan up to three current-owner read/draft requests. No code, external messages, settings or production writes.',
 'parameters':{'type':'object','properties':{
  'steps':{'type':'array','items':{'type':'object','properties':FIELDS,'required':list(FIELDS),'additionalProperties':False}},
  'needs_clarification':{'type':'boolean'}},'required':['steps','needs_clarification'],'additionalProperties':False}}
PROMPT='''Interpret the manager's WHOLE current message, then call plan_conversation exactly once.
The indexed tokens hide unknown words/names/contacts as PRIVATE tokens. Select spans from this
request; never invent their values. business_date and validated date_candidates resolve dates.
Greetings/testing filler are not tasks. Handle multiple requests instead of choosing just one:
"get PRIVATE's orders from DATE and make a joke write-up for PRIVATE for PREMISE" needs BOTH
guest and writeup steps with separate non-overlapping clause spans. Up to three steps, at most
one per action. Preserve requested order. clause_start/end are token indices, end exclusive.
guest: select ONLY identity in selector_start/end and date in period_start/end; both must lie
inside this clause. A possessive identity token may end in 's; local code strips that suffix.
Select the complete date expression, such as "Friday the 23rd of October", excluding
introducing "from". Do not discard the month or a supplied weekday/day confirmation.
Name/tab candidates are not verified identities. show_orders=true for items/orders/tabs.
For a date-only or same-person follow-up, preserve previous_guest_orders=true unless the
manager explicitly switches to contact/profile information. "What about Saturday for PRIVATE?"
still asks what was ordered when the prior question did. Do not require repeated order wording.
Reuse_guest=true is only for an unambiguous pronoun follow-up when has_guest_context=true;
then selectors must be zero. Never reuse it when another person is named. Missing dates use
zero period indices (existing seven-date default, or previous period for a guest follow-up).
writeup: workflow_action=draft for a requested NEW form, note for added facts to an existing form; status for questions/yes/no,
revise for directions to edit the existing draft/PDF (make it firmer, expand impact, shorten,
change tone, remove a section, improve expectations). Preserve the incident rather than
starting over. Instructions are not incident evidence. If the message supplies new factual
information or a factual correction, use note even if it also contains wording directions.
cancel to leave, resume to return, retry ONLY for an explicit retry, pdf to return the form,
new ONLY for an explicit fresh blank draft. Its clause contains only the writeup request/facts,
never the guest lookup clause. mode=fictional for explicit joke/practice/test samples, factual
for genuine incidents, otherwise unspecified. A deliberately absurd sports/fiction premise
in a testing request is fictional, not a factual employee allegation. Unknown details stay unknown.
When awaiting_answers=true, factual answers to the coaching questions are writeup note, including short answers such as "I saw it myself" or "unknown". A request to proceed/create the PDF despite unknowns is pdf; a bare yes/no is note only when awaiting_answers=true (the drafting model can clarify ambiguous answers), otherwise status. Never treat confirmation as incident evidence.
Workflow context is not a command to absorb all chat. New topics are ordinary.
ordinary: current business questions or conversation; the existing read tools answer this clause.
Aggregate orders/pizzas booked for a future day ("how many orders for Saturday?", "pizzas
booked for Saturday") are ordinary business reads, not guest lookups or forecasts. A guest
lookup requires a specific person/contact/tab selector, not merely the word orders.
Requests to send mail, approve code, change settings or production systems remain ordinary
conversation about existing approval workflows; this planner grants no such capability.
For non-guest steps all selector/period indices, reuse_guest, show_orders must be zero/false.
For non-writeup workflow_action=none and mode=unspecified. Set needs_clarification=true only
when no safe interpretation exists, not merely because there are two different requests.
No retrieved data/history is provided or authorized. Never clear/delete existing drafts.'''


def validate_plan(text,tokens,value,today,guest_context=None):
    if not isinstance(value,dict) or set(value)!={'steps','needs_clarification'} or type(value['needs_clarification']) is not bool:raise ValueError('Invalid plan')
    steps=value['steps']
    if not isinstance(steps,list) or not 1<=len(steps)<=3:raise ValueError('Invalid step count')
    if value['needs_clarification']:return {'clarification':'I could not confidently separate those requests. Which part should I clarify?'}
    result=[];seen=set();ranges=[]
    for step in steps:
        if not isinstance(step,dict) or set(step)!=set(FIELDS):raise ValueError('Invalid step schema')
        action=step['action']
        if action not in ('guest','writeup','ordinary') or action in seen:raise ValueError('Unsupported/duplicate action')
        seen.add(action)
        for k in ('reuse_guest','show_orders'):
            if type(step[k]) is not bool:raise ValueError('Invalid flag')
        for k in ('clause_start','clause_end','selector_start','selector_end','period_start','period_end'):
            if type(step[k]) is not int or not 0<=step[k]<=len(tokens):raise ValueError('Invalid span')
        a,b=step['clause_start'],step['clause_end']
        if a>=b or any(max(a,x)<min(b,y) for x,y in ranges):raise ValueError('Overlapping clauses')
        ranges.append((a,b))
        clause=text[tokens[a].start():tokens[b-1].end()]
        if action!='writeup' and (step['workflow_action']!='none' or step['mode']!='unspecified'):raise ValueError('Unexpected workflow')
        if action=='guest':
            args={'route':'guest','workflow_action':'none','show_orders':step['show_orders']}
            for start,end in (('selector_start','selector_end'),('period_start','period_end')):
                p,q=step[start],step[end]
                if p==q==0:args[start]=args[end]=0
                elif not a<=p<q<=b:raise ValueError('Guest span outside clause')
                else:args[start],args[end]=p-a,q-a
            local_tokens,_=masked_request(clause)
            # Adjust possessives locally without asking the model to invent a name.
            if step['reuse_guest']:
                if not guest_context or args['selector_start'] or args['selector_end']:raise ValueError('Missing guest context')
                _,view=masked_request(clause)
                if any(t['token'].startswith('PRIVATE_') for t in view):raise ValueError('Ambiguous guest follow-up')
                start,end=guest_context['start'],guest_context['end']
                if args['period_end']:
                    p,q=args['period_start'],args['period_end']
                    phrase=clause[local_tokens[p].start():local_tokens[q-1].end()]
                    remaining,period=natural_period('context '+phrase,today)
                    if remaining!='context' or not period:raise ValueError('Unsupported period')
                    start,end=(x.isoformat() for x in period)
                request=f"{'find any orders for' if step['show_orders'] else '/guest'} {guest_context['kind']} {guest_context['query']} | {start} | {end}"
            else:
                p,q=args['selector_start'],args['selector_end']
                if p==q:raise ValueError('Missing guest identity')
                token=local_tokens[q-1]
                suffix=token[0][-2:]
                if suffix.casefold() in ("'s",'\u2019s'):
                    # Keep offsets stable for all subsequent token/date spans.
                    clause=clause[:token.end()-2]+'  '+clause[token.end():]
                    local_tokens,_=masked_request(clause)
                request=validated_request(clause,local_tokens,args,today)['request']
            # Preserve a same-identity follow-up's view locally even if the model
            # omits show_orders. Never inherit across another identity or a
            # deliberate switch to contact/profile information.
            if guest_context and guest_context.get('orders') and not step['show_orders']:
                kind,query,start,end=parse(request,today)
                same=kind==guest_context['kind'] and query.casefold()==guest_context['query'].casefold()
                continuation=step['reuse_guest'] or bool(re.match(r'\s*(?:what about|how about|and\b|same\b)',clause,re.I))
                contacts=bool(re.search(r'\b(?:email|phone|contact|profile|directory)\b',clause,re.I))
                if same and continuation and not contacts:
                    request=f'find any orders for {kind} {query} | {start.isoformat()} | {end.isoformat()}'
            result.append({'action':'guest','request':request})
        else:
            if any(step[k] for k in ('selector_start','selector_end','period_start','period_end','reuse_guest','show_orders')):raise ValueError('Unexpected selector')
            if action=='writeup':
                if step['workflow_action'] not in ('draft','note','revise','status','cancel','resume','retry','pdf','new') or step['mode'] not in ('unspecified','factual','fictional'):raise ValueError('Invalid workflow action')
                result.append({'action':'writeup','workflow_action':step['workflow_action'],'mode':step['mode'],'text':clause})
            else:result.append({'action':'ordinary','text':clause})
    return {'steps':result}


class ConversationPlanner:
    def __init__(self,ai,db):self.ai,self.db,self.lock=ai,db,asyncio.Lock()
    @property
    def enabled(self):return bool(self.ai and getattr(self.ai,'supports_conversation_planning',False))

    async def plan(self,text,today,workflow=None,guest_context=None):
        tokens,view=masked_request(text)
        failure={'clarification':'I could not interpret the complete request safely. Please clarify the part you want me to handle; no action was performed.'}
        if not text.strip() or len(text)>4000 or len(tokens)>800:return failure
        words={t[0].casefold() for t in tokens}
        if (report_needed(text) and not any(x['token'].startswith('PRIVATE_') for x in view)
                and not words.intersection({'guest','guests','customer','customers','name','named','email','phone','orders','person'})):
            return {'steps':[{'action':'ordinary','text':text}]}
        async with self.lock:
            key='intent_router_calls:'+utcnow()[:10];used=self.db.get(key,0)
            if type(used) is not int or used>=LIMIT:return {'clarification':'The daily AI interpretation limit is reached. Explicit commands still work; no tasks were executed.'}
            self.db.set(key,used+1)
            try:
                response=await asyncio.wait_for(self.ai.response([
                    {'role':'developer','content':PROMPT},
                    {'role':'user','content':json.dumps({'tokens':view,'business_date':today.isoformat(),'date_candidates':date_candidates(text,tokens,today),
                        'workflow_context':workflow or {},'has_guest_context':bool(guest_context),
                        'previous_guest_orders':bool(guest_context and guest_context.get('orders'))})}],
                    [TOOL],tool_choice={'type':'function','name':'plan_conversation'},local_output_chars=3000),timeout=40)
                calls=[x for x in response.get('output',[]) if x.get('type')=='function_call']
                if len(calls)!=1 or calls[0].get('name')!='plan_conversation':raise ValueError('Invalid plan call')
                raw=calls[0].get('arguments','')
                if not isinstance(raw,str) or len(raw)>6000:raise ValueError('Invalid plan size')
                result=validate_plan(text,tokens,json.loads(raw),today,guest_context)
                self.db.set('intent_router_last',{'at':utcnow(),'route':'plan','actions':[s['action'] for s in result.get('steps',[])]})
                return result
            except (AIError,ValueError,TypeError,KeyError,AttributeError,asyncio.TimeoutError):
                self.db.set('intent_router_last',{'at':utcnow(),'route':'unavailable'})
                return failure
