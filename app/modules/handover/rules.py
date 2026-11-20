from collections import defaultdict

from app import manifest
from app.clock import minutes, stamp
from app.modules.changes.rules import ACTIVE, BROKEN, CANCELED, CONFLICT, DEFERRED

STATE_LABELS = {
    ACTIVE: '本班有效',
    CANCELED: '本班撤销',
    CONFLICT: '争议待裁',
    BROKEN: '链条不全',
    DEFERRED: '仅下班事件',
}
ACTION_LABELS = {'reserve': '新订', 'move': '改单', 'cancel': '撤销'}

# Source rows that could not become typed records mean the batch itself is not
# fully usable; revision conflicts and broken chains mean this shift could not
# be reconciled.  Either condition withholds a formal sheet.  Occupancy held as
# disputed minutes (NOT_OPEN, COLLISION, PASS_OVERLAP, WINDOW_OVERLAP) is kept
# as a review task and does not by itself block issuance.
SOURCE_CODES = {'BAD_ROW', 'BAD_TIME', 'BAD_INTERVAL', 'DUP_WINDOW', 'DUP_PASS',
                'DUP_EVENT', 'BAD_SOURCE', 'EMPTY_SOURCE', 'SOURCE_LIMIT'}
RECONCILE_CODES = {'REV_CONFLICT', 'BAD_CHAIN'}


def _clock(text):
    return text[5:16].replace('T', ' ')


def _todo_id(key, kind, line, revision, code=''):
    seed = manifest.encode({'key': key, 'kind': kind, 'line': line,
                            'revision': revision, 'code': code})
    return manifest.digest(seed.encode())[:20]


def _make_todo(key, kind, line, revision, text, code=''):
    return {'id': _todo_id(key, kind, line, revision, code), 'key': key, 'kind': kind,
            'line': line, 'revision': revision, 'code': code, 'text': text}


def _record(event, cutoff, applied, conflict_lines, first_late_line):
    after = event.timestamp > cutoff
    return {
        'line': event.line, 'revision': event.revision, 'action': event.action,
        'action_label': ACTION_LABELS.get(event.action, event.action),
        'time': _clock(event.timestamp.isoformat()), 'owner': event.owner, 'rink': event.rink,
        'start': _clock(event.start.isoformat()) if event.start else '',
        'end': _clock(event.end.isoformat()) if event.end else '',
        'scope': '下班' if after else '本班',
        # Stamped before the cutoff yet delivered after a post-cutoff row:
        # still this shift's occupancy, but flagged for the handover note.
        'late_delivery': (not after and first_late_line is not None
                          and event.line > first_late_line),
        'applied': any(event is item for item in applied),
        'conflict': event.line in conflict_lines,
        'retained': False, 'code': '',
    }


def _rejected(raw_events, accepted_lines, issues_by_line, cutoff=None):
    """Keep source rows dropped by parsing or de-duplication, grouped by booking."""
    grouped = defaultdict(list)
    for line, row in enumerate(raw_events, 1):
        if line in accepted_lines or not isinstance(row, dict):
            continue
        key = row.get('booking')
        if not isinstance(key, str) or not key.strip():
            continue
        issue = issues_by_line.get(line, {})
        after = False
        if cutoff is not None and isinstance(row.get('timestamp'), str):
            try:
                after = stamp(row['timestamp']) > cutoff
            except ValueError:
                after = False
        grouped[key.strip()].append({
            'line': line, 'revision': row.get('revision') if type(row.get('revision')) is int else 0,
            'action': row.get('action', ''), 'action_label': ACTION_LABELS.get(row.get('action'), '异常'),
            'time': _clock(row['timestamp']) if isinstance(row.get('timestamp'), str)
                    and len(row['timestamp']) >= 16 else '',
            'owner': row.get('owner', '') if isinstance(row.get('owner'), str) else '',
            'rink': row.get('rink', '') if isinstance(row.get('rink'), str) else '',
            'start': '', 'end': '', 'scope': '下班' if after else '本班',
            'applied': False, 'conflict': False,
            'retained': True, 'code': issue.get('code', 'BAD_ROW'),
        })
    return grouped


def _late_todos(decision):
    if decision.status == DEFERRED:
        # The structural build_deferred task already covers these events.
        return []
    todos = []
    applied_payload = {event.revision: event.payload() for event in decision.applied}
    highest = max(applied_payload) if applied_payload else 0
    for event in sorted(decision.late, key=lambda e: (e.timestamp, e.line)):
        when = _clock(event.timestamp.isoformat())
        label = ACTION_LABELS.get(event.action, event.action)
        if event.revision in applied_payload and applied_payload[event.revision] != event.payload():
            todos.append(_make_todo(decision.key, 'late_conflict', event.line, event.revision,
                                    f'下班核对：{when} 晚到的{label}（修订 {event.revision}）与本班版本不符',
                                    'LATE_CONFLICT'))
        elif event.revision <= highest:
            todos.append(_make_todo(decision.key, 'late_stale', event.line, event.revision,
                                    f'下班核对：{when} 晚到的{label}（修订 {event.revision}）不晚于本班版本',
                                    'LATE_STALE'))
        elif decision.status == CANCELED:
            todos.append(_make_todo(decision.key, 'after_cancel', event.line, event.revision,
                                    f'下班核对：{when} 撤销后仍有{label}（修订 {event.revision}），确认是否重开',
                                    'AFTER_CANCEL'))
        else:
            todos.append(_make_todo(decision.key, 'apply_late', event.line, event.revision,
                                    f'下班应用：{when} {label}（修订 {event.revision}）'))
    return todos


def _structural_todos(decision):
    if decision.status == CONFLICT:
        revisions = '、'.join(str(e.revision) for e in decision.records
                              if e.line in decision.conflict_lines)
        return [_make_todo(decision.key, 'adjudicate', decision.conflict_lines[0], 0,
                           f'下班裁决：修订 {revisions} 同版异内容（源行 '
                           f'{"、".join(str(line) for line in decision.conflict_lines)}）',
                           'REV_CONFLICT')]
    if decision.status == BROKEN:
        lines = '、'.join(str(e.line) for e in decision.applied or decision.records)
        line = (decision.applied or decision.records)[-1].line if decision.records else 0
        return [_make_todo(decision.key, 'complete_chain', line, 0,
                           f'下班补正：修订链缺新订或改单（源行 {lines}）', 'BAD_CHAIN')]
    if decision.status == DEFERRED:
        line = decision.late[0].line if decision.late else 0
        return [_make_todo(decision.key, 'build_deferred', line, 0,
                           '下班建单：截点前无有效版本，仅见下班后事件', 'DEFERRED')]
    return []


def _dispute_todos(key, debit_rows, debit_issues):
    minutes_held = sum(row['disputed'] for row in debit_rows if row['key'] == key)
    if not minutes_held:
        return []
    related = [issue for issue in debit_issues if issue.get('key') == key]
    codes = sorted({issue['code'] for issue in related if issue.get('code')})
    line = min((issue.get('line', 0) for issue in related), default=0)
    return [_make_todo(key, 'review_dispute', line, 0,
                       f'下班复核：争议 {minutes_held} 分钟（{"、".join(codes) or "未定"}）',
                       '、'.join(codes))]


def _conclusion(decision, late_count):
    revision = decision.current.revision if decision.current else (
        decision.chain[-1].revision if decision.chain
        else (decision.records[-1].revision if decision.records else 0))
    if decision.status == ACTIVE:
        text = f'截点前有效（修订 {revision}）'
    elif decision.status == CANCELED:
        text = f'本班已撤销（修订 {revision}）'
    elif decision.status == CONFLICT:
        text = '同版异内容，待下班裁决'
    elif decision.status == BROKEN:
        text = '修订链不完整，待下班补正'
    else:
        text = '截点前无有效版本'
    if late_count:
        text += f'；另有 {late_count} 条下班事件'
    return text


def _row(decision, cutoff, debit_rows, debit_issues, rejected, first_late_line):
    booked_rows = [row for row in debit_rows if row['key'] == decision.key]
    booked = sum(row['debit'] + row['cash'] + row['waived'] + row['disputed']
                 for row in booked_rows)
    if not booked_rows and decision.current and decision.current.start:
        booked = minutes(decision.current.start, decision.current.end)
    disputed = sum(row['disputed'] for row in booked_rows)
    row_todos = (_late_todos(decision) + _structural_todos(decision)
                 + _dispute_todos(decision.key, debit_rows, debit_issues))
    primary = decision.current or (decision.chain[-1] if decision.chain
                                   else (decision.records[-1] if decision.records else None))
    records = [_record(event, cutoff, decision.applied, decision.conflict_lines,
                        first_late_line)
               for event in decision.records]
    records.extend(rejected.get(decision.key, []))
    records.sort(key=lambda item: (item['line']))
    debit_codes = sorted({code for row in booked_rows for code in row.get('codes', []) if code})
    structural = sorted({todo['code'] for todo in row_todos if todo['code']})
    return {
        'line': primary.line if primary else (records[0]['line'] if records else 0),
        'key': decision.key,
        'state': decision.status,
        'state_label': STATE_LABELS[decision.status],
        'conclusion': _conclusion(decision, len(decision.late)),
        'todo': '；'.join(todo['text'] for todo in row_todos) or '—',
        'todo_ids': [todo['id'] for todo in row_todos],
        'booked': booked,
        'disputed': disputed,
        'codes': sorted(set(debit_codes) | set(structural)),
        'revision': primary.revision if primary else 0,
        'time': _clock(primary.timestamp.isoformat()) if primary else '',
        'record': '；'.join(
            f"行{record['line']} {record['action_label']} {record['time']}"
            + ('（下班）' if record['scope'] == '下班' else '')
            + (f"（{record['code']}）" if record['retained'] else '')
            for record in records) or '—',
        'source_lines': [record['line'] for record in records],
        'records': records,
        'todos': row_todos,
    }


def _orphan_row(key, records):
    return {
        'line': records[0]['line'] if records else 0, 'key': key, 'state': 'unparsed',
        'state_label': '源行异常', 'conclusion': '本班无可用版本，源行待下班补录',
        'todo': '下班补录：源行无法解析，未计入本班账',
        'todo_ids': [], 'booked': 0, 'disputed': 0,
        'codes': sorted({record['code'] for record in records if record['code']}),
        'revision': 0, 'time': '',
        'record': '；'.join(f"行{record['line']} {record['action_label']}"
                           f"（{record['code']}）" for record in records),
        'source_lines': [record['line'] for record in records],
        'records': records, 'todos': [],
    }


def build(decisions, debit_view, balance_view, issues, cutoff, complete=True,
          raw_events=None, booking_issues=()):
    issues_by_line = {issue.get('line', 0): issue for issue in booking_issues}
    accepted = {event.line for decision in decisions for event in decision.records}
    rejected = _rejected(raw_events or [], accepted, issues_by_line, cutoff)
    debit_rows = debit_view['rows']
    debit_issues = debit_view['issues']

    rows, todos = [], []
    handled = set()
    for decision in decisions:
        late_lines = [event.line for event in decision.records if event.timestamp > cutoff]
        row = _row(decision, cutoff, debit_rows, debit_issues, rejected,
                   min(late_lines) if late_lines else None)
        handled.add(decision.key)
        todos.extend(row['todos'])
        rows.append(row)
    for key in sorted(set(rejected) - handled):
        orphan = _orphan_row(key, rejected[key])
        orphan['todos'] = [_make_todo(
            key, 'recover_row', orphan['line'], 0,
            f'下班补录：源行 {orphan["line"]} 无法解析（{"、".join(orphan["codes"]) or "BAD_ROW"}），未计入本班账',
            orphan['codes'][0] if orphan['codes'] else 'BAD_ROW')]
        orphan['todo'] = orphan['todos'][0]['text']
        orphan['todo_ids'] = [orphan['todos'][0]['id']]
        todos.extend(orphan['todos'])
        rows.append(orphan)
    rows.sort(key=lambda row: (row['key'], row['line']))

    reasons, seen = [], set()
    for issue in issues:
        code = issue.get('code')
        if code not in SOURCE_CODES and code not in RECONCILE_CODES:
            continue
        identity = (issue.get('dataset', ''), code, issue.get('line', 0), issue.get('key', ''))
        if identity in seen:
            continue
        seen.add(identity)
        lines = issue.get('lines') or [issue.get('line', 0)]
        reasons.append({'code': code, 'detail': issue['detail'], 'key': issue.get('key', ''),
                        'line': issue.get('line', 0), 'lines': list(lines),
                        'dataset': issue.get('dataset', ''),
                        'class': 'reconcile' if code in RECONCILE_CODES else 'source'})
    if not balance_view['balanced']:
        reasons.append({'code': 'UNBALANCED', 'detail': '分钟或权益未对平，复核单不予正式出具',
                        'key': '', 'line': 0, 'class': 'reconcile'})
    source_intact = complete and not any(item['class'] == 'source' for item in reasons)
    reconciled = balance_view['balanced'] and not any(item['class'] == 'reconcile' for item in reasons)
    if not complete and not any(item['class'] == 'source' for item in reasons):
        reasons.insert(0, {'code': 'INCOMPLETE_SOURCE', 'detail': '源批次不完整，复核单不予正式出具',
                           'key': '', 'line': 0, 'class': 'source'})
    if not source_intact:
        reconciled = False
    formal = source_intact and reconciled

    rank = {'apply_late': 0, 'late_conflict': 1, 'late_stale': 2, 'after_cancel': 3,
            'adjudicate': 4,
            'complete_chain': 5, 'build_deferred': 6, 'review_dispute': 7,
            'recover_row': 8}
    todos.sort(key=lambda todo: (todo['key'], rank.get(todo['kind'], 9), todo['line'],
                                 todo['revision']))
    disputes = [{'code': issue['code'], 'key': issue.get('key', ''), 'line': issue.get('line', 0),
                 'detail': issue['detail'], **{name: issue[name] for name in
                                               ('minutes', 'passes', 'windows', 'peers')
                                               if name in issue}}
                for issue in debit_issues]
    sheet = {
        'rows': rows,
        'issues': reasons,
        'todos': todos,
        'reasons': reasons,
        'disputes': disputes,
        'formal': formal,
        'complete': source_intact,
        'reconciled': reconciled,
        'status': '正式复核单' if formal else '临时复核单（待核）',
        'cutoff': cutoff.isoformat(),
        'metrics': {
            '复核单行': len(rows),
            '本班有效': sum(1 for row in rows if row['state'] == ACTIVE),
            '下班待办': len(todos),
            '争议行': sum(1 for row in rows if row['disputed']),
        },
    }
    # Digest of the sheet body only: stable for a batch, independent of the run
    # manifest, so the next shift can compare the printed copy with a re-export.
    body = {name: sheet[name] for name in ('rows', 'todos', 'reasons', 'disputes',
                                           'formal', 'complete', 'reconciled',
                                           'status', 'cutoff')}
    sheet['sheet_sha256'] = manifest.digest(manifest.encode(body).encode())
    return sheet
