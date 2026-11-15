"""Tests for Dataframe model and data operations."""
import pytest
import pandas as pd
from scripts.models.dataframe import Dataframe
from scripts.models.data_operations import DataOperations


class TestDataframeModel:
    """Test Dataframe model functionality."""

    def test_from_file_creates_dataframe(self, setup_test_env, sample_csv_path):
        """Test loading a CSV file creates a Dataframe."""
        df = Dataframe.from_file("data.csv")
        assert isinstance(df.df, pd.DataFrame)
        assert df.key is not None
        assert len(df.key) == 12
        assert df.source_file == "data.csv"
        assert df.cols == list(df.df.columns)

    def test_save_and_load_roundtrip(self, setup_test_env, sample_csv_path):
        """Test save and load preserves data."""
        df = Dataframe.from_file("data.csv")
        key = df.key
        original_shape = df.df.shape
        original_cols = df.cols

        # Load back
        df2 = Dataframe.load(key)
        assert df2.df.shape == original_shape
        assert df2.cols == original_cols
        assert df2.key == key
        assert df2.source_file == "data.csv"

    def test_save_updates_registry(self, setup_test_env, sample_csv_path):
        """Test that save updates the registry."""
        import json
        import os

        df = Dataframe.from_file("data.csv")
        registry_path = os.getenv("df_registry_path")

        with open(registry_path, "r") as f:
            registry = json.load(f)

        assert df.key in registry
        assert registry[df.key]["path"] == df.path
        assert registry[df.key]["source_file"] == "data.csv"

    def test_promote_sets_current_key(self, setup_test_env, sample_csv_path):
        """Test promote sets the current key for user."""
        from scripts import auth

        df = Dataframe.from_file("data.csv")
        df.promote("testuser")

        assert auth.get_current_key("testuser") == df.key

    def test_current_loads_active_dataframe(self, setup_test_env, sample_csv_path):
        """Test current() loads the active dataframe for user."""
        from scripts import auth

        df = Dataframe.from_file("data.csv")
        df.promote("testuser")

        current_df = Dataframe.current("testuser")
        assert current_df.key == df.key
        assert current_df.df.shape == df.df.shape

    def test_current_raises_when_no_active(self, setup_test_env):
        """Test current() raises when no active dataframe."""
        from scripts import auth
        # Ensure clean state
        auth.reset_session("testuser")

        with pytest.raises(RuntimeError, match="No active Dataframe"):
            Dataframe.current("testuser")

    def test_renamed_columns_in_cols(self, setup_test_env, sample_csv_path):
        """Test that renamed columns are reflected in cols property."""
        # Use constructor directly since from_file doesn't accept renamed param
        import pandas as pd
        df = pd.read_csv(sample_csv_path)
        test_df = Dataframe(df=df, source_file="data.csv", renamed={"Name": "Full Name"})
        assert "Full Name" in test_df.cols
        assert "Name" not in test_df.cols

    def test_deleted_columns_excluded_from_cols(self, setup_test_env, sample_csv_path):
        """Test that deleted columns are excluded from cols property."""
        import pandas as pd
        df = pd.read_csv(sample_csv_path)
        test_df = Dataframe(df=df, source_file="data.csv", deleted=["Salary"])
        assert "Salary" not in test_df.cols


class TestDataOperations:
    """Test DataOperations functionality."""

    def test_filter_operation(self, setup_test_env, sample_csv_path):
        """Test filter operation reduces rows."""
        df = Dataframe.from_file("data.csv")
        df.promote("testuser")

        ops = DataOperations.current("testuser")
        initial_rows = ops.row_count

        # Filter for a specific department - use "=" not "=="
        ops.filter("Department", "=", "Engineering").promote("testuser")

        ops_after = DataOperations.current("testuser")
        assert ops_after.row_count < initial_rows
        assert all(ops_after.df["Department"] == "Engineering")

    def test_limit_operation(self, setup_test_env, sample_csv_path):
        """Test limit operation restricts rows."""
        df = Dataframe.from_file("data.csv")
        df.promote("testuser")

        ops = DataOperations.current("testuser")
        initial_rows = ops.row_count

        ops.limit(5).promote("testuser")

        ops_after = DataOperations.current("testuser")
        assert ops_after.row_count == 5

    def test_offset_operation(self, setup_test_env, sample_csv_path):
        """Test offset operation skips rows."""
        df = Dataframe.from_file("data.csv")
        df.promote("testuser")

        ops = DataOperations.current("testuser")
        initial_rows = ops.row_count

        ops.offset(10).promote("testuser")

        ops_after = DataOperations.current("testuser")
        assert ops_after.row_count == initial_rows - 10

    def test_order_by_operation(self, setup_test_env, sample_csv_path):
        """Test order by operation sorts data."""
        df = Dataframe.from_file("data.csv")
        df.promote("testuser")

        ops = DataOperations.current("testuser")
        ops.order_by("Salary", "desc").promote("testuser")

        ops_after = DataOperations.current("testuser")
        salaries = ops_after.df["Salary"].tolist()
        assert salaries == sorted(salaries, reverse=True)

    def test_distinct_operation(self, setup_test_env, sample_csv_path):
        """Test distinct operation removes duplicates."""
        # Create a dataframe with duplicates
        import pandas as pd
        import os

        df = pd.DataFrame({"A": [1, 1, 2, 2], "B": [3, 3, 4, 4]})
        from scripts.models.dataframe import Dataframe
        test_df = Dataframe(df=df, source_file="test.csv")
        test_df.promote("testuser")

        ops = DataOperations.current("testuser")
        assert ops.row_count == 4

        ops.distinct().promote("testuser")

        ops_after = DataOperations.current("testuser")
        assert ops_after.row_count == 2

    def test_group_by_operation(self, setup_test_env, sample_csv_path):
        """Test group by operation aggregates data."""
        df = Dataframe.from_file("data.csv")
        df.promote("testuser")

        ops = DataOperations.current("testuser")
        ops.group_by_multi(["Department"], [{"col": "Salary", "func": "mean", "alias": "avg_salary"}]).promote("testuser")

        ops_after = DataOperations.current("testuser")
        assert "avg_salary" in ops_after.df.columns
        assert ops_after.row_count <= ops.row_count
