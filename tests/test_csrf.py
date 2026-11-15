"""Tests for CSRF protection."""
import pytest
from app.security import generate_csrf_token, verify_csrf_token, consume_csrf_token, _csrf_tokens as csrf_tokens


class TestCSRFProtection:
    """Test CSRF token generation and verification."""

    def setup_method(self):
        """Clear csrf_tokens before each test."""
        csrf_tokens.clear()

    def test_generate_csrf_token_returns_string(self, setup_test_env):
        """Test that generate_csrf_token returns a string."""
        token = generate_csrf_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_generate_csrf_token_unique(self, setup_test_env):
        """Test that generated tokens are unique."""
        tokens = {generate_csrf_token() for _ in range(100)}
        assert len(tokens) == 100

    def test_verify_csrf_token_valid(self, setup_test_env):
        """Test verifying a valid token."""
        token = generate_csrf_token()
        csrf_tokens[token] = "test"  # Manually store as done in actual endpoints
        assert verify_csrf_token(token) is True

    def test_verify_csrf_token_invalid(self, setup_test_env):
        """Test verifying an invalid token returns False."""
        assert verify_csrf_token("invalid-token") is False

    def test_consume_csrf_token_removes_token(self, setup_test_env):
        """Test that consume_csrf_token removes the token."""
        token = generate_csrf_token()
        csrf_tokens[token] = "test"
        assert verify_csrf_token(token) is True

        result = consume_csrf_token(token)
        assert result is True
        assert verify_csrf_token(token) is False

    def test_consume_csrf_token_invalid_returns_false(self, setup_test_env):
        """Test consuming invalid token returns False."""
        result = consume_csrf_token("invalid-token")
        assert result is False

    def test_token_used_once(self, setup_test_env):
        """Test that a token can only be consumed once."""
        token = generate_csrf_token()
        csrf_tokens[token] = "test"
        assert consume_csrf_token(token) is True
        assert consume_csrf_token(token) is False
