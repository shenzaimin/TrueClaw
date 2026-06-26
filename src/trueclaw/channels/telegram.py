from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Any

from trueclaw.bus.events import OutboundMessageEvent
from trueclaw.channels.base import BaseChannel

_OFFSET_FLUSH_EVERY = 20


class TelegramChannel(BaseChannel):
    name = "telegram"
    display_name = "Telegram"

    def __init__(self, config, bus, *, config_path: str = ""):
        super().__init__(config, bus)
        self._task: asyncio.Task | None = None
        self._log = logging.getLogger(__name__)
        self._offset = 0
        self._config_path = config_path
        self._processed_since_flush = 0
        self._offset_path = self._offset_file_path(config_path)

    @staticmethod
    def _offset_file_path(config_path: str) -> Path:
        expanded = Path(os.path.expanduser(config_path)).resolve() if config_path else None
        if expanded is not None and expanded.suffix == ".json":
            return expanded.parent / "runtime" / "telegram.offset"
        return Path(os.path.expanduser("~/.trueclaw/runtime/telegram.offset"))

    def _cfg(self, key: str, default: Any = None) -> Any:
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    def _api_url(self, method: str) -> str:
        token = self._cfg("botToken", "")
        return f"https://api.telegram.org/bot{token}/{method}"

    async def _http_post_json(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._api_url(method)
        body = json.dumps(payload).encode("utf-8")

        def _run() -> dict[str, Any]:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))

        return await asyncio.to_thread(_run)

    def reload_allowlist(self, allow_from: list[str]) -> list[str]:
        allow = list(allow_from)
        if isinstance(self.config, dict):
            self.config["allowFrom"] = allow
        else:
            setattr(self.config, "allowFrom", allow)
        self._log.info("telegram allowFrom reloaded count=%s", len(allow))
        return allow

    def _load_offset(self) -> None:
        if not self._offset_path.exists():
            return
        try:
            raw = self._offset_path.read_text(encoding="utf-8").strip()
            if raw.isdigit():
                self._offset = int(raw)
                self._log.info("telegram offset restored=%s", self._offset)
        except Exception as e:  # noqa: BLE001
            self._log.warning("telegram offset load failed, reset to 0: %s", e)
            self._offset = 0

    def _flush_offset(self) -> None:
        try:
            self._offset_path.parent.mkdir(parents=True, exist_ok=True)
            self._offset_path.write_text(str(self._offset), encoding="utf-8")
            self._processed_since_flush = 0
        except Exception as e:  # noqa: BLE001
            self._log.warning("telegram offset flush failed: %s", e)

    def _is_allowed(self, user_id: str) -> bool:
        allow = self._cfg("allowFrom", [])
        if not allow:
            return False
        if "*" in allow:
            return True
        return str(user_id) in {str(x) for x in allow}

    def _should_process_group_message(self, text: str, sender_id: str) -> bool:
        policy = str(self._cfg("groupPolicy", "mention_only")).lower()
        if policy == "all":
            return True
        if policy == "owner_only":
            allow = self._cfg("allowFrom", [])
            owner_id = str(allow[0]) if allow else ""
            return bool(owner_id) and sender_id == owner_id

        username = str(self._cfg("botUsername", "")).strip().lstrip("@")
        if not username:
            return False
        return f"@{username.lower()}" in text.lower()

    @staticmethod
    def _extract_content(msg: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        text = (msg.get("text") or msg.get("caption") or "").strip()
        attachments: list[dict[str, Any]] = []
        if "photo" in msg:
            photos = msg.get("photo") or []
            if photos:
                best = photos[-1]
                attachments.append(
                    {
                        "kind": "image",
                        "mime_type": "image/jpeg",
                        "meta": {"file_id": best.get("file_id")},
                    }
                )
                if not text:
                    text = "[image]"
        if "document" in msg:
            doc = msg.get("document") or {}
            attachments.append(
                {
                    "kind": "file",
                    "mime_type": doc.get("mime_type"),
                    "meta": {"file_id": doc.get("file_id"), "file_name": doc.get("file_name")},
                }
            )
            if not text:
                name = doc.get("file_name") or "file"
                text = f"[file: {name}]"
        if "voice" in msg:
            attachments.append({"kind": "voice", "meta": {"file_id": msg.get("voice", {}).get("file_id")}})
            if not text:
                text = "[voice]"
        return text, attachments

    @classmethod
    def default_config(cls) -> dict:
        return {
            "enabled": False,
            "botToken": "",
            "botUsername": "",
            "allowFrom": [],
            "groupPolicy": "mention_only",
            "pollIntervalSec": 1.0,
        }

    async def start(self) -> None:
        self._running = True
        enabled = bool(self._cfg("enabled", False))
        if not enabled:
            self._log.info("telegram disabled, skip start")
            return
        token = self._cfg("botToken", "")
        if not token:
            self._log.warning("telegram enabled but botToken empty; channel will not poll")
            return

        self._load_offset()

        try:
            me = await self._http_post_json("getMe", {})
            if me.get("ok"):
                user = me.get("result") or {}
                username = str(user.get("username", "")).strip()
                if username and not str(self._cfg("botUsername", "")).strip():
                    self._log.info("telegram botUsername auto-detected: @%s", username)
                    if isinstance(self.config, dict):
                        self.config["botUsername"] = username
                    else:
                        setattr(self.config, "botUsername", username)
            else:
                self._log.warning("telegram getMe failed: %s", me)
        except Exception as e:  # noqa: BLE001
            self._log.warning("telegram getMe error: %s", e)

        async def _poll_loop() -> None:
            while self._running:
                try:
                    payload = {
                        "offset": self._offset + 1,
                        "timeout": 20,
                        "allowed_updates": ["message"],
                    }
                    data = await self._http_post_json("getUpdates", payload)
                    if not data.get("ok", False):
                        self._log.warning("telegram getUpdates not ok: %s", data)
                        await asyncio.sleep(1.0)
                        continue
                    updates = data.get("result", [])
                    for upd in updates:
                        uid = int(upd.get("update_id", 0))
                        if uid <= 0:
                            continue
                        msg = upd.get("message") or {}
                        content, attachments = self._extract_content(msg)
                        if not content:
                            self._offset = max(self._offset, uid)
                            continue
                        chat = msg.get("chat") or {}
                        sender = msg.get("from") or {}
                        sender_id = str(sender.get("id", ""))
                        if not self._is_allowed(sender_id):
                            self._offset = max(self._offset, uid)
                            continue
                        chat_type = str(chat.get("type", "private"))
                        if chat_type in {"group", "supergroup"}:
                            if not self._should_process_group_message(content, sender_id):
                                self._offset = max(self._offset, uid)
                                continue
                        metadata: dict[str, Any] = {"telegram_update_id": uid}
                        if attachments:
                            metadata["attachments"] = attachments
                        await self._handle_message(
                            sender_id=sender_id,
                            chat_id=str(chat.get("id", "")),
                            content=content,
                            metadata=metadata,
                        )
                        self._offset = max(self._offset, uid)
                        self._processed_since_flush += 1
                        if self._processed_since_flush >= _OFFSET_FLUSH_EVERY:
                            self._flush_offset()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self._log.warning("telegram poll error: %s", e)
                    await asyncio.sleep(1.5)

        self._task = asyncio.create_task(_poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._flush_offset()

    async def send(self, msg: OutboundMessageEvent) -> None:
        token = self._cfg("botToken", "")
        if not token:
            self._log.info("[telegram stub send/no-token] chat=%s text=%s", msg.chat_id, msg.content)
            return
        payload = {
            "chat_id": msg.chat_id,
            "text": msg.content,
        }
        data = await self._http_post_json("sendMessage", payload)
        if not data.get("ok", False):
            raise RuntimeError(f"telegram sendMessage failed: {data}")
