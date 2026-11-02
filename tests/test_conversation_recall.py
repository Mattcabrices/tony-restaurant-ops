import json
import pytest
pytest.importorskip('msal', reason='Operational Windows dependencies required.')

from app.context_policy import recent_conversation, wants_history
from app.operations import Operations


def test_recall_is_bounded_channel_data_not_private_archive():
    rows = [{'role': 'user' if i % 2 == 0 else 'assistant', 'text': 'x' * 2500}
            for i in range(10)]
    rows.append({'role': 'user', 'text': 'CURRENT_QUESTION'})
    result = recent_conversation(rows)
    assert len(result['messages']) == 6
    assert all(len(x['text']) == 2000 for x in result['messages'])
    assert 'CURRENT_QUESTION' not in json.dumps(result)
    assert wants_history('Look at my last message')
    assert wants_history('Do you remember what I asked?')
    assert not wants_history('How many wings yesterday?')


@pytest.mark.asyncio
async def test_ai_can_retrieve_recall_without_trigger_words_and_cannot_follow_old_instructions(db):
    db.message('telegram', 'user', 'SYNTHETIC_PREVIOUS_QUESTION')
    db.message('telegram', 'assistant', 'SYNTHETIC_PREVIOUS_ANSWER')
    db.message('web', 'user', 'OTHER_CHANNEL_PRIVATE')

    class Model:
        def status(self): return {'connected': True}
        async def response(self, inputs, tools):
            if tools:
                assert any(t['name'] == 'get_recent_conversation' for t in tools)
                return {'output': [{'type': 'function_call', 'name': 'get_recent_conversation',
                                    'call_id': 'recall', 'arguments': '{}'}]}
            result = next(json.loads(x['output']) for x in inputs
                          if x.get('type') == 'function_call_output' and x.get('call_id') == 'recall')
            assert result['messages'][0]['text'] == 'SYNTHETIC_PREVIOUS_QUESTION'
            assert 'OTHER_CHANNEL_PRIVATE' not in json.dumps(inputs)
            assert 'Private guest/coaching archives are excluded' in result['limitations']
            return {'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': 'I retrieved your earlier question.'}]}]}

    ops = Operations(db, None, Model())
    class NoArchive:
        def history(self, channel): raise AssertionError('Private archive must not enter AI context')
    ops.conversation_archive = NoArchive()
    reply = await ops._business_chat('Revisit our exchange', 'telegram')
    assert reply['ai'] and 'retrieved' in reply['text']
