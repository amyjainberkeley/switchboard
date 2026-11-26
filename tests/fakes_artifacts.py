"""Stand-ins for the "behind the page" tests: hook inputs as Claude Code records them for its Artifact tool and
Claude Docs, and a stand-in Chrome that writes a small JPEG where previews.chrome() looks for the browser.

Nothing here reaches the real browser, the real page or the real home. The hook inputs follow the shapes of Claude
Code 2.1.309 (2026-11), read with `strings` from its program file, never by running it: PostToolUse hands a hook the
tool's own answer, which for a publish is {url, path, artifact_id, title, updated, version, ...}; the chat history
keeps the same answer written out ("Published <file> at <link> (version 3)").
"""
import base64
import json
import os
import stat
import struct
import zlib

A, B, G = 'maya@northwind.example', 'maya@harbor.example', 'maya@gmail.example'
ID1, ID2, ID3, ID4 = 'FakeArtifactId00000001', 'LateArtifactId00000002', 'SomeArtifactId00000003', 'HereArtifactId00000004'
LINK = 'https://claude.ai/artifact/%s'
SESSION = '0a4e0000-0000-4000-8000-000000000001'

# A 24 x 15 JPEG made with macOS's sips: what the stand-in Chrome writes as its picture.
TINY_JPEG = base64.b64decode(
    '/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQA'
    'AAABAAAAGKADAAQAAAABAAAADwAAAAD/7QA4UGhvdG9zaG9wIDMuMAA4QklNBAQAAAAAAAA4QklNBCUAAAAAABDUHYzZjwCyBOmA'
    'CZjs+EJ+/8AAEQgADwAYAwEiAAIRAQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQE'
    'AAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldY'
    'WVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk'
    '5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMR'
    'BAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdo'
    'aWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz'
    '9PX29/j5+v/bAEMABAQEBAQEBgQEBgkGBgYJDAkJCQkMDwwMDAwMDxIPDw8PDw8SEhISEhISEhUVFRUVFRkZGRkZHBwcHBwcHBwc'
    'HP/bAEMBBAUFBwcHDAcHDB0UEBQdHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHf/dAAQA'
    'Av/aAAwDAQACEQMRAD8A5T+yv9mj+yv9mvSv7LHpR/ZY9K/Nv7T8z9s/tjzPNf7K/wBmj+yv9mvSv7LHpR/ZY9KP7T8w/tjzP//Z')


def png(w, h):
    """A plain PNG of w x h (a blue band over a light page), made here: what an older Chrome would write."""
    rows = b''.join(b'\x00' + (b'\x1e\x50\xc8' if y < h // 6 else b'\xf4\xf1\xea') * w for y in range(h))

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))


# ---------- what Claude Code hands the PostToolUse hook ----------

def event(tool, tool_input, response, sid=SESSION, cwd='/Users/maya/site', **more):
    return dict({'hook_event_name': 'PostToolUse', 'session_id': sid, 'transcript_path': '', 'cwd': cwd,
                 'tool_name': tool, 'tool_input': tool_input, 'tool_response': response,
                 'tool_use_id': 'toolu_01', 'duration_ms': 812}, **more)


def publish(aid=ID1, path='/Users/maya/site/launch/index.html', title='Launch board', updated=False, **inp):
    """A first publish: the tool's own answer."""
    return event('Artifact', dict({'file_path': path, 'icon': 'chart'}, **inp),
                 {'url': LINK % aid, 'path': path, 'artifact_id': aid, 'title': title, 'updated': updated,
                  'version': 1, 'liveSubscription': 'arming'})


def update(aid=ID2, path='/Users/maya/site/plan.html'):
    """A publish to an artifact from an earlier conversation (its url given)."""
    return event('Artifact', {'url': LINK % aid, 'file_path': path},
                 {'url': LINK % aid, 'path': path, 'artifact_id': aid, 'title': 'Plan', 'updated': True, 'version': 4})


def create(aid=ID3):
    """An Artifact made from a type, with no file of its own yet."""
    return event('Artifact', {'type_url': 'https://claude.ai/artifact/TypeSlidesAAAAAAAAAAA', 'title': 'Q4 review'},
                 {'url': LINK % aid, 'artifact_id': aid, 'title': 'Q4 review', 'version': 1})


def written(text, tool_input=None):
    """An answer written out as the chat history keeps it, as Claude Code's content blocks."""
    return event('Artifact', tool_input or {'file_path': 'x.html'}, [{'type': 'text', 'text': text}])


def doc(aid=ID4, name='Hiring plan'):
    """A Claude Doc made with batch: its answer is the verdict JSON."""
    body = '{"verdict":"ok","created":{"frame":{"slug":"hiring-plan","url":"%s"},"doc":{"id":"x"}}}' % (LINK % aid)
    return event('mcp__claude_ai_Claude_Docs__batch', {'container': {'kind': 'project', 'create': {'name': name}},
                                                        'batch': []}, [{'type': 'text', 'text': body}])


# ---------- a stand-in Chrome ----------

STAND_IN = r'''#!/usr/bin/python3 -I
# A stand-in for Chrome's program: it notes how it was started, then does what the file "mode" beside it says.
import json, os, sys, time
here = os.path.dirname(os.path.abspath(sys.argv[0]))
args = sys.argv[1:]
with open(os.path.join(here, 'calls.jsonl'), 'a') as f:
    f.write(json.dumps({'args': args, 'pid': os.getpid()}) + '\n')
opt = dict(a[2:].split('=', 1) for a in args if a.startswith('--') and '=' in a)
profile = opt.get('user-data-dir')
if profile:
    os.makedirs(os.path.join(profile, 'Default'), exist_ok=True)
    with open(os.path.join(profile, 'Default', 'Preferences'), 'w') as f:
        f.write('{}')
try:
    mode = open(os.path.join(here, 'mode')).read().strip()
except OSError:
    mode = 'jpeg'
shot = opt.get('screenshot')
if mode == 'hang':
    time.sleep(600)
elif mode == 'nothing':
    pass
elif mode == 'fail':
    sys.exit(1)
elif mode == 'vanish':                      # the page's file goes while it is drawn; a picture is written anyway
    from urllib.parse import unquote, urlsplit
    os.remove(unquote(urlsplit(args[-1]).path))
    with open(os.path.join(here, 'tiny.jpg'), 'rb') as f, open(shot, 'wb') as out:
        out.write(f.read())
elif shot:
    name = {'jpeg': 'tiny.jpg', 'wide-png': 'wide.png', 'wide-jpeg': 'wide.jpg', 'junk': 'junk.bin'}[mode]
    with open(os.path.join(here, name), 'rb') as f:
        data = f.read()
    with open(shot, 'wb') as f:
        f.write(data)
'''


class StandInChrome(object):
    """Google Chrome.app in the test home's Applications folder, whose program is the stand-in above. previews.chrome()
    finds it through browser.app_dirs (patched by the test to that folder only)."""

    def __init__(self, home, mode='jpeg'):
        self.app = os.path.join(home, 'Applications', 'Google Chrome.app')
        self.dir = os.path.join(self.app, 'Contents', 'MacOS')
        os.makedirs(self.dir, exist_ok=True)
        self.program = os.path.join(self.dir, 'Google Chrome')
        with open(self.program, 'w') as f:
            f.write(STAND_IN)
        os.chmod(self.program, os.stat(self.program).st_mode | stat.S_IXUSR)
        for name, data in (('tiny.jpg', TINY_JPEG), ('wide.png', png(600, 375)), ('junk.bin', b'not a picture')):
            with open(os.path.join(self.dir, name), 'wb') as f:
                f.write(data)
        self.mode(mode)

    def mode(self, mode):
        with open(os.path.join(self.dir, 'mode'), 'w') as f:
            f.write(mode)

    def calls(self):
        try:
            with open(os.path.join(self.dir, 'calls.jsonl')) as f:
                return [json.loads(line) for line in f if line.strip()]
        except OSError:
            return []
