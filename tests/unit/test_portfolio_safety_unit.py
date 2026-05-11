from bookcraft.components.portfolio.safety import is_safe_portfolio_url


def test_portfolio_safety_allows_approved_public_hosts() -> None:
    assert is_safe_portfolio_url("https://www.amazon.com/example-book")
    assert is_safe_portfolio_url("https://bookcraftpublishers.com/portfolio/sample")
    assert is_safe_portfolio_url("https://www.youtube.com/watch?v=abc")
    assert is_safe_portfolio_url("https://youtu.be/abc")
    assert is_safe_portfolio_url("https://pub-511d184d6c4a4ad1b53c7fdf29e12e40.r2.dev/video.mp4")


def test_portfolio_safety_blocks_private_and_internal_hosts() -> None:
    assert not is_safe_portfolio_url("http://localhost/private")
    assert not is_safe_portfolio_url("http://127.0.0.1/private")
    assert not is_safe_portfolio_url("http://192.168.1.10/private")
    assert not is_safe_portfolio_url("https://portfolio.internal/sample")
    assert not is_safe_portfolio_url("https://staging.example.com/sample")
    assert not is_safe_portfolio_url("https://private.example.com/sample")


def test_portfolio_safety_blocks_unapproved_public_hosts() -> None:
    assert not is_safe_portfolio_url("https://random-example.com/sample")
