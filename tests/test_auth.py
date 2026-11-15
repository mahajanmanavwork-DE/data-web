"""Tests for authentication module."""
import pytest
from scripts.auth import (
    create_user,
    verify_user,
    change_password,
    ensure_default_admin,
    read_session,
    write_session,
    get_current_key,
    set_current_key,
    get_selected_file,
    set_selected_file,
    get_operations,
    set_operations,
    reset_session,
)


class TestUserCreation:
    """Test user creation and verification."""

    def test_create_user_hashes_password(self, setup_test_env):
        """Test that created user has bcrypt hashed password."""
        import os
        import json

        users_path = os.getenv("users_path")
        result = create_user("testuser", "testpass123")
        assert result is True

        # Verify password is hashed in storage
        with open(users_path, "r") as f:
            users = json.load(f)
        assert "testuser" in users
        assert users["testuser"]["password"].startswith("$2b$")
        assert users["testuser"]["password"] != "testpass123"

    def test_create_duplicate_user_returns_false(self, setup_test_env):
        """Test that creating duplicate user returns False."""
        create_user("testuser", "testpass123")
        result = create_user("testuser", "differentpass")
        assert result is False

    def test_verify_user_correct_password(self, setup_test_env):
        """Test verifying user with correct password."""
        create_user("testuser", "testpass123")
        result = verify_user("testuser", "testpass123")
        assert result is True

    def test_verify_user_wrong_password(self, setup_test_env):
        """Test verifying user with wrong password returns False."""
        create_user("testuser", "testpass123")
        result = verify_user("testuser", "wrongpass")
        assert result is False

    def test_verify_nonexistent_user(self, setup_test_env):
        """Test verifying nonexistent user returns False."""
        result = verify_user("nonexistent", "anypass")
        assert result is False


class TestPasswordChange:
    """Test password change functionality."""

    def test_change_password_success(self, setup_test_env):
        """Test successful password change."""
        create_user("testuser", "oldpass123")
        result = change_password("testuser", "oldpass123", "newpass456")
        assert result is True

        # Verify new password works
        assert verify_user("testuser", "newpass456") is True
        # Verify old password no longer works
        assert verify_user("testuser", "oldpass123") is False

    def test_change_password_wrong_old_password(self, setup_test_env):
        """Test password change fails with wrong old password."""
        create_user("testuser", "oldpass123")
        result = change_password("testuser", "wrongold", "newpass456")
        assert result is False

        # Original password should still work
        assert verify_user("testuser", "oldpass123") is True


class TestSessionManagement:
    """Test per-user session management."""

    def test_read_write_session(self, setup_test_env):
        """Test reading and writing session data."""
        create_user("testuser", "testpass123")

        # Write session data
        session_data = {
            "current_key": "test-key-123",
            "selected_file": "test.csv",
            "operations": {"test.csv": {"order": ["col1", "col2"]}},
        }
        write_session("testuser", session_data)

        # Read back
        read_data = read_session("testuser")
        assert read_data["current_key"] == "test-key-123"
        assert read_data["selected_file"] == "test.csv"
        assert read_data["operations"]["test.csv"]["order"] == ["col1", "col2"]

    def test_reset_session_clears_data(self, setup_test_env):
        """Test reset_session clears user's session data."""
        create_user("testuser", "testpass123")
        write_session("testuser", {
            "current_key": "test-key-123",
            "selected_file": "test.csv",
            "operations": {"test.csv": {}},
        })

        reset_session("testuser")

        read_data = read_session("testuser")
        assert read_data["current_key"] is None
        assert read_data["selected_file"] is None
        assert read_data["operations"] == {}

    def test_individual_session_getters_setters(self, setup_test_env):
        """Test individual getter/setter functions."""
        create_user("testuser", "testpass123")

        set_current_key("testuser", "key-123")
        assert get_current_key("testuser") == "key-123"

        set_selected_file("testuser", "file.csv")
        assert get_selected_file("testuser") == "file.csv"

        set_operations("testuser", {"file.csv": {"delete": ["col1"]}})
        ops = get_operations("testuser")
        assert ops["file.csv"]["delete"] == ["col1"]


class TestDefaultAdmin:
    """Test default admin user creation."""

    def test_ensure_default_admin_creates_when_empty(self, setup_test_env):
        """Test admin user is created when no users exist."""
        import os
        import json

        users_path = os.getenv("users_path")
        # Ensure clean state
        if os.path.exists(users_path):
            os.remove(users_path)

        ensure_default_admin()

        with open(users_path, "r") as f:
            users = json.load(f)
        assert "admin" in users
        assert users["admin"]["password"].startswith("$2b$")

    def test_ensure_default_admin_does_not_overwrite(self, setup_test_env):
        """Test admin user is not overwritten if users exist."""
        create_user("existing", "pass123")
        ensure_default_admin()

        import os
        import json
        users_path = os.getenv("users_path")
        with open(users_path, "r") as f:
            users = json.load(f)
        assert "existing" in users
        assert "admin" not in users  # Should not create admin if users exist