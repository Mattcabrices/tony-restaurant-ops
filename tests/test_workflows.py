from datetime import datetime,timedelta,timezone
import pytest
from tony_demo.domain.storage import DB
from tony_demo.domain.exceptions import ExceptionLedger
from tony_demo.integrations.contracts import pending,Availability


def test_exception_persists_across_ledger_instances_and_deduplicates(tmp_path):
    db=DB(tmp_path/'demo.sqlite3');ledger=ExceptionLedger(db)
    one=ledger.observe('demo:source','Example source stale','Synthetic source','warning',{'synthetic':True})
    two=ledger.observe('demo:source','Example source stale','Synthetic source','warning',{'synthetic':True})
    assert one['id']==two['id'] and two['occurrences']==2
    assert len(two['events'])==1
    reopened=ExceptionLedger(DB(db.path))
    assert reopened.read(one['id'])['occurrences']==2


def test_review_history_concurrency_and_false_positive_suppression(tmp_path):
    ledger=ExceptionLedger(DB(tmp_path/'demo.sqlite3'))
    item=ledger.observe('demo:quality','Example unknown item','Synthetic','warning',{'synthetic':True})
    ack=ledger.transition(item['id'],'acknowledged','Checking fixture','Demo manager',expected_revision=item['revision'])
    assert ack['assigned_to']=='Demo manager'
    with pytest.raises(ValueError,match='changed'):
        ledger.transition(item['id'],'resolved','Old form',expected_revision=item['revision'])
    ledger.transition(item['id'],'false_positive','Expected demonstration condition')
    repeated=ledger.observe('demo:quality','Example unknown item','Synthetic','warning',{'synthetic':True,'revision':2})
    assert repeated['status']=='false_positive' and not ledger.listing()['items']


def test_resolved_exception_reopens_on_recurrence_and_due_is_local(tmp_path):
    ledger=ExceptionLedger(DB(tmp_path/'demo.sqlite3'))
    due=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    item=ledger.observe('demo:oven','Example equipment check','Synthetic','warning',{},due)
    assert item['escalation_due']
    ledger.transition(item['id'],'resolved','Fixture resolved')
    assert ledger.observe('demo:oven','Example equipment check','Synthetic','warning',{})['status']=='open'


def test_unconnected_provider_returns_unknown_not_zero():
    record=pending('Restaurant365','food_cost','USD','2026-04-01','2026-04-30')
    assert record.value is None and record.availability==Availability.NOT_CONNECTED
