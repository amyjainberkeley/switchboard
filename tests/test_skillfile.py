import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Switchboard's skill goes in by itself, and comes out cleanly (switchboard/skillfile.py):
#
# - at the first run, a copy goes into Claude Code's skills folder (and the one Codex reads, when Codex is here), with
#   a marker that says it is Switchboard's and from which version; once, never after the person turned it on or off;
# - `switchboard skill off` takes out only Switchboard's own copy, and it stays off; `on` puts it back;
# - an older copy of Switchboard's own is brought up to date; one a newer Switchboard wrote is left;
# - a folder of that name without the marker, or one that is a link, is never written over or removed;
# - a skills folder, or Claude Code's folder, that is a symbolic link takes nothing by itself;
# - `switchboard skill show` prints the whole skill and writes nothing; the uninstaller takes out the same copies.
#
# Every folder is a throwaway one: each test has its own home, Claude Code folder and data folder.
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import core, selfcheck, skillfile


def tree(folder):
    """{relative path: bytes} of every file under folder (links named, never followed)."""
    out = {}
    for top, dirs, files in os.walk(folder):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(top, d))]:
            p = os.path.join(top, name)
            rel = os.path.relpath(p, folder)
            out[rel] = ('link:' + os.readlink(p)).encode() if os.path.islink(p) else open(p, 'rb').read()
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='switchboard-skill-')
        self.home = os.path.join(self.root, 'home')
        self.claude = os.path.join(self.home, '.claude')
        os.makedirs(self.claude)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'CLAUDE_CONFIG_DIR': self.claude,
                                           'SWITCHBOARD_HOME': os.path.join(self.root, 'data')})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(shutil.rmtree, self.root, True)
        skillfile.LINKED.clear()
        self.dest = os.path.join(self.claude, 'skills', 'switchboard')
        self.codex_dest = os.path.join(self.home, '.agents', 'skills', 'switchboard')

    def stand_in(self, text='v2'):
        """A small skill folder in the program's place (skillfile.SOURCE)."""
        src = os.path.join(self.root, 'src-' + text)
        os.makedirs(os.path.join(src, 'reference'))
        with open(os.path.join(src, 'SKILL.md'), 'w') as f:
            f.write('---\nname: switchboard\ndescription: A stand-in.\n---\n\n# Stand-in %s\n' % text)
        with open(os.path.join(src, 'reference', 'one.md'), 'w') as f:
            f.write('# One %s\n' % text)
        p = mock.patch.object(skillfile, 'SOURCE', src)
        p.start()
        self.addCleanup(p.stop)
        return src

    def history_texts(self):
        return [e['text'] for e in core.load()['events']]


class GoesInByItself(Base):
    def test_the_first_run_puts_it_in_with_its_marker(self):
        got = skillfile.by_itself(codex=False)
        self.assertEqual(got, {'claude': 'on'})
        files = tree(self.dest)
        self.assertEqual(files['SKILL.md'], open(os.path.join(skillfile.SOURCE, 'SKILL.md'), 'rb').read())
        for name in os.listdir(os.path.join(skillfile.SOURCE, 'reference')):
            self.assertIn('reference/' + name, files)
        mark = json.loads(files[skillfile.MARK])
        self.assertEqual((mark['by'], mark['version']), ('switchboard', core.VERSION))
        self.assertEqual(mark['files'], [rel for rel, _ in skillfile.guide_files()])
        self.assertTrue(core.settings().get('skill_by_itself'))
        self.assertIn('Skill for Claude Code: on', self.history_texts())
        self.assertFalse(os.path.exists(self.codex_dest))
        leftovers = [n for n in os.listdir(os.path.dirname(self.dest)) if n != 'switchboard']
        self.assertEqual(leftovers, [])                 # the folder it was built in is gone

    def test_nothing_without_claude_codes_folder(self):
        shutil.rmtree(self.claude)
        self.assertEqual(skillfile.by_itself(codex=False), {})
        self.assertFalse(os.path.exists(self.claude))
        self.assertFalse(core.settings().get('skill_by_itself'))

    def test_once_a_copy_taken_out_by_hand_stays_out(self):
        skillfile.by_itself(codex=False)
        shutil.rmtree(self.dest)
        self.assertEqual(skillfile.by_itself(codex=False), {})
        self.assertFalse(os.path.exists(self.dest))

    def test_codex_gets_a_copy_only_when_it_is_here(self):
        self.assertEqual(skillfile.by_itself(codex=True), {'claude': 'on', 'codex': 'on'})
        self.assertEqual(tree(self.codex_dest), tree(self.dest))
        self.assertIn('Skill for Codex: on', self.history_texts())

    def test_the_pages_first_run_puts_it_in_too(self):
        from switchboard import keep
        keep.reset()
        self.addCleanup(keep.reset)
        with mock.patch.object(skillfile, 'codex_here', lambda: False), \
                mock.patch.object(keep, 'notify_by_itself', lambda: False), \
                mock.patch.object(keep, 'statusline_by_itself', lambda: None), \
                mock.patch.object(keep, 'session_hooks_by_itself', lambda: False):
            keep.first_run_tick([])
            self.assertFalse(os.path.exists(self.dest))
            keep.first_run_tick([{'email': 'ana@work.example', 'org_id': '', 'unmanaged': False}])
        self.assertTrue(os.path.isfile(os.path.join(self.dest, 'SKILL.md')))
        self.assertFalse(os.path.exists(self.codex_dest))

    def test_codex_here_starts_no_program(self):
        ran = []
        with mock.patch('subprocess.Popen', side_effect=lambda *a, **k: ran.append(a)), \
                mock.patch('subprocess.run', side_effect=lambda *a, **k: ran.append(a)):
            self.assertIn(skillfile.codex_here(), (True, False))
        self.assertEqual(ran, [])


class OffStaysOff(Base):
    def test_off_takes_it_out_and_it_never_comes_back_by_itself(self):
        skillfile.by_itself(codex=True)
        code, lines = skillfile.turn(False, codex=True)
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.dest))
        self.assertFalse(os.path.exists(self.codex_dest))
        self.assertEqual(lines, ['Skill for Claude Code: taken out of ~/.claude/skills/switchboard.',
                                 'Skill for Codex: taken out of ~/.agents/skills/switchboard.',
                                 'It does not go back in by itself. switchboard skill on puts it back.'])
        self.assertEqual(skillfile.by_itself(codex=True), {})
        self.assertEqual(skillfile.refresh(), {})
        self.assertFalse(os.path.exists(self.dest))
        code, lines = skillfile.turn(False, codex=True)
        self.assertEqual(lines[:2], ['Skill for Claude Code: off already.', 'Skill for Codex: off already.'])

    def test_on_puts_it_in_and_says_so_when_it_is_in_already(self):
        code, lines = skillfile.turn(True, codex=False)
        self.assertEqual(code, 0)
        self.assertEqual(lines, ['Skill for Claude Code: on, in ~/.claude/skills/switchboard.',
                                 'Skill for Codex: Codex was not found on this Mac, so nothing was put in '
                                 '~/.agents/skills.',
                                 skillfile.SHOW_LINE])
        self.assertTrue(os.path.isfile(os.path.join(self.dest, 'SKILL.md')))
        self.assertFalse(os.path.exists(os.path.join(self.home, '.agents')))
        code, lines = skillfile.turn(True, codex=False)
        self.assertEqual(lines[0], 'Skill for Claude Code: on already, in ~/.claude/skills/switchboard.')
        self.assertEqual(skillfile.by_itself(codex=False), {})       # the person's choice: never by itself again

    def test_on_needs_claude_codes_folder(self):
        shutil.rmtree(self.claude)
        code, lines = skillfile.turn(True, codex=False)
        self.assertEqual(code, 1)
        self.assertIn("Claude Code's folder ~/.claude is not there yet", lines[0])
        self.assertFalse(os.path.exists(self.claude))

    def test_off_leaves_files_that_are_not_switchboards(self):
        skillfile.turn(True, codex=False)
        with open(os.path.join(self.dest, 'notes.md'), 'w') as f:
            f.write('mine')
        code, lines = skillfile.turn(False, codex=False)
        self.assertEqual(tree(self.dest), {'notes.md': b'mine'})
        self.assertIn("The folder stays: it holds files that are not Switchboard's (notes.md).", lines[0])


class AnUpdate(Base):
    def test_an_older_copy_is_brought_up_to_date(self):
        self.stand_in('v1')
        skillfile.by_itself(codex=False)
        with open(os.path.join(self.dest, skillfile.MARK)) as f:
            mark = json.load(f)
        mark['version'] = '0.1.0'
        mark['files'].append('reference/gone.md')
        with open(os.path.join(self.dest, skillfile.MARK), 'w') as f:
            json.dump(mark, f)
        with open(os.path.join(self.dest, 'reference', 'gone.md'), 'w') as f:
            f.write('# Gone\n')
        src = self.stand_in('v2')
        self.assertEqual(skillfile.refresh(), {'claude': '0.1.0'})
        files = tree(self.dest)
        self.assertEqual(files['SKILL.md'], open(os.path.join(src, 'SKILL.md'), 'rb').read())
        self.assertEqual(files['reference/one.md'], b'# One v2\n')
        self.assertNotIn('reference/gone.md', files)
        self.assertEqual(json.loads(files[skillfile.MARK])['version'], core.VERSION)
        self.assertEqual(skillfile.refresh(), {})                     # current now: nothing to do

    def test_the_self_check_brings_it_up_to_date(self):
        self.stand_in('v1')
        skillfile.turn(True, codex=False)
        self.stand_in('v2')
        results = selfcheck.check_skill(True, 0)
        self.assertEqual([(r.name, r.state) for r in results], [('skill:claude', selfcheck.REPAIRED)])
        self.assertEqual(tree(self.dest)['reference/one.md'], b'# One v2\n')
        self.assertEqual(selfcheck.check_skill(True, 0), [])
        self.assertEqual(selfcheck.check_skill(False, 0), [])          # doctor only looks

    def test_a_copy_from_a_newer_switchboard_is_left(self):
        self.stand_in('v1')
        skillfile.turn(True, codex=False)
        with open(os.path.join(self.dest, skillfile.MARK)) as f:
            mark = json.load(f)
        mark['version'] = '99.0.0'
        with open(os.path.join(self.dest, skillfile.MARK), 'w') as f:
            json.dump(mark, f)
        before = tree(self.dest)
        self.stand_in('v2')
        self.assertEqual(skillfile.refresh(), {})
        self.assertEqual(skillfile.by_itself(codex=False), {})
        self.assertEqual(tree(self.dest), before)
        self.assertIn('from a newer Switchboard (99.0.0)', skillfile.status_lines(codex=False)[0])


class TheirsIsLeftAlone(Base):
    def theirs(self):
        os.makedirs(self.dest)
        with open(os.path.join(self.dest, 'SKILL.md'), 'w') as f:
            f.write('---\nname: switchboard\ndescription: my own\n---\n')
        return tree(self.dest)

    def test_a_folder_without_the_marker_is_never_written_over_or_removed(self):
        before = self.theirs()
        self.assertEqual(skillfile.by_itself(codex=False), {'claude': 'theirs'})
        said = 'You already have a skill named switchboard in Claude Code, so Switchboard left it.'
        self.assertEqual(self.history_texts().count(said), 1)
        self.assertEqual(skillfile.by_itself(codex=False), {'claude': 'theirs'})
        self.assertEqual(self.history_texts().count(said), 1)           # said once
        code, lines = skillfile.turn(True, codex=False)
        self.assertEqual((code, lines[0]), (1, "Skill for Claude Code: a skill of your own is in "
                                               "~/.claude/skills/switchboard, so Switchboard left it. To use "
                                               "Switchboard's, move yours, then run switchboard skill on again."))
        code, lines = skillfile.turn(False, codex=False)
        self.assertEqual(lines[0], "Skill for Claude Code: the folder ~/.claude/skills/switchboard is not "
                                   "Switchboard's, so it was left as it is.")
        self.assertEqual(skillfile.take_out_all(), ([], []))
        self.assertEqual(tree(self.dest), before)
        self.assertIn('a skill of your own is in ~/.claude/skills/switchboard', skillfile.status_lines(False)[0])

    def test_a_marker_that_does_not_say_switchboard_is_not_one(self):
        before = self.theirs()
        for text in ('{}', 'not json', json.dumps({'by': 'switchboard', 'version': '0.1', 'files': ['../x']})):
            with open(os.path.join(self.dest, skillfile.MARK), 'w') as f:
                f.write(text)
            before[skillfile.MARK] = text.encode()
            skillfile.turn(True, codex=False)
            skillfile.turn(False, codex=False)
            self.assertEqual(tree(self.dest), before)

    def test_a_folder_that_is_a_link_is_never_written_through(self):
        elsewhere = os.path.join(self.root, 'elsewhere')
        os.makedirs(elsewhere)
        with open(os.path.join(elsewhere, skillfile.MARK), 'w') as f:      # even with a marker of Switchboard's
            json.dump({'by': 'switchboard', 'version': '0.1.0', 'files': ['SKILL.md']}, f)
        with open(os.path.join(elsewhere, 'SKILL.md'), 'w') as f:
            f.write('kept')
        os.makedirs(os.path.dirname(self.dest))
        os.symlink(elsewhere, self.dest)
        before = tree(elsewhere)
        self.assertEqual(skillfile.by_itself(codex=False), {'claude': 'theirs'})
        self.assertEqual(skillfile.refresh(), {})
        self.assertEqual(skillfile.turn(True, codex=False)[0], 1)
        skillfile.turn(False, codex=False)
        skillfile.take_out_all()
        self.assertEqual(tree(elsewhere), before)
        self.assertTrue(os.path.islink(self.dest))


class LinkedFolders(Base):
    def test_a_linked_skills_folder_takes_nothing_by_itself_but_on_works(self):
        kept = os.path.join(self.root, 'dotfiles', 'skills')
        os.makedirs(kept)
        os.symlink(kept, os.path.join(self.claude, 'skills'))
        self.assertEqual(skillfile.by_itself(codex=False), {'claude': 'linked'})
        self.assertEqual(os.listdir(kept), [])
        self.assertIn('is a symbolic link, so it does not go in by itself', skillfile.status_lines(False)[0])
        code, lines = skillfile.turn(True, codex=False)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(kept, 'switchboard', 'SKILL.md')))

    def test_a_linked_claude_folder_takes_nothing_by_itself(self):
        real = os.path.join(self.root, 'dotfiles', 'claude')
        os.makedirs(real)
        shutil.rmtree(self.claude)
        os.symlink(real, self.claude)
        self.assertEqual(skillfile.by_itself(codex=False), {'claude': 'linked'})
        self.assertEqual(os.listdir(real), [])

    def test_an_older_copy_behind_a_link_is_not_brought_up_to_date_by_itself(self):
        kept = os.path.join(self.root, 'dotfiles', 'skills')
        os.makedirs(kept)
        os.symlink(kept, os.path.join(self.claude, 'skills'))
        self.stand_in('v1')
        skillfile.turn(True, codex=False)
        before = tree(kept)
        self.stand_in('v2')
        self.assertEqual(skillfile.refresh(), {})
        self.assertEqual(tree(kept), before)

    def test_a_linked_agents_folder_takes_nothing_by_itself(self):
        real = os.path.join(self.root, 'dotfiles', 'agents')
        os.makedirs(real)
        os.symlink(real, os.path.join(self.home, '.agents'))
        self.assertEqual(skillfile.by_itself(codex=True), {'claude': 'on', 'codex': 'linked'})
        self.assertEqual(os.listdir(real), [])


class Show(Base):
    def test_show_prints_the_whole_skill_and_writes_nothing(self):
        before = tree(self.root)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(['skill', 'show']), 0)
        text = out.getvalue()
        self.assertTrue(text.startswith('# Switchboard\n'), text[:80])
        self.assertNotIn('\nname: switchboard\n', text)                # no front matter
        for rel, data in skillfile.guide_files()[1:]:
            self.assertIn('<!-- %s -->\n%s' % (rel, data.decode('utf-8').strip()), text)
        self.assertEqual(tree(self.root), before)

    def test_show_follows_the_order_of_the_map(self):
        text = skillfile.show_text()
        named = [line.split('`')[1] for line in open(os.path.join(skillfile.SOURCE, 'SKILL.md'))
                 if line.startswith('- `reference/')]
        self.assertEqual(sorted(named), [rel for rel, _ in skillfile.guide_files()[1:]])
        self.assertEqual([text.index('<!-- %s -->' % rel) for rel in sorted(named)],
                         sorted(text.index('<!-- %s -->' % rel) for rel in sorted(named)))


class TheCommand(Base):
    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(skillfile, 'codex_here', lambda: False):
            code = cli.main(['skill'] + list(args))
        return code, out.getvalue().splitlines()

    def test_skill_says_where_it_is_one_line_per_place(self):
        code, lines = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(lines, ['Skill for Claude Code: off. switchboard skill on puts it in',
                                 'Skill for Codex: off (Codex was not found on this Mac)', skillfile.SHOW_LINE])
        self.run_cli('on')
        code, lines = self.run_cli()
        self.assertEqual(lines[0], 'Skill for Claude Code: on, in ~/.claude/skills/switchboard (Switchboard %s)'
                         % core.VERSION)
        self.run_cli('off')
        code, lines = self.run_cli()
        self.assertEqual(lines[0], 'Skill for Claude Code: off (turned off with switchboard skill off)')

    def test_words_it_does_not_take(self):
        self.assertEqual(self.run_cli('sideways'), (2, [skillfile.USAGE]))

    def test_the_help_names_it(self):
        self.assertIn('switchboard skill [on|off|show]', cli.command_help('skill'))

    def test_doctor_lines(self):
        with mock.patch.object(skillfile, 'codex_here', lambda: False):
            self.assertEqual(skillfile.doctor_lines(), [(None, 'Skill for Claude Code: off', 'Optional. It answers '
                                                         'questions about Switchboard in your agent. Run: '
                                                         'switchboard skill on')])
            skillfile.turn(True)
            self.assertEqual(skillfile.doctor_lines(), [(True, 'Skill for Claude Code (~/.claude/skills/switchboard)',
                                                         '')])


class TheUninstaller(Base):
    def test_tidy_takes_out_switchboards_own_copies_only(self):
        skillfile.by_itself(codex=True)
        lines, left = skillfile.take_out_all()
        self.assertEqual(left, [])
        self.assertEqual(lines, ['Skill for Claude Code: taken out of ~/.claude/skills/switchboard.',
                                 'Skill for Codex: taken out of ~/.agents/skills/switchboard.'])
        self.assertFalse(os.path.exists(self.dest) or os.path.exists(self.codex_dest))
        self.assertFalse(core.settings().get('skill_by_itself'))      # an install again puts it back at its first run

    def test_uninstall_sh_names_the_same_places_and_marker(self):
        with open(os.path.join(ROOT, 'uninstall.sh'), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/%s"' % skillfile.NAME, text)
        self.assertIn('"$HOME/%s/%s"' % (skillfile.AGENTS_SKILLS, skillfile.NAME), text)
        self.assertIn(skillfile.MARK, text)

    def test_uninstall_sh_without_the_program_takes_only_the_files_the_marker_names(self):
        # The plain shell route, for when no program could run tidy: that block alone, on this throwaway home.
        with open(os.path.join(ROOT, 'uninstall.sh'), encoding='utf-8') as f:
            text = f.read()
        start = text.index("# Switchboard's skill for coding agents, when no program ran tidy")
        block = text[start:text.index('\nfor d in "$HOME/.local/bin"', start)]
        places = ('"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/switchboard"', '"$HOME/.agents/skills/switchboard"')
        for words, here in zip(places, (self.dest, self.codex_dest)):     # (the test above holds these words)
            self.assertEqual(block.count(words), 1)
            block = block.replace(words, "'%s'" % here)
        self.stand_in()
        skillfile.by_itself(codex=True)
        mine = os.path.join(self.dest, 'reference', 'my-notes.md')
        with open(mine, 'w') as f:
            f.write('mine\n')
        env = {'HOME': self.home, 'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}
        run = subprocess.run(['/bin/sh', '-c', 'short() { echo "$1"; }\nTIDY_RAN=0\n' + block], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual((run.returncode, run.stderr), (0, ''))
        self.assertEqual(tree(self.dest), {'reference/my-notes.md': b'mine\n'})    # the person's own file stays
        self.assertFalse(os.path.exists(self.codex_dest))
        self.assertEqual(run.stdout.count("Removed Switchboard's skill"), 2)

    def test_the_first_run_makes_one_call(self):
        import inspect
        self.assertEqual(inspect.getsource(cli).count('skillfile.by_itself('), 1)
        self.assertIn('skillfile.by_itself(', inspect.getsource(cli.ended))


class TheFirstRun(Base):
    def setUp(self):
        super().setUp()
        from switchboard import keep, server
        # Only the skill's step of ended() runs here: every other step of the first run is a stand-in.
        for target, name, value in ((server, 'statusline_every_minute', lambda: None),
                                    (cli, 'auto_here', lambda say=True: False),
                                    (keep, 'notify_by_itself', lambda now=None: False),
                                    (cli, 'notifications_on', lambda: False),
                                    (keep, 'statusline_by_itself', lambda now=None: None),
                                    (cli, 'statusline_on', lambda: False),
                                    (keep, 'session_hooks_by_itself', lambda now=None: None),
                                    (cli.engine, 'auto_settings', lambda: {}),
                                    (skillfile, 'codex_here', lambda: False)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_the_first_run_puts_it_in_once_an_account_is_kept(self):
        self.stand_in()
        cli.ended(0)
        self.assertFalse(os.path.exists(self.dest))
        end = cli.ended(1)
        self.assertEqual(end['skill'], {'claude': 'on'})
        self.assertEqual(sorted(tree(self.dest)), ['.switchboard-skill.json', 'SKILL.md', 'reference/one.md'])
        self.assertFalse(os.path.exists(self.codex_dest))


if __name__ == '__main__':
    unittest.main()
