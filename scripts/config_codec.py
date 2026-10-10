"""Decode only the public lintech envelope understood by the TV client."""

import re
import subprocess


MAX_BYTES = 3_000_000
PUBLIC_HEADER = '24236c696e746563682324'
_PUBLIC_KEY = b'lintech000000000'
_ERROR = 'Unable to decode the public config.'


def decode_public_config(text):
    """Return plain text, or decode the exact public, self-described envelope.

    This does not attempt any format requiring a user password. OpenSSL only
    receives ciphertext and fixed public format metadata; no downloaded code
    is loaded or executed.
    """
    raw = text.strip()
    if raw[:len(PUBLIC_HEADER)].lower() != PUBLIC_HEADER:
        return text
    if len(text) > MAX_BYTES or len(raw) % 2 or not re.fullmatch(r'[0-9a-fA-F]+', raw):
        raise ValueError(_ERROR)
    header_end = raw.index('2324') + 4
    cipher_hex = raw[header_end:-26]
    if not cipher_hex or len(cipher_hex) % 32:
        raise ValueError(_ERROR)
    try:
        iv_text = bytes.fromhex(raw[-26:]).decode('ascii').strip().lower()
        iv = iv_text.ljust(16, '0').encode('ascii')
        ciphertext = bytes.fromhex(cipher_hex)
        result = subprocess.run(
            ['openssl', 'enc', '-d', '-aes-128-cbc',
             '-K', _PUBLIC_KEY.hex(), '-iv', iv.hex()],
            input=ciphertext, capture_output=True, timeout=5,
        )
        if result.returncode or len(result.stdout) > MAX_BYTES:
            raise ValueError(_ERROR)
        return result.stdout.decode('utf-8')
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ValueError(_ERROR) from None
