import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The small JSON Schema checker that the stand-in fake-codex carries (tests/fakes_codex.py). It decides whether an
# answer the stand-in gives, or a line Switchboard sends, has the shape Codex's own generated schema says, so it must
# never be kinder than the schema: three known-bad answers are rejected, the stand-in's own answers fit both copied
# versions, and a keyword it does not know is an error, not a pass.
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

import fakes_codex
from fakes_codex import VERSIONS, schema, schema_errors


class TheCheckerIsBoundInPlainSight(unittest.TestCase):

    def test_schema_errors_is_a_top_level_name_a_reader_of_the_file_can_see(self):
        # The checker is pasted into the stand-in as text and run with exec; a tool that only reads files (it runs
        # nothing) must still find the name the tests import.
        import ast
        with open(fakes_codex.__file__, encoding='utf-8') as f:
            tree = ast.parse(f.read())
        bound = {t.id for node in tree.body if isinstance(node, ast.Assign) for t in node.targets
                 if isinstance(t, ast.Name)}
        bound |= {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        self.assertIn('schema_errors', bound)
        self.assertIs(fakes_codex.schema_errors, fakes_codex.__dict__['schema_errors'])


class KnownBadAnswersAreRejected(unittest.TestCase):

    def test_a_missing_required_field_inside_a_oneOf(self):
        # A ChatGPT account must name its plan; without planType no branch of Account's oneOf fits.
        for v in VERSIONS:
            answer = {'account': {'type': 'chatgpt', 'email': 'maya@northwind.example'}, 'requiresOpenaiAuth': True}
            errs = schema_errors(schema(v, 'GetAccountResponse'), answer)
            self.assertTrue(errs, v)
            self.assertTrue(any('oneOf' in e for e in errs), errs)

    def test_a_wrong_type(self):
        for v in VERSIONS:
            answer = copy.deepcopy(fakes_codex.RATE_LIMITS)
            answer['rateLimits']['primary']['usedPercent'] = '42'
            errs = schema_errors(schema(v, 'GetAccountRateLimitsResponse'), answer)
            self.assertTrue(any('usedPercent' in e and 'str' in e for e in errs), errs)

    def test_an_extra_property_where_additionalProperties_is_false(self):
        closed = {'type': 'object', 'properties': {'code': {'type': 'integer'}}, 'additionalProperties': False}
        self.assertEqual(schema_errors(closed, {'code': 1}), [])
        errs = schema_errors(closed, {'code': 1, 'extra': True})
        self.assertEqual(len(errs), 1)
        self.assertIn('extra', errs[0])

    def test_a_missing_required_top_level_field(self):
        for v in VERSIONS:
            self.assertTrue(schema_errors(schema(v, 'GetAccountRateLimitsResponse'), {'rateLimitsByLimitId': {}}))
            self.assertTrue(schema_errors(schema(v, 'InitializeResponse'), {'codexHome': '/Users/maya/.codex'}))


class TheStandInsOwnAnswersFit(unittest.TestCase):

    def test_the_default_answers_fit_both_versions(self):
        init = {'codexHome': '/Users/maya/.codex', 'platformFamily': 'unix', 'platformOs': 'macos',
                'userAgent': 'fake-codex/0'}
        for v in VERSIONS:
            self.assertEqual(schema_errors(schema(v, 'GetAccountResponse'), fakes_codex.ACCOUNT), [], v)
            self.assertEqual(schema_errors(schema(v, 'GetAccountRateLimitsResponse'), fakes_codex.RATE_LIMITS), [], v)
            self.assertEqual(schema_errors(schema(v, 'InitializeResponse'), init), [], v)

    def test_an_api_key_account_and_no_account_fit(self):
        for v in VERSIONS:
            s = schema(v, 'GetAccountResponse')
            self.assertEqual(schema_errors(s, {'account': {'type': 'apiKey'}, 'requiresOpenaiAuth': True}), [])
            self.assertEqual(schema_errors(s, {'account': None, 'requiresOpenaiAuth': True}), [])

    def test_the_versions_differ_where_their_schemas_do(self):
        # 0.169.1 added the plan "promax" (and "sci"); 0.155.1's schema does not have it.
        answer = {'account': {'type': 'chatgpt', 'email': None, 'planType': 'promax'}, 'requiresOpenaiAuth': True}
        self.assertEqual(schema_errors(schema('0.169.1', 'GetAccountResponse'), answer), [])
        self.assertTrue(schema_errors(schema('0.155.1', 'GetAccountResponse'), answer))

    def test_the_sent_lines_params_fit(self):
        for v in VERSIONS:
            self.assertEqual(schema_errors(schema(v, 'GetAccountParams'), {'refreshToken': False}), [])
            self.assertEqual(schema_errors(schema(v, 'NullableGetAccountRateLimitsParams'),
                                           {'excludeResetCreditDetails': True}), [])
            self.assertEqual(schema_errors(schema(v, 'NullableGetAccountRateLimitsParams'), None), [])
            self.assertTrue(schema_errors(schema(v, 'GetAccountParams'), {'refreshToken': 'no'}))


class TheCheckerIsNeverKinder(unittest.TestCase):

    def test_an_unknown_keyword_is_an_error(self):
        self.assertTrue(schema_errors({'type': 'string', 'pattern': '^a'}, 'b'))

    def test_a_ref_that_is_not_there_is_an_error(self):
        self.assertTrue(schema_errors({'$ref': '#/definitions/Nope'}, 1))

    def test_booleans_are_not_integers_and_enums_are_typed(self):
        self.assertTrue(schema_errors({'type': 'integer'}, True))
        self.assertTrue(schema_errors({'enum': [1]}, True))
        self.assertEqual(schema_errors({'enum': [1]}, 1), [])

    def test_integer_formats_have_ranges(self):
        self.assertTrue(schema_errors({'type': 'integer', 'format': 'int32'}, 1 << 40))
        self.assertEqual(schema_errors({'type': 'integer', 'format': 'int64'}, 1 << 40), [])

    def test_oneOf_with_two_fits_is_an_error(self):
        self.assertTrue(schema_errors({'oneOf': [{'type': 'integer'}, {'type': 'number'}]}, 3))
        self.assertEqual(schema_errors({'oneOf': [{'type': 'integer'}, {'type': 'string'}]}, 3), [])


if __name__ == '__main__':
    unittest.main()
