from prd_agent.ops.log_redact import redact_secrets


def test_redact_telegram_url() -> None:
    raw = (
        'HTTP Request: POST https://api.telegram.org/bot8548298325:'
        'AAG9QKiE4Z-jtOiIpnZ4roWarMzGaTF5Gzc/getMe "HTTP/1.1 200 OK"'
    )
    out = redact_secrets(raw)
    assert "AAG9QKiE4Z" not in out
    assert "8548298325" not in out
    assert "bot***REDACTED***" in out


def test_redact_bare_bot_token() -> None:
    raw = "Conflict bot123456789:ABCdefGHI-token_here already polling"
    out = redact_secrets(raw)
    assert "ABCdefGHI" not in out
    assert "bot***REDACTED***" in out
