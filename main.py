#!/usr/bin/env python3
"""AniVox Monitor v4.6.0 (Parallel Engine, Multimedia Bridge, Session Detector, Live AJAX Dashboard & Render AutoPing)."""

from __future__ import annotations

import base64
import html
import http.server
import json
import logging
import os
import re
import signal
import socket
import socketserver
import ssl
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any, Optional

APP_NAME = "AniVox Monitor"
VERSION = "4.6.0"
CONFIG_PATH = Path(__file__).with_name("anivox_monitor.json")
LOG_PATH = Path(__file__).with_name("anivox_monitor.log")
PROFILE_RE = re.compile(
    r"^https?://(?:www\.)?anivox\.fun/profile/([0-9]+)(?:[/?#].*)?$",
    re.IGNORECASE,
)
CLEAN_TITLE_RE = re.compile(
    r"(\[.*?\]|\(.*?\)|/.*$|\b\d+\s*(?:сезон|season|part|часть|тв|tv)\b|(?:сезон|season|part|часть|тв|tv)\s*\d+\b)",
    re.IGNORECASE,
)
ALLOWED_INTERVALS = (1, 5, 10, 15, 20, 25, 30)
DEFAULT_INTERVAL = 10
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 "
    "Chrome/124.0 Mobile Safari/537.36 AniVoxMonitor/4.6"
)
SHIKIMORI_USER_AGENT = "AniVoxMonitor/4.6 (https://anivox.fun)"

logger = logging.getLogger(APP_NAME)

TELEGRAM_KEYBOARD = {
    "keyboard": [
        ["📊 Проверить всех", "🌐 Открыть сайт-отчет"],
        ["👥 Мои профили", "➕ Добавить", "🗑 Удалить"],
        ["📈 История", "🎭 Друзья"],
        ["⚙️ Настройки", "🔕 Уведомления"],
        ["💬 Связать всех в чат", "🚪 Завершить чат"],
        ["⏸ Пауза", "▶️ Продолжить"],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
}


def almaty_tz() -> timezone:
    return timezone(timedelta(hours=5), name="ALMT")


def almaty_now() -> str:
    return datetime.now(almaty_tz()).isoformat(timespec="seconds")


def compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def mask_secret(value: str) -> str:
    return "не задан" if not value else f"{value[:4]}…{value[-4:]}"


def save_json(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Не удалось прочитать настройки: %s", exc)
        return {}


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "да", "д"}
    return default


def normalize_profile_url(value: str) -> tuple[str, str]:
    candidate = value.strip()
    if not candidate.startswith(("http://", "https://")):
        candidate = "https://" + candidate
    parsed = urllib.parse.urlparse(candidate)
    normalized = urllib.parse.urlunparse(
        ("https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", "")
    )
    match = PROFILE_RE.match(normalized)
    if not match:
        raise ValueError("Нужна ссылка вида https://anivox.fun/profile/27788")
    return normalized, match.group(1)


def parse_bool(value: Any, default: bool = False) -> bool:
    return as_bool(value, default)


def clean_anime_title(raw_title: str) -> str:
    """Очищает название тайтла от сезонов, скобок и слэшей для точного поиска в Shikimori."""
    cleaned = CLEAN_TITLE_RE.sub(" ", raw_title)
    cleaned = re.sub(r"[:\-–—]+$", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) >= 2 else raw_title.strip()


def _shikimori_search_one(query: str) -> str:
    search_url = f"https://shikimori.one/api/animes?search={urllib.parse.quote(query)}&limit=1"
    req = urllib.request.Request(search_url, headers={"User-Agent": SHIKIMORI_USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        if data and isinstance(data, list) and isinstance(data[0], dict):
            return str(data[0].get("id", ""))
    return ""


def fetch_anime_info(title: str) -> dict[str, Any]:
    """Запрашивает жанры и ID аниме через API Shikimori с умной нормализацией названия."""
    if not title or title.lower() in ["неизвестно", "тайтл"]:
        return {"genres": [], "id": ""}
    try:
        time.sleep(1.0)
        cleaned = clean_anime_title(title)
        anime_id = _shikimori_search_one(cleaned)
        if not anime_id and cleaned != title:
            time.sleep(0.8)
            anime_id = _shikimori_search_one(title)
        if not anime_id:
            return {"genres": [], "id": ""}

        detail_url = f"https://shikimori.one/api/animes/{anime_id}"
        req2 = urllib.request.Request(detail_url, headers={"User-Agent": SHIKIMORI_USER_AGENT})
        with urllib.request.urlopen(req2, timeout=10) as resp2:
            detail_data = json.loads(resp2.read().decode("utf-8"))
            genres = [
                g.get("russian") or g.get("name")
                for g in detail_data.get("genres", [])
                if isinstance(g, dict) and (g.get("russian") or g.get("name"))
            ]
            return {"genres": genres, "id": anime_id}
    except Exception as e:
        logger.warning(f"Ошибка Shikimori API для '{title}': {e}")
        return {"genres": [], "id": ""}


def background_fetch_info(title: str, store: "Store") -> None:
    """Фоновая потокобезопасная функция для поиска жанров и ID без гонки данных."""
    with store.lock:
        cached = store.anime_cache.get(title)
        if isinstance(cached, dict) and cached.get("id"):
            return
    info = fetch_anime_info(title)
    store.update_anime_cache(title, info)


def get_anime_data(title: str, cache: dict) -> tuple[str, str]:
    """Возвращает (строка_жанров, прямая_ссылка_anivox_через_id)."""
    cached = cache.get(title)
    genres_str = "-"
    link = "https://anivox.fun"

    if isinstance(cached, dict):
        genres_str = ", ".join(cached.get("genres", [])) or "-"
        if cached.get("id"):
            link = f"https://anivox.fun/anime/{cached['id']}"
    elif isinstance(cached, list):
        genres_str = ", ".join(cached) or "-"

    return genres_str, link


def format_duration_hm(seconds: float) -> str:
    minutes = max(1, int(round(seconds / 60)))
    if minutes < 60:
        return f"{minutes} мин."
    hours = minutes // 60
    rem_min = minutes % 60
    return f"{hours} ч. {rem_min} мин." if rem_min else f"{hours} ч."


@dataclass
class Profile:
    url: str
    profile_id: str
    label: str = ""
    last_signature: str = ""
    last_check: str = ""
    last_ok: bool = False
    last_error: str = ""
    last_status: str = ""
    muted_until: float = 0.0
    active_title: str = ""
    active_title_since: float = 0.0


@dataclass
class Settings:
    interval_minutes: int = DEFAULT_INTERVAL
    auto_cleanup_days: int = 15
    notify_on_change: bool = True
    notify_on_online: bool = True
    notify_on_offline: bool = True
    notify_on_title: bool = True
    notify_on_errors: bool = False
    request_timeout: int = 20
    min_request_gap_seconds: int = 5


class Store:
    def __init__(self, path: Path) -> None:
        self.lock = threading.RLock()
        raw = load_json(path)
        self.path = path
        self.token = str(raw.get("token", os.environ.get("BOT_TOKEN", ""))).strip()
        self.chat_id = str(raw.get("chat_id", os.environ.get("CHAT_ID", ""))).strip()
        self.autoping_url = str(
            raw.get("autoping_url", os.environ.get("RENDER_EXTERNAL_URL", ""))
        ).strip()

        self.allowed_users: list[str] = [str(u) for u in raw.get("allowed_users", [])]
        if self.chat_id and self.chat_id not in self.allowed_users:
            self.allowed_users.append(self.chat_id)

        self.anime_cache: dict[str, Any] = raw.get("anime_cache", {})

        raw_settings = raw.get("settings", {})
        raw_settings = raw_settings if isinstance(raw_settings, dict) else {}
        interval = int(raw_settings.get("interval_minutes", DEFAULT_INTERVAL))
        if interval not in ALLOWED_INTERVALS:
            interval = DEFAULT_INTERVAL

        self.settings = Settings(
            interval_minutes=interval,
            auto_cleanup_days=int(raw_settings.get("auto_cleanup_days", 15)),
            notify_on_change=as_bool(raw_settings.get("notify_on_change"), True),
            notify_on_online=as_bool(raw_settings.get("notify_on_online"), True),
            notify_on_offline=as_bool(raw_settings.get("notify_on_offline"), True),
            notify_on_title=as_bool(raw_settings.get("notify_on_title"), True),
            notify_on_errors=as_bool(raw_settings.get("notify_on_errors"), False),
            request_timeout=max(5, min(90, int(raw_settings.get("request_timeout", 20)))),
            min_request_gap_seconds=max(
                1, min(300, int(raw_settings.get("min_request_gap_seconds", 5)))
            ),
        )

        self.profiles: dict[str, Profile] = {}
        raw_profiles = raw.get("profiles", [])
        if isinstance(raw_profiles, list):
            for item in raw_profiles:
                if not isinstance(item, dict):
                    continue
                try:
                    profile = Profile(
                        url=str(item["url"]),
                        profile_id=str(item["profile_id"]),
                        label=str(item.get("label", "")),
                        last_signature=str(item.get("last_signature", "")),
                        last_check=str(item.get("last_check", "")),
                        last_ok=as_bool(item.get("last_ok"), False),
                        last_error=str(item.get("last_error", "")),
                        last_status=str(item.get("last_status", "")),
                        muted_until=float(item.get("muted_until", 0.0) or 0.0),
                        active_title=str(item.get("active_title", "")),
                        active_title_since=float(item.get("active_title_since", 0.0) or 0.0),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                self.profiles[profile.profile_id] = profile

        self.history: dict[str, list[dict[str, Any]]] = {}
        raw_history = raw.get("history", {})
        if isinstance(raw_history, dict):
            for profile_id, entries in raw_history.items():
                if isinstance(entries, list):
                    cleaned_entries = []
                    for item in entries:
                        if not isinstance(item, dict):
                            continue
                        if not cleaned_entries:
                            cleaned_entries.append(item)
                        else:
                            last_item = cleaned_entries[-1]
                            if (
                                last_item.get("presence") != item.get("presence")
                                or last_item.get("nickname") != item.get("nickname")
                                or last_item.get("level") != item.get("level")
                                or last_item.get("title") != item.get("title")
                            ):
                                cleaned_entries.append(item)
                    self.history[str(profile_id)] = cleaned_entries[-500:]

    def save(self) -> None:
        with self.lock:
            save_json(
                self.path,
                {
                    "token": self.token,
                    "chat_id": self.chat_id,
                    "autoping_url": self.autoping_url,
                    "allowed_users": self.allowed_users,
                    "anime_cache": self.anime_cache,
                    "settings": asdict(self.settings),
                    "profiles": [asdict(p) for p in self.profiles.values()],
                    "history": self.history,
                },
            )

    def update_anime_cache(self, title: str, info: dict[str, Any]) -> None:
        with self.lock:
            self.anime_cache[title] = info
            self.save()

    def add_history(
        self,
        profile_id: str,
        result: "FetchResult",
        changes: list[str],
    ) -> None:
        if not result.ok or not changes:
            return

        with self.lock:
            entries = self.history.setdefault(profile_id, [])
            if entries:
                last_entry = entries[-1]
                if (
                    last_entry.get("presence") == result.presence
                    and last_entry.get("nickname") == result.nickname
                    and last_entry.get("level") == result.level
                    and last_entry.get("title") == result.title
                ):
                    return

            if result.title:
                cached = self.anime_cache.get(result.title)
                if (
                    not cached
                    or (isinstance(cached, dict) and not cached.get("id"))
                    or isinstance(cached, list)
                ):
                    if not isinstance(cached, dict):
                        self.anime_cache[result.title] = {"genres": [], "id": ""}
                    threading.Thread(
                        target=background_fetch_info,
                        args=(result.title, self),
                        daemon=True,
                    ).start()

            entries.append(
                {
                    "checked_at": result.checked_at,
                    "ok": result.ok,
                    "nickname": result.nickname,
                    "level": result.level,
                    "presence": result.presence,
                    "title": result.title,
                    "signature": result.signature,
                    "changes": changes,
                    "error": result.error,
                }
            )
            self.history[profile_id] = entries[-500:]


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, timeout: int = 35) -> None:
        self.token = token.strip()
        self.timeout = timeout
        self.base_url = f"https://api.telegram.org/bot{self.token}/"
        self.offset = 0

    def request(self, method: str, payload: Optional[dict[str, Any]] = None) -> Any:
        body = urllib.parse.urlencode(payload or {}).encode()
        request = urllib.request.Request(
            self.base_url + method,
            data=body,
            headers={"User-Agent": f"{APP_NAME}/{VERSION}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramError(f"HTTP {exc.code}: {detail[:300]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ConnectionError) as exc:
            raise TelegramError(f"Network error (Telegram): {exc}") from exc

        if not result.get("ok"):
            raise TelegramError(str(result.get("description", "ошибка Telegram")))
        return result.get("result")

    def get_me(self) -> dict[str, Any]:
        result = self.request("getMe")
        return result if isinstance(result, dict) else {}

    def send_message(
        self, chat_id: str, text: str, reply_markup: Optional[dict[str, Any]] = None
    ) -> None:
        self.request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
                "reply_markup": json.dumps(
                    reply_markup or TELEGRAM_KEYBOARD, ensure_ascii=False
                ),
            },
        )

    def copy_message(
        self, to_chat_id: str, from_chat_id: str, message_id: int
    ) -> None:
        self.request(
            "copyMessage",
            {
                "chat_id": to_chat_id,
                "from_chat_id": from_chat_id,
                "message_id": message_id,
            },
        )

    def edit_message_text(
        self,
        chat_id: str,
        text: str,
        message_id: int,
        reply_markup: Optional[dict[str, Any]] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        self.request("editMessageText", payload)

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        try:
            self.request(
                "answerCallbackQuery",
                {"callback_query_id": callback_query_id, "text": text[:190]},
            )
        except Exception:
            pass

    def send_document(self, chat_id: str, path: Path, caption: str = "") -> None:
        boundary = f"----AniVoxMonitor{os.urandom(8).hex()}"
        file_bytes = path.read_bytes()
        parts: list[bytes] = []

        def field(name: str, value: str) -> None:
            parts.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )

        field("chat_id", chat_id)
        if caption:
            field("caption", caption[:1024])

        content_type = (
            b"Content-Type: text/html\r\n\r\n"
            if path.name.endswith(".html")
            else b"Content-Type: application/json\r\n\r\n"
        )

        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    'Content-Disposition: form-data; name="document"; '
                    f'filename="{path.name}"\r\n'
                ).encode(),
                content_type,
                file_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        request = urllib.request.Request(
            self.base_url + "sendDocument",
            data=b"".join(parts),
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": f"{APP_NAME}/{VERSION}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            err_details = (
                exc.read().decode()
                if isinstance(exc, urllib.error.HTTPError)
                else str(exc)
            )
            raise TelegramError(f"не удалось отправить файл: {err_details}") from exc
        if not result.get("ok"):
            raise TelegramError(str(result.get("description", "ошибка отправки файла")))

    def get_updates(self, timeout: int = 25) -> list[dict[str, Any]]:
        result = self.request(
            "getUpdates",
            {
                "offset": str(self.offset),
                "timeout": str(max(0, min(25, timeout))),
                "allowed_updates": json.dumps(["message", "callback_query"]),
            },
        )
        updates = result if isinstance(result, list) else []
        for item in updates:
            if isinstance(item, dict) and isinstance(item.get("update_id"), int):
                self.offset = item["update_id"] + 1
        return updates


class FetchResult:
    def __init__(
        self,
        ok: bool,
        profile_id: str,
        url: str,
        nickname: str = "неизвестно",
        level: str = "неизвестно",
        presence: str = "неизвестно",
        title: str = "",
        error: str = "",
        source: str = "AniVox WebSocket /api",
        last_seen_timestamp: float = 0,
    ) -> None:
        self.ok = ok
        self.profile_id = profile_id
        self.url = url
        self.nickname = nickname or "неизвестно"
        self.level = level or "неизвестно"
        self.presence = presence or "неизвестно"
        self.title = title
        self.error = error
        self.source = source
        self.last_seen_timestamp = last_seen_timestamp
        self.checked_at = almaty_now()

    @property
    def signature(self) -> str:
        if self.presence.startswith("Смотрит:"):
            state = "watching:" + self.title.lower()
        elif self.presence.startswith("Онлайн"):
            state = "online"
        else:
            state = "offline"
        return "|".join(
            (self.nickname.lower(), self.level, state, self.title.lower())
        ).lower()


def recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("AniVox WebSocket закрыл соединение")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def ws_send_text(sock: socket.socket, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
    length = len(data)
    if length < 126:
        header = bytes((0x81, 0x80 | length))
    elif length < 65536:
        header = bytes((0x81, 0x80 | 126)) + struct.pack("!H", length)
    else:
        header = bytes((0x81, 0x80 | 127)) + struct.pack("!Q", length)
    sock.sendall(header + mask + masked)


def ws_read_message(sock: socket.socket) -> Optional[str]:
    fragments: list[bytes] = []
    while True:
        header = recv_exact(sock, 2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", recv_exact(sock, 8))[0]
        mask = recv_exact(sock, 4) if masked else b""
        data = recv_exact(sock, length)
        if masked:
            data = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
        if opcode == 8:
            return None
        if opcode == 9:  # ping
            pong = bytes((0x8A, len(data))) + data
            sock.sendall(pong)
            continue
        if opcode == 10:
            continue
        if opcode in (1, 0):
            fragments.append(data)
            if header[0] & 0x80:
                return b"".join(fragments).decode("utf-8", errors="replace")


def anivox_websocket_profile(profile_id: str, timeout: int = 20) -> dict[str, Any]:
    host = "anivox.fun"
    raw_socket = socket.create_connection((host, 443), timeout=timeout)
    context = ssl.create_default_context()
    sock = context.wrap_socket(raw_socket, server_hostname=host)
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    handshake = (
        f"GET /api HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Origin: https://{host}\r\n"
        f"User-Agent: {DEFAULT_USER_AGENT}\r\n\r\n"
    ).encode("ascii")
    try:
        sock.sendall(handshake)
        response = b""
        while b"\r\n\r\n" not in response:
            response += sock.recv(4096)
            if len(response) > 65536:
                raise ConnectionError("слишком длинный WebSocket handshake")
        header_text = response.decode("latin1", errors="replace")
        status_line = header_text.splitlines()[0] if header_text else ""
        if " 101 " not in status_line:
            raise ConnectionError(f"AniVox WebSocket: {status_line}")
        ws_send_text(sock, {"type": "get_profile", "id": int(profile_id)})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = ws_read_message(sock)
            if not message:
                continue
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "get_profile":
                return data
        raise TimeoutError("AniVox не прислал ответ get_profile")
    finally:
        try:
            sock.close()
        except OSError:
            pass


def format_last_seen(timestamp: float) -> str:
    if timestamp <= 0:
        return "Был в сети: время неизвестно"
    dt = datetime.fromtimestamp(timestamp, tz=almaty_tz())
    age = max(0, int(time.time() - timestamp))
    if age < 60:
        relative = "только что"
    elif age < 3600:
        relative = f"{age // 60} мин. назад"
    elif age < 86400:
        relative = f"{age // 3600} ч. назад"
    else:
        relative = f"{age // 86400} дн. назад"
    return f"Был в сети: {relative} ({dt.strftime('%H:%M Алматы')})"


def result_from_ws(data: dict[str, Any], profile_id: str, url: str) -> FetchResult:
    if not isinstance(data, dict):
        return FetchResult(
            False,
            profile_id,
            url,
            error="AniVox ответил некорректным форматом данных",
        )
    container = data.get("data")
    user = container.get("user") if isinstance(container, dict) else None
    if not isinstance(user, dict):
        return FetchResult(
            False,
            profile_id,
            url,
            error="AniVox ответил без объекта data.user",
        )
    online_value = user.get("online", 0)
    try:
        online_timestamp = float(online_value or 0)
    except (TypeError, ValueError):
        online_timestamp = 0
    online_status = container.get("online_status", "")
    if online_timestamp == 0:
        title = ""
        if isinstance(online_status, dict):
            title = compact(online_status.get("text", ""))
        else:
            title = compact(online_status)
        title = re.sub(
            r"^(?:смотрит|watching)\s*[:：-]?\s*",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
        presence = f"Смотрит: {title}" if title else "Онлайн"
    else:
        title = ""
        presence = format_last_seen(online_timestamp)
    return FetchResult(
        True,
        profile_id,
        url,
        nickname=compact(user.get("username")) or "неизвестно",
        level=str(user.get("level", "неизвестно")),
        presence=presence,
        title=title,
        last_seen_timestamp=online_timestamp,
    )


def fetch_profile(profile: Profile, timeout: int = 20) -> FetchResult:
    try:
        data = anivox_websocket_profile(profile.profile_id, timeout)
        return result_from_ws(data, profile.profile_id, profile.url)
    except (ConnectionError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return FetchResult(
            False,
            profile.profile_id,
            profile.url,
            error=f"не удалось получить данные через AniVox WebSocket: {exc}",
        )


def format_profile(
    result: FetchResult,
    cache: dict,
    label: str = "",
    session_note: str = "",
    muted_until: float = 0.0,
) -> str:
    name = html.escape(result.nickname + (f" ({label})" if label else ""))
    lines = [
        f"<b>👤 Профиль:</b> {name}",
        f"<b>⭐ Лвл:</b> {html.escape(str(result.level))}",
    ]

    if result.presence.startswith("Смотрит:"):
        escaped_title = html.escape(result.title)
        genres_str, link = get_anime_data(result.title, cache)
        lines.append(f"🍿 <b>Смотрит:</b> <a href='{link}'>{escaped_title}</a>")
        if genres_str and genres_str != "-":
            lines.append(f"🎭 <b>Жанры:</b> <i>{html.escape(genres_str)}</i>")
    else:
        status_ico = "🟢" if result.presence.startswith("Онлайн") else "🔴"
        lines.append(f"{status_ico} <b>Статус:</b> {html.escape(result.presence)}")

    if session_note:
        lines.append(session_note)

    if muted_until > time.time():
        rem_min = max(1, int((muted_until - time.time()) / 60))
        lines.append(f"🔕 <i>Уведомления заглушены еще на {rem_min} мин.</i>")

    try:
        dt = datetime.fromisoformat(result.checked_at).strftime("%H:%M:%S")
    except Exception:
        dt = result.checked_at

    lines.extend(
        [
            f"<i>⏱ Проверено: {dt} (Алматы)</i>",
            f"<a href='{result.url}'>🌐 Открыть профиль</a>",
        ]
    )
    return "\n".join(lines)


def build_profile_inline_keyboard(
    result: FetchResult, cache: dict, profile: Optional[Profile] = None
) -> dict[str, Any]:
    """Генерирует интерактивные кнопки под карточкой профиля."""
    rows: list[list[dict[str, str]]] = []
    first_row: list[dict[str, str]] = []

    if result.presence.startswith("Смотрит:") and result.title:
        _, link = get_anime_data(result.title, cache)
        if link and link != "https://anivox.fun":
            first_row.append({"text": "🍿 Смотреть тайтл", "url": link})
    first_row.append({"text": "🌐 Профиль", "url": result.url})
    rows.append(first_row)

    is_muted = bool(profile and profile.muted_until > time.time())
    mute_label = "🔔 Вкл звук" if is_muted else "🔕 Тишина 2ч"
    rows.append(
        [
            {"text": "🔄 Обновить", "callback_data": f"recheck_{result.profile_id}"},
            {"text": mute_label, "callback_data": f"mute_{result.profile_id}"},
        ]
    )
    return {"inline_keyboard": rows}


def calc_stats(
    entries: list[dict], anime_cache: dict, interval_minutes: int = DEFAULT_INTERVAL
) -> tuple[float, float, float, dict[str, float], dict[str, float], str, int]:
    """
    Возвращает:
    (часы_в_аниме, часы_онлайн, часы_оффлайн, топ_тайтлов, топ_жанров, пик_активности, оценка_серий)
    С защитой от накрутки часов при сне контейнера или перезапусках.
    """
    w_time = 0.0
    on_time = 0.0
    off_time = 0.0
    titles: dict[str, float] = {}
    genres_time: dict[str, float] = {}
    hour_buckets: dict[int, float] = {h: 0.0 for h in range(24)}
    now = datetime.now(almaty_tz()).timestamp()

    # Ограничение максимального шага одной записи для онлайн/просмотра (защита от простоев)
    max_active_gap_hours = max(0.75, (interval_minutes * 3.5) / 60.0)

    for i in range(len(entries)):
        curr = entries[i]
        try:
            dt1 = datetime.fromisoformat(curr["checked_at"])
            t1 = dt1.timestamp()
            t2 = (
                datetime.fromisoformat(entries[i + 1]["checked_at"]).timestamp()
                if i + 1 < len(entries)
                else now
            )
            raw_dur_hours = max(0.0, (t2 - t1) / 3600.0)
            pres = str(curr.get("presence", ""))

            if "Смотрит" in pres:
                dur_hours = min(raw_dur_hours, max_active_gap_hours)
                w_time += dur_hours
                t = curr.get("title") or "Неизвестно"
                titles[t] = titles.get(t, 0.0) + dur_hours
                hour_buckets[dt1.hour] = hour_buckets.get(dt1.hour, 0.0) + dur_hours

                cached = anime_cache.get(t)
                g_list = (
                    cached.get("genres", [])
                    if isinstance(cached, dict)
                    else (cached if isinstance(cached, list) else [])
                )
                for genre in g_list:
                    genres_time[genre] = genres_time.get(genre, 0.0) + dur_hours

            elif "Онлайн" in pres:
                dur_hours = min(raw_dur_hours, max_active_gap_hours)
                on_time += dur_hours
                hour_buckets[dt1.hour] = hour_buckets.get(dt1.hour, 0.0) + (dur_hours * 0.5)
            else:
                off_time += min(raw_dur_hours, 24.0)
        except (ValueError, TypeError, KeyError):
            continue

    peak_hour_str = "нет данных"
    if any(v > 0 for v in hour_buckets.values()):
        best_h = max(hour_buckets, key=lambda h: hour_buckets[h])
        next_h = (best_h + 2) % 24
        peak_hour_str = f"{best_h:02d}:00–{next_h:02d}:00"

    est_episodes = max(0, int(round((w_time * 60.0) / 24.0)))
    return (
        round(w_time, 1),
        round(on_time, 1),
        round(off_time, 1),
        titles,
        genres_time,
        peak_hour_str,
        est_episodes,
    )


def changed_fields(old: str, result: FetchResult) -> list[str]:
    if not old:
        return ["новый профиль"]
    previous = old.split("|")
    current = result.signature.split("|")
    previous += [""] * (4 - len(previous))
    current += [""] * (4 - len(current))
    changes: list[str] = []
    if previous[0] != current[0]:
        changes.append("ник")
    if previous[1] != current[1]:
        changes.append("уровень")
    old_state, new_state = previous[2], current[2]
    if old_state != new_state:
        changes.append("статус")
    if (
        old_state.startswith("watching:")
        and new_state.startswith("watching:")
        and previous[3] != current[3]
    ):
        changes.append("тайтл")
    return changes


class DashboardHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Отдает файлы отчета, JSON API для живого обновления и скрывает лишние логи."""

    store_ref: Optional[Store] = None

    def do_GET(self) -> None:
        parsed_path = urllib.parse.urlparse(self.path).path
        if parsed_path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        if parsed_path == "/api/data.json" and self.store_ref is not None:
            store = self.store_ref
            with store.lock:
                payload = {
                    "updated_at": almaty_now(),
                    "profiles": [asdict(p) for p in store.profiles.values()],
                    "anime_cache": store.anime_cache,
                }
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(encoded)
            return

        if parsed_path == "/":
            self.path = "/anivox_report.html"
        super().do_GET()

    def log_message(self, format: str, *args: Any) -> None:
        pass


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def get_public_base_url(store: Store) -> str:
    """Возвращает базовый URL сайта (из настроек /autoping, переменной Render или локального IP)."""
    if store.autoping_url:
        return store.autoping_url.rstrip("/")
    render_env = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if render_env:
        return render_env.rstrip("/")
    port = int(os.environ.get("PORT", 15887))
    return f"http://78.154.103.11:{port}"


def run_web_server(store: Store, port: int = 15887) -> None:
    """Запускает многопоточный веб-сервер с поддержкой порта Render.com и Live API."""
    actual_port = int(os.environ.get("PORT", port))
    work_dir = os.path.dirname(os.path.abspath(CONFIG_PATH))
    DashboardHTTPRequestHandler.store_ref = store
    handler_cls = partial(DashboardHTTPRequestHandler, directory=work_dir)
    try:
        with ThreadedTCPServer(("0.0.0.0", actual_port), handler_cls) as httpd:
            base_url = get_public_base_url(store)
            logger.info(
                f"Web-сервер запущен на порту {actual_port}: {base_url}/anivox_report.html"
            )
            httpd.serve_forever()
    except Exception as e:
        logger.error(f"Ошибка запуска Web-сервера: {e}")


class Monitor:
    def __init__(self, store: Store, telegram: TelegramClient) -> None:
        self.store = store
        self.telegram = telegram
        self.stop_event = threading.Event()
        self.paused = False
        self.check_lock = threading.RLock()
        self.check_requested = threading.Event()
        self.ping_requested = threading.Event()
        self.bridge_chat_active: bool = False
        self.bridge_chat_initiator: str = ""
        self.chat_lock = threading.Lock()
        self.update_executor = ThreadPoolExecutor(
            max_workers=10, thread_name_prefix="tg-worker"
        )

    def stop(self, *_args: Any) -> None:
        self.stop_event.set()
        self.check_requested.set()
        self.ping_requested.set()
        self.update_executor.shutdown(wait=False, cancel_futures=True)

    def run_autoping(self) -> None:
        """Фоновый цикл автопинга ссылки Render.com каждые 5 минут (300 секунд)."""
        while not self.stop_event.is_set():
            target_url = self.store.autoping_url.strip()
            if target_url:
                try:
                    ping_endpoint = target_url.rstrip("/") + "/healthz"
                    req = urllib.request.Request(
                        ping_endpoint,
                        headers={"User-Agent": DEFAULT_USER_AGENT},
                    )
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        logger.info(
                            "Автопинг успешен: %s (Статус: %s)",
                            ping_endpoint,
                            resp.getcode(),
                        )
                except Exception:
                    # Фолбэк на корень, если внешний хост не поддерживает /healthz
                    try:
                        req2 = urllib.request.Request(
                            target_url, headers={"User-Agent": DEFAULT_USER_AGENT}
                        )
                        with urllib.request.urlopen(req2, timeout=15) as resp2:
                            logger.info(
                                "Автопинг успешен: %s (Статус: %s)",
                                target_url,
                                resp2.getcode(),
                            )
                    except Exception as exc:
                        logger.warning("Ошибка автопинга (%s): %s", target_url, exc)
            self.ping_requested.wait(timeout=300)
            if self.ping_requested.is_set():
                self.ping_requested.clear()

    def start_bridge_chat(self, sender_id: str, sender_name: str) -> None:
        with self.chat_lock:
            self.bridge_chat_active = True
            self.bridge_chat_initiator = f"{sender_name} (ID: {sender_id})"

        announcement = (
            "🚨 <b>ПРИНУДИТЕЛЬНЫЙ ОБЩИЙ ЧАТ АКТИВИРОВАН!</b>\n\n"
            f"👤 <b>Инициатор:</b> {html.escape(str(sender_name))} (ID: <code>{sender_id}</code>)\n"
            f"👥 <b>Связано участников:</b> {len(self.store.allowed_users)}\n\n"
            "⚡️ Чат начался <b>сразу у всех</b> (поддерживаются текст, голосовые, кружки, фото и стикеры)!\n"
            "💬 Любое сообщение, которое вы отправите сюда, моментально увидят все остальные.\n\n"
            "<i>Для выхода и закрытия моста нажмите кнопку «🚪 Завершить чат».</i>"
        )
        for uid in list(self.store.allowed_users):
            try:
                self.telegram.send_message(uid, announcement, reply_markup=TELEGRAM_KEYBOARD)
            except Exception as exc:
                logger.warning("Ошибка отправки старта чата для %s: %s", uid, exc)

    def stop_bridge_chat(self, sender_id: str, sender_name: str) -> None:
        with self.chat_lock:
            if not self.bridge_chat_active:
                try:
                    self.telegram.send_message(
                        sender_id, "ℹ️ Общий чат в данный момент не активен."
                    )
                except Exception:
                    pass
                return
            self.bridge_chat_active = False

        closing_msg = (
            "🛑 <b>ОБЩИЙ ЧАТ ЗАВЕРШЁН</b>\n\n"
            f"👤 Завершил: <b>{html.escape(str(sender_name))}</b> (ID: <code>{sender_id}</code>)\n"
            "Мост связи отключен для всех. Бот вернулся в стандартный режим отслеживания профилей."
        )
        for uid in list(self.store.allowed_users):
            try:
                self.telegram.send_message(uid, closing_msg, reply_markup=TELEGRAM_KEYBOARD)
            except Exception as exc:
                logger.warning("Ошибка отправки завершения чата для %s: %s", uid, exc)

    def relay_bridge_message(
        self, sender_id: str, sender_name: str, message: dict[str, Any], text: str
    ) -> None:
        """Пересылает как текст, так и любые медиафайлы (кружки, ГС, фото, стикеры) всем участникам."""
        msg_id = message.get("message_id")
        has_media = any(
            k in message
            for k in (
                "photo",
                "voice",
                "video_note",
                "sticker",
                "video",
                "audio",
                "document",
                "animation",
            )
        )

        for uid in list(self.store.allowed_users):
            if str(uid) == str(sender_id):
                continue
            try:
                if has_media and isinstance(msg_id, int):
                    self.telegram.send_message(
                        uid, f"📎 <b>[{html.escape(str(sender_name))}]:</b>"
                    )
                    self.telegram.copy_message(
                        to_chat_id=uid, from_chat_id=sender_id, message_id=msg_id
                    )
                elif text:
                    formatted_msg = (
                        f"💬 <b>[{html.escape(str(sender_name))}]:</b>\n"
                        f"{html.escape(text)}"
                    )
                    self.telegram.send_message(uid, formatted_msg)
            except Exception as exc:
                logger.warning("Не удалось переслать сообщение в чат для %s: %s", uid, exc)

    def allowed_notification(self, old: str, result: FetchResult) -> bool:
        settings = self.store.settings
        fields = changed_fields(old, result)
        if not old or not settings.notify_on_change:
            return bool(not old and settings.notify_on_change)
        if "статус" in fields:
            online = result.presence.startswith(("Онлайн", "Смотрит"))
            if online and not settings.notify_on_online:
                fields.remove("статус")
            if not online and not settings.notify_on_offline:
                fields.remove("статус")
        if "тайтл" in fields and not settings.notify_on_title:
            fields.remove("тайтл")
        return bool(fields)

    def _update_session_tracking(self, profile: Profile, result: FetchResult) -> str:
        """Отслеживает начало и завершение сессии просмотра аниме, возвращая сводку по окончании."""
        now_ts = time.time()
        session_summary = ""
        is_watching = result.presence.startswith("Смотрит:") and bool(result.title)

        if profile.active_title and (not is_watching or result.title != profile.active_title):
            elapsed = max(0.0, now_ts - (profile.active_title_since or now_ts))
            if elapsed >= 120:  # Учитываем сессии от 2 минут
                dur_str = format_duration_hm(elapsed)
                est_eps = max(1, int(round((elapsed / 60.0) / 24.0)))
                session_summary = (
                    f"🎬 <b>Сессия завершена:</b> {html.escape(profile.active_title)}\n"
                    f"⏳ <b>Длительность:</b> {dur_str} (~{est_eps} сер.)"
                )
            profile.active_title = ""
            profile.active_title_since = 0.0

        if is_watching and profile.active_title != result.title:
            profile.active_title = result.title
            profile.active_title_since = now_ts

        return session_summary

    def check_one(self, profile: Profile, announce: bool) -> FetchResult:
        result = fetch_profile(profile, self.store.settings.request_timeout)

        with self.check_lock:
            profile.last_check = result.checked_at
            old_signature = profile.last_signature
            changes = changed_fields(old_signature, result)
            profile.last_ok = result.ok
            profile.last_error = result.error
            self.store.add_history(profile.profile_id, result, changes)

            if result.ok:
                session_note = self._update_session_tracking(profile, result)
                is_muted = profile.muted_until > time.time()

                if (
                    announce
                    and not is_muted
                    and self.allowed_notification(old_signature, result)
                ):
                    prefix = ""
                    if old_signature:
                        prefix = "<b>🔔 Изменение:</b> " + ", ".join(changes) + "\n\n"
                    msg_text = prefix + format_profile(
                        result,
                        self.store.anime_cache,
                        profile.label,
                        session_note=session_note,
                        muted_until=profile.muted_until,
                    )
                    inline_kb = build_profile_inline_keyboard(
                        result, self.store.anime_cache, profile
                    )

                    for uid in list(self.store.allowed_users):
                        try:
                            self.telegram.send_message(uid, msg_text, reply_markup=inline_kb)
                        except TelegramError as exc:
                            logger.warning("Telegram notification failed for %s: %s", uid, exc)

                profile.last_signature = result.signature
                profile.last_status = result.presence

            elif (
                announce
                and self.store.settings.notify_on_errors
                and profile.muted_until <= time.time()
            ):
                err_msg = (
                    f"⚠️ <b>Ошибка проверки {html.escape(profile.url)}</b>\n"
                    f"{html.escape(result.error)}"
                )
                for uid in list(self.store.allowed_users):
                    try:
                        self.telegram.send_message(uid, err_msg)
                    except TelegramError as exc:
                        logger.warning("Telegram error notification failed for %s: %s", uid, exc)

            self.store.save()
            return result

    def check_all(self, announce: bool = True) -> list[FetchResult]:
        """Параллельный опрос всех профилей через ThreadPoolExecutor."""
        profiles = list(self.store.profiles.values())
        if not profiles or self.stop_event.is_set():
            return []

        results_map: dict[str, FetchResult] = {}
        workers = min(len(profiles), 8)
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="anivox-check"
        ) as executor:
            future_to_pid = {
                executor.submit(self.check_one, profile, announce): profile.profile_id
                for profile in profiles
            }
            for future in as_completed(future_to_pid):
                if self.stop_event.is_set():
                    break
                pid = future_to_pid[future]
                try:
                    results_map[pid] = future.result()
                except Exception as exc:
                    logger.error("Ошибка проверки профиля %s: %s", pid, exc)

        return [results_map[p.profile_id] for p in profiles if p.profile_id in results_map]

    def history_text(self, args: str) -> str:
        parts = args.split()
        profile_id = parts[0] if parts and parts[0].isdigit() else ""
        try:
            limit = int(parts[1] if profile_id and len(parts) > 1 else parts[0]) if parts else 15
        except ValueError:
            limit = 15
        limit = max(1, min(30, limit))
        profile_ids = [profile_id] if profile_id else list(self.store.profiles)
        blocks: list[str] = []

        p_idx = 1
        for current_id in profile_ids:
            profile = self.store.profiles.get(current_id)
            entries = self.store.history.get(current_id, [])

            title = profile.label if profile and profile.label else current_id

            try:
                last_chk_str = (
                    datetime.fromisoformat(profile.last_check).strftime("%d.%m %H:%M:%S")
                    if profile and profile.last_check
                    else "никогда"
                )
            except Exception:
                last_chk_str = (
                    profile.last_check if profile and profile.last_check else "никогда"
                )

            w_h, _, _, _, _, peak_str, est_eps = calc_stats(
                entries, self.store.anime_cache, self.store.settings.interval_minutes
            )

            rows = [
                f"👤 <b>{p_idx}. {html.escape(title)}</b> (ID {current_id})",
                f"🔄 <i>Автопроверка: {html.escape(last_chk_str)}</i>",
                f"📊 <i>Просмотр: {w_h} ч. (~{est_eps} сер.) | Пик: {peak_str}</i>",
            ]

            if not entries:
                rows.append("<i>История пуста</i>")
            else:
                hist_idx = 1
                for entry in entries[-limit:][::-1]:
                    status = entry.get("presence", "неизвестно")
                    try:
                        dt_str = datetime.fromisoformat(
                            entry.get("checked_at", "")
                        ).strftime("%d.%m %H:%M")
                    except Exception:
                        dt_str = entry.get("checked_at", "?")

                    rows.append(f"<b>{hist_idx}.</b> {dt_str} — {html.escape(status)}")
                    hist_idx += 1

            blocks.append("\n".join(rows))
            p_idx += 1

        return (
            "\n\n".join(blocks)[:4000]
            if blocks
            else "История пока пуста. Записываются только изменения."
        )

    def summary_text(self) -> str:
        if not self.store.profiles:
            return "Профилей нет. Владелец должен добавить профиль через /add."
        counts = {"онлайн": 0, "смотрит": 0, "оффлайн": 0, "ошибка": 0}
        rows = ["🧾 <b>Сводка по пользователям:</b>"]

        idx = 1
        for profile in self.store.profiles.values():
            status = profile.last_status or "не проверялся"
            status_lower = status.lower()
            if not profile.last_ok and profile.last_check:
                counts["ошибка"] += 1
            elif status_lower.startswith("смотрит"):
                counts["смотрит"] += 1
            elif status_lower.startswith("онлайн"):
                counts["онлайн"] += 1
            elif profile.last_check:
                counts["оффлайн"] += 1
            history_count = len(self.store.history.get(profile.profile_id, []))

            try:
                dt_str = (
                    datetime.fromisoformat(profile.last_check).strftime("%d.%m %H:%M")
                    if profile.last_check
                    else "нет"
                )
            except Exception:
                dt_str = profile.last_check

            rows.append(
                f"<b>{idx}.</b> {html.escape(profile.label or profile.profile_id)}: {html.escape(status)}\n"
                f"  изменений: {history_count}, последнее: {dt_str}"
            )
            idx += 1

        rows.append(
            "\nИтого: "
            f"🟢 онлайн {counts['онлайн']} | "
            f"🍿 смотрит {counts['смотрит']} | "
            f"🔴 оффлайн {counts['оффлайн']} | "
            f"⚠️ ошибки {counts['ошибка']}"
        )
        return "\n".join(rows)[:4000]

    def search_text(self, query: str) -> str:
        needle = query.strip().lower()
        if not needle:
            return "Пример: /find текст или нажмите 🔍 Поиск."
        found: list[str] = []
        for profile in self.store.profiles.values():
            entries = self.store.history.get(profile.profile_id, [])
            latest = entries[-1] if entries else {}
            haystack = " ".join(
                [
                    profile.profile_id,
                    profile.label,
                    str(latest.get("nickname", "")),
                    str(latest.get("title", "")),
                ]
            ).lower()
            if needle in haystack:
                found.append(
                    f"• {html.escape(latest.get('nickname', profile.label or profile.profile_id))} "
                    f"(ID {profile.profile_id}) — {html.escape(latest.get('presence', 'не проверялся'))}"
                )
        return "\n".join(["Найдено:"] + found) if found else "Ничего не найдено."

    def probe_text(self) -> str:
        if self.store.profiles:
            profile = next(iter(self.store.profiles.values()))
        else:
            profile = Profile("https://anivox.fun/profile/27788", "27788")
        started = time.monotonic()
        result = fetch_profile(profile, self.store.settings.request_timeout)
        elapsed = time.monotonic() - started
        if result.ok:
            return (
                f"✅ AniVox доступен, WebSocket /api работает.\n"
                f"Ответ за {elapsed:.2f} сек.\n{format_profile(result, self.store.anime_cache)}"
            )
        return f"❌ AniVox не ответил за {elapsed:.2f} сек.\n{html.escape(result.error)}"

    def export_history(self, chat_id: str) -> str:
        path = CONFIG_PATH.with_name("anivox_history_export.json")
        with self.store.lock:
            payload = {
                "exported_at": almaty_now(),
                "profiles": [asdict(profile) for profile in self.store.profiles.values()],
                "history": self.store.history,
            }
            save_json(path, payload)
        try:
            self.telegram.send_document(chat_id, path, "История AniVox (JSON)")
            return "Экспорт истории отправлен файлом."
        except TelegramError as exc:
            return f"Не удалось отправить экспорт: {exc}"

    def generate_html_report(self, chat_id: Optional[str] = None) -> None:
        path = CONFIG_PATH.with_name("anivox_report.html")
        with self.store.lock:
            profiles_snapshot = list(self.store.profiles.items())
            history_snapshot = {k: list(v) for k, v in self.store.history.items()}
            cache_snapshot = dict(self.store.anime_cache)
            interval_min = self.store.settings.interval_minutes

        html_doc = [
            "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>",
            "<title>AniVox Dashboard</title><script src='https://cdn.jsdelivr.net/npm/chart.js'></script>",
            "<style>",
            ":root { --bg: #f4f6f9; --card: #ffffff; --text: #333; --border: #e0e0e0; --primary: #007bff; --hover: #f1f1f1; --accent: #e3f2fd; }",
            "@media (prefers-color-scheme: dark) { :root { --bg: #121212; --card: #1e1e1e; --text: #e0e0e0; --border: #333; --primary: #bb86fc; --hover: #2c2c2c; --accent: #2c2c2c; } }",
            "body{font-family:'Segoe UI',sans-serif; background:var(--bg); color:var(--text); margin:0; padding:20px; transition: 0.3s;}",
            ".header-bar { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; margin-bottom:12px; }",
            "h2{margin:8px 0; font-size:26px;}",
            ".live-badge { font-size:13px; padding:6px 12px; border-radius:20px; background:var(--accent); border:1px solid var(--border); }",
            ".container { display: flex; flex-direction: column; max-width: 1000px; margin: 0 auto; }",
            ".tabs { display: flex; flex-wrap: wrap; background: var(--card); border-radius: 12px 12px 0 0; border: 1px solid var(--border); border-bottom: none; }",
            ".tabs button { flex: 1; min-width: 120px; background: inherit; color: var(--text); border: none; padding: 15px; cursor: pointer; font-weight: bold; transition: 0.3s; }",
            ".tabs button:hover { background: var(--hover); }",
            ".tabs button.active { border-bottom: 3px solid var(--primary); color: var(--primary); }",
            ".tabcontent { display: none; background: var(--card); padding: 25px; border: 1px solid var(--border); border-radius: 0 0 12px 12px; }",
            ".stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 15px; margin-bottom: 20px; }",
            ".card { background: var(--accent); padding: 16px; border-radius: 10px; text-align: center; border: 1px solid var(--border); font-size: 15px; }",
            ".card span { display: block; font-size: 22px; font-weight: bold; color: var(--primary); margin-top: 5px; }",
            ".charts { display: flex; flex-wrap: wrap; gap: 20px; justify-content: space-around; margin-bottom: 20px; }",
            ".chart-box { width: 300px; max-width: 100%; }",
            "table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }",
            "th, td { border: 1px solid var(--border); padding: 10px; text-align: left; }",
            "th { background: var(--primary); color: #fff; }",
            ".history-table th, .history-table td { padding: 8px; font-size: 13px; border: 1px solid var(--border); }",
            "a { color: var(--primary); text-decoration: none; } a:hover { text-decoration: underline; }",
            "</style></head><body>",
            "<div class='container' id='dashboard-root'>",
            f"<div class='header-bar'><h2>🌌 AniVox Dashboard v{VERSION}</h2><div class='live-badge' id='live-indicator'>🟢 Live-обновление ({almaty_now()[11:19]})</div></div>",
            "<div class='tabs'>",
        ]

        tab_idx = 1
        for pid, p in profiles_snapshot:
            name = (
                (p.last_signature.split("|")[0].capitalize() if p.last_signature else "")
                or p.label
                or pid
            )
            html_doc.append(
                f"<button class='tablinks' data-tab='id_{pid}' onclick=\"openTab('id_{pid}')\">{tab_idx}. {html.escape(name)}</button>"
            )
            tab_idx += 1
        html_doc.append("</div>")

        chart_configs: list[str] = []
        tab_idx = 1
        for pid, p in profiles_snapshot:
            name = (
                (p.last_signature.split("|")[0].capitalize() if p.last_signature else "")
                or p.label
                or pid
            )
            entries = history_snapshot.get(pid, [])

            w_h, on_h, off_h, titles, genres_time, peak_str, est_eps = calc_stats(
                entries, cache_snapshot, interval_min
            )

            html_doc.append(f"<div id='id_{pid}' class='tabcontent'>")
            html_doc.append(
                f"<h3>👤 {tab_idx}. {html.escape(name)} <small style='font-weight:normal; opacity:0.8;'>({html.escape(p.last_status or 'не проверялся')})</small></h3>"
            )

            html_doc.append("<div class='stats-grid'>")
            html_doc.append(f"<div class='card'>В аниме <span>{w_h} ч. (~{est_eps} эп.)</span></div>")
            html_doc.append(f"<div class='card'>Онлайн <span>{on_h} ч.</span></div>")
            html_doc.append(f"<div class='card'>Оффлайн <span>{off_h} ч.</span></div>")
            html_doc.append(f"<div class='card'>Пик активности <span>{peak_str}</span></div>")
            html_doc.append("</div>")

            html_doc.append("<div class='charts'>")
            if w_h > 0 or on_h > 0 or off_h > 0:
                html_doc.append(f"<div class='chart-box'><canvas id='c1_{pid}'></canvas></div>")
                chart_configs.append(
                    f"try{{new Chart(document.getElementById('c1_{pid}'), {{type:'doughnut', data:{{labels:['Смотрит','Онлайн','Оффлайн'], datasets:[{{data:[{w_h},{on_h},{off_h}], backgroundColor:['#bb86fc','#03dac6','#cf6679']}}]}}, options:{{animation:false, plugins:{{legend:{{position:'bottom'}}}}}} }});}}catch(e){{}}"
                )

            if genres_time:
                sorted_genres = sorted(genres_time.items(), key=lambda x: x[1], reverse=True)[:6]
                g_lbls = [json.dumps(g, ensure_ascii=False) for g, _ in sorted_genres]
                g_data = [str(round(v, 1)) for _, v in sorted_genres]
                html_doc.append(f"<div class='chart-box'><canvas id='c2_{pid}'></canvas></div>")
                chart_configs.append(
                    f"try{{new Chart(document.getElementById('c2_{pid}'), {{type:'polarArea', data:{{labels:[{','.join(g_lbls)}], datasets:[{{label:'Часов', data:[{','.join(g_data)}], backgroundColor:'rgba(187, 134, 252, 0.5)'}}]}}, options:{{animation:false, plugins:{{legend:{{position:'bottom'}}}}, scales:{{r:{{ticks:{{display:false}}}}}} }} }});}}catch(e){{}}"
                )
            html_doc.append("</div>")

            if titles:
                html_doc.append(
                    "<h4>🏆 Топ тайтлов:</h4><table><tr><th>Тайтл</th><th>Жанры</th><th>Часы</th><th>Серий (~)</th></tr>"
                )
                for t, hrs in sorted(titles.items(), key=lambda x: x[1], reverse=True):
                    gnrs, link = get_anime_data(t, cache_snapshot)
                    ep_cnt = max(1, int(round((hrs * 60.0) / 24.0)))
                    html_doc.append(
                        f"<tr><td><a href='{link}' target='_blank'>{html.escape(t)}</a></td><td><small>{html.escape(gnrs)}</small></td><td><b>{round(hrs, 1)}</b></td><td>~{ep_cnt}</td></tr>"
                    )
                html_doc.append("</table><br>")

            if entries:
                html_doc.append(
                    "<h4>📈 История (до 50):</h4><table class='history-table'><tr><th>#</th><th>Дата (Алматы)</th><th>Статус</th></tr>"
                )
                hist_idx = 1
                for e in reversed(entries[-50:]):
                    try:
                        dt_str = datetime.fromisoformat(e.get("checked_at", "")).strftime(
                            "%d.%m %H:%M"
                        )
                    except Exception:
                        dt_str = e.get("checked_at", "")

                    presence = str(e.get("presence", ""))

                    if presence.startswith("Смотрит:"):
                        title_part = presence.replace("Смотрит: ", "").strip()
                        _, link = get_anime_data(title_part, cache_snapshot)
                        presence_html = (
                            f"Смотрит: <a href='{link}' target='_blank'>{html.escape(title_part)}</a>"
                        )
                    else:
                        presence_html = html.escape(presence)

                    html_doc.append(
                        f"<tr><td>{hist_idx}</td><td>{dt_str}</td><td>{presence_html}</td></tr>"
                    )
                    hist_idx += 1
                html_doc.append("</table>")

            html_doc.append("</div>")
            tab_idx += 1

        charts_init_js = "\n".join(chart_configs)
        js = f"""
        <script id="charts-runner">
        function renderAllCharts() {{
            if (typeof Chart !== 'undefined' && Chart.instances) {{
                Object.values(Chart.instances).forEach(c => c.destroy());
            }}
            {charts_init_js}
        }}
        </script>
        <script>
        function openTab(id) {{
            try {{
                var t = document.getElementsByClassName('tabcontent');
                for(var i=0;i<t.length;i++) t[i].style.display='none';
                var l = document.getElementsByClassName('tablinks');
                for(var i=0;i<l.length;i++) l[i].className = l[i].className.replace(' active','');

                var content = document.getElementById(id);
                if(content) content.style.display='block';

                var btn = document.querySelector("button[data-tab='" + id + "']");
                if(btn) btn.className += ' active';

                sessionStorage.setItem('activeTab_anivox', id);
            }} catch(err) {{}}
        }}

        function restoreActiveTab() {{
            var activeId = sessionStorage.getItem('activeTab_anivox');
            if (activeId && document.getElementById(activeId)) {{
                openTab(activeId);
            }} else {{
                var firstBtn = document.getElementsByClassName('tablinks')[0];
                if(firstBtn) {{
                    var tabId = firstBtn.getAttribute('data-tab');
                    if(tabId) openTab(tabId);
                }}
            }}
        }}

        document.addEventListener('DOMContentLoaded', function() {{
            restoreActiveTab();
            renderAllCharts();
        }});

        // Live AJAX обновление без моргания экрана каждые 15 секунд
        setInterval(async function() {{
            if (window.location.protocol === 'file:') return;
            try {{
                const res = await fetch(window.location.pathname, {{cache: 'no-store'}});
                if (!res.ok) return;
                const htmlText = await res.text();
                const parser = new DOMParser();
                const doc = parser.parseFromString(htmlText, 'text/html');
                const newRoot = doc.getElementById('dashboard-root');
                const newRunner = doc.getElementById('charts-runner');
                if (newRoot && document.getElementById('dashboard-root')) {{
                    const scrollY = window.scrollY;
                    document.getElementById('dashboard-root').innerHTML = newRoot.innerHTML;
                    if (newRunner) {{
                        eval(newRunner.textContent);
                    }}
                    restoreActiveTab();
                    renderAllCharts();
                    window.scrollTo(0, scrollY);
                }}
            }} catch(e) {{}}
        }}, 15000);
        </script>
        """
        html_doc.append(js)
        html_doc.append("</div></body></html>")

        temp_html = path.with_suffix(".html.tmp")
        temp_html.write_text("\n".join(html_doc), encoding="utf-8")
        os.replace(temp_html, path)

        if chat_id:
            base_url = get_public_base_url(self.store)
            try:
                self.telegram.send_document(
                    chat_id,
                    path,
                    caption=(
                        f"🌐 Нейро-Отчет обновлен (Live AJAX)! Можете открыть этот файл "
                        f"или перейти по адресу: {base_url}/anivox_report.html"
                    ),
                )
            except Exception as exc:
                self.telegram.send_message(chat_id, f"❌ Ошибка отправки отчета: {exc}")

    def enforce_cleanup(self) -> None:
        days = self.store.settings.auto_cleanup_days
        if days <= 0:
            return
        cutoff = time.time() - (days * 86400)
        with self.store.lock:
            for profile_id, entries in self.store.history.items():
                cleaned = []
                for entry in entries:
                    try:
                        when = datetime.fromisoformat(
                            entry.get("checked_at", "")
                        ).timestamp()
                    except (TypeError, ValueError):
                        when = time.time()
                    if when >= cutoff:
                        cleaned.append(entry)
                self.store.history[profile_id] = cleaned
            self.store.save()

    def cleanup_history(self, args: str) -> str:
        try:
            days = max(1, min(3650, int(args or "30")))
        except ValueError:
            return "Пример: /cleanup 30"
        cutoff = time.time() - days * 86400
        removed = 0
        with self.store.lock:
            for profile_id, entries in list(self.store.history.items()):
                kept: list[dict[str, Any]] = []
                for entry in entries:
                    try:
                        when = datetime.fromisoformat(
                            entry.get("checked_at", "")
                        ).timestamp()
                    except (TypeError, ValueError):
                        when = time.time()
                    if when >= cutoff:
                        kept.append(entry)
                    else:
                        removed += 1
                self.store.history[profile_id] = kept
            self.store.save()
        return f"Удалено записей старше {days} дн.: {removed}."

    def send_settings_menu(self, chat_id: str, message_id: Optional[int] = None) -> None:
        if chat_id != self.store.chat_id:
            self.telegram.send_message(
                chat_id, "❌ Настройки может менять только владелец бота."
            )
            return

        s = self.store.settings
        kb = {
            "inline_keyboard": [
                [{"text": f"⏱ Интервал: {s.interval_minutes} мин", "callback_data": "ignore"}],
                [
                    {"text": "1", "callback_data": "set_int_1"},
                    {"text": "5", "callback_data": "set_int_5"},
                    {"text": "10", "callback_data": "set_int_10"},
                    {"text": "30", "callback_data": "set_int_30"},
                ],
                [
                    {
                        "text": f"🧹 Очистка истории: {s.auto_cleanup_days} дн.",
                        "callback_data": "ignore",
                    }
                ],
                [
                    {"text": "5 дн", "callback_data": "set_cln_5"},
                    {"text": "15 дн", "callback_data": "set_cln_15"},
                    {"text": "30 дн", "callback_data": "set_cln_30"},
                    {"text": "Выкл", "callback_data": "set_cln_0"},
                ],
            ]
        }
        text = "⚙️ <b>Панель управления (Владелец)</b>\nМеняйте параметры в один клик:"
        if message_id:
            self.telegram.edit_message_text(chat_id, text, message_id, kb)
        else:
            self.telegram.send_message(chat_id, text, kb)

    def send_notifications_menu(
        self, chat_id: str, message_id: Optional[int] = None
    ) -> None:
        if chat_id != self.store.chat_id:
            self.telegram.send_message(
                chat_id, "❌ Настройки уведомлений может менять только владелец."
            )
            return

        s = self.store.settings
        kb = {
            "inline_keyboard": [
                [
                    {
                        "text": f"{'✅' if s.notify_on_online else '❌'} Онлайн",
                        "callback_data": "ntf_onl",
                    },
                    {
                        "text": f"{'✅' if s.notify_on_offline else '❌'} Оффлайн",
                        "callback_data": "ntf_off",
                    },
                ],
                [
                    {
                        "text": f"{'✅' if s.notify_on_title else '❌'} Смена тайтла",
                        "callback_data": "ntf_ttl",
                    },
                    {
                        "text": f"{'✅' if s.notify_on_errors else '❌'} Ошибки",
                        "callback_data": "ntf_err",
                    },
                ],
                [
                    {
                        "text": f"{'🔔' if s.notify_on_change else '🔕'} ГЛАВНЫЙ РУБИЛЬНИК ({'ВКЛ' if s.notify_on_change else 'ВЫКЛ'})",
                        "callback_data": "ntf_all",
                    }
                ],
            ]
        }
        text = "🔕 <b>Настройка уведомлений</b>\nВыберите, о чем бот должен вам писать:"
        if message_id:
            self.telegram.edit_message_text(chat_id, text, message_id, kb)
        else:
            self.telegram.send_message(chat_id, text, kb)

    def command(self, chat_id: str, is_owner: bool, command: str, args: str) -> str:
        if command in {"/start", "/help"}:
            return (
                f"<b>AniVox Monitor v{VERSION}</b>\n\n"
                "<code>/add &lt;ссылка&gt; [метка]</code> — добавить профиль\n"
                "<code>/list</code> — список профилей\n"
                "<code>/remove &lt;id&gt;</code> — удалить профиль\n"
                "<code>/check [id|all]</code> — принудительная параллельная проверка\n"
                "<code>/history [id] [кол-во]</code> — показать историю и пик активности\n"
                "<code>/report</code> (или /site) — сгенерировать Live HTML-отчет\n"
                "<code>/find &lt;текст&gt;</code> — поиск в истории\n"
                "<code>/settings</code> — меню настроек\n"
                "<code>/notifications</code> — настройка уведомлений\n"
                "<code>/friends</code> — управление друзьями\n"
                "<code>/autoping &lt;ссылка&gt;</code> — автопинг ссылки для Render.com\n"
                "<code>/chat</code> — принудительно связать всех в общий чат\n"
                "<code>/stopchat</code> — завершить общий чат для всех"
            )
        if command == "/autoping":
            if not is_owner:
                return "Настраивать автопинг может только владелец."
            arg = args.strip()
            if not arg:
                current = self.store.autoping_url or "не задана (отключен)"
                return (
                    f"🌐 <b>Текущая ссылка автопинга:</b> <code>{html.escape(current)}</code>\n\n"
                    "Чтобы задать ссылку Render.com, отправьте:\n"
                    "<code>/autoping https://ваш-проект.onrender.com</code>\n\n"
                    "Чтобы отключить автопинг:\n"
                    "<code>/autoping off</code>"
                )
            if arg.lower() in {"off", "stop", "0", "выкл", "нет"}:
                self.store.autoping_url = ""
                self.store.save()
                return "🛑 Автопинг отключен."
            if not arg.startswith(("http://", "https://")):
                arg = "https://" + arg
            self.store.autoping_url = arg.rstrip("/")
            self.store.save()
            self.ping_requested.set()
            return (
                f"✅ <b>Автопинг установлен и запущен!</b>\n"
                f"🔗 Ссылка: <code>{html.escape(self.store.autoping_url)}</code>\n"
                f"⏱ Интервал пинга: каждые 5 минут.\n"
                f"📊 Ваш сайт-отчет теперь доступен по адресу:\n"
                f"{html.escape(self.store.autoping_url)}/anivox_report.html"
            )
        if command == "/list":
            if not self.store.profiles:
                return "Профилей нет. Владелец должен добавить их через /add."
            idx = 1
            out = ["<b>Мои профили:</b>"]
            for p in self.store.profiles.values():
                mute_tag = " 🔕" if p.muted_until > time.time() else ""
                out.append(
                    f"<b>{idx}.</b> <code>{p.profile_id}</code> — "
                    f"{html.escape(p.label or p.url)} — "
                    f"{html.escape(p.last_status or 'не проверялся')}{mute_tag}"
                )
                idx += 1
            return "\n".join(out)
        if command == "/add":
            if not is_owner:
                return "Добавлять профили может только владелец."
            parts = args.split(maxsplit=1)
            if not parts:
                return "Пример: /add https://anivox.fun/profile/27788 метка"
            try:
                url, profile_id = normalize_profile_url(parts[0])
            except ValueError as exc:
                return str(exc)
            existing = self.store.profiles.get(profile_id)
            profile = existing or Profile(url=url, profile_id=profile_id)
            profile.url = url
            if len(parts) > 1:
                profile.label = parts[1].strip()
            self.store.profiles[profile_id] = profile
            self.store.save()
            self.check_requested.set()
            return f"Профиль {profile_id} {'обновлён' if existing else 'добавлен'}."
        if command == "/remove":
            if not is_owner:
                return "Удалять профили может только владелец."
            profile_id = args.split()[0] if args else ""
            if profile_id in self.store.profiles:
                del self.store.profiles[profile_id]
                self.store.save()
                return f"Профиль {profile_id} удалён."
            return "Профиль не найден."
        if command == "/check":
            target = (args or "all").lower()
            if target == "all":
                results = self.check_all(announce=False)
            elif target in self.store.profiles:
                results = [self.check_one(self.store.profiles[target], announce=False)]
            else:
                results = []

            if not results:
                return "Профиль не найден или список пуст."

            for res in results:
                prof = self.store.profiles.get(res.profile_id)
                if res.ok and prof:
                    msg_text = format_profile(
                        res,
                        self.store.anime_cache,
                        prof.label,
                        muted_until=prof.muted_until,
                    )
                    kb = build_profile_inline_keyboard(res, self.store.anime_cache, prof)
                    self.telegram.send_message(chat_id, msg_text, reply_markup=kb)
                else:
                    self.telegram.send_message(
                        chat_id, f"❌ {html.escape(res.url)}\n{html.escape(res.error)}"
                    )
            return ""
        if command == "/history":
            return self.history_text(args)
        if command == "/summary":
            return self.summary_text()
        if command in {"/html", "/report", "/site"}:
            self.telegram.send_message(chat_id, "⏳ Генерирую Live-дашборд с графиками...")
            self.generate_html_report(chat_id)
            return ""
        if command in {"/find", "/search"}:
            return self.search_text(args)
        if command in {"/probe", "/test"}:
            return self.probe_text()
        if command == "/export":
            return self.export_history(chat_id)
        if command == "/cleanup":
            if not is_owner:
                return "Только владелец."
            return self.cleanup_history(args)
        if command == "/settings":
            self.send_settings_menu(chat_id)
            return ""
        if command in {"/notifications", "/notif"}:
            self.send_notifications_menu(chat_id)
            return ""
        if command == "/pause":
            if not is_owner:
                return "Только владелец."
            self.paused = True
            return "⏸ Проверки приостановлены."
        if command == "/resume":
            if not is_owner:
                return "Только владелец."
            self.paused = False
            self.check_requested.set()
            return "▶️ Проверки возобновлены."
        if command in {"/friends", "/friend"}:
            if not is_owner:
                return "Управлять друзьями может только владелец."
            parts = args.split()
            if len(parts) == 2 and parts[0] == "add":
                f_id = parts[1].strip()
                if f_id not in self.store.allowed_users:
                    self.store.allowed_users.append(f_id)
                    self.store.save()
                return (
                    f"✅ Пользователь <code>{f_id}</code> добавлен в Белый список! "
                    "Теперь он будет получать уведомления и сможет использовать меню."
                )
            elif len(parts) == 2 and parts[0] == "remove":
                f_id = parts[1].strip()
                if f_id in self.store.allowed_users and f_id != self.store.chat_id:
                    self.store.allowed_users.remove(f_id)
                    self.store.save()
                return f"✅ Пользователь {f_id} удален."
            else:
                f_list = "\n".join([f"• <code>{u}</code>" for u in self.store.allowed_users])
                return (
                    f"🎭 <b>Ваши друзья (имеют доступ к боту):</b>\n{f_list}\n\n"
                    f"<i>Для добавления:</i>\n<code>/friend add ID</code>\n"
                    f"<i>Для удаления:</i>\n<code>/friend remove ID</code>"
                )

        return "Неизвестная команда. Используйте /help."

    def authorized(self, message: dict[str, Any]) -> bool:
        chat_id = str(message.get("chat", {}).get("id", ""))
        return chat_id in self.store.allowed_users

    def _handle_callback(self, cb: dict[str, Any]) -> None:
        chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
        message_id = cb.get("message", {}).get("message_id")
        data = str(cb.get("data", ""))
        query_id = str(cb.get("id", ""))

        if chat_id not in self.store.allowed_users:
            self.telegram.answer_callback_query(query_id, "⛔️ Доступ ограничен.")
            return

        # Быстрая перепроверка профиля с обновлением карточки на месте (доступна всем из белого списка)
        if data.startswith("recheck_"):
            pid = data.split("_", 1)[1]
            prof = self.store.profiles.get(pid)
            if not prof:
                self.telegram.answer_callback_query(query_id, "Профиль не найден.")
                return
            if chat_id != self.store.chat_id:
                try:
                    self.telegram.send_message(
                        self.store.chat_id,
                        f"🕵️‍♂️ Друг (ID <code>{chat_id}</code>) обновил карточку профиля <code>{pid}</code>",
                    )
                except Exception:
                    pass
            res = self.check_one(prof, announce=False)
            if res.ok and message_id:
                new_text = format_profile(
                    res,
                    self.store.anime_cache,
                    prof.label,
                    muted_until=prof.muted_until,
                )
                new_kb = build_profile_inline_keyboard(res, self.store.anime_cache, prof)
                try:
                    self.telegram.edit_message_text(chat_id, new_text, message_id, new_kb)
                except Exception:
                    pass
                self.telegram.answer_callback_query(query_id, f"✅ {res.presence}")
            else:
                self.telegram.answer_callback_query(
                    query_id, f"⚠️ Ошибка: {res.error[:120]}"
                )
            return

        if chat_id != self.store.chat_id:
            self.telegram.answer_callback_query(
                query_id, "Эту настройку меняет только владелец!"
            )
            try:
                self.telegram.send_message(
                    self.store.chat_id,
                    f"🕵️‍♂️ Друг (ID <code>{chat_id}</code>) попытался нажать кнопку: <b>{html.escape(data)}</b>",
                )
            except Exception:
                pass
            return

        if data.startswith("mute_"):
            pid = data.split("_", 1)[1]
            prof = self.store.profiles.get(pid)
            if not prof:
                self.telegram.answer_callback_query(query_id, "Профиль не найден.")
                return
            if prof.muted_until > time.time():
                prof.muted_until = 0.0
                status_note = "🔔 Уведомления профиля включены"
            else:
                prof.muted_until = time.time() + 7200
                status_note = "🔕 Заглушено на 2 часа"
            self.store.save()
            res = self.check_one(prof, announce=False)
            if res.ok and message_id:
                new_text = format_profile(
                    res,
                    self.store.anime_cache,
                    prof.label,
                    muted_until=prof.muted_until,
                )
                new_kb = build_profile_inline_keyboard(res, self.store.anime_cache, prof)
                try:
                    self.telegram.edit_message_text(chat_id, new_text, message_id, new_kb)
                except Exception:
                    pass
            self.telegram.answer_callback_query(query_id, status_note)
            return

        if data.startswith("set_int_"):
            val = int(data.split("_")[-1])
            self.store.settings.interval_minutes = val
            self.store.save()
            self.send_settings_menu(chat_id, message_id)
            self.telegram.answer_callback_query(
                query_id, f"Интервал изменен на {val} мин."
            )
            self.check_requested.set()
        elif data.startswith("set_cln_"):
            val = int(data.split("_")[-1])
            self.store.settings.auto_cleanup_days = val
            self.store.save()
            self.send_settings_menu(chat_id, message_id)
            self.telegram.answer_callback_query(query_id, f"Авто-очистка: {val} дн.")
        elif data.startswith("ntf_"):
            s = self.store.settings
            if data == "ntf_onl":
                s.notify_on_online = not s.notify_on_online
            elif data == "ntf_off":
                s.notify_on_offline = not s.notify_on_offline
            elif data == "ntf_ttl":
                s.notify_on_title = not s.notify_on_title
            elif data == "ntf_err":
                s.notify_on_errors = not s.notify_on_errors
            elif data == "ntf_all":
                s.notify_on_change = not s.notify_on_change
            self.store.save()
            self.send_notifications_menu(chat_id, message_id)
            self.telegram.answer_callback_query(
                query_id, "Настройки уведомлений обновлены."
            )
        else:
            self.telegram.answer_callback_query(query_id, "Готово")

    def handle_update(self, update: dict[str, Any]) -> None:
        try:
            if "callback_query" in update:
                self._handle_callback(update["callback_query"])
                return

            message = update.get("message", {})
            if not isinstance(message, dict):
                return

            chat_id = str(message.get("chat", {}).get("id", ""))
            if not chat_id:
                return
            user_obj = message.get("from", {})
            user_name = (
                user_obj.get("username") or user_obj.get("first_name") or "Без имени"
            )

            if chat_id not in self.store.allowed_users:
                try:
                    self.telegram.send_message(
                        self.store.chat_id,
                        f"🚨 <b>Внимание! Новый пользователь стучится в бота:</b>\n"
                        f"• Имя: {html.escape(str(user_name))}\n"
                        f"• ID: <code>{chat_id}</code>\n\n"
                        f"Чтобы дать ему доступ, отправьте:\n<code>/friend add {chat_id}</code>",
                    )
                except Exception:
                    pass

                self.telegram.send_message(
                    chat_id,
                    f"⛔️ Доступ ограничен. Ваш ID: <code>{chat_id}</code>.\n"
                    "Владелец бота получил уведомление о вашем запросе.",
                )
                return

            is_owner = chat_id == self.store.chat_id
            text = compact(message.get("text") or message.get("caption", ""))

            if text in {"💬 Связать всех в чат", "/chat", "/chat_start"}:
                self.start_bridge_chat(chat_id, user_name)
                return

            if text in {"🚪 Завершить чат", "/stopchat", "/chat_stop"}:
                self.stop_bridge_chat(chat_id, user_name)
                return

            button_names = {
                "📊 Проверить всех",
                "👥 Мои профили",
                "🌐 Открыть сайт-отчет",
                "📈 История",
                "⚙️ Настройки",
                "🔕 Уведомления",
                "🧪 Тест связи",
                "⏸ Пауза",
                "▶️ Продолжить",
                "🎭 Друзья",
                "➕ Добавить",
                "🗑 Удалить",
                "🔍 Поиск",
            }

            if self.bridge_chat_active and not text.startswith("/"):
                if text not in button_names:
                    self.relay_bridge_message(chat_id, user_name, message, text)
                    return

            if not is_owner and text and not self.bridge_chat_active:
                try:
                    self.telegram.send_message(
                        self.store.chat_id,
                        f"🕵️‍♂️ Друг (ID <code>{chat_id}</code>) нажал/написал:\n<b>{html.escape(text)}</b>",
                    )
                except Exception:
                    pass

            button_commands = {
                "📊 Проверить всех": "/check all",
                "👥 Мои профили": "/list",
                "🌐 Открыть сайт-отчет": "/report",
                "📈 История": "/history",
                "⚙️ Настройки": "/settings",
                "🔕 Уведомления": "/notifications",
                "🧪 Тест связи": "/probe",
                "⏸ Пауза": "/pause",
                "▶️ Продолжить": "/resume",
                "🎭 Друзья": "/friends",
            }

            if text in button_commands:
                text = button_commands[text]

            if text == "➕ Добавить":
                self.telegram.send_message(
                    chat_id,
                    "Для добавления отправьте:\n<code>/add https://anivox.fun/profile/27788 метка</code>",
                )
                return
            if text == "🗑 Удалить":
                self.telegram.send_message(
                    chat_id, "Для удаления отправьте:\n<code>/remove ID</code>"
                )
                return
            if text == "🔍 Поиск":
                self.telegram.send_message(
                    chat_id, "Для поиска отправьте:\n<code>/find текст</code>"
                )
                return

            if not text:
                return

            command, _, args = text.partition(" ")
            command = command.lower().split("@", 1)[0]
            response = self.command(chat_id, is_owner, command, args.strip())
            if response:
                try:
                    self.telegram.send_message(chat_id, response)
                except TelegramError as exc:
                    logger.warning("Не удалось ответить в Telegram: %s", exc)
        except Exception as exc:
            logger.error("Ошибка обработки апдейта: %s", exc, exc_info=True)

    def run(self) -> None:
        """Неблокирующий Long-Polling цикл с обработкой апдейтов в пуле воркеров."""
        while not self.stop_event.is_set():
            try:
                for update in self.telegram.get_updates(timeout=25):
                    if self.stop_event.is_set():
                        break
                    self.update_executor.submit(self.handle_update, update)
            except TelegramError as exc:
                logger.warning("Сбой сети Telegram, жду 3с: %s", exc)
                self.stop_event.wait(3)
            except Exception as exc:
                logger.error("Критическая ошибка цикла: %s", exc)
                self.stop_event.wait(3)

    def run_scheduler(self) -> None:
        last_cleanup = 0.0
        while not self.stop_event.is_set():
            try:
                if time.time() - last_cleanup > 3600:
                    self.enforce_cleanup()
                    last_cleanup = time.time()

                if not self.paused:
                    logger.info(
                        "Параллельная автопроверка: профилей=%s, интервал=%s мин.",
                        len(self.store.profiles),
                        self.store.settings.interval_minutes,
                    )
                    self.check_all(True)
                    self.generate_html_report(None)

            except Exception as exc:
                logger.error("Ошибка во время автопроверки: %s", exc, exc_info=True)

            interval_sec = max(10, self.store.settings.interval_minutes * 60)
            self.check_requested.wait(timeout=interval_sec)
            if self.check_requested.is_set():
                self.check_requested.clear()


def prompt(label: str) -> str:
    while True:
        value = input(label).strip()
        if value:
            return value
        print("Значение не может быть пустым.")


def run_self_test() -> int:
    print(f"{APP_NAME} {VERSION} — автономная проверка")
    checks = [
        (
            "получение реального публичного профиля через WebSocket",
            lambda: _test_live_profile(),
        ),
        (
            "разбор ссылки",
            lambda: normalize_profile_url("https://anivox.fun/profile/27788")
            == ("https://anivox.fun/profile/27788", "27788"),
        ),
        (
            "очистка названий аниме",
            lambda: clean_anime_title("Магическая битва 2 сезон / Jujutsu Kaisen [ТВ-2]")
            == "Магическая битва",
        ),
        ("сохранение настроек", _test_store),
    ]
    failed = 0
    for name, check in checks:
        try:
            result = check()
            if not result:
                raise AssertionError("проверка вернула False")
            print(f"  OK  {name}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
    print(f"Результат: {len(checks) - failed}/{len(checks)} проверок.")
    return 1 if failed else 0


def _test_live_profile() -> bool:
    url, profile_id = normalize_profile_url("https://anivox.fun/profile/27788")
    result = fetch_profile(Profile(url=url, profile_id=profile_id), timeout=20)
    print(format_profile(result, {}))
    return result.ok and result.nickname != "неизвестно" and result.level != "неизвестно"


def _test_store() -> bool:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        store = Store(path)
        store.profiles["27788"] = Profile(
            "https://anivox.fun/profile/27788", "27788"
        )
        store.save()
        return "27788" in Store(path).profiles


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return run_self_test()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
        ],
    )
    store = Store(CONFIG_PATH)
    if not store.token:
        store.token = prompt("Токен Telegram-бота: ")
    if not store.chat_id:
        store.chat_id = prompt("Ваш chat ID: ")
    store.save()
    telegram = TelegramClient(store.token)
    try:
        account = telegram.get_me()
    except TelegramError as exc:
        print(f"Ошибка проверки токена: {exc}")
        return 2
    print(f"Бот подтверждён: @{account.get('username', 'без username')}")
    monitor = Monitor(store, telegram)
    signal.signal(signal.SIGINT, monitor.stop)
    signal.signal(signal.SIGTERM, monitor.stop)
    try:
        telegram.send_message(
            store.chat_id,
            f"✅ <b>AniVox Monitor {VERSION} запущен.</b>\n"
            "• Параллельный движок опроса (ThreadPool)\n"
            "• Мультимедиа мост-чат (ГС, кружки, фото, стикеры)\n"
            "• Детектор сессий и Live-обновление дашборда",
            reply_markup=TELEGRAM_KEYBOARD,
        )
    except TelegramError as exc:
        logger.warning("Стартовое сообщение не отправилось: %s", exc)

    threading.Thread(
        target=run_web_server,
        args=(store,),
        daemon=True,
    ).start()

    threading.Thread(
        target=monitor.run_autoping,
        name="anivox-autoping",
        daemon=True,
    ).start()

    scheduler = threading.Thread(
        target=monitor.run_scheduler,
        name="anivox-scheduler",
        daemon=True,
    )
    scheduler.start()
    try:
        monitor.run()
    finally:
        monitor.stop()
        scheduler.join(timeout=5)
        store.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
