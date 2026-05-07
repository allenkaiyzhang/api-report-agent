from __future__ import annotations

from pathlib import Path

from core.data_pipeline import BASE_DIR, load_jsonl, normalized_file_path, parse_datetime


def build_debug_chart(market: str, trading_date: str, symbol: str) -> Path:
    """Generate a PNG chart for price, volume, spread, and missing markers."""
    import matplotlib.pyplot as plt

    normalized = load_jsonl(normalized_file_path(BASE_DIR, market, trading_date))
    rows = [
        row for row in normalized.records
        if str(row.get("symbol", "")).upper() == symbol.upper()
    ]
    rows.sort(key=lambda row: row.get("event_time") or "")

    times = [parse_datetime(row.get("event_time")) for row in rows]
    prices = [row.get("last_price") for row in rows]
    volumes = [row.get("volume_cumulative") for row in rows]
    spreads = [row.get("spread_pct") for row in rows]
    invalid_x = [time for time, row in zip(times, rows) if time and not row.get("is_valid")]
    invalid_y = [
        row.get("last_price") or 0
        for row in rows
        if not row.get("is_valid")
    ]

    output_dir = BASE_DIR / "charts"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{symbol.upper()}_{trading_date}.png"

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    axes[0].plot(times, prices, label="price")
    if invalid_x:
        axes[0].scatter(invalid_x, invalid_y, color="red", label="invalid/missing")
    axes[0].legend()
    axes[0].set_ylabel("price")

    axes[1].plot(times, volumes, label="volume", color="tab:green")
    axes[1].legend()
    axes[1].set_ylabel("volume")

    axes[2].plot(times, spreads, label="spread_pct", color="tab:orange")
    axes[2].legend()
    axes[2].set_ylabel("spread")

    fig.suptitle(f"{symbol.upper()} {trading_date}")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    return output_path
