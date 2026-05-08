from __future__ import annotations

from pathlib import Path

from core.ai_analyzer import AIAnalysisConfig, analyze_market_report
from core.data_pipeline import BASE_DIR
from core.post_market.common import load_json, metrics_day_dir, quality_file, reference_file, report_path


def generate_ai_narrative(
    market: str,
    trading_date: str,
    ai_config: AIAnalysisConfig | None = None,
    base_dir: Path = BASE_DIR,
) -> Path:
    quality = load_json(quality_file(base_dir, market, trading_date))
    output_path = report_path(base_dir, market, trading_date, "ai_summary.md")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if quality.get("usable_for_analysis") is False:
        output_path.write_text("数据质量不足，不生成市场判断。\n", encoding="utf-8")
        return output_path

    payload = {
        "report_type": "post_market_narrative",
        "market": market,
        "trading_date": trading_date,
        "daily": load_json(metrics_day_dir(base_dir, market, trading_date) / "daily.json"),
        "quality": quality,
        "reference": load_json(reference_file(base_dir, market, trading_date)),
        "rules": [
            "No price prediction.",
            "No buy or sell advice.",
            "No invented macro news.",
        ],
    }
    narrative = analyze_market_report(ai_config, payload) or "AI narrative not enabled.\n"
    output_path.write_text(narrative + "\n", encoding="utf-8")
    return output_path
