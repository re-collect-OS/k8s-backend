# -*- coding: utf-8 -*-
import base64
import hashlib
import re
from secrets import SystemRandom, token_urlsafe

UNICODE_ASCII_CHARACTER_SET = (
    "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789"
)


def generate_token(length: int = 30, chars: str = UNICODE_ASCII_CHARACTER_SET):
    """Generates a non-guessable OAuth token

    OAuth (1 and 2) does not specify the format of tokens except that they
    should be strings of random characters. Tokens should not be guessable
    and entropy when generating the random characters is important. Which is
    why SystemRandom is used instead of the default random.choice method.
    """
    return "".join(SystemRandom().choice(chars) for _ in range(length))


def create_code_verifier(length: int) -> str:
    """Create PKCE **code_verifier** used in computing **code_challenge**.
    See `RFC7636 Section 4.1`_
    :param length: REQUIRED. The length of the code_verifier.
    The client first creates a code verifier, "code_verifier", for each
    OAuth 2.0 [RFC6749] Authorization Request, in the following manner:
    .. code-block:: text
           code_verifier = high-entropy cryptographic random STRING using the
           unreserved characters [A-Z] / [a-z] / [0-9] / "-" / "." / "_" / "~"
           from Section 2.3 of [RFC3986], with a minimum length of 43 characters
           and a maximum length of 128 characters.
    .. _`RFC7636 Section 4.1`: https://tools.ietf.org/html/rfc7636#section-4.1
    """
    if not (length >= 43 or length <= 128):
        raise ValueError("Length must be in [43, 128].")
    allowed_characters = re.compile("^[A-Zaa-z0-9-._~]")
    code_verifier = token_urlsafe(length)
    if not re.search(allowed_characters, code_verifier):
        raise ValueError("code_verifier contains invalid characters.")
    return code_verifier


def create_S256_code_challenge(code_verifier: str) -> str:
    """Create PKCE **code_challenge** derived from the **code_verifier**.
    See _`RFC7636 Section 4.2`: https://tools.ietf.org/html/rfc7636#section-4.2
    """
    h = hashlib.sha256()
    h.update(code_verifier.encode(encoding="ascii"))
    sha256_val = h.digest()
    code_challenge = bytes.decode(base64.urlsafe_b64encode(sha256_val))
    # replace '+' with '-', '/' with '_', and remove trailing '='
    code_challenge = code_challenge.replace("+", "-").replace("/", "_").replace("=", "")
    return code_challenge
