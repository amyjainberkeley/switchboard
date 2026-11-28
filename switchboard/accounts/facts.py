"""Claude Code's own conventions in one table, each with the Claude Code version it was checked against.

Everything here is Claude Code's interface, not ours: its Keychain item names and account name, where it keeps its
files, its lock folders and how long it waits before calling one stale, Anthropic's endpoints and Claude Code's public
OAuth client id. Each was checked on 2026-11-16, as of Claude Code 2.1.325. check() says when the Claude Code on this
Mac is newer than that, so a change in any of these is looked at before it bites.

Every function takes the environment and the home folder it should answer for (a run-as uses its own config folder),
and works them out each time: nothing is fixed at import.
"""
import glob
import hashlib
import os
import pwd
import re
import stat
import unicodedata

CHECKED = '2.1.325'

# ---------- Anthropic's endpoints and Claude Code's OAuth client ----------
TOKEN_URL = 'https://platform.claude.com/v1/oauth/token'
TOKEN_HOST = 'platform.claude.com'
PROFILE_URL = 'https://api.anthropic.com/api/oauth/profile'
USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
API_HOST = 'api.anthropic.com'
CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
EXPIRY_BUFFER_S = 300           # Claude Code calls an access token expired 5 minutes before its expiresAt
STDIN_LIMIT = 4032              # the longest `security -i` command line Claude Code writes its item with (2.1.325)
# How far apart two refreshTokenExpiresAt stamps (ms) of ONE sign-in can be. After a renewal Claude Code stores
# min(the stamp it had, now + the refresh_token_expires_in seconds the server answers) (read in 2.1.329; the same in
# 2.1.331). The server's end date is fixed, but "now + seconds left" lands a little earlier or later each time (whole
# seconds, and the time the answer took), so the stored stamp can go down by milliseconds to seconds. It never goes up.
SAME_SIGN_IN_MS = 120_000

# ---------- the login Claude Code keeps in the Keychain ----------
SERVICE_BASE = 'Claude Code'
CREDENTIALS_SUFFIX = '-credentials'
FALLBACK_ACCOUNT = 'claude-code-user'
OAUTH_KEY = 'claudeAiOauth'
CREDENTIALS_FILE = '.credentials.json'
# Siblings of the login in the same item that belong to the Mac, not to an account: a switch keeps the live copy
# (present or absent) and never puts back a saved one. designOauth is here on purpose: it is its
# own grant that Claude Code renews under its own lock, so a saved copy written back would be a spent generation.
# keychainOverflow is here because it says where THIS Mac's overflow items are (Claude Code 2.1.331, behind a server
# flag, moves long sections of the item into "<service>-overflow-a" / "-b" and leaves this pointer in the item): a
# write keeps the live pointer as it is, and a pointer from a saved copy would point at parts that are gone or newer.
OVERFLOW_KEY = 'keychainOverflow'
SHARED_SIBLINGS = ('mcpOAuth', 'mcpOAuthClientConfig', 'mcpXaaIdp', 'mcpXaaIdpConfig', 'pluginSecrets', 'designOauth',
                   OVERFLOW_KEY)
# What Claude Code's own logout removes from that item (2.1.325): the keys it ties to a sign-in. Kept for doctor and
# for the record; the rule a switch uses is "everything but SHARED_SIBLINGS travels with the account".
LOGOUT_REMOVES = ('claudeAiOauth', 'organizationUuid', 'trustedDeviceToken', 'trustedDeviceEnrolledAfterRefusalAtMs',
                  'enterpriseGateway', 'designOauth', 'cloudSessionsOauth', 'cloudSessionsSignInEnded')

# ---------- its lock folders (npm proper-lockfile: a folder made with mkdir is the lock) ----------
CRED_LOCK_NAME = '.oauth_refresh.lock'
CRED_LOCK_STALE_S = 60.0        # both credential locks: stale after 60 s, touched every 5 s by their holder
CONFIG_LOCK_STALE_S = 10.0      # the global config's lock: stale after 10 s
CC_TOUCH_S = 5.0
LOCK_BUDGET_S = 9.0             # our wait for one lock; Claude Code retries a held credential lock 5 times, 1-2 s apart
# Claude Code makes every save of its Keychain item under this lock (proper-lockfile on <secure-storage
# folder>/.storage-write, realpath off), with a fresh read of the item inside (2.1.329; the same in 2.1.330 and
# 2.1.331). Two saves of one item at the same moment make macOS refuse one, and Claude Code answers a refused save by
# deleting its item, so every write of that item is made under this lock: taken last, let go first.
STORAGE_LOCK_NAME = '.storage-write.lock'
STORAGE_LOCK_STALE_S = 15.0     # Claude Code calls it stale after 15 s
STORAGE_LOCK_BUDGET_S = 9.0     # our wait; Claude Code itself retries 10 times, 0.1 to 1 s apart, then gives up

# ---------- variables that make Claude Code skip the account login (removed for a run-as) ----------
AUTH_OVERRIDES = ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN',
                  'CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR', 'CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR')
# Claude Code picks these before a login too (code.claude.com/docs/en/authentication, "Authentication precedence";
# the providers as its env-vars page lists them; read 2026-11-19): a cloud provider it is told to use, a named
# profile, and Workload Identity Federation, which needs its rule id.
PROVIDERS = ('CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY', 'CLAUDE_CODE_USE_MANTLE',
             'CLAUDE_CODE_USE_ANTHROPIC_AWS', 'CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD')
PROFILES = ('ANTHROPIC_PROFILE', 'ANTHROPIC_FEDERATION_RULE_ID')
NOT_THE_LOGIN = AUTH_OVERRIDES + PROVIDERS + PROFILES


def turned_on(value):
    """Is a provider's variable on? Claude Code reads 1, true, yes and on as on, and 0, false, no and off as off, in
    any casing (its env-vars page); an empty one is off."""
    return value is not None and str(value).strip().lower() not in ('', '0', 'false', 'no', 'off')

# name -> (what it is, the Claude Code version it was checked against)
FACTS = {
    'keychain.service': ('"Claude Code-credentials"; with CLAUDE_CONFIG_DIR (or a non-empty '
                         'CLAUDE_SECURESTORAGE_CONFIG_DIR) "-" and the first 8 hex of the SHA-256 of that folder '
                         'string (NFC, as given, not resolved) are added', CHECKED),
    'keychain.account': ('USER, else the login name; "claude-code-user" when neither works or the name has a '
                         'character outside A-Z a-z 0-9 . _ -', CHECKED),
    'keychain.value': ('JSON: claudeAiOauth {accessToken, refreshToken, expiresAt ms, refreshTokenExpiresAt ms, '
                       'scopes, subscriptionType, rateLimitTier, clientId} beside other top-level keys', CHECKED),
    'keychain.tool': ('Claude Code reads and writes the item through /usr/bin/security (find-generic-password -w; '
                      'a write is the one line add-generic-password -U -a "<account>" -s "<service>" -X "<hex>" on '
                      'the standard input of `security -i` when that line is at most %d bytes, else the same in '
                      'the arguments)' % STDIN_LIMIT, CHECKED),
    'files.credentials': ('<secure-storage folder>/.credentials.json: CLAUDE_SECURESTORAGE_CONFIG_DIR when defined '
                          '(empty means ~/.claude), else the config folder', CHECKED),
    'files.config_home': ('CLAUDE_CONFIG_DIR, else ~/.claude', CHECKED),
    'files.global_config': ('(CLAUDE_CONFIG_DIR or ~)/.claude.json, unless <config folder>/.config.json is a regular '
                            'file that is non-empty, or empty with a write bit while .claude.json is missing', CHECKED),
    'locks.credentials': ('<secure-storage folder>/.oauth_refresh.lock then <its real path>.lock, stale 60 s, touched '
                          'every 5 s; a held second lock makes Claude Code let go of the first and retry', CHECKED),
    'locks.config': ('<global config>.lock, stale 10 s', CHECKED),
    'locks.storage': ('<secure-storage folder>/.storage-write.lock, stale 15 s, retried 10 times 0.1 to 1 s apart; '
                      'held around every save of the Keychain item, with a fresh read inside; skipped only by a '
                      'process with no storage folder at all', '2.1.329'),
    'keychain.overflow': ('behind a server flag, a long item keeps a pointer "keychainOverflow" {slot, key, and '
                          '"login" when the account login itself moved} and moves long sections, encrypted, into '
                          '"<service>-overflow-a" or "-b"; an item without the pointer is read as the whole login',
                          '2.1.331'),
    'oauth.token': ('POST %s {grant_type: refresh_token, refresh_token, client_id}; an answer may carry '
                    'refresh_token, expires_in, refresh_token_expires_in, scope, account, organization' % TOKEN_URL,
                    CHECKED),
    'oauth.profile': ('GET %s with Bearer: account.uuid, account.email, organization.uuid' % PROFILE_URL, CHECKED),
    'oauth.client_id': (CLIENT_ID, CHECKED),
    'oauth.expiry_buffer': ('an access token counts as expired 300 s before expiresAt', CHECKED),
    'refresh.expiry_rule': ('a refresh keeps refreshTokenExpiresAt (the earlier of the old and a new one); only a new '
                            'sign-in sets it fresh; absent means not known', CHECKED),
}


def same_sign_in(a_ms, b_ms):
    """Can two refresh-expiry stamps (epoch ms) belong to one sign-in? Both known and at most SAME_SIGN_IN_MS apart.
    Accounts signed in within that time of each other share it too, so this alone never says whose a login is."""
    if not a_ms or not b_ms:
        return False
    return abs(a_ms - b_ms) <= SAME_SIGN_IN_MS


def _nfc(text):
    return unicodedata.normalize('NFC', text)


def config_home(env, home):
    """Claude Code's config folder: CLAUDE_CONFIG_DIR, else ~/.claude (Unicode NFC). An empty CLAUDE_CONFIG_DIR counts
    as unset here; Claude Code would take it as the current folder, which no working setup uses."""
    value = env.get('CLAUDE_CONFIG_DIR')
    return _nfc(value if value else os.path.join(home, '.claude'))


def secure_dir(env, home):
    """Where the login's plaintext fallback and the credential locks live: CLAUDE_SECURESTORAGE_CONFIG_DIR when it is
    defined at all (empty means ~/.claude), else the config folder."""
    if 'CLAUDE_SECURESTORAGE_CONFIG_DIR' in env:
        value = env.get('CLAUDE_SECURESTORAGE_CONFIG_DIR') or ''
        return _nfc(value or os.path.join(home, '.claude'))
    return config_home(env, home)


def keychain_service(env, suffix=CREDENTIALS_SUFFIX):
    """The Keychain item Claude Code uses for its login in this environment."""
    if 'CLAUDE_SECURESTORAGE_CONFIG_DIR' in env:
        value = env.get('CLAUDE_SECURESTORAGE_CONFIG_DIR') or ''
    else:
        value = env.get('CLAUDE_CONFIG_DIR') or ''
    if not value:
        return SERVICE_BASE + suffix
    return '%s%s-%s' % (SERVICE_BASE, suffix, hashlib.sha256(_nfc(value).encode('utf-8')).hexdigest()[:8])


def keychain_account(env):
    """The account name on Claude Code's Keychain items."""
    try:
        name = env.get('USER') or pwd.getpwuid(os.geteuid()).pw_name
    except Exception:  # noqa: BLE001 - no user database entry: Claude Code falls back the same way
        name = FALLBACK_ACCOUNT
    return name if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9._-]+', name) else FALLBACK_ACCOUNT


def credentials_file(env, home):
    return os.path.join(secure_dir(env, home), CREDENTIALS_FILE)


def _missing(err):
    return isinstance(err, (FileNotFoundError, NotADirectoryError))


def global_config(env, home):
    """The global config file Claude Code reads in this environment (its 2.1.299+ rule, as on macOS).

    <config folder>/.config.json wins when it is a regular file that is non-empty (and its exact name is in its
    folder's listing: a case-insensitive disk could answer for .Config.json), or when it is empty with a write bit
    while the main file is missing. Otherwise (CLAUDE_CONFIG_DIR or ~)/.claude.json."""
    legacy = os.path.join(config_home(env, home), '.config.json')
    main = os.path.join(env.get('CLAUDE_CONFIG_DIR') or home, '.claude.json')
    try:
        ls = os.stat(legacy)
    except OSError:
        return main
    if not stat.S_ISREG(ls.st_mode) or not (ls.st_size > 0 or ls.st_mode & 0o222):
        return main
    ms = None
    try:
        ms = os.stat(main)
    except OSError as e:
        if _missing(e) or ls.st_size == 0:
            return legacy
    if ls.st_size == 0:
        return main
    if ms is not None and (not stat.S_ISREG(ms.st_mode) or ms.st_size == 0):
        return legacy
    try:
        if os.path.basename(legacy) not in os.listdir(os.path.dirname(legacy)):
            return main
    except OSError as e:
        if _missing(e):
            return main
    return legacy


def refresh_lock(env, home):
    return os.path.join(secure_dir(env, home), CRED_LOCK_NAME)


def legacy_lock(env, home):
    """The second credential lock: the secure-storage folder's real path (the folder itself when that fails) + .lock,
    so ~/.claude.lock for the default setup."""
    folder = secure_dir(env, home)
    if os.path.exists(folder):          # Node's realpath fails on a missing folder and Claude Code then uses it as is
        folder = os.path.realpath(folder)
    return folder.rstrip(os.sep) + '.lock'


def config_lock(env, home):
    return global_config(env, home) + '.lock'


def storage_lock(env, home):
    """Claude Code's save lock on its Keychain item: <secure-storage folder>/.storage-write.lock."""
    return os.path.join(secure_dir(env, home), STORAGE_LOCK_NAME)


def default_profile(env, home):
    """True when this environment points Claude Code at its default login (no custom config or secure folder)."""
    if 'CLAUDE_SECURESTORAGE_CONFIG_DIR' in env:
        return not env.get('CLAUDE_SECURESTORAGE_CONFIG_DIR')
    return not env.get('CLAUDE_CONFIG_DIR')


# ---------- is the Claude Code on this Mac newer than these facts? ----------

def _version_key(text):
    parts = re.findall(r'\d+', text or '')
    return tuple(int(p) for p in parts[:4]) if parts else ()


def installed_versions(home):
    """Claude Code versions found on disk, without running anything: the VS Code extension's folders, the native
    installer's versions folder, and an npm install's package.json."""
    found = set()
    for path in glob.glob(os.path.join(home, '.vscode', 'extensions', 'anthropic.claude-code-*')):
        m = re.match(r'anthropic\.claude-code-(\d+\.\d+\.\d+)', os.path.basename(path))
        if m:
            found.add(m.group(1))
    for path in glob.glob(os.path.join(home, '.local', 'share', 'claude', 'versions', '*')):
        m = re.match(r'(\d+\.\d+\.\d+)$', os.path.basename(path))
        if m:
            found.add(m.group(1))
    pkg = os.path.join(home, '.claude', 'local', 'node_modules', '@anthropic-ai', 'claude-code', 'package.json')
    try:
        import json
        with open(pkg, encoding='utf-8') as f:
            v = json.load(f).get('version')
        if isinstance(v, str) and re.match(r'\d+\.\d+\.\d+', v):
            found.add(v)
    except (OSError, ValueError, AttributeError):
        pass
    return sorted(found, key=_version_key)


# After Claude Code updates itself, one read-only look at its program for the names the engine relies on: each is a
# string Claude Code's own code holds (as of 2.1.325). Names only, never a
# secret; a name not found is a warning with the version in it, never a refused switch.
SHAPE_NAMES = (('keychain.service', CREDENTIALS_SUFFIX, 'its Keychain item'),
               ('keychain.value', OAUTH_KEY, "the login's key in that item"),
               ('files.credentials', CREDENTIALS_FILE, 'its login file'),
               ('locks.credentials', CRED_LOCK_NAME, "its login's lock"),
               ('files.global_config', 'oauthAccount', 'the account block in its settings'),
               ('oauth.token', '/v1/oauth/token', 'the address it renews logins at'),
               ('oauth.client_id', CLIENT_ID, 'its sign-in client'),
               ('oauth.usage', '/api/oauth/usage', 'the address limits are read from'),
               ('refresh.expiry_rule', 'refreshTokenExpiresAt', "a sign-in's end date"))
SHAPE_CHUNK = 4 * 1024 * 1024           # read this much at a time (a native Claude Code is about 200 MB)
SHAPE_MAX_BYTES = 1024 * 1024 * 1024    # a file larger than this is not Claude Code's program: not read


def program_of(home, version):
    """The file that holds Claude Code `version`'s code, found without running anything: the native installer's
    versions/<version> file, the VS Code extension's native binary, or an npm install's cli.js. None when none is."""
    found = [os.path.join(home, '.local', 'share', 'claude', 'versions', version)]
    for ext in sorted(glob.glob(os.path.join(home, '.vscode', 'extensions', 'anthropic.claude-code-%s*' % version))):
        found += sorted(glob.glob(os.path.join(ext, 'resources', 'native-binary', 'claude*')))
    found.append(os.path.join(home, '.claude', 'local', 'node_modules', '@anthropic-ai', 'claude-code', 'cli.js'))
    for path in found:
        try:
            st = os.stat(path)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and 0 < st.st_size <= SHAPE_MAX_BYTES:
            if path.endswith('cli.js') and version not in installed_versions(home):
                continue
            return path
    return None


def shape_look(path):
    """[(fact name, plain words)] of SHAPE_NAMES not found in the file at path (read only), or None when it can't be
    read."""
    needles = [(name, text.encode('utf-8'), words) for name, text, words in SHAPE_NAMES]
    left = dict((name, (needle, words)) for name, needle, words in needles)
    keep = max(len(n) for _, n, _ in needles)
    tail = b''
    try:
        with open(path, 'rb') as f:
            while left:
                chunk = f.read(SHAPE_CHUNK)
                if not chunk:
                    break
                block = tail + chunk
                for name in [n for n, (needle, _) in left.items() if needle in block]:
                    del left[name]
                tail = block[-keep:]
    except OSError:
        return None
    return [(name, words) for name, _, words in needles if name in left]


def check(home, versions=None):
    """{'checked': CHECKED, 'installed': newest found or None, 'ok': bool, 'warnings': [...]}."""
    versions = installed_versions(home) if versions is None else sorted(versions, key=_version_key)
    newest = versions[-1] if versions else None
    warnings = []
    if newest is None:
        warnings.append("Claude Code's version on this Mac is not known, so these facts (checked against %s) could "
                        "not be compared with it." % CHECKED)
    elif _version_key(newest) > _version_key(CHECKED):
        warnings.append('Claude Code %s is newer than %s, the version its Keychain item, files and locks were checked '
                        'against. Switching still works the same way unless Claude Code changed them.'
                        % (newest, CHECKED))
    return {'checked': CHECKED, 'installed': newest, 'ok': not warnings, 'warnings': warnings,
            'facts': dict((k, v[0]) for k, v in FACTS.items())}
