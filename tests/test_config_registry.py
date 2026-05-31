from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_registry import apply_registry_to_env, registry_to_env


class ConfigRegistryTest(unittest.TestCase):
    def test_registry_maps_non_sensitive_values_to_env_compatibility_keys(self) -> None:
        values = registry_to_env(
            {
                "market_data": {
                    "provider": "mock",
                    "collection": {"interval_seconds": 30, "output_dir": "data/raw"},
                },
                "notifications": {"channels": ["email", "archive"]},
                "email": {
                    "enabled": False,
                    "smtp": {"port": 587, "force_ipv4": True},
                    "to": ["ops@example.com", "reports@example.com"],
                },
                "ai": {"provider": "mock", "deepseek": {"model": "deepseek-v4-flash"}},
            }
        )

        self.assertEqual(values["MARKET_DATA_PROVIDER"], "mock")
        self.assertEqual(values["DATA_COLLECTION_INTERVAL_SECONDS"], "30")
        self.assertEqual(values["NOTIFY_CHANNELS"], "email,archive")
        self.assertEqual(values["EMAIL_ENABLED"], "false")
        self.assertEqual(values["SMTP_FORCE_IPV4"], "true")
        self.assertEqual(values["EMAIL_TO"], "ops@example.com,reports@example.com")
        self.assertEqual(values["DEEPSEEK_MODEL"], "deepseek-v4-flash")
        self.assertNotIn("DEEPSEEK_API_KEY", values)
        self.assertNotIn("SMTP_PASSWORD", values)

    def test_apply_registry_does_not_override_existing_secret_or_override_env_by_default(self) -> None:
        env = {"MARKET_DATA_PROVIDER": "longbridge", "DEEPSEEK_API_KEY": "secret"}
        path = PROJECT_ROOT / "tests" / "registry_test.yaml"
        path.write_text("market_data:\n  provider: mock\n", encoding="utf-8")
        try:
            apply_registry_to_env(path=path, environ=env)
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(env["MARKET_DATA_PROVIDER"], "longbridge")
        self.assertEqual(env["DEEPSEEK_API_KEY"], "secret")


if __name__ == "__main__":
    unittest.main()
