from app.reasons import evidence
from .rules import ACTIVE, CANCELED, DEFERRED, classify


def decide(events, cutoff):
    """Replay every booking to the cutoff and expose the full decision set."""
    decisions, issues = classify(events, cutoff)
    valid = [d.current for d in decisions if d.status == ACTIVE]
    canceled = [evidence('CANCELED', d.chain[-1].line, d.key) for d in decisions if d.status == CANCELED]
    deferred = [d.key for d in decisions if d.status == DEFERRED]
    view = {
        'rows': [e.export() for e in valid],
        'issues': issues + canceled,
        'metrics': {
            '有效预订': len(valid),
            '撤销': len(canceled),
            '变更诊断': len(issues),
            '下班待办': sum(len(d.late) for d in decisions),
        },
        'cutoff': cutoff.isoformat(),
        'deferred': deferred,
    }
    return decisions, view


def replay(events, cutoff):
    """Compatibility wrapper returning (active bookings, shift view)."""
    return decide(events, cutoff)
