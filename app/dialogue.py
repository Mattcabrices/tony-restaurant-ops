"""Bounded RAM-only query context; no records, permissions, or durable memory."""
import re
import time

from .guest_lookup import parse, natural_period, is_guest_lookup, DATE_PATTERN, WEEKDAY_PATTERN

BUSINESS={
    'get_business_lookup':{'metric','start','end','dimension'},
    'get_booked_orders':{'date'},
    'get_report':{'date'},'get_item_sales':{'query','date'},
    'get_modifier_sales':{'period','query','parent_query'},'get_weekly_sales':{'week_of'},
    'get_product_mix':{'week_of','compare_to'},'get_sales_forecast':{'date'},
    'get_line_build':{'query'},'get_item_leaderboard':{'query','start','end','limit','unit'},
    'get_clover_history':{'start','end','group_by'},'get_clover_items':{'start','end','query'},
    'get_clover_annual_pmix':{'month_day','count'},'get_football_calendar':{'start','end','focus'},
    'get_shift_desk':{'date'},'get_exceptions':set(),'get_seasonal_menu':{'query'},
    'get_corner':{'view','date'},'get_approved_tasks':{'id'},
    'get_business_calendar':{'start','end'},'get_monday_items':{'search'}}


def followup(text):
    if len(text)>240 or text.lstrip().startswith('/'):return False
    if re.fullmatch(r'(?:(?:on|last)\s+)?(?:'+DATE_PATTERN+'|'+WEEKDAY_PATTERN+r'|today|yesterday|last week|this week)',text.strip().rstrip('?!. '),re.I):return True
    return bool(re.search(
        r'^(?:(?:hey\s+tony[,!]?|please)\s+)?(?:what about|how about|and\b|same\b|again\b|try (?:that|again)|'
        r'(?:what|which) did (?:they|he|she) (?:order|buy)|(?:did|has|have) (?:they|he|she)\b|'
        r'(?:what\s+is|what.s) (?:their|his|her) (?:phone|email))',text.strip(),re.I))


def mail_requested(text):
    if not re.search(r'\b(?:emails?|mailbox|inbox)\b',text,re.I):return False
    if re.search(r'\b(?:draft|compose|send|write|setup|connect|integration|enable|how (?:can|do|does)|can i email|can you email)\b',text,re.I):return False
    return bool(re.search(r'\b(?:read|check|show|summari[sz]e|review|unread|new|latest|recent|important|any|anything|receiv(?:e|ed)|came in|what.s in|what is in|catch up)\b',text,re.I))


class Dialogue:
    def __init__(self,clock=time.monotonic,ttl=600):
        self.clock,self.ttl=clock,ttl
        self.topics={}

    def clear(self,channel):self.topics.pop(channel,None)

    def get(self,channel):
        value=self.topics.get(channel)
        if value and value['expires']<=self.clock():self.clear(channel);return None
        return value

    def put(self,channel,value):
        if len(self.topics)>=16 and channel not in self.topics:self.topics.pop(next(iter(self.topics)))
        self.topics[channel]={**value,'expires':self.clock()+self.ttl}

    def remember_guest(self,channel,request,today,result):
        if result.get('blocked'):self.clear(channel);return
        kind,query,start,end=parse(request,today)
        self.put(channel,{'domain':'guest','kind':kind,'query':query,'start':start.isoformat(),
                          'end':end.isoformat(),'orders':bool(result.get('show_orders'))})

    def guest_followup(self,text,channel,today):
        topic=self.get(channel)
        if not topic or topic['domain']!='guest' or not followup(text) or is_guest_lookup(text):return None
        # A new explicit operational subject must not inherit a person's identity.
        if re.search(r'\b(?:sales|revenue|labor|wings?|pizzas?|pies|fingers?|weather|forecast|monday|email inbox|staff|writeup)\b',text,re.I):return None
        period_text=text.strip().rstrip('?!.')
        retry=bool(re.fullmatch(r'(?:please )?(?:again|try again|try that again|same again)',period_text,re.I))
        contact=bool(re.search(r'(?:their|his|her) (?:phone|email)',text,re.I))
        orders=bool(re.search(r'(?:what|which) did (?:they|he|she) (?:order|buy)',text,re.I))
        try:
            remaining,period=natural_period('context '+period_text,today)
        except ValueError as error:
            return {'clarification':str(error)}
        if period:
            prefix=remaining.removeprefix('context').strip()
            generic={'please','what','which','about','how','and','same','for','the','guest','person','customer','but','did','they','he','she','order','buy'}
            if prefix and any(word.casefold() not in generic for word in re.findall(r"[\w@.'-]+",prefix)):
                return {'clarification':'Should I keep the same guest? Please give the new guest name/email/phone, or just the business date for the previous guest.'}
            start,end=(d.isoformat() for d in period)
        elif retry or contact or orders:start,end=topic['start'],topic['end']
        else:return {'clarification':'Which date or date range should I use for the same guest? You can say "what about yesterday?" or give a business date.'}
        prefix='find any orders for' if orders or (topic['orders'] and not contact) else '/guest'
        return {'request':f"{prefix} {topic['kind']} {topic['query']} | {start} | {end}"}

    def business_hint(self,text,channel):
        topic=self.get(channel)
        if topic and topic['domain']=='business' and followup(text):
            return {'tool':topic['tool'],'arguments':dict(topic['arguments']),
                    'note':'Previous successful query, not current facts. Retrieve fresh results; clarify if the new request is ambiguous.'}
        return None

    def remember_business(self,channel,name,args,result):
        if name not in BUSINESS or not isinstance(args,dict):return
        if isinstance(result,dict) and (result.get('error') or result.get('available') is False):return
        clean={}
        for key,value in args.items():
            if key not in BUSINESS[name]:continue
            if isinstance(value,str) and len(value)<=200:clean[key]=value
            elif type(value) is int and 0<=value<=100:clean[key]=value
        self.put(channel,{'domain':'business','tool':name,'arguments':clean})
