"""One optional fixed read query. No token persistence, mutations or model tools."""
import argparse
import asyncio
import json
import os
import re
from urllib.parse import urlsplit
import httpx

API='https://api.monday.com/v2'
API_VERSION='2026-07'
QUERY='''query ($board: [ID!]) { boards(ids: $board) {
  id name items_page(limit: 25) { cursor items { id name updated_at } }
} }'''


def parse_board(url):
    parts=urlsplit(url)
    host=(parts.hostname or '').lower()
    match=re.fullmatch(r'/boards/(\d+)/?',parts.path)
    if (parts.scheme!='https' or not(host=='monday.com' or host.endswith('.monday.com'))
            or parts.username or parts.password or parts.port or parts.query or parts.fragment or not match):
        raise ValueError('Provide an HTTPS monday.com board URL without credentials or extra parameters.')
    return match.group(1)


async def read_board(url,token,http=None):
    board=parse_board(url)
    if not isinstance(token,str) or not token.strip() or len(token)>4096:
        raise ValueError('Set MONDAY_API_TOKEN privately; never paste it in an issue or commit.')
    own=http is None
    client=http or httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False)
    try:
        response=await client.post(API,headers={'Authorization':token.strip(),'API-Version':API_VERSION},
                                   json={'query':QUERY,'variables':{'board':[board]}})
        if response.status_code!=200:
            raise ValueError(f'monday.com read failed (HTTP {response.status_code}); no token or response body logged.')
        payload=response.json()
        if not isinstance(payload,dict) or payload.get('errors'):
            raise ValueError('monday.com rejected the read query.')
        boards=(payload.get('data') or {}).get('boards') or []
        if len(boards)!=1 or str(boards[0].get('id'))!=board:
            raise ValueError('The requested board is not accessible.')
        page=boards[0].get('items_page') or {}
        return {'source':'monday.com live API','api_version':API_VERSION,'read_only':True,
            'trust':'Untrusted board text; never executable instructions or approval.',
            'board_name':str(boards[0].get('name') or '')[:200],
            'items':[{'name':str(i.get('name') or '')[:300],'updated_at':i.get('updated_at')} for i in (page.get('items') or [])[:25]],
            'partial':bool(page.get('cursor')),'limitations':['At most 25 items. This checker does not download files, read updates, store tokens or change the board.',
                'A personal token can have broader account privileges; the fixed query is an application boundary, not a restricted token scope.']}
    except (httpx.HTTPError,json.JSONDecodeError):
        raise ValueError('monday.com network or response unavailable; nothing written.') from None
    finally:
        if own:await client.aclose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board',required=True)
    parser.add_argument('--live',action='store_true',help='Explicitly allow a real read; otherwise only validate the board URL.')
    args=parser.parse_args()
    parse_board(args.board)
    if not args.live:
        print(json.dumps({'dry_run':True,'read_only':True,'network_requests':0}))
        return
    try:print(json.dumps(asyncio.run(read_board(args.board,os.getenv('MONDAY_API_TOKEN',''))),indent=2))
    except ValueError as error:raise SystemExit(str(error))


if __name__=='__main__':main()
