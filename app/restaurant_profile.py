"""Owner configuration only. No restaurant, identity or menu defaults are shared."""
import json
from datetime import date
import re
from .storage import ROOT

DEFAULT = {'restaurant_name':'Your restaurant','public_location':'','public_hosts':[],
           'pieces_per_portion':{},'company_values':['Service','Quality','Teamwork','Safety'],
           'research_topics':[],'toast_start_date':'','share_guest_contacts_with_ai':False}


def profile():
    path=ROOT/'data'/'restaurant.json'
    if not path.exists():return dict(DEFAULT)
    if path.is_symlink() or path.stat().st_size>16000:raise ValueError('Invalid restaurant profile.')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or set(value)-set(DEFAULT):raise ValueError('Unknown restaurant profile field.')
    value=DEFAULT|value
    for key in ('restaurant_name','public_location'):
        if not isinstance(value[key],str) or len(value[key])>120 or any(ord(c)<32 for c in value[key]):
            raise ValueError('Invalid restaurant display text.')
    hosts=value['public_hosts']
    if not isinstance(hosts,list) or len(hosts)>30 or any(not isinstance(h,str) or not re.fullmatch(r'[a-z0-9.-]{3,200}',h) for h in hosts):
        raise ValueError('Use explicit public DNS hostnames; URL validation remains mandatory.')
    pieces=value['pieces_per_portion']
    if not isinstance(pieces,dict) or len(pieces)>200 or any(not isinstance(k,str) or len(k)>120 or type(v)!=int or not 1<=v<=1000 for k,v in pieces.items()):
        raise ValueError('Use confirmed piece counts with exact lower-case menu labels.')
    for key,limit,max_chars in [('company_values',12,60),('research_topics',3,250)]:
        rows=value[key]
        if not isinstance(rows,list) or len(rows)>limit or any(not isinstance(s,str) or not s or len(s)>max_chars or any(ord(c)<32 for c in s) for s in rows):
            raise ValueError('Invalid profile list.')
    if type(value['share_guest_contacts_with_ai']) is not bool:raise ValueError('Use true or false for contact sharing.')
    if not isinstance(value['toast_start_date'],str):raise ValueError('Use YYYY-MM-DD for the Toast start date.')
    if value['toast_start_date']:date.fromisoformat(value['toast_start_date'])
    return value
