"""Switchboard's other assistants: one board and one set of commands for every assistant the person already has.

Beside the account code (switchboard/accounts), never inside it. This package reaches the rest of Switchboard through
two modules only: switchboard.core (its atomic writes, its locks, its log and its events) and switchboard.accounts.api
(read functions). Anything else of Switchboard's that a provider needs (the session hooks, the chat index) is reached
through bridge.py, which answers "not here" on a Switchboard that lacks it.

The registry below names each provider's adapter, in the order they are offered when the person has set no order of
their own. A new provider is one line here, one adapter module, one facts table, one stand-in for the tests and the
shared contract tests (tests/test_providers_contract.py). Adapters are imported only when asked for, so a command that
needs one provider never loads another's code, and an adapter whose module fails to load never stops the others.
"""
import importlib
from typing import Dict, Optional, Tuple

from .contract import ID_RE, Provider, ProviderError, Reach

# (id, module in this package, class): the providers this build knows.
ADAPTERS = (
    ('claude', 'claude', 'ClaudeProvider'),
    ('codex', 'codex', 'CodexProvider'),
)

# Why an adapter this build names could not be loaded, by id: the error's type name only (never its text, which can
# hold a path). The commands say it in one line ("Codex's code did not load here: SyntaxError.").
LOAD_ERRORS: Dict[str, str] = {}


def ids() -> Tuple[str, ...]:
    """Every provider id this build knows, in the registry's order."""
    return tuple(pid for pid, _, _ in ADAPTERS)


def known(word: str) -> bool:
    """Is word a provider's id? (Only ids from the registry are ever used to pick code or build a file name.)"""
    return isinstance(word, str) and bool(ID_RE.match(word)) and word in ids()


def adapter_class(pid: str) -> Optional[type]:
    """The adapter class for pid, or None when this build does not carry its module or the module fails to load (any
    error at import, a syntax error on an older Python among them: one broken adapter must not stop every command).
    The reason is kept in LOAD_ERRORS."""
    for known_id, module, name in ADAPTERS:
        if known_id == pid:
            try:
                mod = importlib.import_module('.' + module, __name__)
            except Exception as e:      # any failure to load is an answer, never a crash
                LOAD_ERRORS[pid] = type(e).__name__
                return None
            cls = getattr(mod, name, None)
            if isinstance(cls, type) and issubclass(cls, Provider):
                LOAD_ERRORS.pop(pid, None)
                return cls
            LOAD_ERRORS[pid] = 'NoAdapter'
            return None
    return None


def name_of(pid: str, default: Optional[str] = None) -> str:
    """The name a person reads for pid ('Codex'), as its adapter class says it, for shared code with no adapter at
    hand (catalog.py, routes.py); else default, else the id with a capital."""
    cls = adapter_class(pid) if known(pid) else None
    return getattr(cls, 'name', '') or (default if default is not None else str(pid).capitalize())


def load(pid: str, reach: Reach) -> Provider:
    """The adapter for pid, made with reach. Raises ProviderError('not_found') for an id the registry does not have
    or whose module this build does not carry or cannot load."""
    cls = adapter_class(pid) if known(pid) else None
    if cls is None:
        raise ProviderError('not_found', 'Switchboard does not know an assistant called "%s".' % str(pid)[:32])
    return cls(reach)


def load_all(reach: Reach) -> Tuple[Provider, ...]:
    """Every adapter this build carries and can load, in the registry's order."""
    return tuple(cls(reach) for cls in (adapter_class(pid) for pid in ids()) if cls is not None)
