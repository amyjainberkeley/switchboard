import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Doing a takeaway (switchboard/optimize.act): only the router's own functions make the change (its switches,
# router.settings.set_switch; the helpers' cache, router.cache_setting.apply and undo), each with its record and
# exact way back. Off after on gives the file back byte for byte; a refusal is one plain sentence; nothing is written
# for a person who never pressed. Every folder is a throwaway one; the Claude Code version is given, never looked up.
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, HERE)

from switchboard import core, optimize  # noqa: E402
from switchboard.router import cache_setting as cs  # noqa: E402
from switchboard.router import cache_words as CW  # noqa: E402
from switchboard.router import settings as rsettings  # noqa: E402
from test_providers_words import problems  # noqa: E402
from test_router_cache_setting import NEW, OLD  # noqa: E402  (the cache setting's own stand-in versions)


# What the page shows when a takeaway offers each press: act() makes a change only for a press a takeaway now offers.
WHO = [{'id': 'who', 'change': {'key': 'dry', 'label': 'Try it', 'estimate': None}, 'can': True}]
TRYIT = 'switchboard.router.tryit'         # the router's own trial-run module; not in this tree
STORING = [{'id': 'storing', 'change': {'key': 'cache_hour', 'label': 'Turn on', 'estimate': None}, 'can': True}]


def one_sentence(test, says):
    test.assertIsInstance(says, str)
    test.assertTrue(says.strip())
    test.assertNotIn('\n', says)
    test.assertEqual(problems(says), [])


class Clean(unittest.TestCase):
    def setUp(self):
        p = core.data_path('router')
        if os.path.isdir(p):
            shutil.rmtree(p)
        self.addCleanup(lambda: os.path.isdir(p) and shutil.rmtree(p))


class TheTrialRun(Clean):
    """The trial run goes through the router's own module (switchboard.router.tryit: on, off, state), and only it.
    That module is not in this tree: a stand-in is put in sys.modules for each test and taken out after."""

    ON = ['Trial run on.', 'It notes new Claude Code chats only.']
    OFF = ['Trial run off.', 'Routing is back as it was.']

    def setUp(self):
        super().setUp()
        p = mock.patch.object(rsettings, 'set_switch', lambda *a: self.fail('optimize set a router switch itself'))
        p.start()
        self.addCleanup(p.stop)

    def standin(self, on=None, off=None, state=None):
        m = types.ModuleType(TRYIT)
        m.calls, m.trying = [], False

        def _on():
            m.calls.append('on')
            m.trying = True
            return list(self.ON) if on is None else on()

        def _off():
            m.calls.append('off')
            m.trying = False
            return list(self.OFF) if off is None else off()
        m.on, m.off = _on, _off
        m.state = state or (lambda: {'trying': m.trying, 'was_on': False, 'was_trial': False})
        p = mock.patch.dict(sys.modules, {TRYIT: m})
        p.start()
        self.addCleanup(p.stop)
        return m

    def test_try_calls_on_once_and_off_calls_off_once(self):
        m = self.standin()
        got = optimize.act('who', 'try', shown=WHO)
        self.assertEqual(got, {'done': True, 'says': self.ON[0], 'more': self.ON[1:]})
        self.assertEqual(m.calls, ['on'])
        back = optimize.act('who', 'off')
        self.assertEqual(back, {'done': True, 'says': self.OFF[0], 'more': self.OFF[1:]})
        self.assertEqual(m.calls, ['on', 'off'])
        self.assertFalse(os.path.exists(core.data_path('router')))           # this module wrote nothing

    def test_the_state_follows_the_modules_state(self):
        self.standin()
        week = {'cuts': {'who': [{'label': 'Workflows', 'share': 20}, {'label': 'Helpers', 'share': 50},
                                 {'label': 'You, in chat', 'share': 30}]}}
        shown = optimize.takeaways(week, optimize.router_state())
        self.assertEqual([(t['id'], t['state'], t['can']) for t in shown], [('who', 'off', True)])
        self.assertTrue(optimize.act('who', 'try', shown=shown)['done'])
        self.assertEqual(optimize.takeaways(week, optimize.router_state())[0]['state'], 'trying')
        self.assertTrue(optimize.act('who', 'off')['done'])
        self.assertEqual(optimize.takeaways(week, optimize.router_state())[0]['state'], 'off')

    def test_without_the_module_nothing_is_pressed_and_nothing_written(self):
        with mock.patch.dict(sys.modules, {TRYIT: None}):
            for do in ('try', 'off'):
                with self.subTest(do=do):
                    self.assertEqual(optimize.act('who', do, shown=WHO), {'done': False, 'says': optimize.NO_SUCH})
        self.assertFalse(os.path.exists(core.data_path('router')))

    def test_a_failure_in_the_module_is_one_sentence(self):
        def boom():
            raise RuntimeError('stand-in failure')
        self.standin(on=boom, off=boom)
        got = optimize.act('who', 'try', shown=WHO)
        self.assertEqual(got, {'done': False, 'says': optimize.TRY_FAILED})
        one_sentence(self, got['says'])
        got = optimize.act('who', 'off')
        self.assertEqual(got, {'done': False, 'says': optimize.STOP_FAILED})
        one_sentence(self, got['says'])
        self.assertFalse(os.path.exists(core.data_path('router')))

    def test_odd_lines_from_the_module_still_say_one_sentence(self):
        for lines in (None, [], 'x', [None, 3, '']):
            with self.subTest(lines=lines):
                self.standin(on=lambda: lines, off=lambda: lines)
                self.assertEqual(optimize.act('who', 'try', shown=WHO),
                                 {'done': True, 'says': optimize.DID_TRY, 'more': []})
                self.assertEqual(optimize.act('who', 'off'), {'done': True, 'says': optimize.DID_STOP, 'more': []})

    def test_on_is_not_offered_for_a_trial_run(self):
        got = optimize.act('who', 'on', shown=WHO)
        self.assertFalse(got['done'])
        one_sentence(self, got['says'])
        self.assertFalse(os.path.exists(core.data_path('router')))

    def test_unknown_id_or_press(self):
        for id_, do in (('nope', 'try'), ('who', 'maybe'), ('rereading', 'on'), (None, None), ('who', ['try'])):
            with self.subTest(id=id_, do=do):
                got = optimize.act(id_, do, shown=WHO)
                self.assertFalse(got['done'])
                one_sentence(self, got['says'])
        self.assertFalse(os.path.exists(core.data_path('router')))

    def test_a_press_no_takeaway_offers_writes_nothing(self):
        advice = [dict(WHO[0], change=None, can=False)]
        cannot = [dict(WHO[0], can=False)]
        for shown in (None, [], advice, cannot, STORING, ['who'], [{'id': 'who'}]):
            with self.subTest(shown=shown):
                got = optimize.act('who', 'try', shown=shown)
                self.assertEqual(got, {'done': False, 'says': optimize.NO_SUCH})
        self.assertFalse(os.path.exists(core.data_path('router')))


class TheHelpersCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sb-optimize-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cd = os.path.join(self.tmp, 'claude')
        os.makedirs(self.cd)
        self.data = os.path.join(self.tmp, 'data')
        self.path = os.path.join(self.cd, 'settings.json')

    def kw(self, **over):
        k = {'claude_dir': self.cd, 'data_dir': self.data, 'versions': NEW, 'environ': {}, 'managed_dir': ''}
        k.update(over)
        return k

    def put(self, raw):
        with open(self.path, 'wb') as f:
            f.write(raw.encode('utf-8'))

    def raw(self):
        with open(self.path, 'rb') as f:
            return f.read()

    def test_on_then_off_gives_the_file_back_byte_for_byte(self):
        before = '{\n  "model": "opus",\n  "env": {\n    "A": "1"\n  }\n}\n'
        self.put(before)
        calls = []
        real_apply, real_undo = cs.apply, cs.undo
        # undo() is apply(None) inside the module, so the stand-in for apply takes its arguments as given
        with mock.patch.object(cs, 'apply', lambda *a, **k: calls.append(('apply',) + a[:1]) or real_apply(*a, **k)), \
                mock.patch.object(cs, 'undo', lambda **k: calls.append(('undo',)) or real_undo(**k)):
            got = optimize.act('storing', 'on', cache=self.kw(), shown=STORING)
            self.assertEqual(got, {'done': True, 'says': CW.DID_ADD.format(value='1h')})
            self.assertEqual(cs.status(**self.kw())['state'], '1h')
            back = optimize.act('storing', 'off', cache=self.kw())
            self.assertEqual(back, {'done': True, 'says': CW.DID_REMOVE})
        self.assertEqual(calls, [('apply', '1h'), ('undo',), ('apply', None)])
        self.assertEqual(self.raw(), before.encode())

    def test_an_older_claude_code_is_refused_in_one_sentence(self):
        self.put('{}')
        got = optimize.act('storing', 'on', cache=self.kw(versions=OLD), shown=STORING)
        self.assertFalse(got['done'])
        self.assertEqual(got['says'], CW.VERSION_OLD_NO_WRITE.format(version='2.1.241', need='2.1.242'))
        self.assertEqual(self.raw(), b'{}')
        self.assertFalse(os.path.exists(self.data))

    def test_a_value_you_set_is_kept(self):
        self.put('{"subagentPromptCacheTtl": "5m"}')
        got = optimize.act('storing', 'on', cache=self.kw(), shown=STORING)
        self.assertEqual(got, {'done': False, 'says': CW.YOURS_KEPT.format(value='5m')})
        self.assertEqual(self.raw(), b'{"subagentPromptCacheTtl": "5m"}')

    def test_claude_codes_settings_are_not_written_when_no_takeaway_offers_it(self):
        self.put('{}')
        for shown in (None, [], WHO):
            with self.subTest(shown=shown):
                got = optimize.act('storing', 'on', cache=self.kw(), shown=shown)
                self.assertEqual(got, {'done': False, 'says': optimize.NO_SUCH})
        self.assertEqual(self.raw(), b'{}')
        self.assertFalse(os.path.exists(self.data))

    def test_a_trial_run_is_not_offered_for_the_cache(self):
        self.put('{}')
        got = optimize.act('storing', 'try', cache=self.kw(), shown=STORING)
        self.assertFalse(got['done'])
        one_sentence(self, got['says'])
        self.assertEqual(self.raw(), b'{}')


COUNTER = 'switchboard.router.cache_count'  # the router's own count of helpers' cache; not in this tree


def counter(test, calls, boom=False):
    m = types.ModuleType(COUNTER)
    m.projects_folders = lambda: ['/nowhere/projects']

    def count(folders, limit_s=None):
        calls.append((folders, limit_s))
        if boom:
            raise RuntimeError('stand-in failure')
        return {'records_read': 900, 'requests': 120, 'restores': 25, 'restore_tokens': 400000, 'cache_writes': 60,
                'by_family': {}}
    m.count = count
    m.say = lambda r: ['Helpers stored their whole context again %d times after a wait.' % r['restores']]
    p = mock.patch.dict(sys.modules, {COUNTER: m})
    p.start()
    test.addCleanup(p.stop)
    return m


class TheCount(unittest.TestCase):
    def test_counted_through_the_routers_module_with_its_own_time_limit(self):
        calls = []
        counter(self, calls)
        got = optimize.count_now(2)
        self.assertEqual(calls, [(['/nowhere/projects'], 2)])
        self.assertEqual((got['restores'], got['says']),
                         (25, ['Helpers stored their whole context again 25 times after a wait.']))

    def test_none_without_the_module_or_when_it_fails(self):
        with mock.patch.dict(sys.modules, {COUNTER: None}):
            self.assertIsNone(optimize.count_now(2))
        counter(self, [], boom=True)
        self.assertIsNone(optimize.count_now(2))


class NeverItsOwnFile(unittest.TestCase):
    def test_the_module_opens_and_writes_no_file(self):
        with open(optimize.__file__, encoding='utf-8') as f:
            src = f.read()
        for word in ('open(', 'write_json', 'write_text', 'os.replace', 'os.rename', 'settings.json'):
            self.assertNotIn(word, src)

    def test_the_trial_module_is_not_imported_when_this_module_is(self):
        with open(optimize.__file__, encoding='utf-8') as f:
            src = f.read()
        top = [ln for ln in src.splitlines() if ln.startswith(('import', 'from'))]
        self.assertEqual([ln for ln in top if 'tryit' in ln or 'cache_count' in ln], [])
        self.assertNotIn('set_switch', src)                                     # no router switch is set from here


if __name__ == '__main__':
    unittest.main()
