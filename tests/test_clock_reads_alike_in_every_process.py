"""A time one process saves is measured rightly by another: the loop's heartbeat read by the page, a switch a hook
made read by the loop. For that the monotonic clock behind those times (usage.Clock.mono) has to read alike in every
process on the Mac. Apple's own Python 3.9 starts time.monotonic() from zero in each process, so these start real
child processes on the Python the tests run on, one of them a second and more before the other."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from switchboard.accounts import usage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Reads the clock after waiting argv[1] seconds.
READS = r'''
import json, sys, time
sys.path.insert(0, %(root)r)
from switchboard.accounts import usage
time.sleep(float(sys.argv[1]))
c = usage.Clock()
print(json.dumps({'mono': c.mono(), 'wall': c.wall()}))
'''

# Writes a heartbeat and a stamp the way the loop does, straight after it starts.
WRITES = r'''
import json, sys
sys.path.insert(0, %(root)r)
from switchboard.accounts import autoloop, usage
c = usage.Clock()
autoloop.Heartbeat(c, path=sys.argv[1]).beat('starting')
usage.write_atomic(sys.argv[2], usage.stamp(c))
'''

# Started first: waits for the file argv[3], then measures the heartbeat and the stamp as the page and the loop do.
MEASURES = r'''
import json, os, sys, time
sys.path.insert(0, %(root)r)
from switchboard.accounts import autoloop, usage
end = time.time() + 60
while not os.path.exists(sys.argv[3]) and time.time() < end:
    time.sleep(0.02)
c = usage.Clock()
status = autoloop.status(c, path=sys.argv[1])
beat = usage.read_json(sys.argv[1])
stamp = usage.read_json(sys.argv[2])
now = c.wall()
print(json.dumps({'beat_age': status['beat_age_s'], 'beat_by_wall': now - beat['wall'],
                  'stamp_age': usage.since_stamp(stamp, c), 'stamp_by_wall': now - stamp['t']}))
'''


class Children(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='switchboard-clock-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.env = dict(os.environ, SWITCHBOARD_HOME=os.path.join(self.dir, 'data'))

    def script(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, 'w') as f:
            f.write(text % {'root': ROOT})
        return path

    def start(self, path, *args):
        return subprocess.Popen([sys.executable, '-I', '-B', path] + list(args), env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    def end(self, child):
        out, err = child.communicate(timeout=120)
        self.assertEqual(child.returncode, 0, err)
        return json.loads(out) if out.strip() else None


class TheClockReadsAlikeInEveryProcess(Children):
    def test_a_process_started_a_second_earlier_reads_the_same_clock(self):
        reads = self.script('reads.py', READS)
        first = self.end(self.start(reads, '1.0'))          # a second old when it reads
        second = self.end(self.start(reads, '0'))            # just started when it reads
        by_wall = second['wall'] - first['wall']
        by_mono = second['mono'] - first['mono']
        self.assertLess(abs(by_mono - by_wall), 0.5,
                        'between the two reads the wall clock moved %.2f s and the monotonic clock %.2f s'
                        % (by_wall, by_mono))

    def test_a_heartbeat_and_a_stamp_are_as_old_as_they_are_to_a_process_that_started_earlier(self):
        beat, stamp, go = (os.path.join(self.dir, n) for n in ('heartbeat.json', 'stamp.json', 'go'))
        reader = self.start(self.script('measures.py', MEASURES), beat, stamp, go)
        try:
            time.sleep(1.2)                                  # the reader is over a second old before anything is written
            self.end(self.start(self.script('writes.py', WRITES), beat, stamp))
            with open(go, 'w'):
                pass
            got = self.end(reader)
        finally:
            if reader.poll() is None:
                reader.kill()
                reader.communicate()
        self.assertIsNotNone(got['beat_age'])
        self.assertLess(abs(got['beat_age'] - got['beat_by_wall']), 0.5, got)
        self.assertLess(abs(got['stamp_age'] - got['stamp_by_wall']), 0.5, got)


class TheClockStillMeasuresWaits(unittest.TestCase):
    def test_it_counts_seconds_and_never_goes_back(self):
        c = usage.Clock()
        a = c.mono()
        time.sleep(0.2)
        b = c.mono()
        self.assertGreaterEqual(b - a, 0.15)
        self.assertLess(b - a, 5.0)
        reads = [c.mono() for _ in range(200)]
        self.assertEqual(reads, sorted(reads))


if __name__ == '__main__':
    unittest.main()
