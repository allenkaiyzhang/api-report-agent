from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.notification import notify


class NotificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.output_dir = PROJECT_ROOT / "tests" / "notification_output_test"
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)
        self.old_env = dict(os.environ)
        os.environ["NOTIFICATION_ARCHIVE_DIR"] = str(self.output_dir)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)

    def test_notify_writes_archive_before_email_and_ignores_telegram(self) -> None:
        os.environ["NOTIFY_CHANNELS"] = "telegram,email,archive"
        os.environ["EMAIL_ENABLED"] = "true"
        os.environ["SMTP_HOST"] = "smtp.example.com"
        os.environ["EMAIL_FROM"] = "from@example.com"
        os.environ["EMAIL_TO"] = "to@example.com"

        with patch("core.notification.send_email", side_effect=RuntimeError("smtp down")):
            result = notify("hello", "body", metadata={"k": "v"})

        self.assertEqual(result["results"]["archive"]["status"], "ok")
        self.assertEqual(result["results"]["telegram"]["status"], "ignored")
        self.assertEqual(result["results"]["email"]["status"], "error")
        files = list(self.output_dir.glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        row = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["project"], "api-report-agent")
        self.assertEqual(row["title"], "hello")
        self.assertIn("results", row)

    def test_notify_archive_only(self) -> None:
        result = notify("archive only", "body", channels=["archive"])

        self.assertEqual(result["results"]["archive"]["status"], "ok")
        self.assertTrue(list(self.output_dir.glob("*.jsonl")))


if __name__ == "__main__":
    unittest.main()
