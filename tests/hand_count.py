"""A second, deliberately naive count of the same history, to check switchboard/usage.py.

It shares no code with usage.py. It walks every folder, opens every .jsonl file, parses
every line with json.loads, and keeps a plain dict per request. Slow on purpose: nothing is
skipped, cached or guessed.

The counting rules it applies (the same rules, written again from scratch):
  - a request is a line with "type": "assistant" whose message.model is not "<synthetic>",
    keyed by (message.id, requestId); a line with neither is skipped;
  - per request, each usage field is the largest seen, and its time the earliest seen;
  - a timestamp with no zone is UTC; a request is counted when since <= time < until;
  - a tool call is a tool_use id inside a counted request, each id once;
  - "who": the copy in the chat file wins, else the agent copy with the lowest spawnDepth
    in its .meta.json (no meta counts as 1), then the first path in sort order. A chat file is
    <root>/<project>/<session>.jsonl; an agent under a "workflows" folder is a workflow agent.

Run by hand:  python3 tests/hand_count.py --day 2026-11-12   (local day; prints numbers only)
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone


def when(text):
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.endswith('Z') or text.endswith('z'):
        text = text[:-1] + '+00:00'
    try:
        d = datetime.fromisoformat(text)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def model_family(name):
    name = (name or '').lower()
    if 'fable' in name:
        return 'Fable'
    if 'opus' in name:
        return 'Opus'
    if 'sonnet' in name:
        return 'Sonnet'
    if 'haiku' in name:
        return 'Haiku'
    return 'Other'


def count(root, since, until):
    requests = {}
    for folder, subfolders, names in os.walk(root):
        subfolders.sort()
        for name in sorted(names):
            if not name.endswith('.jsonl'):
                continue
            path = os.path.join(folder, name)
            depth_from_root = len(os.path.relpath(path, root).split(os.sep))
            if depth_from_root == 2:
                who, rank = 'chat', 0
            else:
                parts = os.path.relpath(path, root).split(os.sep)
                who = 'workflows' if 'workflows' in parts else 'helpers'
                rank = 1
                try:
                    with open(path[:-6] + '.meta.json') as f:
                        m = json.load(f)
                    if isinstance(m.get('spawnDepth'), int) and m['spawnDepth'] > 0:
                        rank = m['spawnDepth']
                except (OSError, ValueError, AttributeError):
                    pass
            try:
                f = open(path, 'rb')
            except OSError:
                continue
            with f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(d, dict) or d.get('type') != 'assistant':
                        continue
                    msg = d.get('message')
                    if not isinstance(msg, dict) or msg.get('model') == '<synthetic>':
                        continue
                    if not msg.get('id') and not d.get('requestId'):
                        continue
                    key = (msg.get('id'), d.get('requestId'))
                    usage = msg.get('usage') or {}
                    hour = (usage.get('cache_creation') or {}) if isinstance(usage.get('cache_creation'), dict) else {}
                    tokens = [usage.get('input_tokens') or 0, usage.get('output_tokens') or 0,
                              usage.get('cache_creation_input_tokens') or 0, usage.get('cache_read_input_tokens') or 0,
                              hour.get('ephemeral_1h_input_tokens') or 0]
                    tool_ids = set()
                    for block in msg.get('content') or []:
                        if isinstance(block, dict) and block.get('type') == 'tool_use':
                            tool_ids.add(block.get('id'))
                    t = when(d.get('timestamp'))
                    if key not in requests:
                        requests[key] = {'time': t, 'tokens': tokens, 'tools': tool_ids, 'model': msg.get('model'),
                                         'owner': (rank, path), 'who': who}
                        continue
                    r = requests[key]
                    if t is not None and (r['time'] is None or t < r['time']):
                        r['time'] = t
                    r['tokens'] = [max(a, b) for a, b in zip(r['tokens'], tokens)]
                    r['tools'] |= tool_ids
                    if (rank, path) < r['owner']:
                        r['owner'], r['who'] = (rank, path), who

    out = {'requests': 0, 'input': 0, 'output': 0, 'cache_write': 0, 'cache_read': 0, 'cache_write_1h': 0, 'tool_calls': 0,
           'family_weight': {}, 'who_weight': {}}
    seen_tools = set()
    for r in requests.values():
        if r['time'] is None or not (since <= r['time'] < until):
            continue
        i, o, cw, cr, cw1h = r['tokens']
        out['requests'] += 1
        out['input'] += i
        out['output'] += o
        out['cache_write'] += cw
        out['cache_read'] += cr
        out['cache_write_1h'] += cw1h
        w = i + 1.25 * (cw - cw1h) + 2 * cw1h + 0.05 * cr + 5 * o           # today's prices
        fam = model_family(r['model'])
        out['family_weight'][fam] = out['family_weight'].get(fam, 0) + w
        out['who_weight'][r['who']] = out['who_weight'].get(r['who'], 0) + w
        seen_tools |= r['tools']
    out['tool_calls'] = len(seen_tools)
    out['weight'] = (out['input'] + 1.25 * (out['cache_write'] - out['cache_write_1h']) + 2 * out['cache_write_1h']
                     + 0.05 * out['cache_read'] + 5 * out['output'])
    return out


def main():
    args = sys.argv[1:]
    root = os.path.join(os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude'), 'projects')
    if '--root' in args:
        root = args[args.index('--root') + 1]
    day = args[args.index('--day') + 1]
    start = datetime.strptime(day, '%Y-%m-%d')
    end = start + timedelta(days=1)
    since = time.mktime(start.timetuple()[:8] + (-1,))
    until = time.mktime(end.timetuple()[:8] + (-1,))
    t0 = time.time()
    out = count(root, since, until)
    out['seconds'] = round(time.time() - t0, 1)
    print(json.dumps(out, indent=1, sort_keys=True))


if __name__ == '__main__':
    main()
