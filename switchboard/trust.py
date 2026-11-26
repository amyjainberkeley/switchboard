"""The TLS context of every secure connection Switchboard makes to another machine, built in one place (context).

What it trusts, each added to the one before:
1. Python's default certificates (ssl.create_default_context), and the file named in SSL_CERT_FILE. OpenSSL reads
   that variable itself, in place of its own default file (that file is then loaded too, so the variable only
   adds); the LibreSSL of Apple's Python 3.9 does not read it at all (checked on macOS 26), so it is loaded here. A
   Python that came with no certificates at all gets macOS's bundle, /etc/ssl/cert.pem.
2. The file named in NODE_EXTRA_CA_CERTS, when it is there and holds certificates: the file a company network's root
   certificate is given to Claude Code in, so where Claude Code connects, Switchboard does too.
3. With network_trust "mac" (the default): the roots this Mac trusts for TLS, as macOS itself answers (mac_roots).
   With "python", only 1 and 2.

Nothing turns off the check of a certificate or of a host name: context() sets both on what it returns, whatever
it was built from, and no setting or variable is read for them.

The roots this Mac trusts: the self-signed certificates in the login keychain and the System keychain (where a
person and an administrator add a root), each kept only when `security verify-cert -c FILE -p ssl -l -L -q` exits 0.
Without -r, verify-cert evaluates against the system's anchors and the trust settings of the user and admin domains,
which is macOS's own answer to "is a server certificate under this root trusted for SSL" (-p ssl); -l lets the
certificate under test be a CA, and -L keeps it from fetching anything. A certificate that merely sits in a keychain
is never added. The answer is kept in memory, in each process: a root is trusted because macOS said so to this
process, never because a file says so. <data>/mac-trust.json (owner-only) keeps only which certificates to ask about
(the SHA-256 digests of the roots macOS called trusted at the last full look), with the modification times and sizes
of the files that answer depends on. So a new process asks macOS again, before its first secure connection, about
those certificates alone (one find-certificate, one verify-cert each); a digest written into the file can only make it
ask about one more certificate already in a keychain. Every root is looked at again when one of those files changes,
or a day later. A command that makes no secure connection never runs security.

When a connection is refused over a certificate, refusal() says in one sentence which issuer was not trusted and
what to do, instead of Python's error text.
"""
import hashlib
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time

from . import core

TOOL = '/usr/bin/security'      # by its full path: a program named security earlier on PATH never decides trust
REAL_TOOL = TOOL
MODE_KEY = 'network_trust'      # in settings.json
MODES = ('mac', 'python')
MODE_DEFAULT = 'mac'
KEPT = 'mac-trust.json'         # in the data folder, owner-only
KEPT_FOR = 24 * 3600            # seconds an answer from macOS is used while nothing it depends on changed
WAIT_S = 10.0                   # how long a connection waits for macOS's answer when there is none to use
ASK_S = 20                      # each call to security, at most
MOST = 300                      # certificates in the keychains looked at, at most
FAILED_PAUSE = 600              # after macOS could not be asked, seconds before it is asked again
LOOK_S = 5.0                    # the look at a refused server's certificate, at most
SAID_FOR = 600                  # seconds a refusal's sentence is kept for its host
SYSTEM_KEYCHAIN = '/Library/Keychains/System.keychain'
ADMIN_TRUST = '/Library/Security/Trust Settings/Admin.plist'
MAC_BUNDLE = '/etc/ssl/cert.pem'
_PEM = re.compile(r'-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----')


# ---------- the setting ----------

def mode():
    """'mac' (the default, and what anything else in settings.json reads as) or 'python'."""
    v = core.settings().get(MODE_KEY)
    return v if v in MODES else MODE_DEFAULT


def set_mode(value):
    """Set network_trust to 'mac' or 'python'. Raises SwitchError for any other value. Returns the line to say."""
    if value not in MODES:
        raise core.SwitchError('network_trust is mac or python, not %s.' % value)
    core.set_setting(MODE_KEY, value)
    if value == 'mac':
        return "Secure connections trust this Mac's trusted certificates too."
    return "Secure connections trust Python's certificates and the files named in SSL_CERT_FILE and " \
           "NODE_EXTRA_CA_CERTS."


# ---------- certificates, read as far as needed ----------

def _tlv(b, i):
    """(tag, start of its value, end of its value) of the DER element at b[i]. Raises ValueError."""
    if i + 2 > len(b):
        raise ValueError('short')
    tag, n = b[i], b[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7f
        if not 1 <= k <= 4 or i + k > len(b):
            raise ValueError('length')
        n = int.from_bytes(b[i:i + k], 'big')
        i += k
    if i + n > len(b):
        raise ValueError('long')
    return tag, i, i + n


def names(der):
    """(issuer, subject) of a certificate, each the DER bytes of its Name, or None when it can't be read."""
    try:
        tag, s, _ = _tlv(der, 0)                    # Certificate
        tag2, i, end = _tlv(der, s)                 # TBSCertificate
        if tag != 0x30 or tag2 != 0x30:
            return None
        parts = []
        while i < end and len(parts) < 6:
            t, vs, ve = _tlv(der, i)
            if not parts and t == 0xa0:             # [0] version, when there
                i = ve
                continue
            parts.append(der[i:ve])
            i = ve
        if len(parts) < 5:
            return None
        return parts[2], parts[4]                   # serial, signature, issuer, validity, subject
    except (ValueError, IndexError, TypeError):
        return None


_CN, _O = b'\x06\x03\x55\x04\x03', b'\x06\x03\x55\x04\x0a'
_TEXT = {0x0c: 'utf-8', 0x13: 'ascii', 0x16: 'ascii', 0x14: 'latin-1', 0x1e: 'utf-16-be'}


def name_said(name):
    """A Name's common name (else its organization), as plain text of at most 80 characters, or None. The text comes
    from a certificate a stranger may have made: it goes through outside.said, so it can't end the quotes the refusal
    puts around it."""
    found = {}
    try:
        _, i, end = _tlv(name, 0)
        while i < end:
            _, si, se = _tlv(name, i)               # SET
            j = si
            while j < se:
                _, ai, ae = _tlv(name, j)           # AttributeTypeAndValue
                _, oi, oe = _tlv(name, ai)
                oid = name[ai:oe]
                vt, vi, ve = _tlv(name, oe)
                if oid in (_CN, _O) and vt in _TEXT and oid not in found:
                    found[oid] = name[vi:ve].decode(_TEXT[vt], 'replace')
                j = ae
            i = se
    except (ValueError, IndexError, TypeError):
        return None
    text = found.get(_CN) or found.get(_O)
    if not text:
        return None
    from .outside import said
    return said(text, 80) or None


def _der(pem):
    try:
        return ssl.PEM_cert_to_DER_cert(pem)
    except (ValueError, TypeError):
        return None


# ---------- the roots this Mac trusts for TLS ----------

def _places():
    """The files macOS's answer depends on: the keychains roots are added to, and the trust settings' own files."""
    home = core.home()
    return [os.path.join(home, 'Library', 'Keychains', 'login.keychain-db'), SYSTEM_KEYCHAIN, ADMIN_TRUST,
            os.path.join(home, 'Library', 'Preferences', 'com.apple.security.trust-settings.plist')]


def _stamp():
    """{path: [modification time in ns, size] or None when it is not there} of _places()."""
    out = {}
    for p in _places():
        try:
            st = os.stat(p)
            out[p] = [st.st_mtime_ns, st.st_size]
        except OSError:
            out[p] = None
    return out


def _run(args):
    return subprocess.run([TOOL] + args, stdin=subprocess.DEVNULL, capture_output=True, timeout=ASK_S)


def _trusted_for_tls(pem, folder):
    """True when macOS trusts this certificate as a root for TLS (see the module's docstring for the call)."""
    path = os.path.join(folder, 'root.pem')
    with open(path, 'w') as f:
        f.write(pem)
    return _run(['verify-cert', '-c', path, '-p', 'ssl', '-l', '-L', '-q']).returncode == 0


def _digest(der):
    return hashlib.sha256(der).hexdigest()


def ask_mac(only=None):
    """The roots this Mac trusts for TLS, as PEM text, from security. only: a set of SHA-256 digests; only those
    certificates are asked about. Raises OSError when macOS could not be asked."""
    keychains = [p for p in _places()[:2] if os.path.exists(p)]
    if not keychains:
        return []
    try:
        r = _run(['find-certificate', '-a', '-p'] + keychains)
    except subprocess.TimeoutExpired:
        raise OSError('security did not answer in time')
    if r.returncode != 0:
        raise OSError('security find-certificate exited %d' % r.returncode)
    roots, seen = [], set()
    folder = tempfile.mkdtemp(prefix='switchboard-trust-')
    try:
        for pem in _PEM.findall(r.stdout.decode('ascii', 'replace'))[:MOST]:
            der = _der(pem)
            pair = names(der) if der else None
            digest = _digest(der) if der else None
            if not pair or pair[0] != pair[1] or digest in seen or (only is not None and digest not in only):
                continue                            # only roots: a certificate that signed itself
            seen.add(digest)
            pem = ssl.DER_cert_to_PEM_cert(der)
            try:
                if _trusted_for_tls(pem, folder):
                    roots.append(pem)
            except subprocess.TimeoutExpired:
                continue
    finally:
        shutil.rmtree(folder, True)
    return roots


_ASKING = threading.Lock()
_FAILED = {'at': None}
_ANSWER = {}                    # this process's answer from macOS: {'stamp', 'at', 'roots'}; never read from a file


def forget():
    """Drop this process's answer, as a new process starts without one."""
    _ANSWER.clear()


def _refresh(only=None):
    """Ask macOS and keep its answer in memory: about every root in the keychains (only None; the list in the file is
    then written again), or only about the certificates whose digests the file lists. True when it was answered."""
    stamp = _stamp()
    try:
        roots = ask_mac(only) if only != set() else []         # the file lists none: nothing to ask about
    except OSError as e:
        _FAILED['at'] = time.monotonic()
        core.log("this Mac's trusted certificates could not be read: %s" % e)
        return False
    _ANSWER.clear()
    _ANSWER.update(stamp=stamp, at=time.time(), roots=roots)
    _FAILED['at'] = None
    if only is None:
        try:
            core.write_json(core.data_path(KEPT), {'schema': 2, 'made_at': time.time(), 'depends_on': stamp,
                                                   'trusted': [_digest(ssl.PEM_cert_to_DER_cert(p)) for p in roots]})
        except (OSError, ValueError) as e:
            core.log("which of this Mac's certificates to ask about could not be kept: %r" % e)
    return True


def _listed(data, stamp, now):
    """The digests the kept file names, as a set, when it answers for now: the same files, not older than KEPT_FOR.
    Else None (macOS is then asked about every root again). The file only says what to ask about: no certificate
    in it is ever trusted, and naming one macOS does not trust adds nothing."""
    listed, made = data.get('trusted'), data.get('made_at')
    if data.get('schema') != 2 or not isinstance(listed, list) or \
            not all(isinstance(x, str) and re.fullmatch(r'[0-9a-f]{64}', x) for x in listed):
        return None
    if data.get('depends_on') != stamp or not isinstance(made, (int, float)) or isinstance(made, bool) or \
            not 0 <= now - made < KEPT_FOR:
        return None
    return set(listed)


def _answer(stamp, now):
    a = _ANSWER.get('roots'), _ANSWER.get('stamp'), _ANSWER.get('at')
    return a[0] if a[1] == stamp and isinstance(a[2], float) and 0 <= now - a[2] < KEPT_FOR else None


def mac_roots(wait=WAIT_S):
    """The roots this Mac trusts for TLS (PEM text), as macOS answered this process: the answer in memory while it
    holds, else macOS is asked (one ask at a time, waited for at most `wait` seconds), about the certificates the kept
    file lists while it holds, else about every root. Not answered in time: the answer in memory when only its age ran
    out, none when a file it depends on changed. In a test run the real security is never run: only a stand-in at
    TOOL is."""
    if TOOL == REAL_TOOL and core._in_tests():
        return []
    stamp, now = _stamp(), time.time()
    roots = _answer(stamp, now)
    if roots is not None:
        return roots
    if _FAILED['at'] is not None and time.monotonic() - _FAILED['at'] < FAILED_PAUSE:
        return []
    old = _ANSWER.get('roots') if _ANSWER.get('stamp') == stamp else None
    if _ASKING.acquire(False):
        only = _listed(core.read_json(core.data_path(KEPT)), stamp, now)

        def ask():
            try:
                _refresh(only)
            finally:
                _ASKING.release()
        t = threading.Thread(target=ask, name='switchboard-trust', daemon=True)
        t.start()
        t.join(wait)
    else:
        deadline = time.monotonic() + wait          # another thread is asking: wait for its answer
        while _ASKING.locked() and time.monotonic() < deadline:
            time.sleep(0.05)
    roots = _answer(stamp, time.time())
    if roots is not None:
        return roots
    return list(old or [])


# ---------- the one context ----------

_BUILT = {}
_LOCK = threading.Lock()
FACTS = {}                      # what the last context was built from (summary)


def _file_key(path):
    try:
        st = os.stat(path)
        return path, st.st_mtime_ns, st.st_size
    except (OSError, TypeError, ValueError):
        return path, None, None


def _empty(ctx):
    try:
        return not ctx.cert_store_stats().get('x509_ca') and not ssl.get_default_verify_paths().capath
    except (AttributeError, ssl.SSLError):
        return False


def _default_file():
    """OpenSSL's own default file, or macOS's bundle when this Python has none."""
    for p in (ssl.get_default_verify_paths().openssl_cafile, MAC_BUNDLE):
        if p and os.path.isfile(p):
            return p
    return None


def build(how, roots):
    """A new context: Python's defaults, the two files, and `roots` (PEM text) when how is 'mac'. With FACTS."""
    ctx = ssl.create_default_context()
    named = os.environ.get('SSL_CERT_FILE') or None
    facts = {'mode': how, 'ssl_cert_file': None, 'node_extra': None, 'mac': None}
    if named:                                       # Apple's Python 3.9 (LibreSSL) does not read it: loaded here
        try:
            ctx.load_verify_locations(cafile=named)
            facts['ssl_cert_file'] = 'loaded'
        except (OSError, ssl.SSLError, ValueError):
            facts['ssl_cert_file'] = 'unreadable' if os.path.exists(named) else 'missing'
    if named or _empty(ctx):
        default = _default_file()
        if default and (not named or os.path.realpath(default) != os.path.realpath(named)):
            try:
                ctx.load_verify_locations(cafile=default)
            except (OSError, ssl.SSLError):
                pass
    extra = os.environ.get('NODE_EXTRA_CA_CERTS') or None
    if extra:
        try:
            ctx.load_verify_locations(cafile=extra)
            facts['node_extra'] = 'loaded'
        except (OSError, ssl.SSLError, ValueError):
            facts['node_extra'] = 'unreadable' if os.path.exists(extra) else 'missing'
    if how == 'mac':
        added = 0
        for pem in roots:
            try:
                ctx.load_verify_locations(cadata=pem)
                added += 1
            except (ssl.SSLError, ValueError):
                pass
        facts['mac'] = added
    ctx.verify_mode = ssl.CERT_REQUIRED             # whatever the defaults or a file did: always both checks
    ctx.check_hostname = True
    return ctx, facts


def context():
    """The TLS context for a secure connection to another machine. Built again only when what it is built from
    changed (the setting, the two variables and their files, this Mac's roots); the same object otherwise."""
    how = mode()
    roots = mac_roots() if how == 'mac' else []
    key = (how, _file_key(os.environ.get('SSL_CERT_FILE')), os.environ.get('SSL_CERT_DIR'),
           _file_key(os.environ.get('NODE_EXTRA_CA_CERTS')), tuple(roots))
    with _LOCK:
        if _BUILT.get('key') != key:
            ctx, facts = build(how, roots)
            _BUILT.update(key=key, ctx=ctx)
            FACTS.clear()
            FACTS.update(facts)
        return _BUILT['ctx']


# ---------- a connection refused over a certificate ----------

_SAID = {}                      # (host, port) -> (monotonic time, the sentence): one look per host while it holds
_EXPIRED, _NOT_YET, _OTHER_NAME = 10, 9, 62     # OpenSSL's verify codes (X509_V_ERR_*), LibreSSL's too


def _presented_issuer(host, port):
    """The issuer named by the certificate a server shows, or None. A look, and only that: a context of its own that
    does not check the certificate, for one handshake that sends nothing and is closed at once. It is never returned
    and never carries a request. It is how a refusal can name what was not trusted: a handshake that failed its
    check does not hand the certificate back."""
    box = {}

    def work():
        try:
            look = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            look.check_hostname = False
            look.verify_mode = ssl.CERT_NONE
            with socket.create_connection((host, port), timeout=LOOK_S) as raw:
                with look.wrap_socket(raw, server_hostname=host) as s:
                    box['der'] = s.getpeercert(binary_form=True)
        except (OSError, ValueError):
            pass
    t = threading.Thread(target=work, name='switchboard-trust-look', daemon=True)
    t.start()
    t.join(LOOK_S + 1)
    der = box.get('der')
    pair = names(der) if der else None
    return name_said(pair[0]) if pair else None


def refusal(host, port, error=None):
    """One sentence for a connection to host:port refused over its certificate (error: the ssl error): what was not
    trusted, and the one thing to do. Never Python's error text."""
    key = (host, port)
    got = _SAID.get(key)
    if got and time.monotonic() - got[0] < SAID_FOR:
        return got[1]
    where = host if port in (None, 443) else '%s:%s' % (host, port)
    code = getattr(error, 'verify_code', None)
    if code == _OTHER_NAME:
        said = ('The certificate %s showed is for another name, so Switchboard did not connect. Try again on '
                'another network.' % where)
    elif code in (_EXPIRED, _NOT_YET):
        said = ("The certificate %s showed is out of date, or this Mac's clock is wrong. Check the date and time in "
                'System Settings.' % where)
    else:
        issuer = _presented_issuer(host, port or 443)
        by = ' (issued by "%s")' % issuer if issuer else ''
        todo = ('add its root certificate to the Keychain and set it to Always Trust' if mode() == 'mac' else
                'name its root certificate file in NODE_EXTRA_CA_CERTS')
        said = ('Switchboard did not trust the certificate %s showed%s. If this network inspects secure connections, '
                '%s.' % (where, by, todo))
    _SAID[key] = (time.monotonic(), said)
    return said


# ---------- a line for switchboard doctor ----------

_FILE_PROBLEM = {'missing': '%s names a file that is not there.',
                 'unreadable': '%s names a file with no certificate in it that can be read.'}


def doctor_line():
    """(good, text, what to do) for `switchboard doctor`'s line(): what secure connections trust, by source. Not good
    when a file named in SSL_CERT_FILE or NODE_EXTRA_CA_CERTS did not load."""
    context()
    facts = dict(FACTS)
    parts = ["Python's certificates"]
    if facts.get('ssl_cert_file') == 'loaded':
        parts.append('the file in SSL_CERT_FILE')
    if facts.get('mode') == 'mac':
        n = facts.get('mac') or 0
        parts.append("this Mac's trusted certificates (%s added on this Mac)" % (n or 'none'))
    if facts.get('node_extra') == 'loaded':
        parts.append('the file in NODE_EXTRA_CA_CERTS')
    said = parts[0] if len(parts) == 1 else ' and '.join(parts) if len(parts) == 2 else \
        ', '.join(parts[:-1]) + ', and ' + parts[-1]
    fix = ' '.join(_FILE_PROBLEM[facts[k]] % name for k, name in (('ssl_cert_file', 'SSL_CERT_FILE'),
                                                                  ('node_extra', 'NODE_EXTRA_CA_CERTS'))
                   if facts.get(k) in _FILE_PROBLEM)
    return not fix, 'Secure connections: ' + said, fix
