"""Metadata scan controls; geometry is neither decoded nor an EDA oracle.

Estimate <256 MiB RSS: one <=4 MiB fixture, <=64 KiB scan chunk and bounded
selected metadata, one test worker. Run under 1 GiB / 180 s with GNU peak RSS.
"""
import json
from pathlib import Path
import random
import tempfile
import unittest

from integration.ibex_sg13g2_ez130.antenna_session.report_fields import fields


class ReportFields(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='report-fields-')
        self.path = Path(self.temp.name) / 'report.json'

    def tearDown(self):
        self.temp.cleanup()

    def scan(self, data, selected=('wanted',), **limits):
        self.path.write_bytes(data)
        return fields(self.path, set(selected), **limits)

    def test_valid_documents_match_standard_json_decoder(self):
        generator = random.Random(20261030)
        alphabet = ['a', '}', '[', '"', '\\', '\n', '\t', '\u00e9', '\U0001f30d']
        for number in range(250):
            value = dict(wanted={
                'number': generator.randint(-10**20, 10**20),
                'text': ''.join(generator.choices(alphabet, k=100)),
                'array': [None, True, 2.5, {'x': []}],
            }, skipped=[dict(text=''.join(generator.choices(alphabet, k=200)))
                       for _ in range(number % 7)])
            data = json.dumps(value, ensure_ascii=number % 2 == 0).encode()
            with self.subTest(number=number):
                self.assertEqual(self.scan(data), {'wanted': json.loads(data)['wanted']})

    def test_refill_at_each_escape_quote_and_bracket_boundary(self):
        suffixes = [b'"', b'\\"x"', b'\\\\"', b'\\u1234"', b'\\/"', b'\\\\\\"x"']
        for shift in range(-4, 5):
            for suffix in suffixes:
                prefix = b'{"skip":"'
                text = prefix + b'x' * (65536 - len(prefix) + shift) + suffix
                data = text + b',"wanted":{"v":[1,2,{"x":"y"}]}}'
                with self.subTest(shift=shift, suffix=suffix):
                    expected = json.loads(data)
                    self.assertEqual(self.scan(data), {'wanted': expected['wanted']})
        for shift in range(-3, 4):
            data = b'{"skip":[' + b' ' * (65536 - 10 + shift) + b'{"x":[]}],"wanted":42}'
            self.assertEqual(self.scan(data), {'wanted': 42})

    def test_selected_utf8_and_escaped_bytes_cross_chunk_boundary(self):
        for shift in range(-3, 4):
            value = 'x' * (65520 + shift) + '\U0001f30d' + '\\"\n'
            data = json.dumps({'wanted': value, 'skip': [1]}, ensure_ascii=False).encode()
            self.assertEqual(self.scan(data), {'wanted': value})

    def test_skipped_values_keep_metadata_only_grammar(self):
        # The scanner intentionally does not interpret skipped JSON values.
        for skipped in (b'undefined', b'[1 2,]', b'"\\q"', b'"\xff"', b'{}junk'):
            with self.subTest(skipped=skipped):
                self.assertEqual(self.scan(b'{"skip":' + skipped + b',"wanted":7}'), {'wanted': 7})
        with self.assertRaises(json.JSONDecodeError):
            self.scan(b'{"wanted":"\\q"}')

    def test_string_controls_are_rejected_even_after_escape_or_refill(self):
        for control in (0, 9, 10, 13, 31):
            for prefix in (b'', b'\\', b'x' * 65524, b'x' * 65524 + b'\\'):
                data = b'{"skip":"' + prefix + bytes([control]) + b'","wanted":1}'
                with self.subTest(control=control, length=len(prefix)), self.assertRaisesRegex(
                        ValueError, 'REPORT_STRING_CONTROL'):
                    self.scan(data)

    def test_duplicate_missing_and_malformed_top_level_are_rejected(self):
        cases = [
            b'[]', b'{"skip":1,"skip":2,"wanted":1}', b'{"skip":1}',
            b'{"wanted":}', b'{"wanted":,}', b'{"wanted" 1}',
            b'{"wanted":1 "x":2}', b'{"wanted":1}tail', b'{"wanted":1,}',
            b'{"skip":[},"wanted":1}', b'{"skip":],"wanted":1}',
            b'{"skip":"unterminated', b'{"wanted":[]', b'{"wanted":1',
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.scan(data)

    def test_depth_key_count_and_key_bytes_are_bounded(self):
        def nested(depth):
            return b'{"skip":' + b'[' * depth + b'0' + b']' * depth + b',"wanted":1}'
        self.assertEqual(self.scan(nested(64)), {'wanted': 1})
        with self.assertRaisesRegex(ValueError, 'REPORT_DEPTH_BUDGET'):
            self.scan(nested(65))
        data = {str(index): index for index in range(127)}
        data['wanted'] = 1
        self.assertEqual(self.scan(json.dumps(data).encode()), {'wanted': 1})
        data['extra'] = 1
        with self.assertRaisesRegex(ValueError, 'REPORT_DUPLICATE_KEY_OR_BUDGET'):
            self.scan(json.dumps(data).encode())
        with self.assertRaisesRegex(ValueError, 'REPORT_KEY_BUDGET_OR_FORMAT'):
            self.scan(json.dumps({'x' * 8192: 1, 'wanted': 1}).encode())

    def test_report_field_and_total_selected_caps_are_unchanged(self):
        data = b'{"wanted":[1,2],"second":123,"skip":[' + b'0,' * 10000 + b'0]}'
        self.assertEqual(self.scan(data, max_bytes=len(data), max_field_bytes=5), {'wanted': [1, 2]})
        with self.assertRaisesRegex(ValueError, 'REPORT_BYTE_BUDGET'):
            self.scan(data, max_bytes=len(data) - 1)
        with self.assertRaisesRegex(ValueError, 'REPORT_FIELD_BYTE_BUDGET'):
            self.scan(data, max_field_bytes=4)
        self.assertEqual(self.scan(data, selected=('wanted', 'second'), max_selected_bytes=8),
                         {'wanted': [1, 2], 'second': 123})
        with self.assertRaisesRegex(ValueError, 'REPORT_SELECTED_BYTE_BUDGET'):
            self.scan(data, selected=('wanted', 'second'), max_selected_bytes=7)

    def test_large_skipped_array_never_uses_json_decoder(self):
        # Deliberately non-JSON entries distinguish scanning from decoding.
        data = b'{"skip":[' + b'{"x":"long\\\"text", "y":invalid},' * 100000 + b'null],"wanted":1}'
        self.assertEqual(self.scan(data), {'wanted': 1})


if __name__ == '__main__':
    unittest.main()
