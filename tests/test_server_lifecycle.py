from unittest.mock import AsyncMock, Mock

import pytest

from jenkins_mcp_server import server


@pytest.mark.asyncio
async def test_server_lifespan_closes_and_discards_cached_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock()
    client.close = AsyncMock()
    get_client = Mock(return_value=client)
    get_client.cache_clear = Mock()
    monkeypatch.setattr(server, "get_client", get_client)

    async with server.server_lifespan(server.mcp):
        assert get_client.call_count == 1

    client.close.assert_awaited_once_with()
    get_client.cache_clear.assert_called_once_with()
