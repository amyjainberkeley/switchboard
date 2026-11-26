import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# When claude-mem's usual port is taken, its install picks a free port a hundred or so above it, else one of its own
# (38800 to 38899). Never a number past 65535: the system refuses to connect to one at all (OverflowError), and the
# install stopped with "Something went wrong" when the taken port was near the top of the range.
# Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_packs_free_port.py'
import os
import socket
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard.packs import claudemem_install as CMI


class AFreePortNearATakenOne(unittest.TestCase):
    def tried(self, port, free):
        """The port _free_port_near picks when only `free` are free, and every port it asked about."""
        seen = []

        def port_free(n):
            seen.append(n)
            if not 0 <= n <= 65535:
                raise OverflowError('connect_ex(): port must be 0-65535.')    # as the socket call does
            return n in free
        with mock.patch.object(CMI, 'port_free', port_free):
            return CMI._free_port_near(port), seen

    def test_the_socket_call_itself_refuses_a_port_past_65535(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s.close)
        with self.assertRaises(OverflowError):                                  # what the stand-in above does
            s.connect_ex(('127.0.0.1', 65536))

    def test_near_the_top_of_the_range_it_never_asks_past_65535(self):
        for port in (65435, 65436, 65500, 65535):
            with self.subTest(port=port):
                got, seen = self.tried(port, free={38800})
                self.assertEqual(got, 38800)
                self.assertLessEqual(max(seen), 65535)

    def test_an_ordinary_port_moves_a_hundred_up(self):
        got, _ = self.tried(37777, free={37877, 37878})
        self.assertEqual(got, 37877)

    def test_none_free_is_said_plainly(self):
        with self.assertRaises(CMI.common.Refused) as said:
            self.tried(37777, free=set())
        self.assertEqual(str(said.exception), "No free port was found for claude-mem's worker.")


if __name__ == '__main__':
    unittest.main()
