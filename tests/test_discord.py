import pytest
from app.delivery.discord import chunk_text, DiscordWebhookClient


def test_chunk_text():
    chunks = chunk_text('a' * 5000, 1900)
    assert len(chunks) >= 3
    assert all(len(c) <= 1900 for c in chunks)


def test_empty_webhook_raises():
    with pytest.raises(ValueError):
        DiscordWebhookClient('').send_message('hello')
