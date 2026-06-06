"""External service clients — MarketDataClient abstraction, MCP adapter, mock client."""

from clients.market_data_client import (
    Candle,
    FundamentalData,
    IntradayPoint,
    MarketDataClient,
    MarketReportDataset,
    MarketStatusInfo,
    Quote,
)
from clients.longbridge_mcp_client import LongbridgeMcpClient
from clients.mock_market_data_client import MockMarketDataClient

__all__ = [
    "Candle",
    "FundamentalData",
    "IntradayPoint",
    "LongbridgeMcpClient",
    "MarketDataClient",
    "MarketReportDataset",
    "MarketStatusInfo",
    "MockMarketDataClient",
    "Quote",
]
