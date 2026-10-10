import shutil
import subprocess
import unittest
from unittest.mock import patch

from scripts import config_codec


PUBLIC_HEADER = '24236c696e746563682324'
PUBLIC_IV = 'publicfixture'


def public_lintech_fixture(plaintext, *, iv=PUBLIC_IV, padding=True):
    """Create a synthetic envelope using public, fixed fixture metadata."""
    command = [shutil.which('openssl') or 'openssl', 'enc', '-aes-128-cbc',
               '-K', b'lintech000000000'.hex(),
               '-iv', iv.lower().encode('ascii').ljust(16, b'0').hex()]
    if not padding:
        command.append('-nopad')
    result = subprocess.run(command, input=plaintext, capture_output=True,
                            check=True, timeout=5)
    return PUBLIC_HEADER + result.stdout.hex() + iv.encode('ascii').hex()


class PublicConfigCodecTests(unittest.TestCase):
    def setUp(self):
        self.codec = config_codec

    def encrypt(self, plaintext, **kwargs):
        if not shutil.which('openssl'):
            self.skipTest('OpenSSL is unavailable for the real encryption fixture')
        return public_lintech_fixture(plaintext, **kwargs)

    def test_public_fixture_round_trip_preserves_unicode_json(self):
        plaintext = '{"sites":[{"key":"fixture","name":"测试线路","api":"csp_Fixture"}]}'
        encoded = self.encrypt(plaintext.encode('utf-8'))
        self.assertEqual(self.codec.decode_public_config(encoded), plaintext)

    def test_public_iv_uses_the_clients_lowercase_and_zero_padding(self):
        encoded = self.encrypt(b'{"sites":[]}', iv='PUBLICFIXTURE')
        self.assertEqual(self.codec.decode_public_config(encoded), '{"sites":[]}')

    def test_uppercase_hex_is_the_same_public_envelope(self):
        encoded = self.encrypt(b'{"sites":[]}').upper()
        self.assertEqual(self.codec.decode_public_config(encoded), '{"sites":[]}')

    def test_collector_reads_public_jsonc_and_keeps_encrypted_format_metadata(self):
        from scripts.collector import classify, parse_jsonc
        from scripts.cloud import describe_cloud_config

        plaintext = '''// synthetic public config
        {"sites":[
          {"key":"setup","name":"配置中心","type":3,"api":"csp_Config"},
          {"key":"ali","name":"阿里云盘","type":3,"api":"csp_PanAli"},
          {"key":"quark","name":"夸克网盘","type":3,"api":"csp_PanQuark"},
        ],}'''
        encoded = self.encrypt(plaintext.encode('utf-8'))
        self.assertEqual(self.codec.decode_public_config(encoded), plaintext)
        parsed = parse_jsonc(encoded)
        self.assertEqual(classify(encoded, 'https://example.com/public-config'),
                         {'kind':'config', 'format':'影视仓加密', 'count':3})
        self.assertEqual(describe_cloud_config(parsed), {
            'site_count':2, 'providers':['阿里云盘', '夸克'],
            'login_names':['配置中心'],
        })

    def test_collector_keeps_corrupt_public_envelope_pending(self):
        from scripts.collector import classify, parse_jsonc

        encoded = self.encrypt(b'x' * 15 + b'\x00', padding=False)
        self.assertEqual(classify(encoded, 'https://example.com/public-config'),
                         {'kind':'config', 'format':'影视仓加密'})
        with self.assertRaises(ValueError):
            parse_jsonc(encoded)

    def test_plain_text_and_unrecognized_password_formats_are_unchanged(self):
        for text in ('  {"sites":[]}\n', '$#other#$not-a-public-config',
                     '2423707269766174652324' + 'a' * 64):
            with self.subTest(text=text):
                self.assertEqual(self.codec.decode_public_config(text), text)

    def test_invalid_hex_truncation_and_non_block_ciphertext_are_rejected(self):
        for suffix in ('xyz', 'a', '00' * 13,
                       '00' * 15 + PUBLIC_IV.encode('ascii').hex()):
            with self.subTest(suffix=suffix):
                with self.assertRaises(ValueError):
                    self.codec.decode_public_config(PUBLIC_HEADER + suffix)

    def test_non_ascii_iv_is_rejected(self):
        encoded = self.encrypt(b'{"sites":[]}')
        with self.assertRaises(ValueError):
            self.codec.decode_public_config(encoded[:-26] + 'ff' + encoded[-24:])

    def test_corrupt_pkcs7_padding_is_rejected(self):
        encoded = self.encrypt(b'x' * 15 + b'\x00', padding=False)
        with self.assertRaises(ValueError):
            self.codec.decode_public_config(encoded)

    def test_non_utf8_plaintext_is_rejected(self):
        encoded = self.encrypt(b'\xff')
        with self.assertRaises(ValueError):
            self.codec.decode_public_config(encoded)

    def test_recognized_input_over_source_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            self.codec.decode_public_config(PUBLIC_HEADER + 'a' * 3_000_000)

    def test_missing_openssl_and_timeout_fail_without_exposing_process_data(self):
        encoded = PUBLIC_HEADER + '00' * 16 + PUBLIC_IV.encode('ascii').hex()
        failures = [FileNotFoundError('fixture-private-detail'),
                    subprocess.TimeoutExpired('openssl', 5, output=b'fixture-private-detail')]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch('scripts.config_codec.subprocess.run', side_effect=failure):
                    with self.assertRaises(ValueError) as context:
                        self.codec.decode_public_config(encoded)
                    self.assertNotIn('fixture-private-detail', str(context.exception))


if __name__ == '__main__':
    unittest.main()
