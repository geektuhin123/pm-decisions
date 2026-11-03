import pytest

from pm_jev.redact import redact

# Fake credentials are assembled at runtime so the repository never contains a literal
# that secret scanners (such as GitHub push protection) would mistake for a real key.
FAKE_GITHUB = "gh" + "p_" + "x" * 24
FAKE_AWS = "AK" + "IA" + "TESTONLYEXAMPLE0"
FAKE_STRIPE = "sk" + "_live_" + "x" * 16
FAKE_PEM = "-----BEGIN " + "RSA PRIVATE KEY-----\nTESTONLY\n-----END " + "RSA PRIVATE KEY-----"


@pytest.mark.parametrize("text,label", [
    ("write to priya.n@example.org today", "EMAIL"),
    ("call +91 98300 12345", "PHONE"),
    ("US office (415) 555-0132", "PHONE"),
    ("London 020 7946 0958", "PHONE"),
    ("card 4111 1111 1111 1111", "CARD_NUMBER"),
    (f"token {FAKE_GITHUB}", "API_KEY"),
    (f"{FAKE_AWS} in the config", "API_KEY"),
    ("password: hunter2hunter2", "SECRET_ASSIGNMENT"),
    ("host 10.0.0.12 is down", "IP_ADDRESS"),
    ("Server=db.internal;Password=x1y2z3", "CONNECTION_STRING"),
])
def test_redacts_structured_secrets(text, label):
    result = redact(text)
    assert label in result.findings
    assert f"[{label}]" in result.text


@pytest.mark.parametrize("text", [
    "released on 2026-11-03",
    "released on 03/11/2026",
    "order 1234567890123 shipped",      # long id, fails Luhn, no separators
    "ticket PROD-1234 and version 2.10.3",
    "grew 18% to 4,200 users",
])
def test_leaves_ordinary_numbers_alone(text):
    result = redact(text)
    assert result.findings == {}
    assert result.text == text


def test_private_key_block():
    assert redact(f"key: {FAKE_PEM}").findings == {"PRIVATE_KEY": 1}


def test_no_double_counting_assignment_after_key():
    result = redact(f"api_key={FAKE_STRIPE}")
    assert result.findings == {"API_KEY": 1}
    assert result.text == "api_key=[API_KEY]"
