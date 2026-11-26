"""Two accounts with one email: one person can be in two organizations under one email, and Switchboard keeps each.

Every surface names an account by its whole email. When two accounts share an email, each is named by its email and
then its organization in brackets: "maya@northwind.example (Northwind)". The organization is said as under the email
on the page ('Personal' for an account's own organization); when it is not known, or two have the same name, the
account's number stands in ('#3'). When every email differs, nothing changes anywhere: an account's name is its
email.

The page's state keeps each account under that name (server.apply_rows), so the page, the terminal, the menu bar,
notifications and History's lines all say it with no change of their own. What a script reads (`switchboard status
--json`, `switchboard log --json`) keeps the email in its email fields, and the organization in its own.

A command given an email that fits two accounts says so, and how to name one (`ambiguous`).
"""
import re

PERSONAL_ORG_RE = re.compile(r"['’]s organi[sz]ation$", re.I)
LABEL_RE = re.compile(r'^(?P<email>[^\s()]+@[^\s()]+) \((?P<org>[^()]+)\)$')


def org_words(org):
    """An organization's words: 'Personal' for an account's own ("you@mail.example's Organization"), else its name,
    with any bracket taken out; '' when not known."""
    name = ' '.join(str(org or '').replace('(', ' ').replace(')', ' ').split())
    if not name:
        return ''
    return 'Personal' if PERSONAL_ORG_RE.search(name) else name


def label(email, org):
    return '%s (%s)' % (email, org)


def keys(rows):
    """Each row's name, in order: its email (lower case), or, when another row has the same email, its email and
    organization ("maya@northwind.example (Northwind)"). Rows are the engine's listing rows: email, org_name, number.
    Two names are never the same."""
    emails = [str(r.get('email') or '').strip().lower() for r in rows]
    seen = {}
    for e in emails:
        seen[e] = seen.get(e, 0) + 1
    out = []
    for r, e in zip(rows, emails):
        if not e or seen[e] < 2:
            out.append(e)
            continue
        words = org_words(r.get('org_name'))
        same = [x for x, y in zip(rows, emails) if y == e and org_words(x.get('org_name')) == words]
        if not words or len(same) > 1:
            n = r.get('number')
            words = ('%s #%s' % (words, n) if words else '#%s' % n) if n else (words or 'not kept')
        out.append(label(e, words))
    return out


def email_of(name):
    """The email in an account's name (the name itself when it is an email)."""
    m = LABEL_RE.match(name or '') if isinstance(name, str) else None
    return m.group('email') if m else name


def org_of(name):
    """The organization in an account's name, or '' when the name is its email alone."""
    m = LABEL_RE.match(name or '') if isinstance(name, str) else None
    return m.group('org') if m else ''


def fitting(email, accounts):
    """The accounts (the page's state's, each with 'email' as its name) an email fits when it is not one name: those
    whose name is that email with an organization."""
    want = (email or '').strip().lower()
    return [a for a in accounts or [] if is_label(a.get('email')) and email_of(a['email'].lower()) == want]


def is_label(name):
    return isinstance(name, str) and LABEL_RE.match(name) is not None


def key_of(accounts, typed):
    """The state's key for what a page or menu sent: an email in lower case as always, or a name with its organization
    matched whatever its case."""
    t = (typed or '').strip()
    if ' (' not in t:
        return t.lower()
    low = t.lower()
    return next((k for k in accounts or {} if isinstance(k, str) and k.lower() == low), low)


def in_doc(doc, account_id):
    """An account's name from the engine's own store ({'accounts': {id: {email, org_name}}, 'order': [ids]}): its
    email, or its email and organization when another kept account has the same email."""
    accounts = (doc or {}).get('accounts') or {}
    a = accounts.get(account_id) or {}
    order = [x for x in (doc or {}).get('order') or [] if x in accounts] or list(accounts)
    rows = [dict(accounts[x], number=i + 1) for i, x in enumerate(order)]
    names = keys(rows)
    return next((n for x, n in zip(order, names) if x == account_id), (a.get('email') or account_id))


def of_account(account, accounts):
    """An engine account's name (api.Account; `accounts` is every account the engine keeps, in the person's order),
    as the page's state keeps it: its email, or its email and organization when another kept account has the same
    email."""
    return all_of(accounts).get(account.id) or account.email or account.id


def all_of(accounts):
    """{account id: its name} for every engine account (of_account), in the person's order."""
    rows = [{'email': x.email, 'org_name': x.org_name, 'number': i + 1} for i, x in enumerate(accounts or [])]
    return dict((x.id, n or x.email or x.id) for x, n in zip(accounts or [], keys(rows)))


def _and(items):
    return items[0] if len(items) == 1 else '%s and %s' % (', '.join(items[:-1]), items[-1])


NUMBER_WORDS = {2: 'two', 3: 'three', 4: 'four'}


def ambiguous(email, fits):
    """What a command given an email that fits more than one account says. `fits` is [(number, organization words)].
    A short name stays with its account, and a number changes when the list changes, so the short name comes first:
    "maya@northwind.example fits two accounts: 1 (Northwind) and 3 (Personal). Give one a short name and use that:
    switchboard alias 3 SHORT. Its number works for now; it changes when the list changes."
    """
    said = ['%s (%s)' % (n, org) if org else str(n) for n, org in fits]
    count = NUMBER_WORDS.get(len(fits), str(len(fits)))
    last = fits[-1][0] if fits else 'N'
    return ('%s fits %s accounts: %s. Give one a short name and use that: switchboard alias %s SHORT. Its number works '
            'for now; it changes when the list changes.' % (email, count, _and(said), last))
