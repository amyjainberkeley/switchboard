"""The one place tests get the page key (switchboard/pagekey.py) for a request to the page's server.

Every POST to the page, and every GET under /api/ but /api/state, must carry the key. Tests that talk to the real
handler over HTTP add it here, the way Switchboard's own programs send it (the X-Switchboard-Key header), never by
loosening the server. Import it after tests/_isolate.py.
"""
import re


def with_key(headers=None):
    """`headers` (a dict, or none) with the page key added."""
    from switchboard import pagekey
    out = dict(headers or {})
    out[pagekey.HEADER] = pagekey.key()
    return out


def ticket_url(base, path=''):
    """The address a person's browser opens: the page's door with a fresh one-use ticket, then `path` (a query, a
    #part, or both) kept after the page sends the browser on to /. `base` ends in '/'."""
    from switchboard import pagekey
    query, _, part = path.partition('#')
    query = query.lstrip('?')
    return base + '?t=' + pagekey.ticket() + ('&' + query if query else '') + ('#' + part if part else '')


def ticket_in(path):
    """The ticket written in a file that opens the page (pagekey.opener)."""
    with open(path, encoding='utf-8') as f:
        return re.search(r'\?t=([0-9]+\.[A-Za-z0-9_-]+\.[0-9a-f]+)"', f.read()).group(1)
