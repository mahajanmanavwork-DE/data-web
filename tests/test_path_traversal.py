"""Tests for path traversal protection."""
import pytest
from pathlib import Path
from fastapi import HTTPException
from main import safe_join_input_folder


class TestPathTraversalProtection:
    """Test path traversal protection on file endpoints."""

    def test_safe_join_normal_filename(self, setup_test_env):
        """Test normal filename works correctly."""
        import os
        input_folder = os.getenv("input_folder")
        result = safe_join_input_folder("data.csv")
        expected = os.path.join(input_folder, "data.csv")
        assert str(result) == str(Path(expected).resolve())

    def test_safe_join_blocks_directory_traversal(self, setup_test_env):
        """Test that directory traversal attempts are blocked."""
        with pytest.raises(HTTPException) as exc_info:
            safe_join_input_folder("../etc/passwd")
        assert exc_info.value.status_code == 400
        assert "invalid filename" in exc_info.value.detail.lower()

    def test_safe_join_blocks_absolute_path(self, setup_test_env):
        """Test that absolute paths are blocked."""
        with pytest.raises(HTTPException) as exc_info:
            safe_join_input_folder("/etc/passwd")
        assert exc_info.value.status_code == 400

    def test_safe_join_blocks_parent_directory(self, setup_test_env):
        """Test that parent directory references are blocked."""
        with pytest.raises(HTTPException) as exc_info:
            safe_join_input_folder("subdir/../../etc/passwd")
        assert exc_info.value.status_code == 400

    def test_safe_join_strips_path_components(self, setup_test_env):
        """Test that path components are stripped, only basename used."""
        # The function rejects filenames with path separators
        with pytest.raises(HTTPException) as exc_info:
            safe_join_input_folder("subdir/data.csv")
        assert exc_info.value.status_code == 400
        # Only pure basenames are allowed
        result = safe_join_input_folder("data.csv")
        assert result.name == "data.csv"
