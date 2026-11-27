"""Tests for async client functionality."""

import pytest

from pyptine.async_ine import AsyncINE
from pyptine.client.async_data import AsyncDataClient


@pytest.mark.asyncio
class TestAsyncINEClient:
    """Tests for AsyncINE client."""

    async def test_async_context_manager(self):
        """Test async context manager initialization and cleanup."""
        async with AsyncINE(language="EN") as ine:
            assert ine.data_client is not None
            assert ine.metadata_client is not None
            assert ine.language == "EN"

    async def test_async_client_invalid_language(self):
        """Test async client with invalid language."""
        with pytest.raises(ValueError):
            AsyncINE(language="INVALID")

    async def test_async_client_without_context(self):
        """Test that async client raises error without context manager."""
        ine = AsyncINE()

        with pytest.raises(RuntimeError):
            await ine.get_data("0004167")

    async def test_async_data_client_creation(self):
        """Test async data client can be created."""
        async with AsyncDataClient(language="EN") as client:
            assert client is not None
            assert client.language == "EN"
            assert client.client is not None


@pytest.mark.asyncio
class TestAsyncMetadata:
    """Tests for async metadata operations."""

    async def test_async_get_metadata(self, mocker, sample_metadata):
        """Test async metadata retrieval."""
        mock_client_obj = mocker.MagicMock()
        mock_client_obj.get_metadata.return_value = mocker.MagicMock(
            varcd="0004167", title="Test", unit="No."
        )

        async with AsyncINE(language="EN") as ine:
            ine.metadata_client = mock_client_obj
            metadata = await ine.get_metadata("0004167")

            assert metadata.varcd == "0004167"
            assert metadata.title == "Test"

    async def test_async_get_dimensions(self, mocker):
        """Test async dimensions retrieval."""
        mock_dimension = mocker.MagicMock()
        mock_dimension.name = "Period"
        mock_dimension.values = []

        mock_client_obj = mocker.MagicMock()
        mock_client_obj.get_dimensions.return_value = [mock_dimension]

        async with AsyncINE(language="EN") as ine:
            ine.metadata_client = mock_client_obj
            dims = await ine.get_dimensions("0004167")

            assert len(dims) == 1
            assert dims[0].name == "Period"


@pytest.mark.asyncio
class TestAsyncBuildParams:
    """Tests for async parameter building."""

    async def test_async_build_params_basic(self):
        """Test basic parameter building in async client."""
        async with AsyncDataClient(language="EN") as client:
            params = client._build_params("0004167")

            assert params["op"] == "2"
            assert params["varcd"] == "0004167"
            assert "lang" not in params  # Added by _make_request

    async def test_async_build_params_with_dimensions(self):
        """Test dimension parameters with mock metadata."""
        from unittest.mock import MagicMock

        mock_metadata = MagicMock()
        mock_metadata_client = MagicMock()
        mock_metadata_client.get_metadata.return_value = mock_metadata
        mock_metadata_client.language = "EN"

        # Add mock dimensions to avoid validation errors
        mock_dim = MagicMock()
        mock_dim.id = 1
        mock_dim.values = [MagicMock(code="2023")]
        mock_metadata.dimensions = [mock_dim]

        async with AsyncDataClient(language="EN", metadata_client=mock_metadata_client) as client:
            params = client._build_params("0004167", dimensions={"Dim1": "2023"})

            assert params["Dim1"] == "2023"
