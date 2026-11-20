from .rules import build


def review(decisions, debit_view, balance_view, issues, cutoff, complete=True,
           raw_events=None, booking_issues=()):
    return build(decisions, debit_view, balance_view, issues, cutoff, complete,
                 raw_events, booking_issues)
