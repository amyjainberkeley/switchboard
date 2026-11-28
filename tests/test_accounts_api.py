"""The engine's contract: the rest of Switchboard codes against switchboard/accounts/api.py, so its names and fields
stay put."""
import dataclasses
import inspect
import unittest

from switchboard.accounts import api


class TheContract(unittest.TestCase):
    def test_every_name_the_rest_of_switchboard_calls_is_there(self):
        for name in ('accounts', 'live', 'find', 'switch', 'add_current', 'add_login', 'remove', 'set_alias',
                     'reorder', 'run_as', 'keep_pass', 'upkeep_pass', 'resolve_journal', 'health', 'access_token',
                     'readings', 'feed_status_line', 'request_read',
                     'activity', 'decide', 'auto_settings', 'set_auto', 'auto_status', 'request_need_switch'):
            self.assertTrue(callable(getattr(api, name, None)), name)
        self.assertEqual(list(inspect.signature(api.add_login).parameters), ['config_dir'])

    def test_the_types_keep_their_fields(self):
        want = {
            api.Read: ['kind', 'value', 'why', 'provenance', 'degraded', 'age_s'],
            api.Account: ['id', 'email', 'org_id', 'org_name', 'plan', 'alias', 'order', 'status',
                          'refresh_expires_at', 'added_at', 'fingerprint', 'tier', 'org_type'],
            api.Live: ['state', 'account_id', 'email', 'proof', 'disagree', 'degraded', 'why', 'bearer', 'from_file',
                       'expires_ms', 'rx_ms'],
            api.SwitchResult: ['ok', 'from_id', 'to_id', 'kind', 'source', 'reason', 'rolled_back', 'note'],
            api.Window: ['name', 'pct', 'resets_at', 'read_at', 'source'],
            api.Reading: ['account_id', 'windows', 'read_at', 'source', 'frozen', 'error', 'checked_at', 'elsewhere'],
            api.Decision: ['action', 'target', 'kind', 'reason', 'hold'],
        }
        for cls, fields in want.items():
            self.assertEqual([f.name for f in dataclasses.fields(cls)], fields, cls.__name__)

    def test_switch_takes_its_words_by_name(self):
        params = inspect.signature(api.switch).parameters
        self.assertEqual([p for p in params if params[p].kind == inspect.Parameter.KEYWORD_ONLY],
                         ['kind', 'source', 'reason'])

    def test_errors_carry_a_kind_a_person_can_act_on(self):
        self.assertEqual(api.AccountsError('keychain_slow', 'x').kind, 'keychain_slow')
        self.assertEqual(api.AccountsError('made-up', 'x').kind, 'other')
