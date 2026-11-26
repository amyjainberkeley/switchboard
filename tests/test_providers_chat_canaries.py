import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The canary census (DESIGN 18): a made-up word planted in every field of every kind of line each reader's table
# names, and a table that says for each word whether it is in what the reader hands on (an event or ReadInfo), not
# in it, or counted (not in it, and counted in ReadInfo). 100% of "in" are in, none of "not in" is, and every counted
# one is counted. The note's own part of the census is lane A's.
import dataclasses
import os
import shutil
import tempfile
import unittest

import fakes_provider_chats as F
from fakes_providers import make_reach
from switchboard.providers import claude_chats, codex_chats, contract, lines, words
from switchboard.providers.contract import ChatRef, ReadLimits

IN, OUT, COUNTED = 'in', 'not in', 'counted'


def w(name):
    """A canary word no other contains."""
    return 'QZ%sQZ' % name.upper()


def everything(obj):
    out = []

    def walk(v):
        if dataclasses.is_dataclass(v):
            for f in dataclasses.fields(v):
                walk(getattr(v, f.name))
        elif isinstance(v, (tuple, list)):
            for x in v:
                walk(x)
        else:
            out.append(repr(v))
    walk(obj)
    return '\n'.join(out)


class StandInRedact:
    @staticmethod
    def secret_file(path):
        return False


class Census(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='canaries-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = os.path.join(self.root, 'home')
        self.reach, _ = make_reach(self.root, env={'HOME': self.home})
        old = lines.redact
        self.addCleanup(setattr, lines, 'redact', old)
        lines.redact = StandInRedact

    def check(self, t, table):
        text = everything(t)
        census = {r[0]: r[1:] for r in t.info.census}
        missing = [name for name, how, _ in table if how == IN and w(name) not in text]
        leaked = [name for name, how, _ in table if how != IN and w(name) in text]
        uncounted = [name for name, how, counted in table if how == COUNTED and not counted(t.info, census)]
        self.assertEqual((missing, leaked, uncounted), ([], [], []))
        self.assertGreater(len(table), 30)

    def test_claude_code(self):
        c = F.ClaudeChat()
        c.ai_title(w('aititle'))
        c.custom_title(w('customtitle'))
        c.skip_lines(w('skipline'))
        c.attachment('environment', content=w('attachment'))
        p = c.person(w('person'))
        p['gitBranch'] = w('gitbranch')
        p['slug'] = w('slug')
        p['promptId'] = w('promptid')
        c.sidechain(w('sidechain'))
        c.meta(w('meta'))
        c.peer(w('peer'))
        c.companion(w('companion'))
        c.scheduled(w('scheduled'))
        c.task_notice(w('tasknotice'))
        c.local_command(w('localcommand'))
        c.person(words.FIRST_WORDS.format(hop='h-0a0b0c0d', path='/tmp/' + w('notepath'), from_name='Codex'))
        c.user_line([{'type': 'text', 'text': w('withblock')}, {'type': 'image', 'source': {'data': w('image')}},
                     {'type': 'hologram', 'data': w('newblock')}], origin={'kind': 'human'})
        c.user_line([{'type': 'text', 'text': '<browser_instruction>%s</browser_instruction>' % w('instruction')},
                     {'type': 'text', 'text': '<ide_opened_file>%s</ide_opened_file>' % w('openfile')},
                     {'type': 'text', 'text': w('afterblocks')}], origin={'kind': 'human'})
        tid = c.tool_use('Read', {'file_path': '/Users/maya/' + w('filepath')}, tool_id='toolu_' + w('toolid'))
        r = c.tool_result(tid, [{'type': 'text', 'text': w('result')},
                                {'type': 'image', 'source': {'data': w('resultimage')}}], is_error=False)
        r['toolUseResult'] = {'stdout': w('toolUseResult')}
        r['sourceToolAssistantUUID'] = w('sourceuuid')
        web = c.tool_use('WebFetch', {'url': 'https://example.com/' + w('url'), 'prompt': w('webprompt')})
        c.tool_result(web, w('webpage'))
        a = c.say(w('assistant'))
        a['message']['model'] = w('model')
        a['requestId'] = w('requestid')
        a['message']['usage'] = {'input_tokens': w('usage')}
        c.assistant([{'type': 'thinking', 'thinking': w('thinking'), 'signature': w('signature')}])
        c.assistant([{'type': 'text', 'text': 'fine'}, {'type': 'sparkle', 'data': w('assistantblock')}])
        c.api_error('rate_limit', words=w('apierror'))
        c.system('stop_hook_summary', content=w('systemother'))
        c.queued(w('queued'), images=1)
        c.queued(w('queuedtask'), kind='task-notification')
        c.queued(w('queuedpeer'), kind='peer')
        c.queue('enqueue', w('queueop'))
        c.queue('remove', w('queueremove'), reason='absorbed_mid_turn')
        b = c.boundary()
        b['content'] = w('boundarycontent')
        b['compactMetadata'] = {'k': w('compactmeta')}
        c.compact_summary(w('summary'))
        c.person('the newest')
        c.last_prompt(w('lastprompt'))
        c.add({'type': 'brand-new-line', 'payload': w('newline')})
        path = c.write(os.path.join(self.root, 'c.jsonl'))
        t = claude_chats.read(self.reach, ChatRef(provider='claude', id=c.session, folder=c.cwd, path=path),
                              ReadLimits(since_boundary=False))

        def census_unknown(kind):
            return lambda info, census: census.get(kind, (0, 0, 0, 0))[3] > 0

        table = [
            ('person', IN, None), ('withblock', IN, None), ('summary', IN, None), ('tasknotice', IN, None),
            ('localcommand', IN, None), ('result', IN, None), ('filepath', IN, None), ('assistant', IN, None),
            ('toolid', IN, None),           # Event.call: the app's own id that ties a result to its call
            ('queued', IN, None), ('queuedtask', IN, None), ('queuedpeer', IN, None), ('customtitle', IN, None),
            ('url', IN, None), ('webprompt', IN, None), ('afterblocks', IN, None), ('openfile', IN, None),
            ('aititle', OUT, None), ('skipline', OUT, None), ('attachment', OUT, None), ('gitbranch', OUT, None),
            ('slug', OUT, None), ('promptid', OUT, None), ('sidechain', OUT, None), ('meta', OUT, None),
            ('peer', OUT, None), ('companion', OUT, None), ('scheduled', OUT, None), ('notepath', OUT, None),
            ('toolUseResult', OUT, None), ('sourceuuid', OUT, None), ('webpage', OUT, None),
            ('model', OUT, None), ('requestid', OUT, None), ('usage', OUT, None), ('apierror', OUT, None),
            ('systemother', OUT, None), ('queueop', OUT, None), ('queueremove', OUT, None),
            ('boundarycontent', OUT, None), ('compactmeta', OUT, None), ('lastprompt', OUT, None),
            ('instruction', COUNTED, lambda info, census: census.get('user/app-block-skipped', (0,))[0] == 1),
            ('image', COUNTED, lambda info, census: info.images >= 3),
            ('resultimage', COUNTED, lambda info, census: info.images >= 3),
            ('thinking', COUNTED, lambda info, census: info.reasoning == 1),
            ('signature', COUNTED, lambda info, census: info.reasoning == 1),
            ('newblock', COUNTED, census_unknown('block:hologram')),
            ('assistantblock', COUNTED, census_unknown('block:sparkle')),
            ('newline', COUNTED, census_unknown('line:brand-new-line')),
        ]
        self.check(t, table)
        self.assertNotIn(F.ACCOUNT, everything(t))
        self.assertNotIn(F.ORG, everything(t))

    def test_codex(self):
        c = F.CodexChat(canary=w('meta'))
        c.skip_lines(w('skipline'))
        tc = c.turn_context(model=w('model'))
        tc['payload']['approval_policy'] = w('approval')
        c.developer(w('developer'))
        c.env_context()
        c.user_message(w('roleonly'))
        c.user_item(w('person'), images=1)
        c.rows[-2]['payload']['item']['content'][1]['path'] = '/Users/maya/' + w('imagepath')
        c.heartbeat(w('heartbeat'))
        c.reasoning()
        c.rows[-1]['payload']['encrypted_content'] = w('encrypted')
        c.command(['ls', w('commandword')], output=w('output'), exit_code=0)
        c.rows[-1]['payload']['item']['cwd'] = '/Users/' + w('commandcwd')
        c.rows[-1]['payload']['item']['parsed_cmd'] = [{'cmd': w('parsed'), 'type': 'unknown'}]
        c.rows[-3]['payload']['input'] = w('execinput')
        c.file_change({'/Users/maya/' + w('changedfile'): ('add', w('addcontent')),
                       '/Users/maya/other.py': ('update', w('diff'))})
        c.mcp('example', 'lookup', {'q': w('arguments')}, w('mcpresult'))
        c.rows[-1]['payload']['item']['result']['_meta'] = {'k': w('mcpmeta')}
        c.rows[-1]['payload']['item']['result']['structuredContent'] = {'k': w('structured')}
        c.function_call('lookup', '{"q":"%s"}' % w('functionargs'), 'call-1', namespace='mcp__example')
        c.function_output('call-1', w('functionoutput'))
        c.assistant(w('assistant'))
        c.task_complete(w('taskcomplete'))
        c.compacted(w('compacted'), retained=[{'text': w('retained'), 'order': 0},
                                              {'text': '<heartbeat>%s</heartbeat>' % w('retainedbeat'),
                                               'origin': 'heartbeat', 'order': 1}])
        c.rows[-1]['payload']['replacement_history'] = [w('replacement')]
        tok = c.token_count(primary={'used_percent': 1.0, 'window_minutes': 300, 'resets_at': 1})
        tok['payload']['rate_limits']['credits']['balance'] = w('balance')
        c.user_item('the newest')
        for _ in range(2):
            c._line('event_msg', {'type': 'brand_new_event', 'data': w('newevent')})
        path = c.write(F.codex_home(self.home))
        t = codex_chats.read(self.reach, ChatRef(provider='codex', id=c.thread, folder=c.cwd, path=path),
                             ReadLimits(since_boundary=False))

        table = [
            ('person', IN, None), ('heartbeat', IN, None), ('commandword', IN, None), ('output', IN, None),
            ('changedfile', IN, None), ('arguments', IN, None), ('mcpresult', IN, None), ('assistant', IN, None),
            ('taskcomplete', IN, None), ('compacted', IN, None), ('retained', IN, None),
            ('meta', OUT, None), ('skipline', OUT, None), ('model', OUT, None), ('approval', OUT, None),
            ('developer', OUT, None), ('roleonly', OUT, None), ('commandcwd', OUT, None), ('parsed', OUT, None),
            ('execinput', OUT, None), ('addcontent', OUT, None), ('diff', OUT, None), ('mcpmeta', OUT, None),
            ('structured', OUT, None), ('functionargs', OUT, None), ('functionoutput', OUT, None),
            ('retainedbeat', OUT, None), ('replacement', OUT, None), ('balance', OUT, None),
            ('imagepath', COUNTED, lambda info, census: info.images >= 1),
            ('encrypted', COUNTED, lambda info, census: info.reasoning >= 1),
            ('newevent', COUNTED, lambda info, census: census.get('event:brand_new_event', (0, 0, 0, 0))[3] == 2),
        ]
        self.check(t, table)
        self.assertNotIn(F.ACCOUNT, everything(t))
        self.assertNotIn('mcp__example__tool', everything(t))
        self.assertEqual(sum(1 for e in t.events if e.kind == contract.PERSON), 3)


if __name__ == '__main__':
    unittest.main()
