"""HTTPS with deadlines for the account engine: name lookup in a worker with its own deadline, then one request whose
failure says whether the request had been sent (a refresh that may have reached the server is treated as spent).

A request's timeout bounds all of it: the name lookup (in a worker, never waited past), the connection, TLS, sending
and the whole answer (a timer shuts the connection when the time is up, so a server that sends a byte now and then
can't hold it). The name is looked up once, and the connection goes to the addresses found, each with a share of the
time left. The timer shuts whichever socket holds the connection: ssl's wrap_socket moves the connection to the TLS
socket and leaves the plain one empty, so a timer on the plain socket alone would end nothing once TLS is on.

Identifies itself honestly as Switchboard/<version>; never as Claude Code.
"""
import http.client
import socket
import ssl
import threading
import time
from urllib.parse import urlparse

from .. import trust

DNS_CAP_S = 10.0                # a request's name lookup waits at most this long (and never past its timeout)


class NetError(Exception):
    """kind: 'dns', 'connect', 'tls', 'tls-cert', 'timeout', 'network'. sent: the request went out before it failed
    (the server may have acted on it)."""

    def __init__(self, kind, sent=False, detail=''):
        super().__init__('%s%s' % (kind, (': ' + detail) if detail else ''))
        self.kind = kind
        self.sent = sent


def tls_context():
    """The one TLS context of Switchboard's secure connections (trust.context): Python's certificates, the files
    named in SSL_CERT_FILE and NODE_EXTRA_CA_CERTS, and the roots this Mac trusts."""
    return trust.context()


class Http:
    """The real network. Tests hand the engine a fake with the same two methods. most: read no more than this many
    bytes of an answer, plus one (so a caller can tell an answer over its limit), instead of the whole of it."""

    def __init__(self, most=None):
        self.most = most

    def resolve(self, host, deadline_s):
        """True when host's name resolves within deadline_s (the lookup runs in a worker the caller never waits
        past)."""
        done = {}

        def work():
            try:
                socket.getaddrinfo(host, 443, 0, socket.SOCK_STREAM)
                done['ok'] = True
            except (OSError, UnicodeError):
                done['ok'] = False

        t = threading.Thread(target=work, name='dns', daemon=True)
        t.start()
        t.join(max(0.0, deadline_s))
        return done.get('ok', False)

    def request(self, method, url, headers=None, body=None, timeout_s=10.0):
        """(status, headers dict, body bytes). Raises NetError. timeout_s bounds the whole request (see the module's
        docstring)."""
        u = urlparse(url)
        host, port = u.hostname, u.port or 443
        end = time.monotonic() + max(0.05, float(timeout_s))
        sock = _connect(host, port, _lookup(host, port, min(DNS_CAP_S, max(0.05, float(timeout_s)))), end)
        deadline = _Deadline(end)                   # TLS, sending and the whole answer
        deadline.start(sock)
        ctx = tls_context()
        try:
            sock.settimeout(_left(end))             # (the handshake is one call: this bounds all of it)
            tls = ctx.wrap_socket(sock, server_hostname=host)
        except ssl.SSLCertVerificationError as e:
            deadline.cancel()
            sock.close()
            raise NetError('tls-cert', False, trust.refusal(host, port, e))     # what was not trusted, in words
        except (socket.timeout, ssl.SSLError, OSError) as e:
            deadline.cancel()
            sock.close()
            if isinstance(e, socket.timeout) or deadline.fired or time.monotonic() >= end - 0.01:
                raise NetError('timeout', False, str(e) or 'no TLS answer within %g s' % timeout_s)
            raise NetError('tls' if isinstance(e, ssl.SSLError) else 'connect', False, str(e))
        deadline.hold(tls)                          # the connection is the TLS socket's from here on
        conn = http.client.HTTPSConnection(host, port, timeout=_left(end), context=ctx)
        conn.sock = tls
        try:
            path = u.path + ('?' + u.query if u.query else '')
            try:
                tls.settimeout(_left(end))
                conn.request(method, path or '/', body=body, headers=headers or {})
                resp = conn.getresponse()
                data = resp.read() if self.most is None else resp.read(self.most + 1)
            except socket.timeout as e:
                raise NetError('timeout', True, str(e))
            except (OSError, http.client.HTTPException) as e:
                if deadline.fired or time.monotonic() >= end - 0.01:
                    raise NetError('timeout', True, 'the whole answer took longer than %g s' % timeout_s)
                raise NetError('network', True, e.__class__.__name__)
            if deadline.fired or time.monotonic() >= end:
                raise NetError('timeout', True, 'the whole answer took longer than %g s' % timeout_s)
            return resp.status, dict((k.lower(), v) for k, v in resp.getheaders()), data
        finally:
            deadline.cancel()
            conn.close()


class _Deadline(object):
    """One request's whole deadline: a timer that ends the connection when the time is up, through whichever socket
    holds it then. hold() names the TLS socket once wrap_socket has moved the connection there (and shuts it at once
    when the time ran out meanwhile); cancel() ends the watch before the connection is closed."""

    def __init__(self, end):
        self.fired = False
        self._lock = threading.Lock()
        self._sock = None
        self._over = False
        self._timer = threading.Timer(_left(end), self._fire)
        self._timer.daemon = True

    def start(self, sock):
        self._sock = sock
        self._timer.start()

    def hold(self, sock):
        with self._lock:
            self._sock = sock
            if self.fired and not self._over:
                _shut(sock)

    def _fire(self):
        with self._lock:
            if self._over:
                return
            self.fired = True
            if self._sock is not None:
                _shut(self._sock)

    def cancel(self):
        with self._lock:
            self._over = True
        self._timer.cancel()


def _left(end):
    return max(0.05, end - time.monotonic())


def _shut(sock):
    """The request's time is up: end its connection, so a read or write in progress returns now. The plain socket's
    own shutdown, for a TLS socket too: ssl's shutdown would drop the TLS state under a read still running in the
    request's thread."""
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except (OSError, TypeError, ValueError):
        pass


def _lookup(host, port, deadline_s):
    """The addresses of host, looked up in a worker the caller never waits past. Raises NetError('dns', sent=False)."""
    done = {}

    def work():
        try:
            done['addrs'] = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except (OSError, UnicodeError) as e:
            done['error'] = e
    t = threading.Thread(target=work, name='dns', daemon=True)
    t.start()
    t.join(max(0.0, deadline_s))
    if 'addrs' in done and done['addrs']:
        return done['addrs']
    if 'error' in done:
        raise NetError('dns', False, str(done['error']))
    raise NetError('dns', False, 'no answer within %g s' % deadline_s)


def _connect(host, port, addrs, end):
    """A connected socket to the first address that answers before `end`. Each address gets an even share of the time
    left (the last one all of it), so an address that never answers (often the first, IPv6 on a broken network) can't
    use the whole request's time before the next is tried. Raises NetError (never sent)."""
    last = None
    addrs = list(addrs)
    for i, (family, socktype, proto, _, addr) in enumerate(addrs):
        left = end - time.monotonic()
        if left <= 0:
            break
        s = socket.socket(family, socktype, proto)
        try:
            s.settimeout(max(0.05, left / (len(addrs) - i)))
            s.connect(addr)
            return s
        except socket.timeout as e:
            s.close()
            last = NetError('timeout', False, str(e))
        except OSError as e:
            s.close()
            last = NetError('connect', False, str(e))
    raise last or NetError('timeout', False, 'no time left to connect to %s' % host)
