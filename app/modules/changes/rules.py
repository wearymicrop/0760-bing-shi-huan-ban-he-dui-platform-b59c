from collections import defaultdict
from dataclasses import dataclass, field

from app.reasons import evidence

ACTIVE = 'active'
CANCELED = 'canceled'
CONFLICT = 'conflict'
BROKEN = 'broken'
DEFERRED = 'deferred'


@dataclass
class Decision:
    """One booking's fate at the shift cutoff, with every source record kept."""

    key: str
    status: str
    current: object | None = None
    chain: list = field(default_factory=list)
    applied: list = field(default_factory=list)
    late: list = field(default_factory=list)
    records: list = field(default_factory=list)
    conflict_lines: list = field(default_factory=list)


def classify(events, cutoff):
    """Decide every booking by occurrence time, never by feed order.

    A change stamped at or before the cutoff belongs to this shift even when
    its row only surfaced later in the feed; a cancellation stamped after the
    cutoff is carried for the next shift even when it already arrived.
    """
    revisions = defaultdict(lambda: defaultdict(list))
    late = defaultdict(list)
    issues = []
    for event in events:
        if event.timestamp > cutoff:
            issues.append(evidence('AFTER_CLOSE', event.line, event.key, raw=event.export()))
            late[event.key].append(event)
        else:
            revisions[event.key][event.revision].append(event)

    decisions = []
    for key in sorted(set(revisions) | set(late)):
        groups = revisions.get(key, {})
        chain, applied, conflict, key_issues = [], [], [], []
        for _, siblings in sorted(groups.items()):
            if len({e.payload() for e in siblings}) > 1:
                conflict.extend(e.line for e in siblings)
                continue
            ordered = sorted(siblings, key=lambda e: (e.timestamp, e.line))
            chain.append(ordered[0])
            applied.append(ordered[0])
            key_issues.extend(evidence('DUP_EVENT', e.line, key, raw=e.export()) for e in ordered[1:])
        issues.extend(key_issues)
        records = sorted([e for siblings in groups.values() for e in siblings] + late.get(key, []),
                         key=lambda e: (e.revision, e.timestamp, e.line))
        if conflict:
            issues.append(evidence('REV_CONFLICT', conflict[0], key, lines=conflict))
            decisions.append(Decision(key, CONFLICT, records=records, late=late.get(key, []),
                                      conflict_lines=conflict))
            continue
        if not chain:
            decisions.append(Decision(key, DEFERRED, records=records, late=late.get(key, [])))
            continue
        if (chain[0].action != 'reserve' or chain[0].revision != 1
                or any(e.action != 'move' for e in chain[1:-1])
                or (len(chain) > 1 and chain[-1].action == 'reserve')
                or any(b.revision != a.revision + 1 or b.timestamp < a.timestamp
                       for a, b in zip(chain, chain[1:]))
                or len({e.owner for e in chain if e.action != 'cancel'}) > 1):
            issues.append(evidence('BAD_CHAIN', chain[-1].line, key, lines=[e.line for e in chain]))
            decisions.append(Decision(key, BROKEN, records=records, late=late.get(key, [])))
            continue
        issues.extend(evidence('STALE_EVENT', e.line, key, revision=e.revision) for e in chain[:-1])
        latest = chain[-1]
        status = CANCELED if latest.action == 'cancel' else ACTIVE
        decisions.append(Decision(key, status, None if status == CANCELED else latest, chain=chain,
                                  applied=applied, late=late.get(key, []), records=records))
    return decisions, issues


def resolve(events, cutoff):
    decisions, issues = classify(events, cutoff)
    valid, canceled = [], []
    for decision in decisions:
        if decision.status == ACTIVE:
            valid.append(decision.current)
        elif decision.status == CANCELED:
            canceled.append(evidence('CANCELED', decision.chain[-1].line, decision.key))
    return valid, canceled, issues
