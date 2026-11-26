"""Time the memory index on a made-up history the size of a busy week, then delete it.

    SWITCHBOARD_HOME="$(mktemp -d)/data" python3 tests/fixtures/packs/memory/bench.py [--mb 1600] [--pad 100000]

Copies the 40 made-up chats until the tree holds about --mb megabytes, with --pad bytes of tool output
per turn (and as much again in each chat's helper transcript), so prompts and replies are well under 1% of
the bytes, as in real history. Prints numbers only. Everything is written to, and removed from, a temp folder.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', '..'))

import make_history as MH  # noqa: E402
from switchboard.packs import memory as M  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mb', type=float, default=1600)
    ap.add_argument('--pad', type=int, default=100000)
    a = ap.parse_args()
    chats, questions = MH.load()
    work = tempfile.mkdtemp(prefix='switchboard-bench-')
    try:
        root = os.path.join(work, 'projects')
        os.makedirs(root)
        probe = os.path.join(work, 'probe')
        _, one = MH.build(probe, copies=1, pad=a.pad, chats=chats)
        shutil.rmtree(probe)
        copies = max(1, int(round(a.mb * 1e6 / one)))
        t = time.time()
        ids, total = MH.build(root, copies=copies, pad=a.pad, chats=chats)
        made = time.time() - t
        chat_bytes = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(root) for f in fs
                         if f.endswith('.jsonl') and 'subagents' not in dp)
        index = os.path.join(work, 'index', M.INDEX_NAME)
        first = M.update_index(root, index)
        again = M.update_index(root, index)
        # one chat grows by a turn
        path = os.path.join(root, MH.project_dir(chats[0]['folder']), ids[chats[0]['id']] + '.jsonl')
        with open(path, 'a') as f:
            f.write(json.dumps({'type': 'user', 'cwd': chats[0]['folder'], 'timestamp': '2026-09-30T10:00:00Z',
                                'message': {'role': 'user', 'content': 'one more question'}}) + '\n')
        grow = M.update_index(root, index)
        by_session = dict((MH.session_id(c['id'], k), c['id']) for c in chats for k in range(copies))
        lat, hits = [], 0
        for q in questions:
            t = time.time()
            got = M.search(q['q'], 3, index)
            lat.append(time.time() - t)
            hits += q['expect'] in [by_session.get(r['session']) for r in got]
        print(json.dumps({
            'python': sys.version.split()[0], 'tree_mb': round(total / 1e6, 1), 'chat_files_mb': round(chat_bytes / 1e6, 1),
            'copies_of_40_chats': copies, 'chat_files': first['files'], 'tree_written_s': round(made, 1),
            'first_index_s': first['seconds'], 'messages': first['messages'], 'index_mb': round(first['index_bytes'] / 1e6, 2),
            'no_change_update_s': again['seconds'], 'one_chat_grew_update_s': grow['seconds'],
            'grow_bytes_read': grow['bytes_read'], 'search_ms_median': round(sorted(lat)[len(lat) // 2] * 1000, 1),
            'search_ms_max': round(max(lat) * 1000, 1), 'right_chat_or_a_copy_in_top3': hits}, indent=1))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
