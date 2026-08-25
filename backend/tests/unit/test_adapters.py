"""Unit tests for InputAdapter abstractions."""

from __future__ import annotations

import pytest

from app.adapters.base import BlobData, InputAdapter
from app.adapters.defender import DefenderAdapter
from app.adapters.manual import ManualAdapter
from app.adapters.purview import PurviewAdapter

pytestmark = pytest.mark.unit


class TestInputAdapterInterface:
    def test_cannot_instantiate_base(self) -> None:
        """InputAdapter is abstract and cannot be directly instantiated."""
        with pytest.raises(TypeError):
            InputAdapter(adapter_name="test")  # type: ignore[abstract]

    def test_manual_adapter_instantiates(self) -> None:
        adapter = ManualAdapter(adapter_name="manual")
        assert adapter is not None
        assert adapter.adapter_name == "manual"

    def test_purview_adapter_instantiates(self) -> None:
        adapter = PurviewAdapter(adapter_name="purview")
        assert adapter is not None

    def test_defender_adapter_instantiates(self) -> None:
        adapter = DefenderAdapter(adapter_name="defender")
        assert adapter is not None


class TestManualAdapter:
    @pytest.mark.asyncio
    async def test_fetch_blobs_returns_data(self) -> None:
        adapter = ManualAdapter(adapter_name="manual")
        text = "Sample compliance document text for analysis."
        result = await adapter.fetch_blobs(content=text, title="Test Doc")
        assert len(result) == 1
        assert result[0].content == text
        assert result[0].title == "Test Doc"

    @pytest.mark.asyncio
    async def test_fetch_blobs_empty_content_returns_empty(self) -> None:
        adapter = ManualAdapter(adapter_name="manual")
        result = await adapter.fetch_blobs(content="")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_fetch_blobs_no_content_returns_empty(self) -> None:
        adapter = ManualAdapter(adapter_name="manual")
        result = await adapter.fetch_blobs()
        assert len(result) == 0


class TestPurviewAdapter:
    @pytest.mark.asyncio
    async def test_purview_returns_stub_data(self) -> None:
        """Purview stub returns sample data for development."""
        adapter = PurviewAdapter(adapter_name="purview")
        result = await adapter.fetch_blobs()
        assert len(result) >= 1
        assert all(isinstance(b, BlobData) for b in result)
        assert all(b.content for b in result)


class TestDefenderAdapter:
    @pytest.mark.asyncio
    async def test_defender_returns_stub_data(self) -> None:
        """Defender stub returns sample data for development."""
        adapter = DefenderAdapter(adapter_name="defender")
        result = await adapter.fetch_blobs()
        assert len(result) >= 1
        assert all(isinstance(b, BlobData) for b in result)
        assert all(b.content for b in result)
