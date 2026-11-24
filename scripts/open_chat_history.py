"""Optional: move earlier encrypted chat archives into the readable local messages table.

Also prints recent questions with the tools used: python -m scripts.open_chat_history --traces 20
Local data only. Output can contain guest and employee information; never publish it.
"""
import argparse
import json
import sys

from app.conversation_archive import ConversationArchive
from app.storage import DB, ROOT


def migrate(db, archive):
    moved = 0
    for channel in ('web', 'telegram'):
        for row in archive.history(channel):
            if not db.rows('SELECT 1 FROM messages WHERE channel=? AND role=? AND text=? AND created=?', (channel, row['role'], row['text'], row['created'])):
                db.execute('INSERT INTO messages(channel,role,text,created) VALUES (?,?,?,?)', (channel, row['role'], row['text'], row['created']))
                moved += 1
        # Clear only after every row is confirmed readable.
        if all(db.rows('SELECT 1 FROM messages WHERE channel=? AND text=? AND created=?', (channel, r['text'], r['created'])) for r in archive.history(channel)):
            archive.clear(channel)
    return moved


def traces(db, count):
    for row in reversed(db.rows('SELECT * FROM agent_traces ORDER BY id DESC LIMIT ?', (count,))):
        print(f"\n{row['created'][:19]} {row['channel']} {row['seconds']}s error={row['error']}\nQ: {row['question']}")
        for step in json.loads(row['steps']):
            print(f"  {step['tool']} {json.dumps(step['args'])} {'ok' if step['ok'] else 'FAILED: ' + str(step['error'])} {step['ms']}ms")
        print('A: ' + row['answer'])


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--traces', type=int)
    options = parser.parse_args()
    database = DB(ROOT / 'data')
    if options.traces:
        traces(database, options.traces)
    else:
        print('Messages opened:', migrate(database, ConversationArchive(ROOT / 'data' / 'conversation_archive')))
