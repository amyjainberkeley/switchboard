import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Claude Code's limit notices say which window refused: a model's own week refuses that model only, usage credits or
# a spend limit the chat's own model, a 5-hour, weekly or session limit everything. And the models the chats ask for
# come from their main loops' last requests. The sentences are the ones Claude Code 2.1.329 and 2.1.332 write.
import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard.accounts import sessions  # noqa: E402
from fakes_autoswitch import Home  # noqa: E402

ALL, FAMILY, CREDITS = sessions.ALL, sessions.FAMILY, sessions.CREDITS
URL = 'https://claude.ai/settings/usage?from=cc_cli_limit_message'

SENTENCES = [
    ("You've hit your Fable limit · resets Nov 30 at 1pm (America/Los_Angeles). Switch to another model, or manage "
     "usage credits at %s, to continue." % URL, (FAMILY, 'fable')),
    ("You've hit your 5-hour limit · resets 12:20pm (America/Los_Angeles) · out of usage credits", (ALL, None)),
    ("You've hit your session limit · resets 4pm", (ALL, None)),
    ("You've hit your weekly limit · resets Mon 9am", (ALL, None)),
    ("You've hit your weekly Opus limit · resets Mon 9am", (FAMILY, 'opus')),
    ("You've hit your weekly Sonnet limit · resets Mon 9am", (FAMILY, 'sonnet')),
    ("You've hit your Opus limit · resets 3pm", (FAMILY, 'opus')),
    ("You've hit your Sonnet limit · resets 3pm", (FAMILY, 'sonnet')),
    ("You've hit your usage credit limit · resets Dec 1", (CREDITS, None)),
    ("You've hit your org's monthly spend limit · run /usage-credits to request more", (CREDITS, None)),
    ("You've hit your monthly spend limit · raise it at %s" % URL, (CREDITS, None)),
    ("You've hit your limit · resets 4pm", (ALL, None)),
    ('Claude AI usage limit reached|1794955200', (ALL, None)),
    ("You're out of extra usage · resets 4pm", (ALL, None)),       # as at 4df2f46: it names no one model
]


def stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace('+00:00', 'Z')


def notice(text, t):
    return {'type': 'assistant', 'timestamp': stamp(t), 'isApiErrorMessage': True,
            'message': {'model': '<synthetic>', 'content': [{'type': 'text', 'text': text}]}}


def asked(model, t, side=False):
    d = {'type': 'assistant', 'timestamp': stamp(t), 'message': {'model': model, 'stop_reason': 'end_turn',
                                                                 'content': [{'type': 'text', 'text': 'ok'}]}}
    if side:
        d['isSidechain'] = True
    return d


class WhichWindow(unittest.TestCase):
    def test_every_sentence_says_its_window(self):
        for text, want in SENTENCES:
            self.assertEqual(sessions.limit_scope(text), want, text)

    def test_the_old_reader_missed_the_fable_limit_and_this_one_does_not(self):
        self.assertTrue(sessions.LIMIT_RE.search(SENTENCES[0][0]))

    def test_not_a_usage_limit(self):
        for text in ('Server is temporarily limiting requests (not your usage limit) · Rate limited',
                     'API Error: 429 This request would exceed your per-minute rate limit', 'the usage meter'):
            self.assertIsNone(sessions.limit_scope(text), text)


class Scanned(Home):
    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.root = os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'projects')
        self.scanner = sessions.Scanner(root=self.root, procs=lambda: 1)

    def write(self, rel, lines, age):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a') as f:
            for d in lines:
                f.write(json.dumps(d) + '\n')
        t = self.now - age
        os.utime(path, (t, t))

    def look(self, light=False):
        return self.scanner.look((), now=self.now, light=light)

    def test_a_fable_limit_refuses_fable_only(self):
        self.write('proj/s1.jsonl', [asked('claude-fable-5-2', self.now - 200), notice(SENTENCES[0][0],
                                                                                      self.now - 120)], 120)
        a = self.look()
        self.assertIsNone(a['limited_at'])
        self.assertAlmostEqual(a['refused']['fable'], self.now - 120, delta=1)

    def test_a_five_hour_limit_refuses_everything(self):
        self.write('proj/s1.jsonl', [notice(SENTENCES[1][0], self.now - 60)], 60)
        a = self.look()
        self.assertAlmostEqual(a['limited_at'], self.now - 60, delta=1)
        self.assertEqual(a['refused'], {})

    def test_a_spend_limit_refuses_the_chats_own_model(self):
        self.write('proj/s1.jsonl', [asked('claude-opus-5-5', self.now - 90), notice(SENTENCES[10][0],
                                                                                    self.now - 30)], 30)
        self.assertEqual(list(self.look()['refused']), ['opus'])

    def test_a_spend_limit_in_a_chat_whose_model_is_not_known_refuses_everything(self):
        self.write('proj/s1.jsonl', [notice(SENTENCES[10][0], self.now - 30)], 30)
        a = self.look()
        self.assertAlmostEqual(a['limited_at'], self.now - 30, delta=1)
        self.assertEqual(a['refused'], {})

    def test_out_of_extra_usage_refuses_everything(self):
        self.write('proj/s1.jsonl', [asked('claude-fable-5-2', self.now - 90),
                                     notice("You're out of extra usage · resets 4pm", self.now - 30)], 30)
        self.assertAlmostEqual(self.look()['limited_at'], self.now - 30, delta=1)

    def test_a_light_look_still_sees_a_new_notice_at_the_end_of_a_chat(self):
        # Nov 27, load 5 to 7 per core: no transcript was read, and 19 limit notices in 11 tabs went unseen
        self.write('proj/s1.jsonl', [asked('claude-fable-5-2', self.now - 300)] * 3 +
                   [notice(SENTENCES[1][0], self.now - 60)], 60)
        a = self.look(light=True)
        self.assertAlmostEqual(a['limited_at'], self.now - 60, delta=1)
        self.assertIsNone(a['asked'])                           # nothing else is read under load

    def test_a_light_look_reads_only_the_newest_end_of_files_written_in_ten_minutes(self):
        filler = {'type': 'user', 'message': {'content': 'x' * 1000}}
        self.write('proj/s1.jsonl', [notice(SENTENCES[1][0], self.now - 60)] + [filler] * 80, 60)   # past the tail
        self.assertIsNone(self.look(light=True)['limited_at'])
        self.write('proj/s2.jsonl', [notice(SENTENCES[1][0], self.now - 30)], 11 * 60)    # file not written lately
        self.assertIsNone(self.look(light=True)['limited_at'])

    def test_an_old_refusal_counts_for_ten_minutes_only(self):
        self.write('proj/s1.jsonl', [notice(SENTENCES[0][0], self.now - 601)], 601)
        self.assertEqual(self.look()['refused'], {})

    def test_the_models_the_chats_ask_for(self):
        self.write('proj/s1.jsonl', [asked('claude-fable-5-2', self.now - 300)], 300)
        self.write('proj/s2.jsonl', [asked('claude-fable-5-2', self.now - 7 * 3600)], 7 * 3600)     # too old
        self.write('proj/s3.jsonl', [asked('claude-sonnet-5-5', self.now - 100, side=True)], 100)   # a helper
        self.write('proj/s4/subagents/agent-1.jsonl', [asked('claude-haiku-4-5', self.now - 50)], 50)  # a helper
        self.write('proj/s5.jsonl', [asked('claude-fable-5-2', self.now - 900), asked('claude-opus-5-5[1m]',
                                                                                        self.now - 60)], 60)
        a = self.look()
        self.assertEqual(sorted(a['asked']), ['fable', 'opus'])

    def test_not_known_until_a_look_reads_the_transcripts(self):
        self.write('proj/s1.jsonl', [asked('claude-fable-5-2', self.now - 30)], 30)
        self.assertIsNone(self.look(light=True)['asked'])
        self.assertEqual(list(self.look()['asked']), ['fable'])
        self.assertEqual(list(self.look(light=True)['asked']), ['fable'])          # kept from the last read


if __name__ == '__main__':
    unittest.main()
