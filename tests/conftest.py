"""Test configuration and fixtures for dataweb tests."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env from project root
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / "env" / ".env")


@pytest.fixture(autouse=True)
def setup_test_env():
    """Set up test environment variables for each test."""
    # Use temporary directories for tests
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        os.environ["input_folder"] = str(PROJECT_ROOT / "input_files")
        os.environ["output_folder"] = str(tmpdir / "output_files")
        os.environ["storage_folder"] = str(tmpdir / "storage")
        os.environ["metadata_path"] = str(tmpdir / "storage" / "metadata.json")
        os.environ["df_registry_path"] = str(tmpdir / "storage" / "registry.json")
        os.environ["operations_path"] = str(tmpdir / "storage" / "operations.json")
        os.environ["current_df_key_path"] = str(tmpdir / "storage" / "current_key.json")
        os.environ["selected_filepath"] = str(tmpdir / "storage" / "selected.txt")
        os.environ["users_path"] = str(tmpdir / "storage" / "users.json")
        os.environ["secret_key"] = "test-secret-key-for-testing-only-32-chars!!"
        yield


@pytest.fixture
def sample_csv_path():
    """Path to sample CSV file for testing."""
    return PROJECT_ROOT / "input_files" / "data.csv"
