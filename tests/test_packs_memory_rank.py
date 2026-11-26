import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
import json
import os
import random
import sys
import tempfile
import time
import unittest

# Ranking tests for switchboard/packs/memory.py on a made-up history shaped like real use: chat lengths are
# very skewed. A few long chats discuss a topic in depth over hundreds of messages, many one- or two-message
# scripted runs repeat the same long first prompt every day, and the rest are medium chats. Every word, name
# and folder here is invented. Both index kinds (FTS5 and the plain fallback) are built from the same tree and
# must rank the same way.

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard.packs import memory as M

# Neutral filler: no word here holds a query word below (checked in a test), so substring matching (the
# fallback) and stemmed matching (FTS5) find exactly the same messages.
FILLER = ('able about account action after again agree almost along already also answer anyone appear apply '
          'area around arrive aside attempt basic began behind belong better beyond board borrow bottom branch '
          'bright bring broad build button cable calm careful carry center change choice circle clear close '
          'common copy corner count cover create current custom daily debate decide deep design detail differ '
          'direct doing double draft early easy edge effect either empty enough entire equal event every exact '
          'example extra fairly family famous field final finish follow formal forward frame fresh front future '
          'gather gentle given global good gravel group handle happen heavy height hidden history hollow honest '
          'idea image inside island issue journey judge keep kitchen label large later layer leave letter level '
          'light likely linen little local logic loose lower major manner margin market matter maybe meadow '
          'method middle minor model moment motion narrow nearly needle neither normal notice number object '
          'office often opinion orange order other output owner paper parcel partly patch pattern people period '
          'pick piece place plain plenty pocket point polish portion pretty print proper public purple quiet '
          'quite rather reach ready reason record region remain repair result ribbon rough round route ruler '
          'saddle sample second section select series settle shadow shape share short signal silver simple '
          'single sketch slight small smooth solid sound source spare speed spring square stable steady stone '
          'story strong subject summer supply switch system table target tender thread timber title total '
          'toward travel trial trouble turn twelve typical under unit upper useful usual valley value version '
          'vessel visit volume wander warm water weekly window winter within wonder wooden worth yellow young').split()

DEEP_Q = 'glaze shrinkage in the kiln'           # the in-depth long chat
SCATTER_Q = 'harbor lantern ledger'              # scattered across a giant chat, together in a short one
RARE_Q = 'quokka'                                # one short chat holds it
QUERY_WORDS = ('glaze', 'shrinkage', 'kiln', 'harbor', 'lantern', 'ledger', 'quokka')
DIGEST_HEAD = '# Morning digest: the studio report'
RUNS = 14


def tmp(prefix='switchboard-memrank-'):
    return os.path.realpath(tempfile.mkdtemp(prefix=prefix))


def iso(base, hours=0.0):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(M.parse_ts(base) + hours * 3600))


def local_date(stamp):
    return time.strftime('%Y-%m-%d', time.localtime(M.parse_ts(stamp)))


def words(rng, n):
    return [rng.choice(FILLER) for _ in range(n)]


def with_word(rng, text_words, w):
    i = rng.randrange(len(text_words) + 1)
    return text_words[:i] + [w] + text_words[i:]


def write_chat(root, folder, sid, msgs, ai_title=None, custom_title=None):
    """msgs: [(role 'you'|'claude', iso time, text)]. Writes Claude Code's transcript format."""
    pdir = os.path.join(root, ''.join(c if c.isalnum() and c.isascii() else '-' for c in folder))
    os.makedirs(pdir, exist_ok=True)
    out = []
    for i, (role, ts, text) in enumerate(msgs):
        if role == 'you':
            d = {'type': 'user', 'isSidechain': False, 'cwd': folder, 'sessionId': sid, 'uuid': '%s-u%d' % (sid, i),
                 'timestamp': ts, 'message': {'role': 'user', 'content': text}}
        else:
            d = {'type': 'assistant', 'isSidechain': False, 'cwd': folder, 'sessionId': sid,
                 'uuid': '%s-a%d' % (sid, i), 'timestamp': ts,
                 'message': {'id': 'msg_%s_%d' % (sid, i), 'type': 'message', 'role': 'assistant',
                             'model': 'claude-opus-5-5', 'content': [{'type': 'text', 'text': text}]}}
        out.append(json.dumps(d, separators=(',', ':')) + '\n')
        if i == 0 and ai_title:
            out.append(json.dumps({'type': 'ai-title', 'aiTitle': ai_title, 'sessionId': sid}) + '\n')
        if i == 0 and custom_title:
            out.append(json.dumps({'type': 'custom-title', 'customTitle': custom_title, 'sessionId': sid}) + '\n')
    with open(os.path.join(pdir, sid + '.jsonl'), 'w') as f:
        f.write(''.join(out))


def conversation(rng, start, turns, place=None):
    """[(role, time, text)] for `turns` prompt/reply pairs one hour apart; place(i, words) may add words to
    message i."""
    msgs = []
    for t in range(turns):
        for k, role in enumerate(('you', 'claude')):
            i = 2 * t + k
            ws = words(rng, rng.randint(8, 18) if role == 'you' else rng.randint(30, 70))
            if place:
                ws = place(i, ws)
            msgs.append((role, iso(start, t + k * 0.1), ' '.join(ws)))
    return msgs


def build(root):
    """Write the skewed history. Returns {name: session id} and facts the tests check against."""
    rng = random.Random(20261113)
    ids, facts = {}, {}

    # 1. The in-depth chat: 640 messages over two weeks. kiln is everywhere, glaze and shrinkage now and then
    #    (never in the same message), and four replies around turn 200 hold all three together.
    together = {401, 403, 405, 407}
    pool = [i for i in range(640) if not 380 <= i <= 420]
    rng.shuffle(pool)
    kiln, glaze, shrink = set(pool[:160]), set(pool[160:172]), set(pool[172:178])

    def deep_place(i, ws):
        if i in together:
            mid = len(ws) // 2
            return ws[:mid] + ['the', 'glaze', 'shrinkage', 'in', 'the', 'kiln'] + ws[mid:]
        for s, w in ((kiln, 'kiln'), (glaze, 'glaze'), (shrink, 'shrinkage')):
            if i in s:
                ws = with_word(rng, ws, w)
        return ws
    deep = conversation(rng, '2026-07-01T12:00:00Z', 320, deep_place)
    write_chat(root, '/Users/x/studio', 'deep', deep, ai_title='Studio notes')
    ids['deep'] = 'deep'
    facts['deep_matched'] = local_date(deep[max(together)][1])
    facts['deep_first'], facts['deep_last'] = local_date(deep[0][1]), local_date(deep[-1][1])

    # 2. Scripted runs: the same long first prompt every morning for two weeks, one or two messages each. It
    #    happens to name each query word once, far apart.
    body = words(rng, 1300)
    for pos, w in ((100, 'kiln'), (600, 'glaze'), (1100, 'shrinkage'), (400, 'harbor'), (800, 'ledger')):
        body.insert(pos, w)
    prompt = DIGEST_HEAD + '\n\n  You are the  studio morning digest writer.\n' + ' '.join(body)
    runs = []
    for d in range(RUNS):
        sid = 'run%02d' % d
        at = iso('2026-08-01T15:00:00Z', 24 * d)
        msgs = [('you', at, prompt)] + ([('claude', iso(at, 0.05), 'Written and filed.')] if d % 2 else [])
        write_chat(root, '/Users/x/bots/digest', sid, msgs)
        runs.append((sid, msgs[-1][1]))
    facts['runs'] = runs

    # 3. Medium chats. One has the in-depth chat's title in another folder (a different chat: never folded),
    #    one holds all three studio words in one message, a few hold one of them.
    def one_at(index, phrase):
        return lambda i, ws: (ws[:3] + phrase.split() + ws[3:]) if i == index else ws
    write_chat(root, '/Users/x/other', 'same-title', conversation(rng, '2026-09-02T12:00:00Z', 12,
               lambda i, ws: with_word(rng, ws, 'kiln') if i in (3, 9) else ws), ai_title='Studio notes')
    write_chat(root, '/Users/x/craft', 'craft-once', conversation(rng, '2026-09-05T12:00:00Z', 6,
               one_at(2, 'why does glaze shrinkage happen in a kiln')), ai_title='Craft fair prep')
    for k in range(30):
        n = rng.randint(2, 20)
        word = ('glaze', 'kiln', 'harbor', 'ledger', None, None)[k % 6]
        hits = set(rng.sample(range(2 * n), 1)) if word else set()
        write_chat(root, '/Users/x/code/proj%d' % (k % 9), 'mid%02d' % k,
                   conversation(rng, iso('2026-06-01T12:00:00Z', 24 * k), n,
                                lambda i, ws, w=word, h=hits: with_word(rng, ws, w) if i in h else ws),
                   ai_title='Medium chat %d' % k)

    # 4. A giant chat where the three words of SCATTER_Q are each common but never share a message, and a short
    #    chat that has them together once.
    pool = list(range(1000))
    rng.shuffle(pool)
    sets = (set(pool[:40]), set(pool[40:80]), set(pool[80:120]))

    def scatter_place(i, ws):
        for s, w in zip(sets, ('harbor', 'lantern', 'ledger')):
            if i in s:
                ws = with_word(rng, ws, w)
        return ws
    write_chat(root, '/Users/x/code/shipping', 'giant', conversation(rng, '2026-05-01T12:00:00Z', 500, scatter_place),
               ai_title='Shipping log')
    write_chat(root, '/Users/x/notes', 'together', [
        ('you', '2026-04-04T12:00:00Z', 'we should take the lantern down to the harbor and check the ledger there'),
        ('claude', '2026-04-04T12:01:00Z', 'Sounds good; bring a coat, it gets cold by the water at night.'),
        ('you', '2026-04-04T12:05:00Z', 'and a flask')], ai_title='Weekend plans')

    # 5. A rare word in one short chat.
    write_chat(root, '/Users/x/zoo', 'rare', [
        ('you', '2026-03-03T12:00:00Z', 'is a quokka really always smiling or is that a myth'),
        ('claude', '2026-03-03T12:01:00Z', 'It is the shape of its mouth; it is not a sign of mood.')],
        ai_title='Animal facts')
    return ids, facts


class Skewed(object):
    _made = None

    @classmethod
    def get(cls):
        if cls._made is None:
            root = os.path.join(tmp(), 'projects')
            os.makedirs(root)
            ids, facts = build(root)
            fts = os.path.join(tmp(), 'fts.sqlite3')
            plain = os.path.join(tmp(), 'plain.sqlite3')
            assert M.update_index(root, fts)['fts5']
            assert not M.update_index(root, plain, fts=False)['fts5']
            cls._made = (root, fts, plain, ids, facts)
        return cls._made


class Ranking(object):
    """The same checks on both index kinds."""
    index_kind = None

    def index(self):
        _, fts, plain, _, _ = Skewed.get()
        return fts if self.index_kind == 'fts' else plain

    def search(self, q, limit=8):
        return M.search(q, limit, self.index())

    def test_fixture_words_never_collide(self):
        for f in FILLER:
            for w in QUERY_WORDS + ('myth', 'digest', 'studio', 'report'):
                self.assertNotIn(w, f)

    def test_in_depth_long_chat_ranks_first(self):
        got = self.search(DEEP_Q)
        self.assertEqual(got[0]['session'], 'deep', [(r['title'], r['score']) for r in got])
        got = self.search('kiln glaze')
        self.assertEqual(got[0]['session'], 'deep')

    def test_scripted_runs_fold_into_one_row(self):
        _, _, _, _, facts = Skewed.get()
        got = self.search(DEEP_Q)
        runs = [r for r in got if r['session'].startswith('run')]
        self.assertEqual(len(runs), 1, [(r['title'], r['score']) for r in got])
        r = runs[0]
        newest, second = facts['runs'][-1], facts['runs'][-2]
        self.assertEqual(r['session'], newest[0])               # equal scores: the newest run stands for them
        self.assertEqual(r['alike'], RUNS - 1)
        self.assertEqual(r['alike_last'], local_date(second[1]))
        self.assertTrue(r['title'].startswith('Morning digest: the studio report You are the studio morning'),
                        r['title'])
        self.assertLessEqual(len(r['title']), 80)
        others = [x for x in got if not x['session'].startswith('run')]
        self.assertTrue(all(x['alike'] == 0 and x['alike_last'] is None for x in others))

    def test_rows_are_distinct_by_title_and_folder(self):
        for q in (DEEP_Q, SCATTER_Q, 'kiln', 'glaze', 'ledger harbor', 'digest studio report'):
            got = self.search(q, 20)
            keys = [(r['title'], r['folder']) for r in got]
            self.assertEqual(len(keys), len(set(keys)), q)
        titles = [(r['title'], r['folder']) for r in self.search('kiln', 40)]
        self.assertIn(('Studio notes', '/Users/x/studio'), titles)    # same title, another folder: both stay
        self.assertIn(('Studio notes', '/Users/x/other'), titles)

    def test_words_together_in_a_short_chat_beat_scattered_words_in_a_giant_one(self):
        got = [r['session'] for r in self.search(SCATTER_Q, 20)]
        self.assertEqual(got[0], 'together', got)
        self.assertIn('giant', got)
        self.assertLess(got.index('together'), got.index('giant'))

    def test_a_rare_word_finds_its_one_short_chat(self):
        got = self.search(RARE_Q)
        self.assertEqual([r['session'] for r in got], ['rare'])
        self.assertEqual(self.search('quokka myth kiln')[0]['session'], 'rare')

    def test_matched_is_the_date_of_the_best_message(self):
        _, _, _, _, facts = Skewed.get()
        r = self.search(DEEP_Q)[0]
        self.assertEqual(r['matched'], facts['deep_matched'])
        self.assertNotEqual(r['matched'], r['date'])
        self.assertNotEqual(r['matched'], r['last'])
        self.assertEqual((r['date'], r['last']), (facts['deep_first'], facts['deep_last']))
        rare = self.search(RARE_Q)[0]
        self.assertEqual(rare['matched'], local_date('2026-03-03T12:00:00Z'))

    def test_snippets_come_from_the_messages_with_most_words(self):
        r = self.search(DEEP_Q)[0]
        self.assertEqual(len(r['snippets']), 2)
        for s in r['snippets']:                          # the four replies with all three words, not the first kiln
            for w in ('glaze', 'shrinkage', 'kiln'):
                self.assertIn('[%s]' % w, s, s)
            self.assertLess(len(s.split()), 30)
        r = [x for x in self.search(SCATTER_Q) if x['session'] == 'together'][0]
        self.assertEqual(len(r['snippets']), 1)
        for w in ('harbor', 'lantern', 'ledger'):
            self.assertIn('[%s]' % w, r['snippets'][0])

    def test_marks_are_the_ones_given(self):
        r = M.search(RARE_Q, 1, self.index(), marks=('\x02', '\x03'))[0]
        self.assertIn('\x02quokka\x03', r['snippets'][0])

    def test_ties_go_to_the_newer_chat(self):
        got = self.search('digest studio report', 20)
        runs = [r for r in got if r['session'].startswith('run')]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['session'], 'run%02d' % (RUNS - 1))


class TestRankingFts(Ranking, unittest.TestCase):
    index_kind = 'fts'


class TestRankingPlain(Ranking, unittest.TestCase):
    index_kind = 'plain'


class TestBothPathsSameOrder(unittest.TestCase):
    def test_same_rows_same_order(self):
        _, fts, plain, _, _ = Skewed.get()
        for q in (DEEP_Q, SCATTER_Q, RARE_Q, 'kiln', 'glaze kiln', 'ledger', 'harbor ledger', 'studio digest'):
            a = [(r['session'], r['alike'], r['matched'], r['score']) for r in M.search(q, 12, fts)]
            b = [(r['session'], r['alike'], r['matched'], r['score']) for r in M.search(q, 12, plain)]
            self.assertEqual(a, b, q)


class TestTitles(unittest.TestCase):
    def test_fallback_title_is_cleaned(self):
        self.assertEqual(M.fallback_title('# The Daily Report: what  happened\n today'),
                         'The Daily Report: what happened today')
        self.assertEqual(M.fallback_title('## Plan'), 'Plan')
        self.assertEqual(M.fallback_title('- first item'), 'first item')
        self.assertEqual(M.fallback_title('> quoted  words'), 'quoted words')
        self.assertEqual(M.fallback_title('  >  ## - nested marks'), 'nested marks')
        self.assertEqual(M.fallback_title('#hashtag stays'), '#hashtag stays')
        self.assertEqual(M.fallback_title('-1 stays'), '-1 stays')
        self.assertEqual(M.fallback_title('x' * 200), 'x' * 80)
        self.assertEqual(M.fallback_title(''), '')
        self.assertEqual(M.fallback_title(None), '')

    def test_a_named_chat_keeps_its_name(self):
        r = M.search(RARE_Q, 1, Skewed.get()[1])[0]
        self.assertEqual(r['title'], 'Animal facts')


class TestSpeed(unittest.TestCase):
    """About 50,000 short messages, skewed like real use: a search stays well under a second on both paths."""

    @classmethod
    def setUpClass(cls):
        rng = random.Random(50000)
        vocab = sorted(set(''.join(rng.choice('bcdfghklmnprstvz') + rng.choice('aeiou') for _ in range(3))
                           for _ in range(3000)))
        weights = [1.0 / (k + 1) for k in range(len(vocab))]           # a few words are very common
        root = os.path.join(tmp(), 'projects')
        os.makedirs(root)
        start = M.parse_ts('2026-01-01T00:00:00Z')

        cum, acc = [], 0.0
        for w in weights:
            acc += w
            cum.append(acc)

        def text(n):
            return ' '.join(rng.choices(vocab, cum_weights=cum, k=n))
        total = 0
        plan = [('giant', 4000)] + [('c%03d' % k, 100) for k in range(450)] + [('one%03d' % k, 1) for k in range(1000)]
        for sid, n in plan:
            msgs = [('you' if i % 2 == 0 else 'claude', time.strftime('%Y-%m-%dT%H:%M:%SZ',
                     time.gmtime(start + total * 60 + i)), text(rng.randint(6, 30))) for i in range(n)]
            total += n
            write_chat(root, '/Users/x/speed/%s' % sid[:3], sid, msgs)
        cls.total = total
        cls.fts = os.path.join(tmp(), 'fts.sqlite3')
        cls.plain = os.path.join(tmp(), 'plain.sqlite3')
        M.update_index(root, cls.fts)
        M.update_index(root, cls.plain, fts=False)
        cls.queries = [' '.join(vocab[:3]), ' '.join(vocab[:6]), '%s %s' % (vocab[0], vocab[-1]), vocab[1500]]

    def test_fifty_thousand_messages(self):
        self.assertGreaterEqual(self.total, 50000)
        self.assertGreaterEqual(M.search_status(self.fts)['messages'], 50000)
        for index in (self.fts, self.plain):
            for q in self.queries:
                t = time.thread_time()                     # this thread's CPU time: a loaded Mac stretches the
                got = M.search(q, 8, index)                # clock (2.46 s at load 23), not the work (SQLite runs
                spent = time.thread_time() - t             # in this thread)
                self.assertTrue(got)
                self.assertLess(spent, 0.8, (index, q, spent))     # about 0.1 s on an idle Mac


if __name__ == '__main__':
    unittest.main()
