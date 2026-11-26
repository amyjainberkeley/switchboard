"""A switch run in a child process that kills itself with SIGKILL at a named step (test_accounts_switch's cut-off
tests). Its Keychain is a file both processes share; it never reaches the network."""
import json
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fakes_accounts import FileKeychain  # imports _isolate: the child is watched like the tests
from switchboard.accounts import context, net, switcher


class NoNetwork:
    def resolve(self, host, deadline_s):
        return False

    def request(self, *a, **kw):
        raise net.NetError('dns', False, 'no network in this child')


def main():
    spec = json.loads(sys.argv[1])
    ctx = context.Context(env=spec['env'], home=spec['home'], data_dir=spec['data'],
                          keychain=FileKeychain(spec['keychain']), http=NoNetwork(), events=lambda *a: None,
                          log=lambda text: None, disk_free=lambda p: 10 ** 12, proc_start=lambda pid: 'x',
                          pid_alive=lambda pid: True)

    def hook(step):
        if step == spec['kill_at']:
            os.kill(os.getpid(), signal.SIGKILL)

    switcher._HOOK = hook
    res = switcher.switch(ctx, spec['target'])
    print(json.dumps({'ok': res.ok}))


if __name__ == '__main__':
    main()
