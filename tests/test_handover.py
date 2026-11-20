import json

from app import clock, source
from app.modules.balance import service as balance
from app.modules.bookings import service as bookings
from app.modules.calendar import service as calendar
from app.modules.changes import service as changes
from app.modules.debit import service as debit
from app.modules.handover import service as handover
from app.modules.passes import service as passes
from app.source import sample

CUTOFF = '2026-11-20T22:00:00+08:00'


def run(document):
    """Process an in-memory document the same way the pipeline does."""
    data = source.read(json.dumps(document).encode())
    cutoff = clock.stamp(data['cutoff'])
    windows, calendar_view = calendar.load(data['windows'])
    versions, pass_view = passes.load(data['passes'])
    events, booking_view = bookings.load(data['events'])
    decisions, change_view = changes.decide(events, cutoff)
    all_issues = [issue | {'dataset': name}
                  for name, view in (('calendar', calendar_view), ('passes', pass_view),
                                     ('bookings', booking_view), ('changes', change_view))
                  for issue in view['issues']]
    debit_view = debit.allocate(decisions, windows, versions)
    balance_view = balance.reconcile([d.current for d in decisions if d.current], debit_view, all_issues)
    sheet = handover.review(decisions, debit_view, balance_view, all_issues, cutoff,
                            raw_events=data['events'], booking_issues=booking_view['issues'])
    return sheet, debit_view


def base(events):
    return {
        'cutoff': CUTOFF,
        'windows': [{'rink': '北冰', 'start': '2026-11-20T19:00:00+08:00',
                     'end': '2026-11-21T04:00:00+08:00', 'kind': 'paid'}],
        'passes': [{'pass': '00041', 'owner': '纸鸢', 'rink': '北冰',
                    'start': '2026-11-20T19:00:00+08:00', 'end': '2026-11-21T00:00:00+08:00',
                    'allowance': 60}],
        'events': events,
    }


RESERVE = {'booking': '0042', 'revision': 1, 'timestamp': '2026-11-20T18:00:00+08:00',
           'action': 'reserve', 'owner': '纸鸢', 'rink': '北冰',
           'start': '2026-11-20T22:30:00+08:00', 'end': '2026-11-20T23:20:00+08:00'}
# 21:55 change whose row only appears in the 22:20 feed.
CHANGE_2155 = {'booking': '0042', 'revision': 2, 'timestamp': '2026-11-20T21:55:00+08:00',
               'action': 'move', 'owner': '纸鸢', 'rink': '北冰',
               'start': '2026-11-20T22:40:00+08:00', 'end': '2026-11-20T23:10:00+08:00'}
# 23:02 cancellation that was already delivered before handover.
CANCEL_2302 = {'booking': '0042', 'revision': 3, 'timestamp': '2026-11-20T23:02:00+08:00',
               'action': 'cancel'}


def booking(sheet, key):
    return next(row for row in sheet['rows'] if row['key'] == key)


def test_change_stamped_before_cutoff_counts_even_when_row_arrives_late():
    # The 21:55 move is delivered after the cutoff row but was effective before it.
    sheet, debit_view = run(base([RESERVE, CANCEL_2302, CHANGE_2155]))
    row = booking(sheet, '0042')
    assert row['state'] == 'active' and row['revision'] == 2
    assert row['booked'] == 30
    assert next(r for r in row['records'] if r['line'] == 3)['late_delivery'] is True
    assert next(r for r in debit_view['rows'] if r['key'] == '0042')['cash'] == 0


def test_cancellation_after_cutoff_does_not_rewrite_this_shift():
    sheet, debit_view = run(base([RESERVE, CHANGE_2155, CANCEL_2302]))
    row = booking(sheet, '0042')
    assert row['state'] == 'active' and row['revision'] == 2 and row['booked'] == 30
    assert [(todo['kind'], todo['revision']) for todo in row['todos']] == [('apply_late', 3)]
    assert any(item['key'] == '0042' for item in debit_view['rows'])


def test_handover_sheet_keeps_original_records_and_next_shift_todo():
    sheet, _ = run(base([RESERVE, CHANGE_2155, CANCEL_2302]))
    row = booking(sheet, '0042')
    assert row['source_lines'] == [1, 2, 3]
    assert '行3 撤销 11-20 23:02（下班）' in row['record']


def test_rerunning_same_batch_does_not_add_todos():
    document = base([RESERVE, CHANGE_2155, CANCEL_2302])
    first, _ = run(document)
    second, _ = run(document)
    assert first['todos'] == second['todos']
    assert [todo['id'] for todo in first['todos']] == [todo['id'] for todo in second['todos']]
    assert first['sheet_sha256'] == second['sheet_sha256']


def test_changed_batch_issues_fresh_todos():
    first, _ = run(base([RESERVE, CANCEL_2302]))
    second, _ = run(base([RESERVE, CHANGE_2155, CANCEL_2302]))
    assert not set(todo['id'] for todo in first['todos']) & set(todo['id'] for todo in second['todos'])


def test_late_change_conflicting_with_this_shift_version_is_a_todo():
    # The 21:55 move is applied; a different revision-2 payload is stamped
    # 23:10 but arrives with the same batch.
    clash = {'booking': '0042', 'revision': 2, 'timestamp': '2026-11-20T23:10:00+08:00',
             'action': 'move', 'owner': '纸鸢', 'rink': '北冰',
             'start': '2026-11-20T22:50:00+08:00', 'end': '2026-11-20T23:20:00+08:00'}
    sheet, _ = run(base([RESERVE, CHANGE_2155, clash]))
    row = booking(sheet, '0042')
    assert row['state'] == 'active' and row['revision'] == 2
    assert [todo['kind'] for todo in row['todos']] == ['late_conflict']


def test_late_cancellation_on_canceled_booking_is_stale_note():
    cancel = {'booking': '0042', 'revision': 2, 'timestamp': '2026-11-20T18:20:00+08:00',
              'action': 'cancel'}
    late = {'booking': '0042', 'revision': 3, 'timestamp': '2026-11-20T23:20:00+08:00',
            'action': 'move', 'owner': '纸鸢', 'rink': '北冰',
            'start': '2026-11-20T22:30:00+08:00', 'end': '2026-11-20T22:40:00+08:00'}
    sheet, _ = run(base([RESERVE, cancel, late]))
    row = booking(sheet, '0042')
    assert row['state'] == 'canceled'
    assert [todo['kind'] for todo in row['todos']] == ['after_cancel']


def test_conflict_is_retained_and_blocks_formal_sheet():
    first = {'booking': '0051', 'revision': 1, 'timestamp': '2026-11-20T18:00:00+08:00',
             'action': 'reserve', 'owner': '海棠', 'rink': '北冰',
             'start': '2026-11-20T20:00:00+08:00', 'end': '2026-11-20T20:20:00+08:00'}
    second = first | {'timestamp': '2026-11-20T18:02:00+08:00',
                      'end': '2026-11-20T20:23:00+08:00'}
    sheet, _ = run(base([RESERVE, first, second]))
    row = booking(sheet, '0051')
    assert row['state'] == 'conflict' and row['source_lines'] == [2, 3]
    assert [record['conflict'] for record in row['records']] == [True, True]
    assert not sheet['formal'] and any(r['code'] == 'REV_CONFLICT' for r in sheet['reasons'])


def test_incomplete_source_withholds_formal_sheet():
    sheet, _ = run(base([RESERVE]))
    assert sheet['formal'] and sheet['complete']
    broken = base([RESERVE]) | {'events': [RESERVE, {'booking': ''}]}
    sheet, _ = run(broken)
    assert not sheet['formal'] and not sheet['complete']
    assert any(reason['class'] == 'source' for reason in sheet['reasons'])


def test_clean_sample_issues_formal_sheet():
    from app.pipeline import process
    sheet = process(sample('clean'))['handover']
    assert sheet['formal'] and sheet['status'] == '正式复核单' and sheet['todos'] == []


def test_night_sample_keeps_disputes_and_withholds_formal_sheet(ledger):
    sheet = ledger['handover']
    assert not sheet['formal']
    assert any(row['key'] == '0045' and row['disputed'] == 20 for row in sheet['rows'])
    assert any(row['key'] == '0051' and row['state'] == 'conflict' for row in sheet['rows'])
    assert any(todo['kind'] == 'review_dispute' and todo['code'] == 'COLLISION'
               for todo in sheet['todos'])


def test_export_matches_page_and_is_repeatable(client):
    record = client.post('/api/runs', json={'sample': 'night'}).json
    page = client.get(f"/desk/handover?run={record['id']}")
    assert page.status_code == 200
    first = client.get(f"/api/runs/{record['id']}/handover")
    second = client.get(f"/api/runs/{record['id']}/handover")
    assert first.status_code == 200 and 'attachment' in first.headers['Content-Disposition']
    assert first.data == second.data
    document = json.loads(first.data)
    assert document['handover'] == record['views']['handover']
    assert document['handover']['sheet_sha256'] in page.data.decode()


def test_module_api_exposes_handover_rows(client):
    record = client.post('/api/runs', json={'sample': 'night'}).json
    stage = client.get(f"/api/modules/handover/{record['id']}").json
    row = next(item for item in stage['rows'] if item['key'] == '0042')
    assert row['state'] == 'active' and row['todo'].startswith('下班应用')
