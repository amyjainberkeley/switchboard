"""The link app: Switchboard Links.app standing in as the Mac's default browser.

macOS hands every clicked link to the default browser. Switchboard Links takes
that seat, looks at each link, and passes it on: a claude.ai artifact goes to
the profile of the account that owns it, and everything else goes to the browser
that was the default before, exactly as it did yesterday.

Only a person can make it the default. macOS asks, and this code can't answer.
Opening links as their owner is always on: the first run at a terminal asks
macOS once, the page's banner offers it until it is the default, and only
`switchboard links off` (or uninstalling) gives the seat back.

The installer builds it, as ~/Applications/Switchboard Links.app, when Apple's
command line tools are there; `switchboard update --link-app` adds it later. It
is the only app this file knows. Before 0.2.0 it was ~/Applications/
Switchboard.app. That copy is known by its bundle identifier, which has never
changed (macOS keeps the default browser by it), and it counts as the link app
until the installer, once it has built the new one, moves it.

Who may post a link to the page (the one header contract, POSTERS below): the link app, the page itself (go.html),
Switchboard's command line, and the old Link Router, which may only open links. Everyone finds the page the
same way (page_port: $SWITCHBOARD_PORT, then the `port` file the page writes when it starts, then settings.json's
"port", then 7799), the same order the link app's Swift code uses, and a test holds them to it.
"""
import json
import os
import plistlib
import re
import subprocess
import time
import urllib.error
import urllib.request

from . import browser, pagekey
from .core import SwitchError, data_path, home, port, read_json, read_text, run, write_text

CACHE = {'at': 0, 'value': None}
BUNDLE_ID = 'io.github.amyjainberkeley.switchboard.links'
PAGE = {'up': False}        # set in the page's own process when it starts (announce_port): its background link work

# The header contract for POST /api/go (the server's Handler._from_here keeps it): every poster sends
# Content-Type: application/json and one of these headers. Only the old Link Router's may do nothing but open links.
POSTERS = {
    'link app': {'X-Switchboard': '1'},          # router/SwitchboardLinks.swift: {url, wait: false, sent_at, ...}
    'page': {'X-Switchboard': '1'},              # web/go.html and web/index.html
    'command line': {'X-Switchboard': '1'},      # `switchboard open`, artifacts.open_link
    'old Link Router': {'X-Claude-Switch': '1'},  # Link Router.app: /api/go only
}

# Apps known to take clicked links, by bundle identifier: the browsers (where a link goes plain). Any other app in the
# seat is a link handler, whose links open as their owner only when it sends them to the page (server.passes_links).
BROWSER_IDS = {
    'com.google.Chrome': 'Google Chrome', 'com.google.Chrome.beta': 'Google Chrome Beta',
    'com.google.Chrome.canary': 'Google Chrome Canary', 'com.apple.Safari': 'Safari',
    'com.apple.SafariTechnologyPreview': 'Safari Technology Preview', 'org.mozilla.firefox': 'Firefox',
    'com.brave.Browser': 'Brave', 'com.microsoft.edgemac': 'Microsoft Edge', 'company.thebrowser.Browser': 'Arc',
    'com.operasoftware.Opera': 'Opera', 'com.vivaldi.Vivaldi': 'Vivaldi', 'com.kagi.kagimacOS': 'Orion',
    'org.chromium.Chromium': 'Chromium', 'ai.perplexity.comet': 'Comet', 'app.zen-browser.zen': 'Zen',
}


def bundle_info(app):
    """An app's Info.plist ({} when there is none, or it can't be read)."""
    try:
        with open(os.path.join(app, 'Contents', 'Info.plist'), 'rb') as f:
            info = plistlib.load(f)
    except Exception:
        return {}
    return info if isinstance(info, dict) else {}


def kind(app):
    """What is at this path: 'links' (the link app, by its bundle identifier), 'other' (some other app), or None
    (nothing)."""
    if not app or not os.path.exists(app):
        return None
    if bundle_info(app).get('CFBundleIdentifier') == BUNDLE_ID:
        return 'links'
    return 'other'


def older_path():
    """~/Applications/Switchboard.app: where the link app was before 0.2.0."""
    return os.path.join(home(), 'Applications', 'Switchboard.app')


def app_path():
    """~/Applications/Switchboard Links.app, or an older install's link app at ~/Applications/Switchboard.app
    while that is still there and the new one is not."""
    path = os.path.join(home(), 'Applications', 'Switchboard Links.app')
    if not os.path.exists(path) and kind(older_path()) == 'links':
        return older_path()
    return path


def bin_path():
    """The link app's program, as its Info.plist names it (SwitchboardLinks; Switchboard before 0.2.0)."""
    app = app_path()
    exe = bundle_info(app).get('CFBundleExecutable')
    if not isinstance(exe, str) or not exe or '/' in exe or exe.startswith('.'):
        exe = 'SwitchboardLinks'
    return os.path.join(app, 'Contents', 'MacOS', exe)


def installed():
    """The link app is there, and it is the link app: nothing else is ever run in its place."""
    return kind(app_path()) == 'links' and os.path.exists(bin_path())


def holder_of(path):
    """What holds the default-browser seat, from the app's path: {'path', 'id' (bundle identifier or None), 'name',
    'kind': 'links' (Switchboard Links, any copy), 'browser' (a known browser), 'handler' (another app that takes
    links) or None (nothing found)}."""
    if not path:
        return {'path': None, 'id': None, 'name': None, 'kind': None}
    info = bundle_info(path)
    bid = info.get('CFBundleIdentifier') if isinstance(info.get('CFBundleIdentifier'), str) else None
    name = info.get('CFBundleDisplayName') or info.get('CFBundleName') or \
        os.path.splitext(os.path.basename(path.rstrip('/')))[0]
    if bid == BUNDLE_ID:
        k = 'links'
    elif bid in BROWSER_IDS:
        k, name = 'browser', BROWSER_IDS[bid]
    else:
        k = 'handler'
    return {'path': path, 'id': bid, 'name': name if isinstance(name, str) else None, 'kind': k}


def status(fresh=False):
    """Is the app installed, and does macOS hand it every clicked link? {'installed', 'on', 'browser' (the default
    browser's name)}. Asked of the link app (--status), at most every 20 s. seat() says who holds the seat."""
    if not fresh and time.time() - CACHE['at'] < 20 and CACHE['value'] is not None:
        return CACHE['value']
    value, path = {'installed': installed(), 'on': False, 'browser': None}, None
    if value['installed']:
        try:
            out = run([bin_path(), '--status'], timeout=10).stdout.strip()
            value['browser'] = os.path.splitext(os.path.basename(out.rstrip('/')))[0] or None
            # The default browser is the link app: this copy, or (by its bundle identifier) a copy elsewhere.
            value['on'] = bool(out) and (os.path.realpath(out) == os.path.realpath(app_path()) or kind(out) == 'links')
            path = out or None
        except (OSError, subprocess.TimeoutExpired):
            pass
    CACHE.update(at=time.time(), value=value, path=path)
    return value


def seat(fresh=False, look=True):
    """Who holds the default-browser seat: {'holder': holder_of the default browser, 'loop': the browser plain links
    go to (previous-browser.txt) is the link app itself, which would send links round in a circle; the link app then
    sends them to Chrome instead}. look=False: from the status last read (the page's loop has just read it), nothing
    run."""
    if look:
        status(fresh)
    return {'holder': holder_of(CACHE.get('path')),
            'loop': read_text(data_path('previous-browser.txt')) == BUNDLE_ID}


def banner(st=None, where=None, forwards=False):
    """The page's links line in the server's words, or None when links open as their owner (or the link app isn't
    built): {'text', 'action': 'make_default' (one button: --make-default; macOS asks you)}. The words are the page's
    (index.html, linksLine) for whoever holds the default-browser seat: an app that sends clicked links to the page
    (`forwards`, server.passes_links), so they do open as their owner while it runs; a browser, where they open
    plainly; another app not seen passing links on. The links-lost notification shows them away from the page, so
    they say Switchboard and the page where the page says "here" and "this page". Never re-claims the seat by itself:
    the person may have chosen another default on purpose."""
    st = st or status()
    if not st.get('installed') or st.get('on'):
        return None
    holder = (where or seat())['holder'] or {}
    who = holder.get('name')
    if holder.get('kind') == 'handler' and forwards:
        text = ('%s takes clicked links and sends them to Switchboard: they open as their owner while the page runs. '
                'Switchboard Links also works when the page is closed.' % (who or 'Another app'))
    elif holder.get('kind') == 'browser':
        text = '%s is the default browser, so links you click in other apps open there, not as their owner.' % who
    else:
        text = ('%s takes clicked links now. They open as their owner only if it sends them to Switchboard.'
                % (who or st.get('browser') or 'Another app'))
    return {'text': text, 'action': 'make_default'}


def turn(on):
    if not installed():
        raise SwitchError('Needs the link app: switchboard update --link-app (it needs Apple’s command line tools)')
    exe = bin_path()
    # macOS itself asks "change your default web browser?". Only a person can say yes.
    subprocess.Popen([exe, '--make-default' if on else '--restore'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    CACHE['at'] = 0


def home_profile():
    return read_text(data_path('home-profile.txt'))


def set_home_profile(profile):
    """Which profile ordinary links open in ('' = the browser decides, as it always has)."""
    if profile and (not browser.PROFILE_RE.match(profile) or profile not in browser.profiles()):
        raise SwitchError('That browser profile does not exist.')
    write_text(data_path('home-profile.txt'), profile)


def announce_port():
    """The link app reads these one-line files: where the page is, and which browser holds the profiles. Only the page
    calls this, as it starts: so it also marks this process as the page, whose background link work may run (the
    owner map, the daily quiet re-proof of each account's link: artifacts.background_on)."""
    write_text(data_path('port'), str(port()))
    write_text(data_path('profile-browser.txt'), browser.info()['bundle'])
    PAGE['up'] = True
    browser.PAGE_PROCESS['on'] = True


# ---------- finding the page and asking it, the way the link app does ----------

def page_port():
    """The page's port, found exactly as the link app finds it (router/SwitchboardLinks.swift pagePort, the same
    rules): $SWITCHBOARD_PORT, then the `port` file the page wrote when it started (the port it is really on, even when
    settings.json changed since), then settings.json's "port" when it is a JSON number (never true/false, never a
    string), then 7799. Each must be a whole number from 1 to 65535, with only spaces or tabs around it."""
    said_settings = read_json(data_path('settings.json')).get('port')
    if isinstance(said_settings, bool) or not isinstance(said_settings, (int, float)):
        said_settings = ''
    elif isinstance(said_settings, float):
        said_settings = str(int(said_settings)) if said_settings.is_integer() else ''
    for said in (os.environ.get('SWITCHBOARD_PORT') or '', read_text(data_path('port')), str(said_settings)):
        said = str(said).strip(' \t')
        if re.fullmatch(r'[+-]?[0-9]{1,40}', said) and 1 <= int(said) <= 65535:
            return int(said)
    return 7799


class PageDown(OSError):
    """The page did not answer: nothing on its port, a refused or cut-off connection, or no answer in time."""


def ask_page(path, body, timeout=2.0):
    """POST to this Mac's page directly (never through a proxy), with the header only Switchboard's own callers send.
    Returns its JSON answer. Raises PageDown when it doesn't answer, SwitchError with its words when it says no."""
    req = pagekey.request('http://127.0.0.1:%d' % page_port(), path, body)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            got = json.loads(r.read(1 << 20).decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read(1 << 16).decode('utf-8'))
        except Exception:  # noqa: BLE001 - a refusal whose words can't be read is still a refusal
            said = {}
        finally:
            e.close()
        if e.code == 400 and isinstance(said, dict) and said.get('error'):
            raise SwitchError(said['error'])        # its words for the person (e.g. no profile is signed in as the owner)
        raise PageDown('the page answered %d' % e.code)     # a refusal (403) or a fault: the caller falls back
    except (OSError, ValueError, UnicodeDecodeError) as e:      # refused, timed out, cut off, not JSON
        raise PageDown(str(e))
    except Exception as e:  # noqa: BLE001 - http.client's own errors (a reply that is not HTTP)
        raise PageDown(repr(e))
    if not isinstance(got, dict):
        raise PageDown('the page answered, but not with an object')
    return got


# ---------- other apps that take clicked links ----------

HANDLERS = {'at': 0, 'value': None}
HANDLERS_FRESH = 600


def other_link_handlers(fresh=False, folders=None):
    """Apps in /Applications and ~/Applications that tell macOS they open web links but are neither a browser nor
    Switchboard Links (an old Link Router, a switcher app, a URL handler): [{'name', 'path', 'id', 'default'}]. Only
    each app's Info.plist is read; nothing is run, nothing is stopped. Looked for at most every 10 minutes."""
    if folders is None and not fresh and HANDLERS['value'] is not None and \
            0 <= time.time() - HANDLERS['at'] < HANDLERS_FRESH:
        return HANDLERS['value']
    default = holder_of(CACHE.get('path'))
    out, seen = [], set()
    for folder in folders or ('/Applications', os.path.join(home(), 'Applications')):
        try:
            names = sorted(n for n in os.listdir(folder) if n.endswith('.app'))
        except OSError:
            continue
        for n in names:
            path = os.path.join(folder, n)
            info = bundle_info(path)
            bid = info.get('CFBundleIdentifier')
            if not isinstance(bid, str) or bid == BUNDLE_ID or bid in BROWSER_IDS or bid in seen:
                continue
            schemes = set()
            for t in info.get('CFBundleURLTypes') or []:
                if isinstance(t, dict):
                    schemes.update(s.lower() for s in t.get('CFBundleURLSchemes') or [] if isinstance(s, str))
            if not schemes & {'http', 'https'}:
                continue
            seen.add(bid)
            name = info.get('CFBundleDisplayName') or info.get('CFBundleName') or n[:-4]
            out.append({'name': name if isinstance(name, str) else n[:-4], 'path': path, 'id': bid,
                        'default': default.get('id') == bid})
    if folders is None:
        HANDLERS.update(at=time.time(), value=out)
    return out


def handlers_note(found=None, on=False, skip=()):
    """One plain line for the page when other apps also take clicked links, or None. Never stops anything: it says
    which apps, and that only one should. Nothing while Switchboard Links is the default (`on`: no other app gets a
    click). Never the app in the seat (the links line above it says what that app does), nor an app seen sending
    clicked links to the page: `skip`, their bundle identifiers."""
    found = other_link_handlers() if found is None else found
    found = [x for x in found or [] if not x.get('default') and x.get('id') not in skip]
    if on or not found:
        return None
    names = ', '.join('%s (%s)' % (x['name'], x['path'].replace(home(), '~', 1)) for x in found[:4])
    more = ' and %d more' % (len(found) - 4) if len(found) > 4 else ''
    return ('Other apps here can also take clicked links: %s%s. Only one should; quit or remove the ones you no '
            'longer use.' % (names, more))
