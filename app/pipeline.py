from . import clock, source
from .modules.balance import service as balance
from .modules.bookings import service as bookings
from .modules.calendar import service as calendar
from .modules.changes import service as changes
from .modules.debit import service as debit
from .modules.handover import service as handover
from .modules.passes import service as passes


def process(raw):
    data = source.read(raw)
    cutoff = clock.stamp(data.get('cutoff'))
    windows, calendar_view = calendar.load(data['windows'])
    versions, pass_view = passes.load(data['passes'])
    events, booking_view = bookings.load(data['events'])
    decisions, change_view = changes.decide(events, cutoff)
    all_issues = [issue | {'dataset': name}
                  for name, view in (('calendar', calendar_view), ('passes', pass_view),
                                     ('bookings', booking_view), ('changes', change_view))
                  for issue in view['issues']]
    debit_view = debit.allocate(decisions, windows, versions)
    balance_view = balance.reconcile(
        [decision.current for decision in decisions if decision.current], debit_view, all_issues)
    handover_view = handover.review(decisions, debit_view, balance_view, all_issues, cutoff,
                                    raw_events=data['events'],
                                    booking_issues=booking_view['issues'])
    return {'calendar': calendar_view, 'passes': pass_view, 'bookings': booking_view,
            'changes': change_view, 'debit': debit_view, 'balance': balance_view,
            'handover': handover_view}
