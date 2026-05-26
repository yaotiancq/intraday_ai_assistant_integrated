from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List
import requests


@dataclass
class DiscordWebhookClient:
    webhook_url: str
    timeout: int = 20

    def send_message(self, content: str) -> None:
        if not self.webhook_url:
            raise ValueError('Discord webhook URL is empty')
        for chunk in chunk_text(content, 1900):
            resp = requests.post(self.webhook_url, json={'content': chunk}, timeout=self.timeout)
            if resp.status_code >= 300:
                raise RuntimeError(f'Discord webhook failed: {resp.status_code} {resp.text[:300]}')

    def send_warning(self, title: str, warnings: Iterable[str]) -> None:
        body = title + '\n' + '\n'.join(f'- {w}' for w in warnings)
        self.send_message(body)


def chunk_text(text: str, max_len: int = 1900) -> List[str]:
    if len(text) <= max_len:
        return [text]
    chunks: List[str] = []
    current = ''
    for line in text.splitlines(keepends=True) or [text]:
        while len(line) > max_len:
            if current:
                chunks.append(current)
                current = ''
            chunks.append(line[:max_len])
            line = line[max_len:]
        if len(current) + len(line) > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks
