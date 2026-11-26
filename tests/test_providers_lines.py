import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Reading a chat file from its end (switchboard/providers/lines.py): whole lines, newest first, within a byte cap, a
# time cap and a line cap, with multibyte characters cut across chunk edges, and a torn last line told apart from an
# unknown one. Every file here is made up in a temp folder.
import calendar
import os
import random
import shutil
import tempfile
import unittest
from unittest import mock

from switchboard.providers import contract, lines
from switchboard.providers.contract import ReadLimits


def write(folder, name, data):
    path = os.path.join(folder, name)
    with open(path, 'wb') as f:
        f.write(data)
    return path


def collect(path, limits=ReadLimits(), mono=None, chunk=lines.CHUNK):
    it = lines.back_lines(path, limits, mono or (lambda: 0.0), chunk=chunk)
    try:
        got = list(it)
    finally:
        it.close()
    return got, it


class Clock:
    """A stand-in monotonic clock: each call is step seconds after the one before."""

    def __init__(self, step):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


class BackLines(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='lines-')
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_a_file_with_no_final_newline_gives_its_last_line_first(self):
        path = write(self.dir, 'a.jsonl', b'{"a":1}\n{"b":2}')
        got, it = collect(path)
        self.assertEqual(got, [(b'{"b":2}', 8), (b'{"a":1}', 0)])
        self.assertTrue(it.unterminated)
        self.assertEqual(it.stopped, 'start')
        self.assertEqual(it.bytes_read, 15)

    def test_a_final_newline_is_not_a_line(self):
        path = write(self.dir, 'a.jsonl', b'{"a":1}\n{"b":2}\n')
        got, it = collect(path)
        self.assertEqual(got, [(b'{"b":2}', 8), (b'{"a":1}', 0)])
        self.assertFalse(it.unterminated)

    def test_blank_lines_are_passed_over(self):
        path = write(self.dir, 'a.jsonl', b'one\n\n\ntwo\n')
        got, _ = collect(path)
        self.assertEqual([g[0] for g in got], [b'two', b'one'])

    def test_an_empty_file_gives_nothing(self):
        path = write(self.dir, 'a.jsonl', b'')
        got, it = collect(path)
        self.assertEqual(got, [])
        self.assertEqual(it.stopped, 'start')

    def test_a_line_one_byte_over_the_line_cap_is_skipped_and_counted(self):
        big = b'x' * ((4 << 20) + 1)
        path = write(self.dir, 'a.jsonl', b'first\n' + big + b'\nlast\n')
        got, it = collect(path)
        self.assertEqual(got, [(b'last', 6 + len(big) + 1), (b'', 6), (b'first', 0)])
        self.assertEqual(it.skipped_large, 1)
        self.assertEqual(it.skipped_head, b'')         # the newest line handed out was a whole one

    def test_a_line_exactly_at_the_line_cap_is_kept(self):
        edge = b'y' * (4 << 20)
        path = write(self.dir, 'a.jsonl', b'first\n' + edge + b'\nlast')
        got, it = collect(path)
        self.assertEqual([len(g[0]) for g in got], [4, 4 << 20, 5])
        self.assertEqual(it.skipped_large, 0)

    def test_a_long_line_inside_one_chunk_is_skipped_too(self):
        limits = ReadLimits(line_max=10)
        path = write(self.dir, 'a.jsonl', b'short\n' + b'z' * 11 + b'\nend\n')
        got, it = collect(path, limits, chunk=1 << 20)
        self.assertEqual(got, [(b'end', 18), (b'', 6), (b'short', 0)])
        self.assertEqual(it.skipped_large, 1)

    def test_a_long_line_across_many_small_chunks_keeps_no_more_than_the_cap(self):
        limits = ReadLimits(line_max=10)
        path = write(self.dir, 'a.jsonl', b'short\n' + b'z' * 100 + b'\nend\n')
        got, it = collect(path, limits, chunk=3)
        self.assertEqual(got, [(b'end', 107), (b'', 6), (b'short', 0)])
        self.assertLessEqual(it.most_held, 10 + 3)

    def test_a_skipped_line_hands_over_its_first_bytes(self):
        """ADDENDA-3 C18: a reader can tell what kind of line it was without holding it."""
        front = b''.join(b'%04d,' % i for i in range(300))          # 1,500 bytes, every place distinct
        long_line = front + b'z' * 600
        data = b'short\n' + long_line + b'\nend\n'
        path = write(self.dir, 'a.jsonl', data)
        for chunk in (1, 3, 7, 64, 255, 256, 257, 1000, 1023, 1024, 1025, 1 << 20):
            it = lines.back_lines(path, ReadLimits(line_max=300), lambda: 0.0, chunk=chunk)
            seen = []
            try:
                for raw, at in it:
                    seen.append((raw, at, it.skipped_head))
            finally:
                it.close()
            self.assertEqual(seen, [(b'end', 6 + len(long_line) + 1, b''), (b'', 6, front[:lines.SKIPPED_HEAD]),
                                    (b'short', 0, b'')], chunk)

    def test_a_skipped_first_line_of_the_file_hands_over_its_head_too(self):
        front = b''.join(b'%04d;' % i for i in range(300))
        path = write(self.dir, 'a.jsonl', front + b'q' * 500 + b'\nend\n')
        for chunk in (5, 64, 1 << 20):
            it = lines.back_lines(path, ReadLimits(line_max=100), lambda: 0.0, chunk=chunk)
            try:
                got = [(raw, it.skipped_head) for raw, _ in it]
            finally:
                it.close()
            self.assertEqual(got, [(b'end', b''), (b'', front[:lines.SKIPPED_HEAD])], chunk)

    @unittest.skipUnless(lines.zstd_module() is not None, 'needs compression.zstd (Python 3.14)')
    def test_a_compressed_files_skipped_line_hands_over_its_head(self):
        front = b''.join(b'%04d,' % i for i in range(300))
        data = b'short\n' + front + b'z' * 600 + b'\nend\n'
        path = write(self.dir, 'a.jsonl.zst', lines.zstd_module().compress(data))
        for chunk in (7, 1 << 20):
            it = lines.zst_back_lines(path, ReadLimits(line_max=300), lambda: 0.0, chunk=chunk)
            try:
                got = [(raw, it.skipped_head) for raw, _ in it]
            finally:
                it.close()
            self.assertEqual(got, [(b'end', b''), (b'', front[:lines.SKIPPED_HEAD]), (b'short', b'')], chunk)

    def test_multibyte_characters_cut_across_chunk_edges_come_back_whole(self):
        words = ['café', '日本語', '\U0001f600 x', 'pläin', 'é' * 9, 'end']
        data = '\n'.join(words).encode('utf-8') + b'\n'
        path = write(self.dir, 'a.jsonl', data)
        for chunk in range(1, 17):
            got, _ = collect(path, chunk=chunk)
            self.assertEqual([g[0].decode('utf-8') for g in got], list(reversed(words)), chunk)

    def test_any_chunk_size_gives_the_same_lines_as_a_plain_split(self):
        rnd = random.Random(7)
        rows = [bytes(rnd.choice(b'abc{}":,\xc3\xa9') for _ in range(rnd.randint(1, 40))) for _ in range(60)]
        data = b'\n'.join(rows)
        path = write(self.dir, 'a.jsonl', data)
        want, at = [], 0
        for r in rows:
            want.append((r, at))
            at += len(r) + 1
        want.reverse()
        for chunk in (1, 2, 5, 64, 1 << 20):
            got, _ = collect(path, chunk=chunk)
            self.assertEqual(got, want, chunk)

    def test_the_byte_cap_stops_at_whole_lines_and_says_why(self):
        data = b''.join(b'line-%04d\n' % i for i in range(20))     # 10 bytes a line
        path = write(self.dir, 'a.jsonl', data)
        got, it = collect(path, ReadLimits(max_bytes=45), chunk=8)
        self.assertEqual([g[0] for g in got], [b'line-0019', b'line-0018', b'line-0017', b'line-0016'])
        self.assertEqual(it.stopped, 'bytes')
        self.assertLessEqual(it.bytes_read, 45)

    def test_the_time_cap_stops_reading_and_says_why(self):
        data = b''.join(b'line-%04d\n' % i for i in range(2000))
        path = write(self.dir, 'a.jsonl', data)
        got, it = collect(path, ReadLimits(max_seconds=2.5), mono=Clock(1.0), chunk=100)
        self.assertEqual(it.stopped, 'time')
        self.assertLess(len(got), 2000)
        self.assertGreater(len(got), 0)

    @unittest.skipUnless(lines.zstd_module() is not None, 'needs compression.zstd (Python 3.14)')
    def test_a_compressed_file_is_never_stopped_on_time_so_its_newest_lines_are_read(self):
        # It can only be read from its start: a stop on time would hand out its oldest lines as the newest.
        data = b''.join(b'line-%04d\n' % i for i in range(2000))
        path = write(self.dir, 'a.jsonl.zst', lines.zstd_module().compress(data))
        it = lines.zst_back_lines(path, ReadLimits(max_seconds=2.5), Clock(1.0), chunk=100)
        try:
            got = [raw for raw, _ in it]
        finally:
            it.close()
        self.assertEqual(got[:2], [b'line-1999', b'line-1998'])
        self.assertEqual(len(got), 2000)
        self.assertEqual(it.stopped, 'start')

    @unittest.skipUnless(lines.zstd_module() is not None, 'needs compression.zstd (Python 3.14)')
    def test_a_damaged_compressed_file_never_raises(self):
        good = lines.zstd_module().compress(b''.join(b'line-%04d\n' % i for i in range(500)))
        for name, data in (('garbage.jsonl.zst', b'not compressed at all ' * 20),
                           ('middle.jsonl.zst', good[:len(good) // 2] + b'\x00' * 64 + good[len(good) // 2:]),
                           ('cut.jsonl.zst', good[:-5])):
            path = write(self.dir, name, data)
            it = lines.zst_back_lines(path, ReadLimits(), lambda: 0.0)
            try:
                list(it)
            finally:
                it.close()
            lines.head_line(path)
            list(lines.head_lines(path, 1 << 20, 10))

    def test_a_skipped_lines_head_is_long_enough_to_name_a_chat_lines_kind(self):
        # Claude Code puts a line's "type" after its folder and ids: 1 KiB holds it for a folder of 600 characters.
        self.assertGreaterEqual(lines.SKIPPED_HEAD, 1024)

    def test_closing_early_closes_the_file(self):
        path = write(self.dir, 'a.jsonl', b'a\nb\nc\n')
        it = lines.back_lines(path, ReadLimits(), lambda: 0.0)
        self.assertEqual(next(it)[0], b'c')
        it.close()
        self.assertTrue(it.closed)

    def test_a_missing_file_gives_nothing_and_says_so(self):
        got, it = collect(os.path.join(self.dir, 'gone.jsonl'))
        self.assertEqual(got, [])
        self.assertEqual(it.stopped, 'missing')


class TornTail(unittest.TestCase):
    """A last line cut mid-write is counted as a torn tail, never as an unknown line; one that does not parse
    elsewhere in the file is unknown."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='lines-')
        self.addCleanup(shutil.rmtree, self.dir, True)

    def read(self, data):
        from fakes_providers import make_reach
        from switchboard.providers import claude_chats
        from switchboard.providers.contract import ChatRef
        reach, _ = make_reach(self.dir)
        path = write(self.dir, 'c.jsonl', data)
        ref = ChatRef(provider='claude', id='aaaaaaaa-0000-4000-8000-00000000000a', folder='/Users/maya/site',
                      path=path)
        return claude_chats.read(reach, ref).info

    def test_a_torn_last_line(self):
        whole = (b'{"type":"user","message":{"role":"user","content":"hi"},"cwd":"/Users/maya/site"}\n'
                 b'{"type":"assistant","message":{"content":[{"type":"text","text":"he')
        info = self.read(whole)
        self.assertEqual((info.torn_tail, info.unknown_lines), (1, 0))

    def test_a_broken_line_that_is_not_the_last_is_unknown(self):
        info = self.read(b'{"type":"user","mess\n{"type":"custom-title","customTitle":"x"}\n')
        self.assertEqual((info.torn_tail, info.unknown_lines), (0, 1))

    def test_a_last_line_that_parses_without_a_newline_is_read(self):
        info = self.read(b'{"type":"custom-title","customTitle":"named"}')
        self.assertEqual((info.torn_tail, info.title), (0, 'named'))


class HeadLine(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='lines-')
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_the_first_line(self):
        path = write(self.dir, 'a.jsonl', b'{"first":1}\n{"second":2}\n')
        self.assertEqual(lines.head_line(path), b'{"first":1}')

    def test_a_one_line_file_without_a_newline(self):
        path = write(self.dir, 'a.jsonl', b'{"only":1}')
        self.assertEqual(lines.head_line(path), b'{"only":1}')

    def test_none_past_the_cap(self):
        path = write(self.dir, 'a.jsonl', b'x' * 50 + b'\nnext\n')
        self.assertIsNone(lines.head_line(path, cap=20))
        self.assertEqual(lines.head_line(path, cap=50), b'x' * 50)

    def test_none_for_a_missing_file(self):
        self.assertIsNone(lines.head_line(os.path.join(self.dir, 'gone')))

    def test_head_lines_reads_forward_within_its_caps(self):
        path = write(self.dir, 'a.jsonl', b'a\nb\n\nc\nd')
        self.assertEqual(list(lines.head_lines(path, max_bytes=1 << 20, max_lines=3)), [b'a', b'b', b'c'])
        self.assertEqual(list(lines.head_lines(path, max_bytes=4, max_lines=10)), [b'a', b'b'])


class ShellWords(unittest.TestCase):
    def test_a_line_of_usual_length_is_split_the_shells_way(self):
        self.assertEqual(lines.shell_words("cat 'my notes.txt' .env"), ['cat', 'my notes.txt', '.env'])
        self.assertEqual(lines.shell_words(''), [])
        at = 'ab ' * (contract.SPLIT_MAX // 3) + 'x' * (contract.SPLIT_MAX % 3)
        self.assertEqual(len(at), contract.SPLIT_MAX)                 # the longest that still goes the shell's way
        with mock.patch('shlex.split', return_value=['asked']) as split:
            self.assertEqual(lines.shell_words(at), ['asked'])
        split.assert_called_once_with(at)

    def test_a_line_shlex_cannot_read_is_split_at_its_blanks_as_it_stands(self):
        self.assertEqual(lines.shell_words("echo 'no end"), ['echo', "'no", 'end'])

    def test_a_longer_line_is_never_given_to_shlex(self):
        long = "printf '%s' '" + 'x' * contract.SPLIT_MAX + "' > notes.txt; cat '.env' \"$HOME/.aws/credentials\""
        with mock.patch('shlex.split', side_effect=AssertionError('shlex was given a long line')):
            got = lines.shell_words(long)
        self.assertEqual(got[:2], ['printf', '%s'])                   # quote marks at a word's ends are taken off,
        self.assertEqual(got[-2:], ['.env', '$HOME/.aws/credentials'])   # so a quoted file name is still seen
        self.assertIn('notes.txt;', got)
        self.assertNotIn('', got)

    def test_a_word_met_twice_is_asked_about_once(self):
        asked = []

        class Counting:
            @staticmethod
            def secret_file(path):
                asked.append(path)
                return path == '.env'
        self.addCleanup(setattr, lines, 'redact', lines.redact)
        lines.redact = Counting
        self.assertEqual(lines.secret_words(['cat', '.env', 'x', 'x', '.env', 'cat', 7, '']), ('.env',))
        self.assertEqual(asked, ['cat', '.env', 'x'])       # a file's text in one command repeats its words

    def test_both_readers_and_the_secret_file_rule_split_with_the_same_cap(self):
        from switchboard.providers import claude_chats, codex_chats, redact
        long = 'echo ' + 'x' * contract.SPLIT_MAX + " > notes.txt; cat '.env'"
        with mock.patch('shlex.split', side_effect=AssertionError('shlex was given a long line')):
            self.assertEqual(claude_chats._split(long)[-1], '.env')
            self.assertEqual(codex_chats._split(long)[-1], '.env')
            self.assertEqual(redact.command_files(long), ('.env',))


class Shared(unittest.TestCase):
    def test_the_census_keeps_four_counts_a_kind(self):
        c = lines.Census()
        c.add('user', 'taken')
        c.add('user', 'skipped')
        c.add('block:image', 'counted')
        c.add('x', 'unknown', 3)
        rows = dict((r[0], r[1:]) for r in c.rows())
        self.assertEqual(rows['user'], (2, 1, 1, 0))
        self.assertEqual(rows['block:image'], (1, 0, 1, 0))
        self.assertEqual(rows['x'], (3, 0, 0, 3))

    def test_a_kind_name_from_a_file_is_kept_short_and_plain(self):
        self.assertEqual(lines.kind_name('tool_use'), 'tool_use')
        self.assertEqual(lines.kind_name('a b'), 'other')
        self.assertEqual(lines.kind_name('x' * 41), 'other')
        self.assertEqual(lines.kind_name(None), 'other')

    def test_iso_times(self):
        at = float(calendar.timegm((2026, 11, 23, 18, 4, 14, 0, 0, 0)))
        self.assertEqual(lines.iso_time('2026-11-23T18:04:14.500Z'), at + 0.5)
        self.assertEqual(lines.iso_time('2026-11-23T18:04:14Z'), at)
        self.assertEqual(lines.iso_time('2026-11-23T10:04:14-08:00'), at)
        self.assertEqual(lines.iso_time('2026-11-23T18:04:14.123456789Z'), at + 0.123456)
        self.assertIsNone(lines.iso_time('yesterday'))
        self.assertIsNone(lines.iso_time(12))

    def test_keep_cuts_and_says_how_long_it_was(self):
        self.assertEqual(lines.keep('abc', 10), ('abc', 3))
        text, n = lines.keep('a' * 100, 40)
        self.assertEqual(n, 100)
        self.assertLessEqual(len(text), 40)
        self.assertTrue(text.startswith('a'))
        text, n = lines.keep('h' * 50 + 't' * 50, 40, both_ends=True)
        self.assertTrue(text.startswith('h') and text.endswith('t'))
        self.assertIn('left out', text)
        self.assertLessEqual(len(text), 40)

    def test_secret_words_ask_the_note_code_and_keep_nothing_without_it(self):
        class Stand:
            @staticmethod
            def secret_file(p):
                return os.path.basename(p).startswith('.env')
        old = lines.redact
        self.addCleanup(setattr, lines, 'redact', old)
        lines.redact = Stand
        self.assertEqual(lines.secret_words(['cat', '.env', 'notes.md']), ('.env',))
        lines.redact = False                       # the note's code is not in this build
        self.assertEqual(lines.secret_words(['cat', '.env']), ())


if __name__ == '__main__':
    unittest.main()
