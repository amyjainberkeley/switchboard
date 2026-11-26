"""A made-up third assistant, Tern, written on the test side only, to show that any assistant is one adapter.

TernProvider is a contract.Provider subclass with its own facts (its program's name, how it signs in, what it cannot
do, its rule file, where its memory lives) and answers a test sets on the class. registered() puts it in the
package's registry the way the package lists its own adapters: one (id, module, class) entry in providers.ADAPTERS,
its module importable as switchboard.providers.fake_tern. No shared module of the package is edited for it, and
nothing here is a real program: its "program" is a stand-in file that no test runs.

Made-up data only: the name Tern, the vendor Tern Labs and the plan Tern Team exist nowhere.
"""
import os
import sys
from contextlib import contextmanager

from switchboard import providers
from switchboard.providers import contract, run as provider_run, words
from switchboard.providers.contract import (ChatRef, Event, Found, Launch, Limits, Program, Provider, ReadInfo, Seat,
                                            Transcript, Window)

PID = 'tern'
MODULE = 'fake_tern'
HOUR, DAY = 3600.0, 86400.0


class TernProvider(Provider):
    """Tern: one login, one 3-day window, chats a test hands it, and a start that is only recorded."""
    id = PID
    name = 'Tern'
    vendor = 'Tern Labs'
    can = frozenset((contract.DETECT, contract.SEATS, contract.LIMITS, contract.CHATS, contract.READ, contract.START))
    app_name = 'Tern Code'
    sign_in = 'run tern auth in Tern Code'
    cannot = {contract.JOB: 'Tern takes no jobs from Switchboard.'}
    own_rules = '~/.tern/RULES.md'
    rule_files = ('TERN.md',)

    # What a test sets (on the class, since the package makes the adapter itself).
    program_path = None             # a stand-in file; None: Tern is not on this Mac
    signed_in = True
    used = 30.0                     # its 3-day window, percent used
    read_ago = 60.0
    resets_in = DAY
    chat_list = ()                  # ChatRefs
    started = []                    # each start(): (folder, how, the note's path)

    @classmethod
    def reset(cls):
        cls.program_path, cls.signed_in, cls.used, cls.read_ago, cls.resets_in = None, True, 30.0, 60.0, DAY
        cls.chat_list, cls.started = (), []

    def _program(self):
        if not self.program_path:
            return None
        return Program(path=self.program_path, version='1.2.0', source='path', where='your PATH')

    def detect(self):
        prog = self._program()
        return Found(PID, installed=prog is not None, program=prog, programs=(prog,) if prog else (),
                     signed_in=self.signed_in if prog else None, tested=True)

    def seats(self):
        return (Seat(PID, PID, 'Tern Team', plan='team', in_use=True),)

    def limits(self, ask=False):
        if ask:
            return self._unsupported(contract.ASK)
        now = self.reach.wall()
        w = Window('3-day', self.used, now + self.resets_in, 3 * 24 * 60, 95.0)
        return (Limits(PID, PID, (w,), now - self.read_ago, 'file', plan='team'),)

    def chats(self, folder):
        here = os.path.realpath(folder)
        return tuple(c for c in self.chat_list if os.path.realpath(c.folder) == here)

    def read(self, chat, limits=contract.ReadLimits()):
        return Transcript(chat, (Event(contract.PERSON, 'made-up request for Tern'),), ReadInfo(reader='tern'))

    def start(self, folder, handoff, how):
        prog = self._program()
        TernProvider.started.append((folder, how, handoff.path))
        first = words.FIRST_WORDS.format(hop=handoff.hop, path=handoff.path, from_name='another assistant')
        argv = (prog.path, first)
        return Launch(PID, how, prog, argv, env=provider_run.child_env(self.reach.env), cwd=folder,
                      line=provider_run.shell_line(argv, folder, self.reach.env), first_words=first)


def chat(folder, cid='dddddddd-0000-4000-8000-00000000000d', name='Tern chat', last_at=None):
    return ChatRef(PID, cid, folder, '/made-up/tern-%s.jsonl' % cid[:8], name=name, last_at=last_at)


@contextmanager
def registered():
    """TernProvider in the registry, as one more (id, module, class) entry of providers.ADAPTERS, its module this
    one under the name the registry imports; both taken out again after."""
    full = 'switchboard.providers.' + MODULE
    before = providers.ADAPTERS
    providers.ADAPTERS = before + ((PID, MODULE, 'TernProvider'),)
    sys.modules[full] = sys.modules[__name__]
    TernProvider.reset()
    try:
        yield TernProvider
    finally:
        providers.ADAPTERS = before
        sys.modules.pop(full, None)
        providers.LOAD_ERRORS.pop(PID, None)
        TernProvider.reset()
