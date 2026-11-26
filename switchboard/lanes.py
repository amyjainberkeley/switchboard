"""Keys when every account is out: the lanes (keys.py) and the gateway (gateway.py), run inside the page's own
server process by one switch.

- The lanes: a name and the key's last four characters are all that is ever shown. A key arrives once, from the
  page (a password field, cleared as it is sent) or `switchboard keys add` (asked without echo), and goes straight
  to the Keychain through `security -i` on standard input (keys.py). It is never kept here.
- The gateway listens on 127.0.0.1 only, while the switch is on (and again after a restart, until it is turned
  off). Its refusal of subscription logins is checked first for every request and has no setting: nothing here
  can turn it off.
- `switchboard claude [args...]` starts Claude Code against it for one terminal; plain `claude` is untouched.
"""
import http.client
import threading

from . import core, gateway, keys
from .core import SwitchError

KEYS = {'store': None}      # tests put a Keys with a made-up Keychain here
GW = {'gw': None}
LOCK = threading.RLock()          # re-entrant: the SIGTERM handler may stop it while this thread starts it
KW = {}                     # tests: port (0 = any free one) and the gateway's timing options
COMMAND = 'switchboard claude'


def store():
    return KEYS['store'] or keys.default()


def lanes():
    """The lanes in order, without any key: name, kind, last four. Reads only Switchboard's own list."""
    try:
        return store().list()
    except Exception as e:  # noqa: BLE001 - an unreadable list is shown as none, never as a broken page
        core.log('lanes: %r' % e)
        return []


def brief():
    """For the Accounts page: how many keys there are and whether the gateway is running."""
    return {'count': len(lanes()), 'on': GW['gw'] is not None, 'command': COMMAND}


def view():
    gw = GW['gw']
    stats = gw.stats() if gw else {}
    out = []
    for x in lanes():
        s = stats.get(x['name']) or {}
        out.append({'name': x['name'], 'kind': x.get('kind'), 'last4': x.get('last4') or '',
                    'counts': {'answered': s.get('answered', 0), 'passed_on': s.get('failed_over', 0),
                               'errors': s.get('errors_returned', 0),
                               'tokens_in': s.get('input_tokens', 0), 'tokens_out': s.get('output_tokens', 0)}
                    if gw else None})
    return {'on': gw is not None, 'port': gw.port if gw else gateway.port(), 'lanes': out, 'command': COMMAND,
            'wanted': bool(core.settings().get('gateway_on'))}


def kind_of(key):
    """An OpenRouter key says so (sk-or-); anything else is checked as an Anthropic API key."""
    return 'openrouter' if isinstance(key, str) and key.startswith('sk-or-') else 'anthropic'


def add(name, key):
    """One key, once: to the Keychain (keys.py checks it and never shows it). Returns the name and last four."""
    if not isinstance(name, str) or not isinstance(key, str):
        raise SwitchError('A key needs a name and the key.')
    name, key = name.strip(), key.strip()
    try:
        return store().add(name, key, kind=kind_of(key))
    finally:
        key = None                                    # noqa: F841 - nothing here holds it after the call


def remove(name):
    return store().remove(name)


def move(name, step):
    order = [x['name'] for x in lanes()]
    if name not in order or step not in (-1, 1):
        raise SwitchError('There is no lane named %s.' % name)
    i = order.index(name)
    j = i + step
    if 0 <= j < len(order):
        order[i], order[j] = order[j], order[i]
        store().reorder(order)
    return {'order': order}


def turn(on):
    """Start or stop the gateway inside this process; the choice is kept, so a restart brings it back."""
    with LOCK:
        if on and GW['gw'] is None:
            s = store()
            want = KW.get('port', gateway.port())
            gw = gateway.Gateway(s.list, gateway.secret(), s._secret, port=want, **KW.get('options', {}))
            try:
                gw.start()
            except OSError:
                raise SwitchError('Port %d on this Mac is taken by another program, so the gateway cannot start. '
                                  'Set another with "gateway_port" in %s.' % (want, core.data_path('settings.json')))
            GW['gw'] = gw
        elif not on and GW['gw'] is not None:
            GW['gw'].stop()
            GW['gw'] = None
        core.set_setting('gateway_on', bool(on))
    return view()


def resume():
    """At start: the gateway runs again when it was on."""
    if core.settings().get('gateway_on'):
        try:
            turn(True)
        except SwitchError as e:
            core.log('gateway: %s' % e)


def stop():
    with LOCK:
        if GW['gw'] is not None:
            GW['gw'].stop()
            GW['gw'] = None


def answers(port=None, timeout=2):
    """Does Switchboard's gateway answer on this Mac's port (its connection probe, which needs no credential)?"""
    return hello(port, timeout) == 'ours'


def hello(port=None, timeout=2):
    """Who answers the connection probe on the gateway's port: 'ours' when the answer carries the Server header only
    Switchboard's gateway sends, 'other' when another program answers it, None when nothing does. Another program
    holding the port must never get the gateway's secret or a prompt."""
    c = http.client.HTTPConnection('127.0.0.1', port or gateway.port(), timeout=timeout)
    try:
        c.request('HEAD', '/api/hello')
        r = c.getresponse()
        if r.status != 200:
            return None
        return 'ours' if (r.getheader('Server') or '').strip() == gateway._Handler.server_version else 'other'
    except OSError:
        return None
    finally:
        c.close()
