"""Tests for the privacy filter."""

from openclaw_memory.privacy import PrivacyFilter


def test_detect_openai_key():
    pf = PrivacyFilter(
        patterns=[r"sk-[a-zA-Z0-9]{20,}"],
        enabled=True,
    )
    assert pf.contains_sensitive("my key is sk-abcdefghij1234567890abcdef")
    assert not pf.contains_sensitive("this is normal text")


def test_detect_github_token():
    pf = PrivacyFilter(
        patterns=[r"ghp_[a-zA-Z0-9]{36}"],
        enabled=True,
    )
    assert pf.contains_sensitive("token: ghp_abcdefghijklmnopqrstuvwxyz1234567890")
    assert not pf.contains_sensitive("ghp_short")


def test_detect_password():
    pf = PrivacyFilter(
        patterns=[r"password\s*[:=]\s*\S+"],
        enabled=True,
    )
    assert pf.contains_sensitive("password = my_secret_123")
    assert pf.contains_sensitive("password: hunter2")
    assert not pf.contains_sensitive("please change your password")


def test_detect_internal_ip():
    pf = PrivacyFilter(
        patterns=[r"192\.168\.\d+\.\d+", r"localhost:\d+"],
        enabled=True,
    )
    assert pf.contains_sensitive("server at 192.168.1.100")
    assert pf.contains_sensitive("running on localhost:3000")
    assert not pf.contains_sensitive("public IP 8.8.8.8")


def test_disabled():
    pf = PrivacyFilter(
        patterns=[r"sk-[a-zA-Z0-9]{20,}"],
        enabled=False,
    )
    assert not pf.contains_sensitive("sk-abcdefghij1234567890abcdef")


def test_get_violations():
    pf = PrivacyFilter(
        patterns=[r"sk-[a-zA-Z0-9]{20,}", r"password\s*[:=]\s*\S+"],
        enabled=True,
    )
    text = "key: sk-abcdefghij1234567890abcdef and password = secret"
    violations = pf.get_violations(text)
    assert len(violations) == 2


def test_redact():
    pf = PrivacyFilter(
        patterns=[r"sk-[a-zA-Z0-9]{20,}"],
        enabled=True,
    )
    text = "key is sk-abcdefghij1234567890abcdef here"
    redacted = pf.redact(text)
    assert "[REDACTED]" in redacted
    assert "sk-" not in redacted
