"""Bounded encrypted owner-visible chat transcripts; never model context."""
import json
import secrets
from .storage import ROOT,utcnow
from .writeups import EncryptedStore


class ConversationArchive:
    def __init__(self,directory=None,store=None):
        self.store=store or EncryptedStore(directory or ROOT/'data'/'conversation_archive')

    def history(self,channel):
        if channel not in ('web','telegram'):return []
        raw=self.store.get(channel)
        return json.loads(raw) if raw else []

    def append(self,channel,question,reply):
        if channel not in ('web','telegram'):return
        rows=self.history(channel)
        stamp=utcnow();key=secrets.token_hex(8)
        rows.extend([{'id':'encrypted-'+key+'-user','channel':channel,'role':'user','text':question[:4000],'created':stamp},
                     {'id':'encrypted-'+key+'-assistant','channel':channel,'role':'assistant','text':reply[:30000],'created':stamp}])
        rows=rows[-40:]
        while len(json.dumps(rows).encode())>250000 and len(rows)>2:rows=rows[2:]
        self.store.put(channel,json.dumps(rows).encode())

    def clear(self,channel):
        if channel in ('web','telegram'):self.store.delete(channel)
