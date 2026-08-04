"""
Unit Tests for Telegram Notifier & Command Router (Sprint 3)
"""

import pytest
from unittest.mock import MagicMock

from src.config import config, AppConfig
from src.notifications.notifier import TelegramNotifier
from src.notifications.telegram_bot import TelegramCommandRouter


def test_telegram_notifier_enqueue():
    notifier = TelegramNotifier(bot_token="TEST_TOKEN", chat_id="123456")
    assert notifier.msg_queue.qsize() == 0

    notifier.notify_signal("2026-07-24 00:00:00", "UP", 0.65, 0.62, 0.40)
    assert notifier.msg_queue.qsize() == 1

    notifier.notify_fill("2026-07-24 00:00:00", "UP", 0.40, 125.0, 50.0)
    assert notifier.msg_queue.qsize() == 2

    notifier.notify_stop_loss("2026-07-24 00:00:00", 0.20, -25.0)
    assert notifier.msg_queue.qsize() == 3


def test_telegram_command_router_authorization():
    notifier = TelegramNotifier()
    router = TelegramCommandRouter(notifier)

    # Test with whitelist empty -> all allowed
    config.telegram_authorized_user_ids = []
    assert router._is_authorized(99999) is True

    # Test with whitelist configured
    config.telegram_authorized_user_ids = [12345, 67890]
    assert router._is_authorized(12345) is True
    assert router._is_authorized(99999) is False
