"""Switchboard's local gateway: Claude Code on your own API keys when every subscription account is out.

`switchboard claude [args...]` (claude_command()) starts Claude Code for one terminal with
ANTHROPIC_BASE_URL pointing here and ANTHROPIC_AUTH_TOKEN set to this install's random secret. Plain `claude`
is left alone and stays on the person's subscription. The gateway listens on 127.0.0.1 only and forwards each
request to the first of the person's API-key lanes (keys.py) that can serve it.

THE ONE RULE (it cannot be switched off)
Anthropic's rules for Claude Code forbid routing requests through subscription (Free, Pro, Max) logins on
someone's behalf. So every request is first checked by subscription_login(), before anything else is read, and
one that carries a subscription login is refused with a clear message and nothing is forwarded. No setting,
argument or environment variable changes that. Then only the gateway's own secret is accepted (constant-time
compare), and the credential a request carries is never forwarded: the only credential sent upstream is the
lane's own API key, read from Switchboard's own Keychain items. The gateway never reads Claude Code's stored login.

WHAT IT SPEAKS (https://code.claude.com/docs/en/llm-gateway-protocol, checked 2026-11-13)
- POST /v1/messages (Claude Code posts to /v1/messages?beta=true; the query string is forwarded as it came),
  POST /v1/messages/count_tokens (optional for Claude Code: "when they're absent, Claude Code falls back to a
  character-based estimate"), GET /v1/models (model discovery: "GET /v1/models?limit=1000 with a timeout of 3
  seconds"), and HEAD /api/hello (a "connection-warming probe", answered here without going upstream).
- Request headers: every header goes on unchanged ("pass anthropic-* request headers and request body fields
  through unchanged rather than allowlisting"), except the transport ones (Host, Content-Length, Connection and
  other hop-by-hop headers, Accept-Encoding, which becomes identity so the stream can be read as it passes),
  the request's own credential (Authorization, x-api-key: replaced by the lane's key) and browser headers.
- Response headers: all pass through, including content-type, retry-after, x-should-retry and
  anthropic-ratelimit-unified-*; error bodies pass through unmodified ("forward error response bodies unmodified").
- Streams pass through event by event as they arrive ("If your gateway buffers a response until it is complete,
  Claude Code stalls"), pings included.

FAIL-OVER (the table is FAILOVER below; classify() and _Watch implement it)
A lane is skipped, and the next one tried, when it is rate limited, out of credit, overloaded, unreachable, rejects
its own key, or when its stream ends or reports an error before its first content. To know that, the beginning of
a streamed reply (message_start and pings) is held back until the first content event arrives, for at most `hold`
seconds; after that, or after the first content event, the reply is committed and is never switched to another
lane. At most one pass through the lanes per request. When every lane fails, the most useful error is returned
unchanged (transient errors that Claude Code retries first, then an ended stream, then no connection, then out of
credit, then a rejected key; ties go to the earlier lane).

MODEL NAMES
The model asked for is never changed, unless the lane has an explicit name map (a lane setting, shown by
`keys.list()` and named in the x-switchboard-model response header). A lane whose map has no entry for the model
asked for is skipped, and the x-switchboard-skipped header says so. Only the top-level "model" value is replaced;
every other byte of the request body stays as it came.

RESPONSE HEADERS ADDED
x-switchboard-lane (the lane that answered, or none), x-switchboard-skipped (each lane not used and why),
x-switchboard-model (asked -> sent, only when a name map changed it), x-switchboard-refused (on the gateway's own
refusals).

LOG
A rolling, in-memory log of attempts: time, lane, route, model asked, status, outcome, reason, and the token counts
from the reply's own usage numbers. Never a prompt, a reply or a key. stats() sums it per lane.

LIMITS (each with the doc sentence it rests on: see LIMITS and limits())
- Claude models only: Anthropic "doesn't support routing Claude Code to non-Claude models through any gateway".
- API keys only, never a subscription login (the rule above).
- Each request is billed per token to the key's owner, and subscription usage limits do not apply to it.
- OpenRouter: only its Anthropic first-party provider is guaranteed with Claude Code; fast mode only there;
  thinking budgets are not forwarded and preserved thinking is not enforced; it lists no token-counting endpoint,
  so Claude Code estimates context use on an OpenRouter lane.
- Behind a base URL other than api.anthropic.com, Remote Control is off; with ANTHROPIC_AUTH_TOKEN set, voice
  dictation is off and fast mode's organization check reports it disabled unless skipped.
- A settings-file env block beats the shell, so a base URL or token set in Claude Code's settings wins over
  `switchboard claude`; a Claude apps gateway sign-in or managed forceLoginMethod also beats it.
- Moving to another lane mid-conversation starts a new prompt cache and can drop earlier thinking blocks
  (Claude Code removes them and retries by itself).
- Sessions through a gateway are not eligible for the HIPAA configuration.
Limits of this build (no doc sentence; they are ours): the secret is in the started Claude Code's environment, so
another program running as the same user can read it (as it could read Switchboard's own files); everything is
proven against a fake upstream (tests/test_gateway.py), never against Anthropic or OpenRouter.
"""
import collections
import hashlib
import hmac
import http.client
import itertools
import json
import os
import queue
import re
import secrets as _secrets
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import core
from . import trust

LOOPBACK = '127.0.0.1'
DEFAULT_PORT = 7797
# https://platform.claude.com/docs/en/api/errors: "Messages API | 128 MB", "Token Counting API | 128 MB".
MAX_BODY = 128 * 1024 * 1024
MAX_HELD_REPLY = 64 * 1024 * 1024    # a non-streamed reply is read whole (up to this) and checked before it is sent
CONNECT_TIMEOUT = 10                 # seconds to reach a lane
STREAM_FIRST_BYTE = 60               # seconds for a lane to answer a streamed request with its headers
REPLY_FIRST_BYTE = 300               # ... a non-streamed one (the whole reply is made before its headers)
COUNT_FIRST_BYTE = 30                # ... token counting
# Model discovery: "The request is GET /v1/models?limit=1000 with a timeout of 3 seconds by default" (protocol page).
MODELS_FIRST_BYTE = 2.5
READ_TIMEOUT = 300                   # longest silence from a lane once it is answering
# The longest wait for the first content event of a stream before the reply is committed anyway. Through a custom
# ANTHROPIC_BASE_URL, Claude Code's byte-level watchdog is "300 seconds" (network-config page), so this is far below it.
HOLD = 60
BUDGET = 240                         # no new lane is tried after this many seconds on one request

ROUTES = {('POST', '/v1/messages'): 'messages',
          ('POST', '/v1/messages/count_tokens'): 'count_tokens',
          ('GET', '/v1/models'): 'models'}

# Docs checked 2026-11-13:
# - https://code.claude.com/docs/en/llm-gateway-protocol : `Authorization` "With a claude.ai login and no gateway
#   credential, `Authorization` carries the login's token", and `anthropic-beta` "When the developer authenticates
#   with a claude.ai login ... this header also carries an OAuth capability that the upstream requires".
# - https://platform.claude.com/docs/en/cli-sdks-libraries/cli/authentication : an OAuth login's token is shown as
#   `sk-ant-oat01-EXA...`; https://platform.claude.com/docs/en/manage-claude/authentication : an API key is a
#   "Static `sk-ant-api...` secret".
# The docs do not name the OAuth capability's exact value, so every `anthropic-beta` value that starts with
# "oauth" counts. Both checks are only a first line: no credential except the gateway's own secret is accepted
# at all, and the credential a request carries is never forwarded.
OAUTH_TOKEN_PREFIX = 'sk-ant-oat'
OAUTH_BETA_PREFIX = 'oauth'

SUBSCRIPTION_REFUSAL = (
    "Switchboard's gateway refused this request: %s. A Claude subscription login (Free, Pro, Max or Ultra) is "
    "never sent through the gateway, because Anthropic's rules for Claude Code do not allow routing requests "
    "through subscription plan credentials on anyone's behalf. The gateway works only with your own API keys. "
    "Start Claude Code with `switchboard claude` to use them; plain `claude` keeps your subscription.")
SECRET_REFUSAL = (
    "Switchboard's gateway refused this request: %s. It accepts only its own local secret, which "
    "`switchboard claude` gives to Claude Code. It never forwards the credential a request carries.")
FOREIGN_REFUSAL = ("Switchboard's gateway refused this request: %s. It answers only Claude Code on this Mac, "
                   "never a web page or another address.")

# ---------------------------------------------------------------------------------------------------------------
# The rule that cannot bend
# ---------------------------------------------------------------------------------------------------------------


def _values(pairs, name):
    """Every value of one header (names are case-insensitive), from a list of (name, value) pairs."""
    name = name.lower()
    return [v for k, v in pairs if k.lower() == name]


def credentials(pairs):
    """Each credential a request carries: the token in every Authorization header, and every x-api-key."""
    found = []
    for value in _values(pairs, 'authorization'):
        parts = value.strip().split(None, 1)
        found.append(parts[1].strip() if len(parts) == 2 and parts[0].lower() == 'bearer' else value.strip())
    found.extend(v.strip() for v in _values(pairs, 'x-api-key'))
    return found


def subscription_login(pairs):
    """Why these request headers carry a Claude subscription login, or '' when they do not.

    Called first for every request, whatever its method or path. Nothing can turn it off.
    """
    for value in _values(pairs, 'anthropic-beta'):
        for item in value.split(','):
            if item.strip().lower().startswith(OAUTH_BETA_PREFIX):
                return 'it carries the OAuth capability that a claude.ai login sends in anthropic-beta'
    for token in credentials(pairs):
        if token.startswith(OAUTH_TOKEN_PREFIX):
            return 'its credential is a Claude OAuth login token'
    return ''


def _digest(text):
    return hashlib.sha256(text.encode('latin-1', 'replace')).digest()


def secret_problem(pairs, secret):
    """'' when every credential the request carries is exactly the gateway's secret; else what is wrong.

    The comparison is constant time over fixed-length digests, so neither the secret's content nor its
    length leaks through timing.
    """
    given = credentials(pairs)
    if not given:
        return 'it carries no credential'
    want = _digest(secret)
    good = True
    for token in given:
        good = hmac.compare_digest(_digest(token), want) and good
    return '' if good else 'its credential is not the gateway secret'


def error_body(kind, message):
    """An error in the Claude API's own shape: {"type": "error", "error": {"type": ..., "message": ...}}."""
    return json.dumps({'type': 'error', 'error': {'type': kind, 'message': message}}).encode()


# ---------------------------------------------------------------------------------------------------------------
# When to fail over: the table (data), then the function that applies it
# ---------------------------------------------------------------------------------------------------------------
ANTHROPIC_ERRORS = 'https://platform.claude.com/docs/en/api/errors'
ANTHROPIC_LIMITS = 'https://platform.claude.com/docs/en/api/rate-limits'
ANTHROPIC_STREAM = 'https://platform.claude.com/docs/en/build-with-claude/streaming'
OPENROUTER_ERRORS = 'https://openrouter.ai/docs/api_reference/errors-and-debugging'
OPENROUTER_LIMITS = 'https://openrouter.ai/docs/api_reference/limits'
PROTOCOL = 'https://code.claude.com/docs/en/llm-gateway-protocol'

FAILOVER = (
    # (what the lane answers, category, fail over?, source, the doc sentence it rests on)
    ('no connection: refused, no such host, TLS failure, nothing within the connect time, connection broken '
     'before any reply, or a redirect (never followed: it could carry the request elsewhere)', 'unreachable', True,
     ANTHROPIC_ERRORS, 'The official SDK automatically retries transient failures (such as connection errors, rate '
     'limits, and 5xx server errors) with exponential backoff'),
    ('no response headers within the first-byte time', 'unreachable', True, ANTHROPIC_ERRORS,
     'The official SDK automatically retries transient failures (such as connection errors, rate limits, and 5xx '
     'server errors)'),
    ('429 rate_limit_error (a rate limit or a ramp limit)', 'rate limited', True, ANTHROPIC_ERRORS,
     '429 - `rate_limit_error`: Your organization has hit a rate limit, reached its usage tier’s monthly spend '
     'cap, or reached a spend limit on the Claude Code workspace.'),
    ('429 whose error.details.error_code is enforced_spend_limit_reached', 'out of credit', True, ANTHROPIC_LIMITS,
     'On the Messages API, `error.details.error_code` is `enforced_spend_limit_reached`. Use it to tell this '
     'response apart from a rate limit.'),
    ('402 billing_error', 'out of credit', True, ANTHROPIC_ERRORS,
     '402 - `billing_error`: There’s an issue with your billing or payment information.'),
    ('400 invalid_request_error whose message starts "You have reached your specified API usage limits" or '
     '"You have reached your specified workspace API usage limits"', 'out of credit', True, ANTHROPIC_LIMITS,
     'When usage reaches a spend limit you set, requests return HTTP 400 with error type `invalid_request_error`. '
     'The message starts with `You have reached your specified API usage limits`, or `You have reached your '
     'specified workspace API usage limits` for a workspace limit'),
    ('529 overloaded_error', 'overloaded', True, ANTHROPIC_ERRORS,
     '529 - `overloaded_error`: The API is temporarily overloaded.'),
    ('500 api_error, and any other 5xx (504 timeout_error included)', 'server error', True, ANTHROPIC_ERRORS,
     '500 - `api_error`: An unexpected error has occurred internal to Anthropic’s systems. Retry the request '
     'with exponential backoff'),
    ('401 authentication_error: the lane\'s own key is wrong (the request\'s credential was the gateway secret, '
     'already checked)', 'key rejected', True, ANTHROPIC_ERRORS,
     '401 - `authentication_error`: There’s an issue with your API key (for example, it’s malformed, '
     'revoked, or expired'),
    ('403 permission_error from an Anthropic lane: this key may not use what was asked for', 'key not allowed', True,
     ANTHROPIC_ERRORS, '403 - `permission_error`: Your API key does not have permission to use the specified '
     'resource.'),
    ('OpenRouter 402', 'out of credit', True, OPENROUTER_ERRORS,
     '**402**: Your account or API key has insufficient credits.'),
    ('OpenRouter 403 whose error.metadata.limit_source is openrouter_guardrail_budget, openrouter_workspace_budget '
     'or openrouter_key_limit', 'out of credit', True, OPENROUTER_ERRORS,
     'When a guardrail budget, a workspace budget, or the key\'s own limit is spent, the request is rejected with a '
     '`403` before it reaches a provider.'),
    ('OpenRouter 408', 'server error', True, OPENROUTER_ERRORS, '**408**: Your request timed out'),
    ('OpenRouter 502 and 503', 'server error', True, OPENROUTER_ERRORS,
     '**502**: Your chosen model is down or we received an invalid response from it'),
    ('a 200 whose body is only an error object (OpenRouter, non-streamed), by its error.type as in the stream row',
     'by type', True, OPENROUTER_ERRORS,
     'If the provider returns headers and then fails, you receive a `200 OK` whose JSON body holds only an `error` '
     'object and no `choices`'),
    ('a stream whose first event after message_start and pings is an `error` event of type rate_limit_error, '
     'overloaded_error, api_error, timeout_error, billing_error or authentication_error', 'by type', True,
     ANTHROPIC_STREAM, 'The API might occasionally send errors in the event stream. For example, during periods of '
     'high usage, you might receive an `overloaded_error`, which would normally correspond to an HTTP 529 in a '
     'non-streaming context'),
    ('a stream that ends or breaks before its first content event', 'empty reply', True, OPENROUTER_ERRORS,
     'A streaming request receives an SSE error event; a non-streaming request receives an error body. This '
     'includes provider errors that arrive before any token.'),
    ('anything after the first content event has been sent to Claude Code', 'never', False, OPENROUTER_ERRORS,
     'Failover also stops once part of the answer has reached you, since your application already holds output '
     'from the first provider.'),
    ('any other 400 (bad request, prompt too long), 404, 413, 422, and OpenRouter 403s that are not budgets '
     '(guardrail, moderation, refusal): another lane would answer the same, or must not be used to get round a '
     'content decision', 'never', False, OPENROUTER_ERRORS,
     '**403**: Forbidden (insufficient permissions, guardrail block, or moderation flag)'),
)

# The error.type of an error event or a 200 error body, and what it means for fail-over. OpenRouter's Anthropic
# skin uses the same names: payment_required -> billing_error, rate_limit_exceeded -> rate_limit_error,
# provider_overloaded and timeout -> overloaded_error, provider_unavailable and server -> api_error (errors page).
EVENT_ERRORS = {'rate_limit_error': 'rate limited', 'overloaded_error': 'overloaded', 'api_error': 'server error',
                'timeout_error': 'server error', 'billing_error': 'out of credit',
                'authentication_error': 'key rejected'}
SPEND_LIMIT_MESSAGES = ('You have reached your specified API usage limits',
                        'You have reached your specified workspace API usage limits')
OPENROUTER_BUDGETS = ('openrouter_guardrail_budget', 'openrouter_workspace_budget', 'openrouter_key_limit')
# Most useful first, when every lane failed: what Claude Code retries by itself, then the rest.
RANK = {'rate limited': 0, 'overloaded': 0, 'server error': 0, 'empty reply': 1, 'unreachable': 2,
        'out of credit': 3, 'key not allowed': 4, 'key rejected': 4}


def _json(data):
    try:
        value = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    return value if isinstance(value, dict) else None


def classify(status, body, kind='anthropic'):
    """The fail-over category of a whole reply (status + body), or '' when it must go to Claude Code as it is."""
    obj = _json(body) or {}
    err = obj.get('error') if isinstance(obj.get('error'), dict) else {}
    etype = err.get('type') if isinstance(err.get('type'), str) else ''
    message = err.get('message') if isinstance(err.get('message'), str) else ''
    details = err.get('details') if isinstance(err.get('details'), dict) else {}
    metadata = err.get('metadata') if isinstance(err.get('metadata'), dict) else {}
    if status == 200:
        if err and 'content' not in obj and obj.get('type') in ('error', None):
            if etype in EVENT_ERRORS:
                return EVENT_ERRORS[etype]
            code = err.get('code')
            if isinstance(code, int) and code != 200:
                return classify(code, body, kind)
        return ''
    if status == 429:
        return 'out of credit' if details.get('error_code') == 'enforced_spend_limit_reached' else 'rate limited'
    if status == 402:
        return 'out of credit'
    if status == 400:
        return 'out of credit' if message.startswith(SPEND_LIMIT_MESSAGES) else ''
    if status == 401:
        return 'key rejected'
    if status == 403:
        if metadata.get('limit_source') in OPENROUTER_BUDGETS:
            return 'out of credit'
        return 'key not allowed' if kind == 'anthropic' and etype == 'permission_error' else ''
    if status == 408 or 500 <= status <= 599:
        return 'overloaded' if status == 529 else 'server error'
    return ''


# ---------------------------------------------------------------------------------------------------------------
# Model names: a lane's explicit map (data, never rules in code)
# ---------------------------------------------------------------------------------------------------------------
# The table an OpenRouter lane gets when it is added (keys.add copies it into the lane, where it can be read and
# changed). Each pair was checked 2026-11-13:
# - Claude API IDs: https://platform.claude.com/docs/en/about-claude/models/overview ("Claude API ID":
#   `claude-fable-5-2`, `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`); `claude-opus-4-8` from
#   https://code.claude.com/docs/en/llm-gateway-protocol ("Anthropic IDs, such as `claude-opus-4-8`").
# - OpenRouter slugs: https://openrouter.ai/docs/cookbook/evaluate-and-optimize/model-migrations/claude-5-5
#   ("OpenRouter slug": `anthropic/claude-opus-5.5`, `anthropic/claude-sonnet-5.5`, `anthropic/claude-haiku-5.5`) and
#   https://openrouter.ai/docs/cookbook/coding-agents/claude-code-integration (`anthropic/claude-fable-5.2`,
#   `anthropic/claude-opus-4.8`).
OPENROUTER_NAMES = {
    'claude-fable-5-2': 'anthropic/claude-fable-5.2',
    'claude-opus-5-5': 'anthropic/claude-opus-5.5',
    'claude-sonnet-5-5': 'anthropic/claude-sonnet-5.5',
    'claude-haiku-5-5': 'anthropic/claude-haiku-5.5',
    'claude-opus-4-8': 'anthropic/claude-opus-4.8',
}

_WS = re.compile(r'[ \t\n\r]*')


def _model_span(text):
    """(start, end, value) of the top-level "model" member's value in a JSON object, or None."""
    decoder = json.JSONDecoder()
    i = _WS.match(text, 0).end()
    if text[i:i + 1] != '{':
        return None
    i = _WS.match(text, i + 1).end()
    found = []
    if text[i:i + 1] == '}':
        return None
    while True:
        if text[i:i + 1] != '"':
            return None
        try:
            key, i = json.decoder.scanstring(text, i + 1)
        except ValueError:
            return None
        i = _WS.match(text, i).end()
        if text[i:i + 1] != ':':
            return None
        i = _WS.match(text, i + 1).end()
        try:
            value, end = decoder.raw_decode(text, i)
        except ValueError:
            return None
        if key == 'model':
            found.append((i, end, value))
        i = _WS.match(text, end).end()
        if text[i:i + 1] == ',':
            i = _WS.match(text, i + 1).end()
            continue
        if text[i:i + 1] == '}' and not text[i + 1:].strip(' \t\n\r'):
            break
        return None
    if len(found) != 1 or not isinstance(found[0][2], str):
        return None
    return found[0]


def asked_model(body):
    """The model a request body asks for ('' when it names none) and whether it asks for a stream."""
    obj = _json(body) or {}
    model = obj.get('model') if isinstance(obj.get('model'), str) else ''
    return model, obj.get('stream') is True


def replace_model(body, new):
    """The same body bytes with only the top-level "model" value replaced, or None when that cannot be done exactly."""
    try:
        text = body.decode('utf-8')
    except (UnicodeDecodeError, AttributeError):
        return None
    span = _model_span(text)
    if span is None:
        return None
    start, end, _ = span
    return (text[:start] + json.dumps(new) + text[end:]).encode('utf-8')


# ---------------------------------------------------------------------------------------------------------------
# Reading a stream as it passes (never changing it)
# ---------------------------------------------------------------------------------------------------------------
USAGE_FIELDS = ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')


def _usage_from(obj, usage):
    if isinstance(obj, dict):
        for field in USAGE_FIELDS:
            if isinstance(obj.get(field), int) and not isinstance(obj.get(field), bool):
                usage[field] = obj[field]


class _Watch(object):
    """Follows the server-sent events of a reply: is there content yet, did an error come first, and the usage.

    "Each stream uses the following event flow: 1. message_start ... 2. A series of content blocks ... 3. One or more
    message_delta events ... 4. A final message_stop event", with "any number of ping events", and "The token counts
    shown in the usage field of the message_delta event are cumulative" (streaming page).
    """
    LIMIT = 32 * 1024 * 1024

    def __init__(self):
        self.rest = b''
        self.event = ''
        self.data = []
        self.first = None            # None, 'content', or ('error', category)
        self.usage = {}
        self.blind = False

    def feed(self, chunk):
        if self.blind:
            return
        lines = (self.rest + chunk).split(b'\n')
        self.rest = lines.pop()
        if len(self.rest) > self.LIMIT:                # an endless line: stop looking, keep passing it through
            self.blind = True
            self.first = self.first or 'content'
            return
        for line in lines:
            if line.endswith(b'\r'):
                line = line[:-1]
            if not line:
                self._dispatch()
            elif not line.startswith(b':'):
                field, _, value = line.partition(b':')
                value = value[1:] if value.startswith(b' ') else value
                if field == b'event':
                    self.event = value.decode('utf-8', 'replace')
                elif field == b'data':
                    self.data.append(value)

    def _dispatch(self):
        if not self.data and not self.event:
            return
        obj = _json(b'\n'.join(self.data))
        kind = obj.get('type') if obj and isinstance(obj.get('type'), str) else self.event
        self.event, self.data = '', []
        if kind == 'message_start' and obj and isinstance(obj.get('message'), dict):
            _usage_from(obj['message'].get('usage'), self.usage)
        elif kind == 'message_delta' and obj:
            _usage_from(obj.get('usage'), self.usage)
        if self.first is None and kind not in ('message_start', 'ping'):
            if kind == 'error':
                err = obj.get('error') if obj and isinstance(obj.get('error'), dict) else {}
                self.first = ('error', EVENT_ERRORS.get(err.get('type'), ''))
            else:
                self.first = 'content'


def _piece(resp):
    """The next bytes of a lane's reply as they arrive; b'' at its end. A cut reply raises.

    http.client's read1() returns b'' (no error) when a reply with a Content-Length stops short; a cut chunked
    reply already raises IncompleteRead. Both count as a broken reply here.
    """
    data = resp.read1(65536)
    if not data and resp.length:
        raise http.client.IncompleteRead(b'', resp.length)
    return data


def _pump(resp, out):
    """Read a lane's reply as it arrives and hand each piece to the request's thread."""
    try:
        while True:
            data = _piece(resp)
            if not data:
                out.put(('end', None))
                return
            out.put(('data', data))
    except Exception:  # noqa: BLE001 - any failure means the connection broke; its text is never kept
        out.put(('broke', None))


# ---------------------------------------------------------------------------------------------------------------
# Forwarding
# ---------------------------------------------------------------------------------------------------------------
HOP = frozenset(('connection', 'keep-alive', 'proxy-connection', 'proxy-authorization', 'proxy-authenticate', 'te',
                 'trailer', 'trailers', 'transfer-encoding', 'upgrade', 'content-length'))
NOT_FORWARDED = HOP | frozenset(('host', 'expect', 'authorization', 'x-api-key', 'accept-encoding', 'cookie',
                                 'origin', 'referer'))
NOT_RETURNED = HOP | frozenset(('date', 'server'))
OURS = 'x-switchboard-'
KEY_OK = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._~+/=-]{15,511}\Z')


def _header_text(text, limit=2000):
    return ''.join(c if ' ' <= c <= '~' else '?' for c in str(text))[:limit]


def _lane_url(lane):
    """(scheme, host, port, path prefix) of a lane, or None when it is not https or plain http to this Mac."""
    parts = urlsplit(lane.get('base_url') or '')
    try:
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        return None
    if parts.scheme == 'https' or (parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost', '::1')):
        return parts.scheme, parts.hostname, port, parts.path.rstrip('/')
    return None


class _Unreachable(Exception):
    def __init__(self, what):
        Exception.__init__(self, what)
        self.what = what


class _Upstream(object):
    """One connection to a lane. close() wakes a reader blocked on it."""

    def __init__(self, conn, sock):
        self.conn, self.sock, self.resp = conn, sock, None

    def close(self):
        for step in (lambda: self.sock.shutdown(socket.SHUT_RDWR), lambda: self.resp and self.resp.close(),
                     self.conn.close, self.sock.close):
            try:
                step()
            except Exception:  # noqa: BLE001
                pass


class _Failure(object):
    """A lane that could not serve this request, and what it sent (kept to return it unchanged if all fail)."""

    def __init__(self, lane, category, why, status=None, headers=(), body=None, stream=None, ended='end'):
        self.lane, self.category, self.why = lane, category, why
        self.status, self.headers, self.body, self.stream, self.ended = status, list(headers), body, stream, ended


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class Gateway(object):
    """The gateway. lanes() gives the lanes in order (keys.Keys().list); key_for(name) gives one lane's key.

    There is deliberately no option here (or anywhere) to accept a subscription login.
    """

    def __init__(self, lanes, secret, key_for, port=0, hold=HOLD, connect_timeout=CONNECT_TIMEOUT,
                 stream_first_byte=STREAM_FIRST_BYTE, reply_first_byte=REPLY_FIRST_BYTE,
                 count_first_byte=COUNT_FIRST_BYTE, models_first_byte=MODELS_FIRST_BYTE, read_timeout=READ_TIMEOUT,
                 budget=BUDGET, max_body=MAX_BODY, log_size=500, tls=None):
        if not isinstance(secret, str) or len(secret) < 16:
            raise ValueError('the gateway needs its secret (16 characters or more)')
        self._lanes = lanes
        self._secret = secret
        self._key_for = key_for
        self._want_port = int(port)
        self.hold, self.connect_timeout, self.read_timeout = hold, connect_timeout, read_timeout
        self.first_byte = {'stream': stream_first_byte, 'reply': reply_first_byte, 'count_tokens': count_first_byte,
                           'models': models_first_byte}
        self.budget, self.max_body = budget, max_body
        self._tls = tls
        self._log = collections.deque(maxlen=log_size)
        self._log_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._server = None
        self.port = None

    # ---------- running ----------

    def start(self, poll_interval=0.5):
        """Listen on 127.0.0.1 (never any other address) and serve in background threads. Returns the port."""
        server = _Server((LOOPBACK, self._want_port), _Handler)
        server.gateway = self
        self._server = server
        self.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, args=(poll_interval,), name='switchboard-gateway',
                         daemon=True).start()
        return self.port

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def address(self):
        return self._server.server_address if self._server else None

    def lanes(self):
        lanes = self._lanes() if callable(self._lanes) else self._lanes
        return [dict(x) for x in lanes or () if isinstance(x, dict) and x.get('name')]

    # ---------- the log ----------

    def _note(self, rid, lane, route, model, status, outcome, reason='', usage=None):
        entry = {'time': _now(), 'request': rid, 'lane': lane, 'route': route, 'model': _header_text(model, 128),
                 'status': status, 'outcome': outcome, 'reason': reason}
        for field in USAGE_FIELDS:
            entry[field] = (usage or {}).get(field)
        with self._log_lock:
            self._log.append(entry)

    def log(self):
        """The recent attempts, oldest first. Times, lanes, models, statuses, outcomes and token counts only."""
        with self._log_lock:
            return [dict(e) for e in self._log]

    def stats(self):
        """Per lane: attempts, answers, fail-overs, skips, tokens (from the replies' own usage), last status."""
        out = {}
        for e in self.log():
            s = out.setdefault(e['lane'], {'attempts': 0, 'answered': 0, 'failed_over': 0, 'skipped': 0,
                                           'errors_returned': 0, 'input_tokens': 0, 'output_tokens': 0,
                                           'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0,
                                           'last_time': None, 'last_status': None, 'last_outcome': None})
            if e['outcome'] == 'skipped':
                s['skipped'] += 1
            else:
                s['attempts'] += 1
                if e['outcome'] == 'failed over':
                    s['failed_over'] += 1
                elif e['outcome'] == 'returned error':
                    s['errors_returned'] += 1
                else:
                    s['answered'] += 1
            for field in USAGE_FIELDS:
                s[field] += e.get(field) or 0
            s['last_time'], s['last_status'], s['last_outcome'] = e['time'], e['status'], e['outcome']
        return out

    # ---------- one request ----------

    def precheck(self, h, method):
        """Everything decided from the request line and headers. True when the request is already answered.

        Runs before the body is read, and before "100 Continue" is sent to a client that asked for it.
        """
        pairs = list(h.headers.items())
        why = subscription_login(pairs)                  # first, always, for every method and path
        if why:
            h.refuse(403, 'permission_error', 'subscription-login', SUBSCRIPTION_REFUSAL % why)
            return True
        why = self._foreign(h, pairs)
        if why:
            h.refuse(403, 'permission_error', 'not-local', FOREIGN_REFUSAL % why)
            return True
        path = h.path.partition('?')[0]
        if method == 'HEAD' and path == '/api/hello':     # Claude Code's connection-warming probe
            h.reply(200, [], b'', head=True)
            return True
        why = secret_problem(pairs, self._secret)
        if why:
            h.refuse(401, 'authentication_error', 'secret', SECRET_REFUSAL % why)
            return True
        if (method, path) not in ROUTES:
            h.refuse(404, 'not_found_error', 'path', "Switchboard's gateway serves POST /v1/messages, "
                     "POST /v1/messages/count_tokens and GET /v1/models only.")
            return True
        return False

    def handle(self, h, method):
        if self.precheck(h, method):
            return
        pairs = list(h.headers.items())
        route = ROUTES[(method, h.path.partition('?')[0])]
        body, why = h.read_body(self.max_body)
        if why:
            status, kind = (413, 'request_too_large') if why == 'too large' else (400, 'invalid_request_error')
            return h.refuse(status, kind, 'body', "Switchboard's gateway could not take this request: its body is %s."
                            % why)
        self._forward(h, route, method, body, pairs)

    def _foreign(self, h, pairs):
        if _values(pairs, 'origin') or _values(pairs, 'sec-fetch-site') or _values(pairs, 'sec-fetch-mode'):
            return 'it came from a web page'
        hosts = ('127.0.0.1:%d' % self.port, 'localhost:%d' % self.port)
        given = _values(pairs, 'host')
        if len(given) != 1 or given[0].strip().lower() not in hosts:
            return 'it was not addressed to this gateway on this Mac'
        if not h.path.startswith('/'):
            return 'it was not a plain request'
        return ''

    def _forward(self, h, route, method, body, pairs):
        rid = next(self._ids)
        started = time.time()
        asked, stream = asked_model(body) if route != 'models' else ('', False)
        lanes = self.lanes()
        trail, failures, tried = [], [], 0
        if not lanes:
            return h.refuse(503, 'api_error', 'no-lanes', "Switchboard's gateway has no API key lanes yet. Add one in "
                            "Switchboard (keys are kept in your Keychain).")
        for lane in lanes:
            name = _header_text(lane['name'], 40)
            if tried and time.time() - started > self.budget:
                trail.append((name, 'not tried: out of time'))
                self._note(rid, name, route, asked, None, 'skipped', 'out of time')
                continue
            skip, local, send, sent = self._plan(lane, route, asked, body)
            if skip:
                trail.append((name, skip))
                self._note(rid, name, route, asked, None, 'skipped', skip)
                continue
            if local is not None:
                try:
                    h.reply(200, [('Content-Type', 'application/json')] + self._marks(name, trail), local)
                finally:
                    self._note(rid, name, route, asked, 200, 'answered', 'from its name map')
                return
            try:
                key = self._key_for(lane['name'])
            except Exception:  # noqa: BLE001 - the Keychain's own words are never shown
                key = None
            if not isinstance(key, str) or not KEY_OK.match(key):
                trail.append((name, 'no usable key in the Keychain'))
                self._note(rid, name, route, asked, None, 'skipped', 'no usable key')
                continue
            tried += 1
            result = self._attempt(h, lane, name, route, method, send, pairs, key, stream, asked, sent, trail, rid)
            key = None
            if result is None:
                return
            failures.append(result)
            trail.append((name, result.why))
            self._note(rid, name, route, asked, result.status, 'failed over', result.category)
        if failures:
            best = min(range(len(failures)), key=lambda i: (RANK.get(failures[i].category, 9), i))
            return self._replay(h, failures[best], trail, rid, route, asked)
        h.refuse(404, 'not_found_error', 'no-lane', "Switchboard's gateway has no lane that can serve this request: "
                 + _header_text('; '.join('%s: %s' % t for t in trail)), extra=self._marks('none', trail))

    def _plan(self, lane, route, asked, body):
        """(why skip, local answer, body to send, model sent) for one lane."""
        if _lane_url(lane) is None:
            return 'its base URL is not https (or http to this Mac)', None, None, None
        names = lane.get('names') if isinstance(lane.get('names'), dict) else None
        if route == 'models':
            if names:
                return None, _models_list(names, lane['name']), None, None
            return None, None, b'', None
        if route == 'count_tokens' and lane.get('kind') == 'openrouter':
            return 'OpenRouter lists no token counting endpoint', None, None, None
        if names is None:
            return None, None, body, None
        if asked not in names:
            return 'its name map has no entry for %s' % (asked or 'a request without a model'), None, None, None
        changed = replace_model(body, names[asked])
        if changed is None:
            return 'the model in this request could not be set exactly', None, None, None
        return None, None, changed, names[asked]

    def _marks(self, lane, trail, asked='', sent=None):
        marks = [('x-switchboard-lane', _header_text(lane, 40))]
        if trail:
            marks.append(('x-switchboard-skipped', _header_text('; '.join('%s: %s' % t for t in trail))))
        if sent and sent != asked:
            marks.append(('x-switchboard-model', _header_text('%s -> %s (name map of lane %s)' % (asked, sent, lane))))
        return marks

    def _open(self, lane, method, target, body, pairs, key, first_byte):
        scheme, host, port, prefix = _lane_url(lane)
        if scheme == 'https':
            conn = http.client.HTTPSConnection(host, port or 443, timeout=self.connect_timeout,
                                               context=self._tls or trust.context())
        else:
            conn = http.client.HTTPConnection(host, port or 80, timeout=self.connect_timeout)
        try:
            conn.connect()
        except OSError as e:
            sock = conn.sock                                 # a TLS failure can leave the plain socket open
            conn.close()
            if sock is not None:
                sock.close()
            if isinstance(e, socket.gaierror):
                raise _Unreachable('no such host')
            if isinstance(e, ConnectionRefusedError):
                raise _Unreachable('connection refused')
            if isinstance(e, socket.timeout):
                raise _Unreachable('no connection within %g s' % self.connect_timeout)
            if isinstance(e, ssl.SSLError):
                raise _Unreachable('TLS failed')
            raise _Unreachable('connection failed')
        up = _Upstream(conn, conn.sock)
        try:
            up.sock.settimeout(first_byte)
            conn.putrequest(method, prefix + target, skip_accept_encoding=True)
            drop = {n.strip().lower() for v in _values(pairs, 'connection') for n in v.split(',')}
            for k, v in pairs:
                low = k.lower()
                if low in NOT_FORWARDED or low in drop or low.startswith(OURS) or low.startswith('sec-'):
                    continue
                conn.putheader(k, v)
            conn.putheader('Accept-Encoding', 'identity')
            if lane.get('auth') == 'x-api-key' or (not lane.get('auth') and lane.get('kind') == 'anthropic'):
                conn.putheader('x-api-key', key)
            else:
                conn.putheader('Authorization', 'Bearer ' + key)
            if method != 'GET':
                conn.putheader('Content-Length', str(len(body)))
            conn.endheaders(body if method != 'GET' else None)
            up.resp = conn.getresponse()
            up.sock.settimeout(self.read_timeout)
            return up
        except socket.timeout:
            up.close()
            raise _Unreachable('no reply within %g s' % first_byte)
        except (OSError, http.client.HTTPException, ValueError):
            up.close()
            raise _Unreachable('connection broke before a reply')

    def _attempt(self, h, lane, name, route, method, body, pairs, key, stream, asked, sent, trail, rid):
        """Try one lane. None when its reply went to Claude Code (from then on, no other lane); else a _Failure."""
        if route == 'messages':
            first = self.first_byte['stream' if stream else 'reply']
        else:
            first = self.first_byte[route]
        try:
            up = self._open(lane, method, h.path, body, pairs, key, first)
        except _Unreachable as e:
            return _Failure(name, 'unreachable', 'unreachable: ' + e.what)
        resp = up.resp
        status = resp.status
        headers = [(k, v) for k, v in resp.getheaders() if k.lower() not in NOT_RETURNED and
                   not k.lower().startswith(OURS)]
        if 300 <= status <= 399:
            up.close()
            return _Failure(name, 'unreachable', 'unreachable: answered with a redirect (%d), not followed' % status)
        if status == 200 and 'text/event-stream' in (resp.getheader('content-type') or '').lower():
            return self._stream(h, up, lane, name, route, asked, sent, headers, trail, rid)
        data, state = self._read_whole(resp)
        if state == 'broke':
            up.close()
            return _Failure(name, 'unreachable', 'unreachable: connection broke during the reply')
        if state == 'whole':
            up.close()
            category = classify(status, data, lane.get('kind'))
            if category:
                return _Failure(name, category, '%s (%d)' % (category, status), status, headers, data)
            usage = {}
            if route == 'messages' and status == 200:
                _usage_from((_json(data) or {}).get('usage'), usage)
            outcome = 'answered' if status < 400 else 'returned error'
            try:
                h.reply(status, headers + self._marks(name, trail, asked, sent), data)
            except OSError:
                outcome = 'client closed'
                h.abort()
            self._note(rid, name, route, asked, status, outcome, '', usage)
            return None
        # Too large to hold: it goes to Claude Code as it arrives, from this lane only.
        outcome = 'answered'
        try:
            h.start_stream(status, headers + self._marks(name, trail, asked, sent))
            h.chunk(data)
            while True:
                more = _piece(resp)
                if not more:
                    h.end_stream()
                    break
                h.chunk(more)
        except (http.client.HTTPException, socket.timeout, ValueError):
            outcome = 'broke mid-reply'
            h.abort()
        except OSError:
            outcome = 'client closed'
            h.abort()
        finally:
            up.close()
        self._note(rid, name, route, asked, status, outcome)
        return None

    def _read_whole(self, resp):
        chunks, total = [], 0
        try:
            while True:
                data = _piece(resp)
                if not data:
                    return b''.join(chunks), 'whole'
                chunks.append(data)
                total += len(data)
                if total > MAX_HELD_REPLY:
                    return b''.join(chunks), 'big'
        except Exception:  # noqa: BLE001
            return b''.join(chunks), 'broke'

    def _stream(self, h, up, lane, name, route, asked, sent, headers, trail, rid):
        pieces = queue.Queue()
        threading.Thread(target=_pump, args=(up.resp, pieces), daemon=True).start()
        watch, held = _Watch(), []
        deadline = time.time() + self.hold
        while True:                                          # hold the start back until the first content event
            wait = deadline - time.time()
            if wait <= 0:
                break
            try:
                kind, data = pieces.get(timeout=wait)
            except queue.Empty:
                break
            if kind == 'data':
                held.append(data)
                watch.feed(data)
                if watch.first is None:
                    continue
                if watch.first != 'content' and watch.first[1]:
                    up.close()
                    category = watch.first[1]
                    return _Failure(name, category, '%s (error event before any content)' % category, 200, headers,
                                    stream=b''.join(held), ended='end')
                break                                        # content, or an error another lane would not fix
            up.close()
            ended = 'end' if kind == 'end' else 'broke'
            return _Failure(name, 'empty reply', 'reply %s before any content' % ('ended' if ended == 'end' else
                            'broke'), 200, headers, stream=b''.join(held), ended=ended)
        outcome = 'answered'                                 # committed: from here on, this lane only
        try:
            h.start_stream(200, headers + self._marks(name, trail, asked, sent))
            for data in held:
                h.chunk(data)
            while True:
                kind, data = pieces.get()
                if kind == 'data':
                    watch.feed(data)
                    h.chunk(data)
                elif kind == 'end':
                    h.end_stream()
                    break
                else:
                    outcome = 'broke mid-reply'
                    h.abort()
                    break
        except OSError:
            outcome = 'client closed'
            h.abort()
        finally:
            up.close()
        self._note(rid, name, route, asked, 200, outcome, '', watch.usage if route == 'messages' else None)
        return None

    def _replay(self, h, failure, trail, rid, route, asked):
        """Every lane failed: send the most useful failure as it came (or say plainly that none answered)."""
        marks = [('x-switchboard-lane', 'none'), ('x-switchboard-error-from', _header_text(failure.lane, 40)),
                 ('x-switchboard-skipped', _header_text('; '.join('%s: %s' % t for t in trail)))]
        if failure.body is not None:
            h.reply(failure.status, failure.headers + marks, failure.body)
        elif failure.stream is not None:
            try:
                h.start_stream(200, failure.headers + marks)
                h.chunk(failure.stream)
                if failure.ended == 'end':
                    h.end_stream()
                else:
                    h.abort()
            except OSError:
                h.abort()
        else:
            h.reply(502, [('Content-Type', 'application/json')] + marks, error_body(
                'api_error', "Switchboard's gateway could not reach any lane: "
                + _header_text('; '.join('%s: %s' % t for t in trail))))
        self._note(rid, 'none', route, asked, failure.status or 502, 'returned error', failure.category)


def _models_list(names, lane):
    """A models list in the Claude API's shape, from a lane's name map (no request upstream)."""
    data = [{'type': 'model', 'id': asked, 'display_name': asked,
             'description': 'Through lane %s as %s' % (lane, sent)} for asked, sent in names.items()]
    return json.dumps({'data': data, 'has_more': False, 'first_id': data[0]['id'] if data else None,
                       'last_id': data[-1]['id'] if data else None}).encode()


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass                                                 # never print a traceback (it could hold anything)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'switchboard-gateway'
    sys_version = ''
    timeout = READ_TIMEOUT + 30                              # an idle connection from Claude Code is closed after this
    disable_nagle_algorithm = True                           # small stream events leave at once

    def log_message(self, *args):
        pass                                                 # nothing about a request is printed anywhere

    def handle_expect_100(self):
        """A client that asks before sending its body hears a refusal first, never "100 Continue"."""
        self.sent = False
        self.chunked = False
        try:
            if self.server.gateway.precheck(self, self.command):
                return False
        except Exception:  # noqa: BLE001
            self.close_connection = True
            return False
        return BaseHTTPRequestHandler.handle_expect_100(self)

    def _go(self, method):
        self.sent = False
        self.chunked = False
        try:
            self.server.gateway.handle(self, method)
        except Exception:  # noqa: BLE001 - the exception's text is never sent or logged
            if not self.sent:
                try:
                    self.refuse(502, 'api_error', 'failed', "Switchboard's gateway failed on this request.")
                except Exception:  # noqa: BLE001
                    pass
            self.close_connection = True

    def do_GET(self):
        self._go('GET')

    def do_POST(self):
        self._go('POST')

    def do_HEAD(self):
        self._go('HEAD')

    def do_PUT(self):
        self._go('PUT')

    def do_DELETE(self):
        self._go('DELETE')

    def do_PATCH(self):
        self._go('PATCH')

    def do_OPTIONS(self):
        self._go('OPTIONS')

    # ---------- reading ----------

    def read_body(self, cap):
        """(body, '') or (None, what is wrong). Content-Length or chunked; never more than cap bytes."""
        encoding = ','.join(_values(list(self.headers.items()), 'transfer-encoding')).lower()
        if 'chunked' in encoding:
            parts, total = [], 0
            while True:
                line = self.rfile.readline(1024)
                try:
                    size = int(line.split(b';')[0].strip(), 16)
                except ValueError:
                    return None, 'badly chunked'
                if size == 0:
                    while self.rfile.readline(1024) not in (b'\r\n', b'\n', b''):
                        pass
                    return b''.join(parts), ''
                total += size
                if total > cap:
                    return None, 'too large'
                data = self.rfile.read(size)
                if len(data) != size:
                    return None, 'cut short'
                parts.append(data)
                if self.rfile.readline(3) not in (b'\r\n', b'\n'):
                    return None, 'badly chunked'
        lengths = _values(list(self.headers.items()), 'content-length')
        if not lengths:
            return b'', ''
        try:
            size = int(lengths[0])
        except ValueError:
            return None, 'of unknown length'
        if len(set(lengths)) != 1 or size < 0:
            return None, 'of unknown length'
        if size > cap:
            return None, 'too large'
        data = self.rfile.read(size)
        if len(data) != size:
            return None, 'cut short'
        return data, ''

    # ---------- writing ----------

    def reply(self, status, headers, body, head=False, close=False):
        self.sent = True
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        if close:
            self.send_header('Connection', 'close')
            self.close_connection = True
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def refuse(self, status, kind, tag, message, extra=()):
        """The gateway's own answer; nothing was forwarded. The connection closes (the body may be unread)."""
        self.reply(status, [('Content-Type', 'application/json'), ('x-should-retry', 'false'),
                            ('x-switchboard-refused', tag)] + list(extra), error_body(kind, message),
                   head=self.command == 'HEAD', close=True)

    def start_stream(self, status, headers):
        self.sent = True
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        if self.request_version == 'HTTP/1.1':
            self.chunked = True
            self.send_header('Transfer-Encoding', 'chunked')
        else:
            self.send_header('Connection', 'close')
            self.close_connection = True
        self.end_headers()

    def chunk(self, data):
        if not data:
            return
        if self.chunked:
            self.wfile.write(b'%X\r\n' % len(data) + data + b'\r\n')
        else:
            self.wfile.write(data)
        self.wfile.flush()

    def end_stream(self):
        if self.chunked:
            self.wfile.write(b'0\r\n\r\n')
            self.wfile.flush()

    def abort(self):
        """End the connection without finishing the reply, so Claude Code sees it broke (as the lane's did)."""
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


# ---------------------------------------------------------------------------------------------------------------
# Settings, the secret, and how `switchboard claude` starts Claude Code
# ---------------------------------------------------------------------------------------------------------------

def port():
    """The gateway's port: settings.json "gateway_port", else 7797."""
    try:
        value = int(core.settings().get('gateway_port') or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return value if 1024 <= value <= 65535 else DEFAULT_PORT


def secret():
    """This install's gateway secret: made once at random, kept in data/gateway-secret (mode 0600)."""
    path = core.data_path('gateway-secret')
    for _ in range(2):
        try:
            with open(path) as f:
                value = f.read().strip()
            if len(value) >= 32:
                return value
            raise core.SwitchError('The gateway secret file is damaged. Remove %s to make a new one.' % path)
        except FileNotFoundError:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            draft = '%s.%d.%s' % (path, os.getpid(), _secrets.token_hex(4))
            fd = os.open(draft, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write('sbgw-' + _secrets.token_urlsafe(32) + '\n')
            try:
                os.link(draft, path)                     # appears whole, and only if no other start made one first
            except FileExistsError:
                pass
            finally:
                os.remove(draft)
    raise core.SwitchError('Could not read the gateway secret.')


# Variables taken out of the started Claude Code's environment, and why (https://code.claude.com/docs/en/authentication,
# "Authentication precedence", checked 2026-11-13):
# - CLAUDE_CODE_USE_BEDROCK / _VERTEX / _FOUNDRY rank first ("Cloud provider credentials, when ... is set"), and the
#   env-vars page lists CLAUDE_CODE_USE_MANTLE, CLAUDE_CODE_USE_ANTHROPIC_AWS and (checked again 2026-11-19)
#   CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD as provider choices too: any of them would send requests somewhere other
#   than ANTHROPIC_BASE_URL.
# - ANTHROPIC_API_KEY: "Sent as the X-Api-Key header"; left in, a real key would also reach the gateway (refused there)
#   and an interactive session would ask to approve it.
# - CLAUDE_CODE_OAUTH_TOKEN: a subscription login ("A long-lived OAuth token generated by `claude setup-token`"). It
#   ranks below ANTHROPIC_AUTH_TOKEN, but it has no business in a session meant for API keys.
REMOVED_FROM_ENV = ('ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX',
                    'CLAUDE_CODE_USE_FOUNDRY', 'CLAUDE_CODE_USE_MANTLE', 'CLAUDE_CODE_USE_ANTHROPIC_AWS',
                    'CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD')


def claude_command(args=(), env=None, gateway_port=None, gateway_secret=None):
    """(argv, environment) that start Claude Code against the gateway, for one terminal only.

    The environment is a copy of `env` (the caller passes the terminal's own) with two variables set:
    - ANTHROPIC_BASE_URL: "Override the API endpoint to route requests through a proxy or gateway" (env-vars page).
    - ANTHROPIC_AUTH_TOKEN: "Custom value for the `Authorization` header (the value you set here will be prefixed with
      `Bearer `)"; "With `ANTHROPIC_AUTH_TOKEN`, the variable takes precedence immediately" over a saved login
      (llm-gateway-connect page).
    and REMOVED_FROM_ENV taken out. Nothing is written anywhere, so plain `claude` stays on the subscription.
    """
    out = dict(env or {})
    for name in REMOVED_FROM_ENV:
        out.pop(name, None)
    out['ANTHROPIC_BASE_URL'] = 'http://%s:%d' % (LOOPBACK, gateway_port or port())
    out['ANTHROPIC_AUTH_TOKEN'] = gateway_secret or secret()
    return ['claude'] + [str(a) for a in args], out


# ---------------------------------------------------------------------------------------------------------------
# Limits, each with the doc sentence it rests on (checked 2026-11-13)
# ---------------------------------------------------------------------------------------------------------------
LIMITS = (
    ('Claude models only. A lane name map may only point at Claude models.',
     'https://code.claude.com/docs/en/llm-gateway',
     'Anthropic doesn\'t endorse, maintain, or audit third-party gateway products, and doesn\'t support routing '
     'Claude Code to non-Claude models through any gateway.'),
    ('API keys only: a subscription login is refused and never forwarded.',
     'https://code.claude.com/docs/en/legal-and-compliance',
     'Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to '
     'route requests through Free, Pro, Max, or Ultra plan credentials on behalf of their users.'),
    ('Requests through the gateway are billed per token to the key\'s owner; subscription limits do not apply.',
     'https://code.claude.com/docs/en/llm-gateway',
     'That traffic is billed per token to whoever owns the credential the gateway forwards'),
    ('OpenRouter is guaranteed with Claude Code only on its Anthropic first-party provider; the gateway does not '
     'change OpenRouter\'s provider choice (that would change the request).',
     'https://openrouter.ai/docs/cookbook/coding-agents/claude-code-integration',
     'Claude Code with OpenRouter is only guaranteed to work with the Anthropic first-party provider.'),
    ('OpenRouter serves fast mode only through Anthropic itself.',
     'https://openrouter.ai/docs/cookbook/coding-agents/claude-code-integration',
     'Fast mode is only served by the Anthropic first-party provider, since other providers (e.g. Amazon Bedrock, '
     'Google Vertex) do not support it.'),
    ('OpenRouter does not forward thinking budgets and does not enforce preserved thinking on Claude 5.5 models.',
     'https://openrouter.ai/docs/cookbook/evaluate-and-optimize/model-migrations/claude-5-5',
     'Thinking budgets are not forwarded'),
    ('OpenRouter passes thinking blocks and tool use through, and maps model names on its side; the gateway still '
     'sends it only names from the lane\'s own map.',
     'https://openrouter.ai/docs/cookbook/coding-agents/claude-code-integration',
     'OpenRouter\'s "Anthropic Skin" behaves exactly like the Anthropic API. It handles model mapping and passes '
     'through advanced features like "Thinking" blocks and native tool use.'),
    ('OpenRouter lists no token counting endpoint, so on an OpenRouter lane Claude Code estimates context use.',
     'https://code.claude.com/docs/en/llm-gateway-protocol',
     'Token-counting endpoints are the only optional ones: when they\'re absent, Claude Code falls back to a '
     'character-based estimate of context usage.'),
    ('Remote Control is off in sessions started through the gateway.',
     'https://code.claude.com/docs/en/env-vars',
     'As of v2.1.196, Remote Control is disabled when this points at a host other than `api.anthropic.com`'),
    ('Voice dictation is off while the gateway token is set.',
     'https://code.claude.com/docs/en/llm-gateway-connect',
     'They are unavailable while `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or an `apiKeyHelper` is active.'),
    ('Fast mode\'s availability check skips the gateway; with a bearer token it reports fast mode as disabled unless '
     'CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK=1 is set.',
     'https://code.claude.com/docs/en/llm-gateway-connect',
     'The availability check requires a claude.ai login or an API key; with only a bearer token, Claude Code treats '
     'fast mode as disabled without sending the check'),
    ('A base URL or token set in a Claude Code settings file wins over `switchboard claude`.',
     'https://code.claude.com/docs/en/llm-gateway-connect',
     'When both a shell export and a settings-file `env` block set the same variable, the settings-file value '
     'applies.'),
    ('A Claude apps gateway sign-in outranks the gateway token.',
     'https://code.claude.com/docs/en/authentication',
     'When a gateway session exists, the CLI authenticates with the gateway token even if `CLAUDE_CODE_USE_BEDROCK`, '
     '`CLAUDE_CODE_USE_VERTEX`, or `CLAUDE_CODE_USE_FOUNDRY` is set, and credential sources above such as the bearer '
     'token, API key, `apiKeyHelper`, and profiles are not used.'),
    ('Managed settings that force a login method cannot be combined with the gateway token.',
     'https://code.claude.com/docs/en/llm-gateway-connect',
     'Managed settings include `forceLoginMethod` or `forceLoginOrgUUID`, which cannot coexist with '
     '`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or `apiKeyHelper`'),
    ('With a saved claude.ai login, Claude Code may warn at start that two credential sources are set; the gateway '
     'token is the one used.',
     'https://code.claude.com/docs/en/llm-gateway-connect',
     'With `ANTHROPIC_AUTH_TOKEN`, the variable takes precedence immediately.'),
    ('Moving to another lane mid-conversation can drop earlier thinking blocks; Claude Code removes them and retries.',
     'https://code.claude.com/docs/en/llm-gateway-protocol',
     'When the upstream rejects a thinking signature, including with a `400` whose message says the block is '
     '`bound to a different conversation`, Claude Code removes earlier thinking blocks from the request, retries, '
     'and keeps them out of every later request.'),
    ('When every lane fails and the most useful error left is a lane\'s own 401, it comes back unchanged; Claude Code '
     'then shows it after "Please run /login", which does not help: the lane\'s key needs fixing.',
     'https://code.claude.com/docs/en/errors',
     'If `ANTHROPIC_BASE_URL` points at an LLM gateway, the text after `401` is your gateway\'s message rather than '
     'Anthropic\'s, and `/login` doesn\'t change it.'),
    ('Sessions through a gateway are not eligible for the HIPAA configuration.',
     'https://code.claude.com/docs/en/llm-gateway-protocol',
     'Sessions that go through a gateway aren\'t eligible for the HIPAA configuration.'),
)


def limits():
    """The gateway's limits, each with the doc sentence it rests on and where that sentence is."""
    return [{'limit': text, 'url': url, 'quote': quote} for text, url, quote in LIMITS]


def serve(block=True):
    """Start the gateway on its port with this Mac's lanes (Keychain), on its own. The page runs it through lanes.py."""
    from . import keys
    store = keys.default()
    gateway = Gateway(store.list, secret(), store._secret, port=port())
    gateway.start()
    while block:
        time.sleep(3600)
    return gateway
