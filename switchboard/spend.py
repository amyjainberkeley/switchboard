"""A spend limit: on some plans Anthropic limits what an account may spend in a period, beside or instead of its usage
windows. Where a reading carries one with numbers, an account's row shows it as one of its lines ("Spend $412 of
$1,000", its bar, and when it resets), on the page, in the terminal, in the menu bar and in `switchboard status
--json` (limits.spend).

Where it comes from: Claude Code hands its status line a `spend_limit` beside its 5-hour and 7-day windows
(https://code.claude.com/docs/en/statusline, checked 2026-11-13: "Each window (`five_hour`, `seven_day`,
`spend_limit`) may be independently absent"). statusline.live_record keeps its numbers (used_percentage, resets_at,
used_usd, limit_usd, period), and server.live_records hands them on here. So only the account in use has one, and only
while Claude Code's record is fresh. The usage answer Switchboard reads for every account is not read for it.

In the state (an account's usage['spend']): {'pct', 'used', 'limit', 'currency', 'resets'} (an ISO time), any of them
None when not known. A spend limit never counts for switching, so it has no switch line: no mark on its bar, never red.

An account row has at most two limit lines. The weekly one is always there; the other is whichever of the 5-hour and
the spend limit has more of it used (a percent of its own limit), the 5-hour one on a tie. In the usual order: 5-hour,
weekly, spend.
"""
from datetime import datetime, timezone

from .core import parse_iso

SYMBOLS = {'USD': '$'}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf')


def from_live(raw):
    """A spend limit in the state's shape from Claude Code's rate_limits as the status line keeps them, or None when
    it holds none with a number."""
    s = (raw or {}).get('spend_limit') if isinstance(raw, dict) else None
    if not isinstance(s, dict):
        return None
    pct, used, limit, resets = (s.get(k) for k in ('used_percentage', 'used_usd', 'limit_usd', 'resets_at'))
    when = None
    if _num(resets) and 0 < resets < 1e11:
        when = datetime.fromtimestamp(resets, timezone.utc).isoformat()
    out = {'pct': min(100.0, max(0.0, float(pct))) if _num(pct) else None,
           'used': float(used) if _num(used) and used >= 0 else None,
           'limit': float(limit) if _num(limit) and limit > 0 else None, 'currency': 'USD', 'resets': when}
    return out if of({'spend': out}) else None


def of(u):
    """The spend limit of a reading's usage, with its percent worked out when only the amounts are known:
    {'pct', 'used', 'limit', 'currency', 'resets'}; None when it has no number to show."""
    s = (u or {}).get('spend') if isinstance(u, dict) else None
    if not isinstance(s, dict):
        return None
    used = s.get('used') if _num(s.get('used')) and s['used'] >= 0 else None
    limit = s.get('limit') if _num(s.get('limit')) and s['limit'] > 0 else None
    pct = s.get('pct') if _num(s.get('pct')) else None
    if pct is None and used is not None and limit is not None:
        pct = used * 100.0 / limit
    if pct is None:
        return None
    currency = s.get('currency') if isinstance(s.get('currency'), str) and s['currency'].strip() else 'USD'
    return {'pct': min(100.0, max(0.0, float(pct))), 'used': used, 'limit': limit,
            'currency': currency.strip().upper()[:8], 'resets': s.get('resets') if parse_iso(s.get('resets')) else None}


def shown(u, five_pct):
    """The spend limit when it is the row's second line beside the weekly one, else None: it has a number, and more of
    it is used than of the 5-hour limit (`five_pct`: None when the row has no 5-hour number to show)."""
    s = of(u)
    if s is None or (five_pct is not None and not _whole(s['pct']) > _whole(five_pct)):
        return None
    return s


def _whole(x):
    """A whole percent as every surface shows it: halves up."""
    return int(x + 0.5)


def money(x, currency='USD'):
    """'$412', '$9.04', '$1,000': whole amounts without cents. Another currency by its code: '412 EUR'."""
    text = '{:,.0f}'.format(x) if abs(x - round(x)) < 0.005 else '{:,.2f}'.format(x)
    sign = SYMBOLS.get(currency)
    return sign + text if sign else '%s %s' % (text, currency)


def amounts(s):
    """'$412 of $1,000' (or '$412 spent' with no limit known); '' when the amount spent is not known."""
    if not s or s.get('used') is None:
        return ''
    if s.get('limit') is None:
        return '%s spent' % money(s['used'], s['currency'])
    return '%s of %s' % (money(s['used'], s['currency']), money(s['limit'], s['currency']))
