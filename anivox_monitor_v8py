#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AniVox Monitor v7.2.0 — Full Integrated Edition
Использует родной файл состояния: anivox_monitor.json
Генерирует адаптивный веб-отчет: anivox_report.html
"""

import os
import re
import sys
import ssl
import csv
import json
import time
import base64
import socket
import logging
import argparse
import threading
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime
from dataclasses import dataclass, field, asdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List, Optional, Any

# ==================== КОНСТАНТЫ И ПУТИ ====================
APP_NAME = "AniVox Monitor"
VERSION = "8.0.0"
CONFIG_PATH = os.environ.get("ANIVOX_STATE_FILE", "anivox_monitor.json")
REPORT_PATH = os.environ.get("ANIVOX_REPORT_FILE", "anivox_report.html")
LOG_PATH = os.environ.get("ANIVOX_LOG_FILE", "anivox_monitor.log")
KODIK_TOKEN = os.environ.get("KODIK_TOKEN", "")
PORT = int(os.environ.get("PORT", 8080))

DEFAULT_INTERVAL = 5  # сек
PROFILE_RE = re.compile(r"(?:anivox\.(?:fun|ru|org)/(?:profile|users?)/|id=|^)(\d+)", re.IGNORECASE)
ANIME_RE = re.compile(r"(\d+)")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; SM-A515F) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(APP_NAME)

# ==================== КЛАВИАТУРЫ TELEGRAM ====================
TELEGRAM_KEYBOARD = {
    "keyboard": [
        [{"text": "📊 Проверить всех"}, {"text": "🌐 Открыть сайт-отчет"}],
        [{"text": "👥 Мои профили"}, {"text": "➕ Добавить"}, {"text": "🗑 Удалить"}],
        [{"text": "🎬 Ждать серию"}, {"text": "📺 Мои ожидания"}, {"text": "🔕 Удалить ожидание"}],
        [{"text": "🧱 Написать на стену"}, {"text": "🎯 Поймать онлайн"}, {"text": "🏆 Топ"}],
        [{"text": "📈 История"}, {"text": "🎭 Друзья"}],
        [{"text": "⚙️ Настройки"}, {"text": "🔕 Уведомления"}],
        [{"text": "💬 Связать всех в чат"}, {"text": "🚪 Завершить чат"}],
        [{"text": "⏸ Пауза"}, {"text": "▶️ Продолжить"}]
    ],
    "resize_keyboard": True,
    "is_persistent": True
}

CANCEL_KEYBOARD = {
    "keyboard": [[{"text": "❌ Отмена"}]],
    "resize_keyboard": True,
    "one_time_keyboard": True
}

CHAT_KEYBOARD = {
    "keyboard": [[{"text": "🚪 Завершить чат"}]],
    "resize_keyboard": True
}


# ==================== DATACLASS-МОДЕЛИ ====================
@dataclass
class Profile:
    url: str
    profile_id: str
    label: str = ""
    avatar: str = ""
    last_signature: str = ""
    last_check: str = ""
    last_ok: bool = True
    last_error: str = ""
    last_status: Dict[str, Any] = field(default_factory=dict)
    last_status_change_ts: int = 0
    prev_status_text: str = "Нет данных"
    online_seconds: int = 0
    watching_seconds: int = 0
    titles_count: int = 0


@dataclass
class FetchResult:
    ok: bool
    profile_id: str
    url: str
    nickname: str = ""
    avatar: str = ""
    level: str = ""
    presence: str = "Оффлайн"  # "Онлайн" | "Оффлайн" | "Смотрит"
    title: str = ""
    error: str = ""
    source: str = "http"
    last_seen_timestamp: int = 0
    checked_at: str = ""
    status_changed_at: str = ""

    def signature(self) -> str:
        return f"{self.presence}|{self.title}|{self.level}"


@dataclass
class Settings:
    interval_seconds: int = 5
    interval_minutes: float = 0.083
    auto_cleanup_minutes: int = 0
    auto_cleanup_days: int = 7
    notify_on_change: bool = True
    online: bool = True
    offline: bool = True
    title: bool = True
    errors: bool = False
    edit_mode: bool = False
    silent_until: float = 0.0
    silent_no_sound: bool = False
    request_timeout: int = 12
    min_request_gap_seconds: float = 0.5


# ==================== ХРАНИЛИЩЕ ANIVOX_MONITOR.JSON ====================
class Store:
    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self.lock = threading.RLock()
        self.token: str = os.environ.get("BOT_TOKEN", "")
        self.chat_id: str = os.environ.get("CHAT_ID", "")
        self.autoping_url: str = os.environ.get("RENDER_EXTERNAL_URL", "")
        self.allowed_users: Dict[str, str] = {}
        self.anime_cache: Dict[str, Any] = {}
        self.anime_subs: List[Dict[str, Any]] = []
        self.wall_messages: List[Dict[str, Any]] = []
        self.online_snipers: Dict[str, List[str]] = {}
        self.settings: Settings = Settings()
        self.user_settings: Dict[str, Dict[str, Any]] = {}
        self.profiles: Dict[str, Profile] = {}
        self.history: List[Dict[str, Any]] = []
        self.load()

    def load(self):
        with self.lock:
            if not os.path.exists(self.path):
                logger.info(f"Файл {self.path} еще не создан, будет инициализирован при сохранении.")
                return
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as e:
                logger.error(f"Ошибка чтения {self.path}: {e}")
                return

            self.token = raw.get("token") or self.token
            self.chat_id = str(raw.get("chat_id") or self.chat_id or "")
            self.autoping_url = raw.get("autoping_url") or self.autoping_url

            # Загрузка друзей / разрешенных пользователей
            raw_allowed = raw.get("allowed_users", {})
            if isinstance(raw_allowed, list):
                self.allowed_users = {str(x): f"Друг {x}" for x in raw_allowed}
            elif isinstance(raw_allowed, dict):
                self.allowed_users = {str(k): str(v) for k, v in raw_allowed.items()}

            self.anime_cache = raw.get("anime_cache", {})
            self.anime_subs = raw.get("anime_subs", [])
            self.wall_messages = raw.get("wall_messages", [])
            self.online_snipers = raw.get("online_snipers", {})
            self.user_settings = raw.get("user_settings") or raw.get("users") or {}

            # Настройки
            s_raw = raw.get("settings", {})
            if isinstance(s_raw, dict):
                for k, v in s_raw.items():
                    if hasattr(self.settings, k):
                        setattr(self.settings, k, v)
                if "interval_seconds" not in s_raw and "interval_minutes" in s_raw:
                    self.settings.interval_seconds = max(1, int(float(s_raw["interval_minutes"]) * 60))

            # Профили
            self.profiles = {}
            p_raw = raw.get("profiles", {})
            if isinstance(p_raw, dict):
                items = p_raw.items()
            elif isinstance(p_raw, list):
                items = [(str(x.get("profile_id", i)), x) for i, x in enumerate(p_raw) if isinstance(x, dict)]
            else:
                items = []

            for pid, pdata in items:
                if not isinstance(pdata, dict):
                    continue
                pid_str = str(pdata.get("profile_id") or pid)
                # Старые версии могли сохранять last_status обычной строкой.
                # Нормализуем значение, чтобы .get() вызывался только у словаря.
                last_st_raw = pdata.get("last_status")
                last_st = last_st_raw if isinstance(last_st_raw, dict) else {}
                legacy_status_text = (
                    str(last_st_raw).strip()
                    if isinstance(last_st_raw, (str, int, float)) else ""
                )
                prev_status_text = pdata.get("prev_status_text")
                if not isinstance(prev_status_text, str) or not prev_status_text.strip():
                    prev_status_text = legacy_status_text or "Нет данных"
                self.profiles[pid_str] = Profile(
                    url=pdata.get("url") or f"https://anivox.fun/profile/{pid_str}",
                    profile_id=pid_str,
                    label=pdata.get("label") or last_st.get("nickname") or f"ID {pid_str}",
                    avatar=pdata.get("avatar") or last_st.get("avatar") or "",
                    last_signature=pdata.get("last_signature", ""),
                    last_check=pdata.get("last_check", ""),
                    last_ok=bool(pdata.get("last_ok", True)),
                    last_error=pdata.get("last_error", ""),
                    last_status=last_st,
                    last_status_change_ts=int(pdata.get("last_status_change_ts") or last_st.get("last_seen_timestamp") or time.time()),
                    prev_status_text=prev_status_text,
                    online_seconds=int(pdata.get("online_seconds", 0)),
                    watching_seconds=int(pdata.get("watching_seconds", 0)),
                    titles_count=int(pdata.get("titles_count", 0))
                )

            self.history = raw.get("history", [])

    def save(self):
        with self.lock:
            data = {
                "token": self.token,
                "chat_id": self.chat_id,
                "autoping_url": self.autoping_url,
                "allowed_users": self.allowed_users,
                "anime_cache": self.anime_cache,
                "anime_subs": self.anime_subs,
                "wall_messages": self.wall_messages[-100:],
                "online_snipers": self.online_snipers,
                "settings": asdict(self.settings),
                "user_settings": self.user_settings,
                "profiles": {pid: asdict(p) for pid, p in self.profiles.items()},
                "history": self.history[-1000:]
            }
            tmp_path = self.path + ".tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self.path)
            except Exception as e:
                logger.error(f"Ошибка сохранения {self.path}: {e}")

    def get_user_cfg(self, uid: Any) -> Dict[str, Any]:
        uid_s = str(uid)
        with self.lock:
            u = self.user_settings.setdefault(uid_s, {})
            u.setdefault("interval", self.settings.interval_seconds)
            u.setdefault("cleanup_min", self.settings.auto_cleanup_minutes)
            u.setdefault("silent_until", self.settings.silent_until)
            u.setdefault("silent_no_sound", self.settings.silent_no_sound)
            ntf = u.setdefault("notify", {})
            ntf.setdefault("onl", self.settings.online)
            ntf.setdefault("off", self.settings.offline)
            ntf.setdefault("ttl", self.settings.title)
            ntf.setdefault("err", self.settings.errors)
            ntf.setdefault("all", self.settings.notify_on_change)
            ntf.setdefault("edit_mode", self.settings.edit_mode)
            u.setdefault("live_msg_id", None)
            u.setdefault("msg_queue", [])
            return u

    def add_history(self, item: Dict[str, Any]):
        with self.lock:
            self.history.append(item)
            if len(self.history) > 1000:
                self.history = self.history[-1000:]
            self.save()


# ==================== КЛИЕНТ TELEGRAM BOT API ====================
class TelegramClient:
    def __init__(self, token: str):
        self.token = token

    def request(self, method: str, payload: Optional[Dict[str, Any]] = None, timeout: int = 20) -> Dict[str, Any]:
        if not self.token:
            return {"ok": False, "description": "Empty bot token"}
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            if payload is not None:
                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url, data=body,
                    headers={"Content-Type": "application/json", "User-Agent": DEFAULT_USER_AGENT}
                )
            else:
                req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_text = e.read().decode("utf-8", errors="ignore")
            if "message is not modified" in err_text:
                return {"ok": True, "not_modified": True}
            logger.warning(f"TG API HTTP {e.code} ({method}): {err_text}")
            return {"ok": False, "error_code": e.code, "description": err_text}
        except Exception as e:
            logger.warning(f"TG API Network Error ({method}): {e}")
            return {"ok": False, "description": str(e)}

    def get_me(self) -> Dict[str, Any]:
        return self.request("getMe")

    def send_message(
        self, chat_id: Any, text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "HTML",
        disable_notification: bool = False
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
            "disable_notification": disable_notification
        }
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        return self.request("sendMessage", data)

    def edit_message_text(
        self, chat_id: Any, message_id: int, text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "HTML"
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        return self.request("editMessageText", data)

    def delete_message(self, chat_id: Any, message_id: int) -> Dict[str, Any]:
        return self.request("deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)})

    def answer_callback_query(self, callback_query_id: str, text: str = "", show_alert: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text:
            payload["text"] = text[:190]
        return self.request("answerCallbackQuery", payload, timeout=10)

    def send_document(self, chat_id: Any, filepath: str, caption: str = "") -> Dict[str, Any]:
        if not os.path.exists(filepath):
            return {"ok": False, "description": "File not found"}
        boundary = f"----AniVoxBoundary{int(time.time() * 1000)}"
        url = f"https://api.telegram.org/bot{self.token}/sendDocument"
        try:
            with open(filepath, "rb") as f:
                file_bytes = f.read()
            filename = os.path.basename(filepath)
            parts = [
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode("utf-8")
            ]
            if caption:
                parts.append(
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode("utf-8")
                )
                parts.append(
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML\r\n".encode("utf-8")
                )
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{filename}\"\r\n"
                f"Content-Type: text/html\r\n\r\n".encode("utf-8")
            )
            parts.append(file_bytes)
            parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
            body = b"".join(parts)
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": DEFAULT_USER_AGENT
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.error(f"Ошибка отправки документа: {e}")
            return {"ok": False, "description": str(e)}

    def get_updates(self, offset: int = 0, timeout: int = 25) -> Dict[str, Any]:
        return self.request("getUpdates", {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"]
        }, timeout=timeout + 10)


# ==================== СКРАПЕР ANIVOX + SHIKIMORI + KODIK ====================
def make_svg_avatar(name: str) -> str:
    initials = (name or "AV").strip()[:2].upper()
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f'<defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">'
        f'<stop offset="0%" stop-color="#6366f1"/><stop offset="100%" stop-color="#a855f7"/>'
        f'</linearGradient></defs>'
        f'<rect width="100" height="100" rx="50" fill="url(#g)"/>'
        f'<text x="50" y="54" font-family="Arial,sans-serif" font-size="38" font-weight="bold" '
        f'fill="#ffffff" text-anchor="middle" dominant-baseline="middle">{initials}</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def anivox_websocket_profile(profile_id: str, timeout: int = 6) -> Optional[Dict[str, Any]]:
    endpoints = [
        ("anivox.fun", 443, f"/ws/profile/{profile_id}"),
        ("anivox.fun", 443, f"/api/ws/profile/{profile_id}")
    ]
    for host, port, path in endpoints:
        try:
            ctx = ssl.create_default_context()
            raw_sock = socket.create_connection((host, port), timeout=timeout)
            ssock = ctx.wrap_socket(raw_sock, server_hostname=host)
            ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
            req_str = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {ws_key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"User-Agent: {DEFAULT_USER_AGENT}\r\n\r\n"
            )
            ssock.sendall(req_str.encode("utf-8"))
            header = ssock.recv(4096).decode("utf-8", errors="ignore")
            if "101" not in header:
                ssock.close()
                continue
            frame = ssock.recv(8192)
            ssock.close()
            if len(frame) > 2:
                plen = frame[1] & 127
                offset = 2 + (2 if plen == 126 else (8 if plen == 127 else 0))
                text = frame[offset:].decode("utf-8", errors="ignore")
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
        except Exception:
            continue
    return None


def fetch_profile(profile: Profile, timeout: int = 12) -> FetchResult:
    pid = str(profile.profile_id).strip()
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    now_ts = int(time.time())

    # 1. Пробуем WebSocket
    ws = anivox_websocket_profile(pid, timeout=min(6, timeout))
    if ws and any(k in ws for k in ("nickname", "username", "online", "is_online", "watching")):
        nick = str(ws.get("nickname") or ws.get("username") or ws.get("name") or profile.label or f"ID {pid}")
        ava = str(ws.get("avatar") or ws.get("avatar_url") or ws.get("photo") or "")
        if ava.startswith("/"):
            ava = "https://anivox.fun" + ava
        title = ws.get("watching") or ws.get("current_title") or ws.get("anime") or ""
        if isinstance(title, dict):
            title = title.get("title") or title.get("name_ru") or title.get("name") or ""
        title = str(title).strip()
        is_onl = bool(ws.get("online") or ws.get("is_online") or title)
        presence = "Смотрит" if title else ("Онлайн" if is_onl else "Оффлайн")
        lvl = str(ws.get("level") or ws.get("rank") or "")
        return FetchResult(
            ok=True, profile_id=pid, url=profile.url, nickname=nick,
            avatar=ava or profile.avatar or make_svg_avatar(nick),
            level=lvl, presence=presence, title=title, source="websocket",
            last_seen_timestamp=now_ts, checked_at=now_str
        )

    # 2. Пробуем HTTP JSON API и HTML страницу
    urls = [
        f"https://anivox.fun/api/profile/{pid}",
        f"https://anivox.fun/api/users/{pid}",
        profile.url or f"https://anivox.fun/profile/{pid}"
    ]
    ctx = ssl.create_default_context()

    for u in urls:
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "application/json, text/html;q=0.9,*/*;q=0.8"
            })
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")

            if raw.lstrip().startswith("{"):
                data = json.loads(raw)
                obj = data.get("user") or data.get("profile") or data.get("data") or data
                if isinstance(obj, dict):
                    nick = str(obj.get("nickname") or obj.get("username") or obj.get("name") or profile.label or f"ID {pid}")
                    ava = str(obj.get("avatar") or obj.get("avatar_url") or obj.get("image") or "")
                    if ava.startswith("/"):
                        ava = "https://anivox.fun" + ava
                    title_raw = obj.get("watching_title") or obj.get("watching") or obj.get("current_title") or ""
                    if isinstance(title_raw, dict):
                        title_raw = title_raw.get("title") or title_raw.get("name_ru") or title_raw.get("name") or ""
                    title = str(title_raw).strip()
                    is_onl = bool(obj.get("online") or obj.get("is_online") or obj.get("status") == "online" or title)
                    presence = "Смотрит" if title else ("Онлайн" if is_onl else "Оффлайн")
                    lvl = str(obj.get("level") or obj.get("rank") or "")
                    return FetchResult(
                        ok=True, profile_id=pid, url=profile.url, nickname=nick,
                        avatar=ava or profile.avatar or make_svg_avatar(nick),
                        level=lvl, presence=presence, title=title, source="api",
                        last_seen_timestamp=now_ts, checked_at=now_str
                    )

            # HTML парсинг
            nick = profile.label or f"ID {pid}"
            m_nick = re.search(r'<h1[^>]*>(.*?)</h1>|<title>(.*?)</title>', raw, re.I | re.S)
            if m_nick:
                cand = re.sub(r"<[^>]+>", "", (m_nick.group(1) or m_nick.group(2) or "")).replace("— AniVox", "").replace("| AniVox", "").strip()
                if cand:
                    nick = cand[:35]

            m_ava = re.search(r'<img[^>]+class="[^"]*avatar[^"]*"[^>]+src="([^"]+)"', raw, re.I)
            if not m_ava:
                m_ava = re.search(r'property="og:image"\s+content="([^"]+)"', raw, re.I)
            ava = m_ava.group(1).strip() if m_ava else ""
            if ava.startswith("/"):
                ava = "https://anivox.fun" + ava

            m_watch = re.search(r'(?:Смотрит сейчас|Сейчас смотрит|watching)[^<]*<[^>]+>([^<]+)<', raw, re.I)
            title = m_watch.group(1).strip() if m_watch else ""

            has_onl = bool(title or re.search(r'(?:в сети|online|status-online|is-online)', raw, re.I))
            has_off = bool(re.search(r'(?:не в сети|оффлайн|offline)', raw, re.I))
            is_onl = has_onl and not has_off
            presence = "Смотрит" if title else ("Онлайн" if is_onl else "Оффлайн")

            return FetchResult(
                ok=True, profile_id=pid, url=profile.url, nickname=nick,
                avatar=ava or profile.avatar or make_svg_avatar(nick),
                level="", presence=presence, title=title, source="html",
                last_seen_timestamp=now_ts, checked_at=now_str
            )
        except Exception as e:
            last_err = str(e)
            continue

    return FetchResult(
        ok=False, profile_id=pid, url=profile.url,
        nickname=profile.label or f"ID {pid}",
        avatar=profile.avatar or make_svg_avatar(profile.label or pid),
        error="Не удалось получить ответ от AniVox", checked_at=now_str
    )


def fetch_anime_info(query_or_id: str) -> List[Dict[str, Any]]:
    """Поиск аниме и жанров через Shikimori API"""
    q = str(query_or_id).strip()
    if q.isdigit():
        url = f"https://shikimori.one/api/animes/{q}"
    else:
        url = f"https://shikimori.one/api/animes?search={urllib.parse.quote(q)}&limit=5"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])
    except Exception as e:
        logger.warning(f"Shikimori error: {e}")
        return []


def find_anime_voices(shikimori_id: str, title: str = "") -> List[Dict[str, Any]]:
    """Поиск доступных озвучек и серий через Kodik API"""
    voices: Dict[str, Dict[str, Any]] = {}
    params = {"token": KODIK_TOKEN, "with_episodes": "true"}
    if str(shikimori_id).isdigit():
        params["shikimori_id"] = str(shikimori_id)
        url = "https://kodikapi.com/search?" + urllib.parse.urlencode(params)
    else:
        params["title"] = title or str(shikimori_id)
        url = "https://kodikapi.com/search?" + urllib.parse.urlencode(params)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for item in data.get("results", []):
            tr = item.get("translation", {})
            v_id = str(tr.get("id") or "0")
            v_title = tr.get("title") or "Оригинал / Субтитры"
            last_ep = int(item.get("last_episode") or item.get("episodes_count") or 1)
            if v_id not in voices or last_ep > voices[v_id]["last_episode"]:
                voices[v_id] = {
                    "voice_id": v_id,
                    "voice_name": v_title,
                    "last_episode": last_ep,
                    "title": item.get("title") or title
                }
    except Exception as e:
        logger.warning(f"Kodik API error: {e}")

    return list(voices.values())


# ==================== ОСНОВНОЙ КЛАСС MONITOR ====================
class Monitor:
    def __init__(self, store: Store):
        self.store = store
        self.tg = TelegramClient(store.token)
        self.running = True
        self.paused = False
        self.user_states: Dict[str, Dict[str, Any]] = {}
        self.bridge_active: bool = False
        self.bridge_participants: List[str] = []

    def is_authorized(self, uid: Any) -> bool:
        uid_s = str(uid)
        if not self.store.chat_id:
            self.store.chat_id = uid_s
            self.store.save()
            return True
        return uid_s == str(self.store.chat_id) or uid_s in self.store.allowed_users

    def all_recipients(self) -> List[str]:
        res = []
        if self.store.chat_id:
            res.append(str(self.store.chat_id))
        for k in self.store.allowed_users.keys():
            if str(k) not in res:
                res.append(str(k))
        return res

    def allowed_notification(self, uid: Any, kind: str) -> bool:
        ucfg = self.store.get_user_cfg(uid)
        if time.time() < float(ucfg.get("silent_until", 0)):
            return False
        ntf = ucfg.get("notify", {})
        if not ntf.get("all", True):
            return False
        return bool(ntf.get(kind, True))

    def deliver_notification(self, uid: Any, text: str, reply_markup: Optional[Dict[str, Any]] = None):
        ucfg = self.store.get_user_cfg(uid)
        no_sound = bool(ucfg.get("silent_no_sound", False))
        edit_mode = bool(ucfg.get("notify", {}).get("edit_mode", False))
        live_mid = ucfg.get("live_msg_id")

        if edit_mode and live_mid:
            r = self.tg.edit_message_text(uid, int(live_mid), text, reply_markup=reply_markup)
            if r.get("ok"):
                return

        r = self.tg.send_message(uid, text, reply_markup=reply_markup, disable_notification=no_sound)
        if r.get("ok") and "result" in r:
            mid = r["result"]["message_id"]
            ucfg["live_msg_id"] = mid
            if int(ucfg.get("cleanup_min", 0)) > 0:
                ucfg.setdefault("msg_queue", []).append({"mid": mid, "ts": time.time()})
            self.store.save()

    def enforce_cleanup(self):
        now = time.time()
        changed = False
        for uid_s, ucfg in list(self.store.user_settings.items()):
            cln_min = int(ucfg.get("cleanup_min", 0))
            if cln_min <= 0:
                continue
            q = ucfg.get("msg_queue", [])
            keep = []
            for item in q:
                if now - float(item.get("ts", now)) >= cln_min * 60:
                    self.tg.delete_message(uid_s, int(item["mid"]))
                    changed = True
                else:
                    keep.append(item)
            ucfg["msg_queue"] = keep
        if changed:
            self.store.save()

    # -------------------- ПРОВЕРКА ПРОФИЛЕЙ --------------------
    def check_one(self, profile: Profile, notify: bool = True) -> FetchResult:
        res = fetch_profile(profile, timeout=self.store.settings.request_timeout)
        now_ts = int(time.time())
        now_dt = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        gap = max(1, int(self.store.settings.interval_seconds))

        with self.store.lock:
            old_sig = profile.last_signature
            old_pres = profile.last_status.get("presence", "Оффлайн")
            old_title = profile.last_status.get("title", "")

            if res.ok:
                profile.label = res.nickname or profile.label
                profile.avatar = res.avatar or profile.avatar
                profile.last_ok = True
                profile.last_error = ""
                profile.last_check = now_dt

                if res.presence in ("Онлайн", "Смотрит"):
                    profile.online_seconds += gap
                if res.presence == "Смотрит" or res.title:
                    profile.watching_seconds += gap

                new_sig = res.signature()
                changed = (old_sig != "" and old_sig != new_sig) or (profile.last_status_change_ts == 0)

                if changed:
                    if old_sig != "":
                        profile.prev_status_text = f"{old_pres}" + (f" ({old_title})" if old_title else "")
                    profile.last_status_change_ts = now_ts
                    if res.title and res.title != old_title:
                        profile.titles_count += 1

                res.status_changed_at = datetime.fromtimestamp(profile.last_status_change_ts).strftime("%d.%m.%Y %H:%M:%S")
                profile.last_signature = new_sig
                profile.last_status = asdict(res)

                if changed and old_sig != "":
                    changes_list = []
                    if old_pres != res.presence:
                        changes_list.append(f"Статус: {old_pres} → {res.presence}")
                    if old_title != res.title:
                        changes_list.append(f"Тайтл: {old_title or 'нет'} → {res.title or 'нет'}")

                    self.store.add_history({
                        "checked_at": now_dt,
                        "timestamp": now_ts,
                        "ok": True,
                        "profile_id": profile.profile_id,
                        "nickname": profile.label,
                        "avatar": profile.avatar,
                        "level": res.level,
                        "presence": res.presence,
                        "title": res.title,
                        "signature": new_sig,
                        "changes": ", ".join(changes_list) or res.presence,
                        "error": ""
                    })

                    # Снайпер онлайна
                    if res.presence in ("Онлайн", "Смотрит"):
                        waiters = self.store.online_snipers.pop(profile.profile_id, [])
                        for w_uid in waiters:
                            self.tg.send_message(
                                w_uid,
                                f"🎯 <b>СНАЙПЕР ОНЛАЙНА!</b>\n\n"
                                f"👤 <b>{profile.label}</b> (<code>#{profile.profile_id}</code>) только что появился в сети!\n"
                                f"📡 Статус: <b>{res.presence}</b>\n"
                                f"🎬 Тайтл: <i>{res.title or 'Не смотрит'}</i>\n"
                                f"🕒 Время: <code>{now_dt}</code>"
                            )

                    if notify:
                        kind = "ttl" if (res.title and res.title != old_title) else (
                               "onl" if res.presence in ("Онлайн", "Смотрит") else "off")
                        badge = "🟣 СМОТРИТ АНИМЕ" if res.presence == "Смотрит" else (
                                "🟢 В СЕТИ" if res.presence == "Онлайн" else "⚪ ОФФЛАЙН")
                        msg_text = (
                            f"🔔 <b>Изменение статуса AniVox</b>\n\n"
                            f"👤 Профиль: <b>{profile.label}</b> (<code>#{profile.profile_id}</code>)\n"
                            f"📡 Статус: <b>{badge}</b>\n"
                            f"🎬 Смотрит: <i>{res.title or 'Ничего не смотрит'}</i>\n"
                            f"⏮ До этого: <code>{profile.prev_status_text}</code>\n"
                            f"🕒 Время смены: <code>{now_dt}</code>"
                        )
                        for r_uid in self.all_recipients():
                            if self.allowed_notification(r_uid, kind):
                                self.deliver_notification(r_uid, msg_text)
            else:
                profile.last_ok = False
                profile.last_error = res.error
                profile.last_check = now_dt

            self.store.save()
        return res

    def check_all(self, notify: bool = True) -> List[FetchResult]:
        results = []
        for pid, prof in list(self.store.profiles.items()):
            results.append(self.check_one(prof, notify=notify))
            time.sleep(self.store.settings.min_request_gap_seconds)
        self.generate_html_report()
        return results

    def check_anime_updates(self):
        if not self.store.anime_subs:
            return
        changed = False
        for sub in list(self.store.anime_subs):
            shiki_id = str(sub.get("shikimori_id", ""))
            voice_id = str(sub.get("voice_id", ""))
            last_ep = int(sub.get("last_episode", 0))
            title = sub.get("title", "Аниме")
            voices = find_anime_voices(shiki_id, title)
            for v in voices:
                if (not voice_id or v["voice_id"] == voice_id) and v["last_episode"] > last_ep:
                    sub["last_episode"] = v["last_episode"]
                    changed = True
                    txt = (
                        f"🍿 <b>Вышла новая серия!</b>\n\n"
                        f"🎬 Аниме: <b>{title}</b>\n"
                        f"🎙 Озвучка: <b>{v['voice_name']}</b>\n"
                        f"🔢 Серия: <b>{v['last_episode']}</b>"
                    )
                    for r_uid in self.all_recipients():
                        self.tg.send_message(r_uid, txt)
        if changed:
            self.store.save()

    # -------------------- ИНЛАЙН-МЕНЮ НАСТРОЕК, ТИШИНЫ, ДРУЗЕЙ --------------------
    def _build_settings_markup(self, chat_id: Any) -> Dict[str, Any]:
        ucfg = self.store.get_user_cfg(chat_id)
        cur_int = int(ucfg.get("interval", self.store.settings.interval_seconds))
        cur_cln = int(ucfg.get("cleanup_min", self.store.settings.auto_cleanup_minutes))
        return {
            "inline_keyboard": [
                [
                    {"text": f"{'🟢 ' if cur_int == 1 else ''}⏱ 1 сек", "callback_data": "set_int_1"},
                    {"text": f"{'🟢 ' if cur_int == 5 else ''}⏱ 5 сек", "callback_data": "set_int_5"},
                    {"text": f"{'🟢 ' if cur_int == 10 else ''}⏱ 10 сек", "callback_data": "set_int_10"},
                    {"text": f"{'🟢 ' if cur_int == 30 else ''}⏱ 30 сек", "callback_data": "set_int_30"}
                ],
                [
                    {"text": f"{'🧹 ' if cur_cln == 5 else ''}Очистка 5м", "callback_data": "set_cln_5"},
                    {"text": f"{'🧹 ' if cur_cln == 15 else ''}15м", "callback_data": "set_cln_15"},
                    {"text": f"{'🧹 ' if cur_cln == 30 else ''}30м", "callback_data": "set_cln_30"},
                    {"text": f"{'♾️ ' if cur_cln == 0 else ''}Выкл", "callback_data": "set_cln_0"}
                ],
                [{"text": "🔕 Перейти к Уведомлениям / Тишине", "callback_data": "open_ntf_menu"}]
            ]
        }

    def _build_settings_text(self, chat_id: Any) -> str:
        ucfg = self.store.get_user_cfg(chat_id)
        cur_int = int(ucfg.get("interval", self.store.settings.interval_seconds))
        cur_cln = int(ucfg.get("cleanup_min", self.store.settings.auto_cleanup_minutes))
        return (
            "⚙️ <b>Настройки мониторинга AniVox</b>\n\n"
            f"• Интервал проверки профилей: <b>{cur_int} сек.</b>\n"
            f"• Автоочистка сообщений бота: <b>{'Выключена' if cur_cln == 0 else str(cur_cln) + ' мин.'}</b>\n\n"
            "Нажми на нужную кнопку ниже для мгновенного переключения:"
        )

    def send_settings_menu(self, chat_id: Any, message_id: Optional[int] = None):
        txt = self._build_settings_text(chat_id)
        markup = self._build_settings_markup(chat_id)
        if message_id:
            self.tg.edit_message_text(chat_id, message_id, txt, reply_markup=markup)
        else:
            self.tg.send_message(chat_id, txt, reply_markup=markup)

    def _build_notifications_markup(self, chat_id: Any) -> Dict[str, Any]:
        ucfg = self.store.get_user_cfg(chat_id)
        ntf = ucfg["notify"]
        now = time.time()
        silent_until = float(ucfg.get("silent_until", 0))
        is_silent = silent_until > now
        no_sound = bool(ucfg.get("silent_no_sound", False))

        return {
            "inline_keyboard": [
                [
                    {"text": f"{'✅' if ntf.get('onl', True) else '❌'} Вход в сеть", "callback_data": "ntf_onl"},
                    {"text": f"{'✅' if ntf.get('off', True) else '❌'} Выход (Оффлайн)", "callback_data": "ntf_off"}
                ],
                [
                    {"text": f"{'✅' if ntf.get('ttl', True) else '❌'} Смена тайтла", "callback_data": "ntf_ttl"},
                    {"text": f"{'✅' if ntf.get('err', False) else '❌'} Ошибки сети", "callback_data": "ntf_err"}
                ],
                [
                    {"text": f"📝 Режим: {'Live-Редакт.' if ntf.get('edit_mode') else 'Новые сообщ.'}", "callback_data": "ntf_edit_mode"},
                    {"text": f"{'🟢 Все ВКЛ' if ntf.get('all', True) else '🔴 Все ВЫКЛ'}", "callback_data": "ntf_all"}
                ],
                [
                    {"text": "🔇 Тишина 30м", "callback_data": "silent_30"},
                    {"text": "🔇 1 час", "callback_data": "silent_60"},
                    {"text": "🌙 8 часов", "callback_data": "silent_480"}
                ],
                [
                    {"text": f"{'🔕 Без звука: ДА' if no_sound else '🔊 со звуком'}", "callback_data": "silent_sound"},
                    {"text": "🔔 Снять тишину" if is_silent else "✅ Тишина выкл", "callback_data": "silent_0"}
                ],
                [{"text": "⚙️ Назад к интервалам", "callback_data": "open_set_menu"}]
            ]
        }

    def _build_notifications_text(self, chat_id: Any) -> str:
        ucfg = self.store.get_user_cfg(chat_id)
        now = time.time()
        silent_until = float(ucfg.get("silent_until", 0))
        if silent_until > now:
            rem_min = int((silent_until - now) / 60) + 1
            until_str = datetime.fromtimestamp(silent_until).strftime("%H:%M:%S")
            silent_status = f"🔇 <b>АКТИВНА до {until_str} (еще ~{rem_min} мин.)</b>"
        else:
            silent_status = "🔔 <b>ВЫКЛЮЧЕНА (уведомления приходят)</b>"

        return (
            "🔕 <b>Управление уведомлениями и режимом «Тишина»</b>\n\n"
            f"• Режим тишины: {silent_status}\n"
            f"• Звук в Telegram: <b>{'Отключен (тихие сообщения)' if ucfg.get('silent_no_sound') else 'Включен'}</b>\n\n"
            "Выбери, какие события присылать, или включи временную тишину:"
        )

    def send_notifications_menu(self, chat_id: Any, message_id: Optional[int] = None):
        txt = self._build_notifications_text(chat_id)
        markup = self._build_notifications_markup(chat_id)
        if message_id:
            self.tg.edit_message_text(chat_id, message_id, txt, reply_markup=markup)
        else:
            self.tg.send_message(chat_id, txt, reply_markup=markup)

    def send_delete_menu(self, chat_id: Any, message_id: Optional[int] = None):
        rows = []
        for pid, p in self.store.profiles.items():
            rows.append([{"text": f"🗑 Удалить {p.label} (#{pid})", "callback_data": f"rm_prof_{pid}"}])
        if not rows:
            txt = "Список профилей пуст."
            markup = None
        else:
            txt = "🗑 <b>Выбери профиль для удаления из мониторинга:</b>"
            markup = {"inline_keyboard": rows}
        if message_id:
            self.tg.edit_message_text(chat_id, message_id, txt, reply_markup=markup)
        else:
            self.tg.send_message(chat_id, txt, reply_markup=markup)

    def send_friends_menu(self, chat_id: Any, message_id: Optional[int] = None):
        rows = [[{"text": "➕ Добавить друга (по TG ID)", "callback_data": "fr_add"}]]
        for fid, fname in self.store.allowed_users.items():
            rows.append([{"text": f"🎭 {fname} ({fid}) — ❌ Удалить", "callback_data": f"rm_fr_{fid}"}])
        txt = (
            "🎭 <b>Управление друзьями (совместный доступ к боту и мост-чату)</b>\n"
            f"Всего друзей: <b>{len(self.store.allowed_users)}</b>"
        )
        markup = {"inline_keyboard": rows}
        if message_id:
            self.tg.edit_message_text(chat_id, message_id, txt, reply_markup=markup)
        else:
            self.tg.send_message(chat_id, txt, reply_markup=markup)

    # -------------------- ОБРАБОТКА CALLBACK-КНОПОК --------------------
    def _handle_callback(self, cb: Dict[str, Any]):
        cb_id = cb.get("id", "")
        data = cb.get("data", "")
        msg = cb.get("message") or {}
        chat_id = msg.get("chat", {}).get("id")
        msg_id = msg.get("message_id")
        from_id = cb.get("from", {}).get("id", chat_id)

        if not self.is_authorized(from_id):
            self.tg.answer_callback_query(cb_id, "⛔ Нет доступа к этому боту.", show_alert=True)
            return

        ucfg = self.store.get_user_cfg(chat_id)
        ntf = ucfg["notify"]

        if data == "ignore":
            self.tg.answer_callback_query(cb_id)
            return

        if data == "open_ntf_menu":
            self.tg.answer_callback_query(cb_id)
            self.send_notifications_menu(chat_id, message_id=msg_id)
            return

        if data == "open_set_menu":
            self.tg.answer_callback_query(cb_id)
            self.send_settings_menu(chat_id, message_id=msg_id)
            return

        if data.startswith("set_int_"):
            sec = int(data.replace("set_int_", ""))
            ucfg["interval"] = sec
            self.store.settings.interval_seconds = sec
            self.store.settings.interval_minutes = round(sec / 60.0, 4)
            self.store.save()
            self.tg.answer_callback_query(cb_id, f"⏱ Интервал проверки: {sec} сек.")
            self.send_settings_menu(chat_id, message_id=msg_id)
            return

        if data.startswith("set_cln_"):
            mins = int(data.replace("set_cln_", ""))
            ucfg["cleanup_min"] = mins
            self.store.settings.auto_cleanup_minutes = mins
            self.store.save()
            label = "Автоочистка выключена" if mins == 0 else f"🧹 Автоочистка: {mins} мин."
            self.tg.answer_callback_query(cb_id, label)
            self.send_settings_menu(chat_id, message_id=msg_id)
            return

        if data.startswith("ntf_"):
            key = data.replace("ntf_", "")
            if key in ("onl", "off", "ttl", "err", "edit_mode", "all"):
                ntf[key] = not bool(ntf.get(key, True))
                # Синхронизируем с глобальными настройками Store
                self.store.settings.online = bool(ntf["onl"])
                self.store.settings.offline = bool(ntf["off"])
                self.store.settings.title = bool(ntf["ttl"])
                self.store.settings.errors = bool(ntf["err"])
                self.store.settings.edit_mode = bool(ntf["edit_mode"])
                self.store.settings.notify_on_change = bool(ntf["all"])
                self.store.save()
                self.tg.answer_callback_query(cb_id, "✅ Настройка уведомлений сохранена!")
                self.send_notifications_menu(chat_id, message_id=msg_id)
            return

        if data.startswith("silent_"):
            act = data.replace("silent_", "")
            if act == "sound":
                ucfg["silent_no_sound"] = not bool(ucfg.get("silent_no_sound", False))
                self.store.settings.silent_no_sound = ucfg["silent_no_sound"]
                self.store.save()
                self.tg.answer_callback_query(cb_id, "Звук уведомлений переключен")
            elif act.isdigit():
                mins = int(act)
                until = 0.0 if mins == 0 else (time.time() + mins * 60)
                ucfg["silent_until"] = until
                self.store.settings.silent_until = until
                self.store.save()
                self.tg.answer_callback_query(
                    cb_id,
                    "🔔 Режим тишины выключен!" if mins == 0 else f"🔇 Тишина включена на {mins} мин."
                )
            self.send_notifications_menu(chat_id, message_id=msg_id)
            return

        if data.startswith("rm_prof_"):
            pid = data.replace("rm_prof_", "")
            removed = self.store.profiles.pop(pid, None)
            self.store.save()
            self.generate_html_report()
            self.tg.answer_callback_query(cb_id, f"🗑 Профиль #{pid} удален")
            self.send_delete_menu(chat_id, message_id=msg_id)
            return

        if data == "fr_add":
            self.user_states[str(chat_id)] = {"step": "add_friend"}
            self.tg.answer_callback_query(cb_id)
            self.tg.send_message(
                chat_id,
                "🎭 Введи <b>Telegram ID друга</b> и (через пробел) его имя:\nНапример: <code>123456789 Кент</code>",
                reply_markup=CANCEL_KEYBOARD
            )
            return

        if data.startswith("rm_fr_"):
            fid = data.replace("rm_fr_", "")
            self.store.allowed_users.pop(fid, None)
            self.store.save()
            self.tg.answer_callback_query(cb_id, "Друг удален из списка")
            self.send_friends_menu(chat_id, message_id=msg_id)
            return

        if data.startswith("snipe_"):
            pid = data.replace("snipe_", "")
            lst = self.store.online_snipers.setdefault(pid, [])
            if str(chat_id) not in lst:
                lst.append(str(chat_id))
                self.store.save()
            self.tg.answer_callback_query(cb_id, f"🎯 Снайпер взведен на #{pid}!")
            self.tg.edit_message_text(
                chat_id, msg_id,
                f"🎯 <b>Снайпер онлайна активен для профиля #{pid}!</b>\nКак только пользователь появится в сети, ты мгновенно получишь сигнал."
            )
            return

        if data.startswith("sub|"):
            parts = data.split("|")
            shiki_id = parts[1]
            voice_id = parts[2] if len(parts) > 2 else ""
            cached = self.store.anime_cache.get(shiki_id, {})
            title = cached.get("title", f"Anime #{shiki_id}")
            voices = find_anime_voices(shiki_id, title)
            v_name = "Любая озвучка"
            last_ep = 0
            for v in voices:
                if v["voice_id"] == voice_id:
                    v_name = v["voice_name"]
                    last_ep = v["last_episode"]
                    break
            self.store.anime_subs.append({
                "shikimori_id": shiki_id,
                "title": title,
                "voice_id": voice_id,
                "voice_name": v_name,
                "last_episode": last_ep
            })
            self.store.save()
            self.tg.answer_callback_query(cb_id, f"✅ Добавлено: {title} ({v_name})")
            self.tg.edit_message_text(
                chat_id, msg_id,
                f"✅ <b>Подписка на серии оформлена!</b>\n🎬 Тайтл: <b>{title}</b>\n🎙 Озвучка: <b>{v_name}</b> (вышло: {last_ep} эп.)"
            )
            return

        if data.startswith("delwait_"):
            idx = int(data.replace("delwait_", ""))
            if 0 <= idx < len(self.store.anime_subs):
                removed = self.store.anime_subs.pop(idx)
                self.store.save()
                self.tg.answer_callback_query(cb_id, f"🗑 Удалено: {removed.get('title')}")
            else:
                self.tg.answer_callback_query(cb_id, "Запись уже удалена")
            self.send_delwait_menu(chat_id, message_id=msg_id)
            return

        self.tg.answer_callback_query(cb_id, "Команда выполнена")

    def send_delwait_menu(self, chat_id: Any, message_id: Optional[int] = None):
        if not self.store.anime_subs:
            txt = "📺 Список ожиданий серий пуст."
            markup = None
        else:
            txt = "🔕 <b>Нажми на аниме, чтобы удалить его из ожиданий:</b>"
            rows = []
            for i, s in enumerate(self.store.anime_subs):
                rows.append([{
                    "text": f"🗑 {s.get('title')} ({s.get('voice_name', 'Все')})",
                    "callback_data": f"delwait_{i}"
                }])
            markup = {"inline_keyboard": rows}
        if message_id:
            self.tg.edit_message_text(chat_id, message_id, txt, reply_markup=markup)
        else:
            self.tg.send_message(chat_id, txt, reply_markup=markup)

    # -------------------- МОСТ-ЧАТ --------------------
    def start_bridge_chat(self, initiator_id: Any):
        participants = self.all_recipients()
        if len(participants) < 2:
            self.tg.send_message(
                initiator_id,
                "⚠️ Для общего чата нужен хотя бы 1 добавленный друг в разделе «🎭 Друзья».",
                reply_markup=TELEGRAM_KEYBOARD
            )
            return
        self.bridge_active = True
        self.bridge_participants = participants
        for uid in participants:
            self.tg.send_message(
                uid,
                "💬 <b>Общий мост-чат активирован!</b>\nВсе сообщения теперь пересылаются участникам напрямую. Для выхода нажми «🚪 Завершить чат».",
                reply_markup=CHAT_KEYBOARD
            )

    def stop_bridge_chat(self, initiator_id: Any):
        if not self.bridge_active:
            self.tg.send_message(initiator_id, "Чат уже завершен.", reply_markup=TELEGRAM_KEYBOARD)
            return
        self.bridge_active = False
        for uid in self.bridge_participants:
            self.tg.send_message(uid, "🚪 <b>Мост-чат завершен.</b> Возврат в меню мониторинга.", reply_markup=TELEGRAM_KEYBOARD)
        self.bridge_participants = []

    # -------------------- ОБРАБОТКА ВХОДЯЩИХ СООБЩЕНИЙ --------------------
    def summary_text(self) -> str:
        if not self.store.profiles:
            return "Список профилей пуст. Нажми «➕ Добавить», чтобы добавить профиль AniVox."
        lines = [f"📊 <b>Сводка {APP_NAME} v{VERSION}</b>\n"]
        now_ts = int(time.time())
        for pid, p in self.store.profiles.items():
            st = p.last_status or {}
            pres = st.get("presence", "Оффлайн")
            ttl = st.get("title", "")
            chg_ts = p.last_status_change_ts or now_ts
            chg_str = datetime.fromtimestamp(chg_ts).strftime("%d.%m.%Y %H:%M:%S")
            dur_m = max(0, (now_ts - chg_ts) // 60)

            icon = "🟣 СМОТРИТ" if pres == "Смотрит" else ("🟢 ОНЛАЙН" if pres == "Онлайн" else "⚪ ОФФЛАЙН")
            lines.append(
                f"👤 <b>{p.label}</b> (<code>#{pid}</code>) — <b>{icon}</b>\n"
                f"🎬 Тайтл: <i>{ttl or 'Ничего не смотрит'}</i>\n"
                f"🕒 Изменено: <code>{chg_str}</code> ({dur_m} мин. назад)\n"
            )
        return "\n".join(lines)

    def handle_update(self, upd: Dict[str, Any]):
        if "callback_query" in upd:
            self._handle_callback(upd["callback_query"])
            return

        msg = upd.get("message")
        if not msg:
            return
        chat_id = msg.get("chat", {}).get("id")
        from_user = msg.get("from") or {}
        uid = from_user.get("id", chat_id)
        sender_name = from_user.get("first_name") or f"User {uid}"
        text = (msg.get("text") or "").strip()
        if not text:
            return

        if not self.is_authorized(uid):
            self.tg.send_message(chat_id, f"⛔ Доступ закрыт. Твой Telegram ID: <code>{uid}</code>")
            return

        # Обработка выхода из мост-чата
        if text == "🚪 Завершить чат":
            self.stop_bridge_chat(chat_id)
            return

        # Если активен мост-чат — пересылаем сообщения
        if self.bridge_active and str(chat_id) in self.bridge_participants and not text.startswith("/"):
            for target_uid in self.bridge_participants:
                if str(target_uid) != str(chat_id):
                    self.tg.send_message(target_uid, f"💬 <b>{sender_name}:</b> {text}", reply_markup=CHAT_KEYBOARD)
            return

        if text == "❌ Отмена":
            self.user_states.pop(str(chat_id), None)
            self.tg.send_message(chat_id, "Действие отменено.", reply_markup=TELEGRAM_KEYBOARD)
            return

        # Диалоговые шаги
        state = self.user_states.get(str(chat_id))
        if state:
            step = state.get("step")
            self.user_states.pop(str(chat_id), None)

            if step == "add_profile":
                m = PROFILE_RE.search(text)
                if not m:
                    self.tg.send_message(chat_id, "❌ Не удалось распознать ID или ссылку на профиль AniVox.", reply_markup=TELEGRAM_KEYBOARD)
                    return
                pid = m.group(1)
                prof = Profile(url=f"https://anivox.fun/profile/{pid}", profile_id=pid, label=f"ID {pid}", last_status_change_ts=int(time.time()))
                self.store.profiles[pid] = prof
                res = self.check_one(prof, notify=False)
                self.generate_html_report()
                self.tg.send_message(
                    chat_id,
                    f"✅ Профиль <b>{prof.label}</b> (<code>#{pid}</code>) добавлен!\nСтатус сейчас: <b>{res.presence}</b>",
                    reply_markup=TELEGRAM_KEYBOARD
                )
                return

            if step == "add_friend":
                parts = text.split(maxsplit=1)
                fid = parts[0].strip()
                fname = parts[1].strip() if len(parts) > 1 else f"Друг {fid}"
                self.store.allowed_users[fid] = fname
                self.store.save()
                self.tg.send_message(chat_id, f"✅ Друг <b>{fname}</b> (<code>{fid}</code>) добавлен!", reply_markup=TELEGRAM_KEYBOARD)
                return

            if step == "post_wall":
                self.store.wall_messages.append({
                    "author": sender_name,
                    "text": text,
                    "time": datetime.now().strftime("%d.%m.%Y %H:%M:%S")
                })
                self.store.save()
                self.generate_html_report()
                self.tg.send_message(chat_id, "🧱 Сообщение опубликовано на стене сайта!", reply_markup=TELEGRAM_KEYBOARD)
                return

            if step == "wait_series":
                animes = fetch_anime_info(text)
                if not animes:
                    self.tg.send_message(chat_id, "😕 Аниме не найдено на Shikimori.", reply_markup=TELEGRAM_KEYBOARD)
                    return
                a = animes[0]
                shiki_id = str(a.get("id"))
                title = a.get("russian") or a.get("name") or text
                genres = [g.get("russian") or g.get("name") for g in a.get("genres", []) if isinstance(g, dict)]
                self.store.anime_cache[shiki_id] = {"id": shiki_id, "title": title, "genres": genres}
                self.store.save()

                voices = find_anime_voices(shiki_id, title)
                kb = []
                if voices:
                    for v in voices[:8]:
                        kb.append([{
                            "text": f"🎙 {v['voice_name']} ({v['last_episode']} эп.)",
                            "callback_data": f"sub|{shiki_id}|{v['voice_id']}"
                        }])
                kb.append([{"text": "🔔 Любая озвучка", "callback_data": f"sub|{shiki_id}|"}])
                self.tg.send_message(
                    chat_id,
                    f"🎬 Найдено: <b>{title}</b>\nВыбери озвучку для отслеживания новых серий:",
                    reply_markup={"inline_keyboard": kb}
                )
                return

        # Кнопки главного меню
        if text in ("/start", "/menu"):
            self.tg.send_message(
                chat_id,
                f"👋 <b>{APP_NAME} v{VERSION}</b> готов к работе!\n"
                f"📂 База: <code>{self.store.path}</code> (профилей: {len(self.store.profiles)})",
                reply_markup=TELEGRAM_KEYBOARD
            )
        elif text == "📊 Проверить всех":
            self.tg.send_message(chat_id, "🔄 Проверяю все профили...")
            self.check_all(notify=False)
            self.tg.send_message(chat_id, self.summary_text(), reply_markup=TELEGRAM_KEYBOARD)
        elif text == "🌐 Открыть сайт-отчет":
            path = self.generate_html_report()
            url_hint = f"\n🌐 Online URL: {self.store.autoping_url}" if self.store.autoping_url else f"\n🌐 Локально: http://127.0.0.1:{PORT}"
            self.tg.send_document(chat_id, path, caption=f"📊 <b>Веб-отчет AniVox Monitor</b>{url_hint}")
        elif text == "👥 Мои профили":
            self.tg.send_message(chat_id, self.summary_text(), reply_markup=TELEGRAM_KEYBOARD)
        elif text == "➕ Добавить":
            self.user_states[str(chat_id)] = {"step": "add_profile"}
            self.tg.send_message(chat_id, "Отправь ссылку на профиль AniVox или его ID:", reply_markup=CANCEL_KEYBOARD)
        elif text == "🗑 Удалить":
            self.send_delete_menu(chat_id)
        elif text == "🎬 Ждать серию":
            self.user_states[str(chat_id)] = {"step": "wait_series"}
            self.tg.send_message(chat_id, "Введи название аниме для поиска озвучек:", reply_markup=CANCEL_KEYBOARD)
        elif text == "📺 Мои ожидания":
            if not self.store.anime_subs:
                self.tg.send_message(chat_id, "📺 Активных ожиданий серий нет.", reply_markup=TELEGRAM_KEYBOARD)
            else:
                lines = ["📺 <b>Мои ожидания серий:</b>\n"]
                for s in self.store.anime_subs:
                    lines.append(f"• <b>{s.get('title')}</b> — 🎙 {s.get('voice_name')} (вышло: {s.get('last_episode', 0)} эп.)")
                self.tg.send_message(chat_id, "\n".join(lines), reply_markup=TELEGRAM_KEYBOARD)
        elif text == "🔕 Удалить ожидание":
            self.send_delwait_menu(chat_id)
        elif text == "🧱 Написать на стену":
            self.user_states[str(chat_id)] = {"step": "post_wall"}
            self.tg.send_message(chat_id, "Напиши сообщение для публикации на стене сайта:", reply_markup=CANCEL_KEYBOARD)
        elif text == "🎯 Поймать онлайн":
            if not self.store.profiles:
                self.tg.send_message(chat_id, "Сначала добавь хотя бы один профиль.")
            else:
                rows = [[{"text": f"🎯 {p.label} (#{pid})", "callback_data": f"snipe_{pid}"}] for pid, p in self.store.profiles.items()]
                self.tg.send_message(chat_id, "🎯 <b>Выбери профиль, чей вход в сеть нужно поймать:</b>", reply_markup={"inline_keyboard": rows})
        elif text == "🏆 Топ":
            ranked = sorted(self.store.profiles.values(), key=lambda x: x.online_seconds, reverse=True)
            lines = ["🏆 <b>Топ активности профилей:</b>\n"]
            for idx, p in enumerate(ranked, 1):
                hrs = p.online_seconds // 3600
                mins = (p.online_seconds % 3600) // 60
                lines.append(f"{idx}. <b>{p.label}</b> — ⏱ {hrs}ч {mins}м онлайн | 🎬 тайтлов: {p.titles_count}")
            self.tg.send_message(chat_id, "\n".join(lines), reply_markup=TELEGRAM_KEYBOARD)
        elif text == "📈 История":
            recent = self.store.history[-15:]
            if not recent:
                self.tg.send_message(chat_id, "История изменений пока пуста.")
            else:
                lines = ["📈 <b>Последние события:</b>\n"]
                for h in reversed(recent):
                    lines.append(
                        f"🕒 <code>{h.get('checked_at')}</code> — <b>{h.get('nickname')}</b>: "
                        f"{h.get('presence')} {('(' + h.get('title') + ')') if h.get('title') else ''}"
                    )
                self.tg.send_message(chat_id, "\n".join(lines), reply_markup=TELEGRAM_KEYBOARD)
        elif text == "🎭 Друзья":
            self.send_friends_menu(chat_id)
        elif text == "⚙️ Настройки":
            self.send_settings_menu(chat_id)
        elif text == "🔕 Уведомления":
            self.send_notifications_menu(chat_id)
        elif text == "💬 Связать всех в чат":
            self.start_bridge_chat(chat_id)
        elif text == "⏸ Пауза":
            self.paused = True
            self.tg.send_message(chat_id, "⏸ Автопроверка профилей поставлена на паузу.", reply_markup=TELEGRAM_KEYBOARD)
        elif text == "▶️ Продолжить":
            self.paused = False
            self.tg.send_message(chat_id, "▶️ Автопроверка профилей возобновлена!", reply_markup=TELEGRAM_KEYBOARD)

    # -------------------- ГЕНЕРАТОР АДАПТИВНОГО САЙТА ANIVOX_REPORT.HTML --------------------
    def generate_html_report(self, output_path: str = REPORT_PATH) -> str:
        with self.store.lock:
            profiles = list(self.store.profiles.values())
            history = list(self.store.history)
            wall = list(self.store.wall_messages)
            now_ts = int(time.time())
            now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

            hourly_bins = [0] * 24
            title_counter: Dict[str, int] = {}
            genre_counter: Dict[str, int] = {}
        for h in history:
            raw_ts = h.get("timestamp") or h.get("ts") or 0
            if isinstance(raw_ts, str) and not raw_ts.isdigit():
                try:
                    ts = int(datetime.fromisoformat(raw_ts).timestamp())
                except Exception:
                    ts = 0
            else:
                ts = int(raw_ts or 0)

            if ts > 0:
                hourly_activity[datetime.fromtimestamp(ts).hour] += 1

            det = h.get("details", "")
            if "Смотрит:" in det:
                t_name = det.split("Смотрит:", 1)[1].strip()
                if t_name and t_name != "Ничего не смотрит":
                    title_counts[t_name] = title_counts.get(t_name, 0) + 1


        profile_cards_html = ""
        leaderboard_rows_html = ""
        cnt_online = 0
        cnt_watching = 0
        cnt_offline = 0

        # Сортировка профилей для рейтинга активности во 2-й вкладке
        sorted_profs = sorted(
            profiles.items(),
            key=lambda kv: (kv[1].get("online_seconds", 0), kv[1].get("titles_watched", 0)),
            reverse=True
        )
        max_onl_sec = max([p.get("online_seconds", 0) for _, p in sorted_profs] + [60])

        for rank, (pid, pdata) in enumerate(sorted_profs, 1):
            name = pdata.get("nickname") or pdata.get("name") or f"ID #{pid}"
            is_onl = bool(pdata.get("online", False))
            cur_title = pdata.get("current_title") or pdata.get("anime") or "Ничего не смотрит"
            is_wat = bool(pdata.get("watching", False) or (is_onl and cur_title != "Ничего не смотрит"))

            # 3 состояния аватарки: Фиолетовая (смотрит), Зеленая (онлайн), Серая (оффлайн)
            if is_wat:
                state_key = "watching"
                card_state_cls = "state-watching"
                badge_html = '<span class="status-badge badge-watching">🟣 СМОТРИТ</span>'
                cnt_watching += 1
                cnt_online += 1
            elif is_onl:
                state_key = "online"
                card_state_cls = "state-online"
                badge_html = '<span class="status-badge badge-online">🟢 В СЕТИ</span>'
                cnt_online += 1
            else:
                state_key = "offline"
                card_state_cls = "state-offline"
                badge_html = '<span class="status-badge badge-offline">⚪ ОФФЛАЙН</span>'
                cnt_offline += 1

            if cur_title and cur_title != "Ничего не смотрит":
                title_counts[cur_title] = title_counts.get(cur_title, 0) + 1

            ava_url = pdata.get("avatar") or make_svg_avatar(name)
            fallback_ava = make_svg_avatar(name)
            chg_ts = int(pdata.get("last_status_change") or pdata.get("updated_at") or now_ts)
            chg_dt_str = datetime.fromtimestamp(chg_ts).strftime("%d.%m.%Y в %H:%M:%S")
            prev_st = pdata.get("prev_status") or "Нет данных"
            onl_sec = int(pdata.get("online_seconds", 0))
            onl_hours_str = f"{onl_sec // 3600}ч {(onl_sec % 3600) // 60}м"
            watched_cnt = int(pdata.get("titles_watched", 0))

            profile_cards_html += f"""
            <div class="prof-card {card_state_cls}" data-state="{state_key}" data-name="{name.lower()} {cur_title.lower()}">
                <div class="prof-top">
                    <div class="ava-wrap {card_state_cls}">
                        <img src="{ava_url}" alt="{name}" class="ava-img" onerror="this.onerror=null;this.src='{fallback_ava}';">
                        <span class="ava-dot"></span>
                    </div>
                    <div class="prof-meta">
                        <div class="prof-name-row">
                            <span class="prof-name">{name}</span>
                            {badge_html}
                        </div>
                        <div class="prof-sub">AniVox ID: <b>#{pid}</b> • Тайтлов: <b>{watched_cnt}</b></div>
                    </div>
                </div>

                <div class="watching-box">
                    <div class="watching-lbl">🎬 Текущий тайтл / активность:</div>
                    <div class="watching-val">{cur_title}</div>
                </div>

                <div class="time-panel">
                    <div class="time-item">
                        <span class="t-lbl">📅 Статус изменен:</span>
                        <span class="t-val">{chg_dt_str}</span>
                    </div>
                    <div class="time-item">
                        <span class="t-lbl">⏳ Длительность:</span>
                        <span class="t-val live-timer" data-ts="{chg_ts}">считаем...</span>
                    </div>
                    <div class="time-item">
                        <span class="t-lbl">⏮ До этого:</span>
                        <span class="t-val t-muted">{prev_st}</span>
                    </div>
                </div>
            </div>
            """

            pct = min(100, int((onl_sec / max_onl_sec) * 100)) if max_onl_sec > 0 else 10
            leaderboard_rows_html += f"""
            <div class="leader-item">
                <div class="leader-head">
                    <span><b>#{rank} {name}</b> (ID {pid})</span>
                    <span>⏱ {onl_hours_str} в сети • 🎬 {watched_cnt} тайтл(ов)</span>
                </div>
                <div class="leader-bar-bg">
                    <div class="leader-bar-fill" style="width: {max(6, pct)}%;"></div>
                </div>
            </div>
            """

        if not profile_cards_html:
            profile_cards_html = '<div class="empty-state">Профили еще не добавлены. Добавь их через кнопки бота в Telegram!</div>'

        history_rows_html = ""
        for item in reversed(history[-150:]):
            r_ts = item.get("timestamp") or item.get("ts") or now_ts
            if isinstance(r_ts, str) and not r_ts.isdigit():
                try:
                    ts = int(datetime.fromisoformat(r_ts).timestamp())
                except Exception:
                    ts = now_ts
            else:
                ts = int(r_ts or now_ts)

            dt_full = datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S")
            p_name = item.get("name", f"ID {item.get('pid', '?')}")
            ev_type = item.get("event", "Статус")
            details = item.get("details", "")

            ev_low = ev_type.lower()
            if "смотр" in ev_low or "тайтл" in ev_low:
                ev_cls = "tag-watch"
            elif "онлайн" in ev_low or "вход" in ev_low:
                ev_cls = "tag-onl"
            else:
                ev_cls = "tag-off"

            history_rows_html += f"""
            <tr class="log-row" data-search="{p_name.lower()} {ev_low} {details.lower()} {dt_full}">
                <td class="td-time">{dt_full}</td>
                <td><b>{p_name}</b></td>
                <td><span class="ev-pill {ev_cls}">{ev_type}</span></td>
                <td class="td-det">{details}</td>
            </tr>
            """

        top_titles = sorted(title_counts.items(), key=lambda x: x[1], reverse=True)[:8]
        top_labels_json = json.dumps([x[0][:26] for x in top_titles], ensure_ascii=False)
        top_vals_json = json.dumps([x[1] for x in top_titles])
        hourly_json = json.dumps(hourly_activity)

        html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>AniVox Monitor v{VERSION}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg: #0b0d14;
            --card: #151926;
            --card-2: #1c2235;
            --border: #283048;
            --text: #f1f5f9;
            --muted: #94a3b8;
            --green: #10b981;
            --green-glow: rgba(16, 185, 129, 0.45);
            --purple: #a855f7;
            --purple-glow: rgba(168, 85, 247, 0.55);
            --grey: #64748b;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }}
        body {{
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 14.5px;
            line-height: 1.45;
            overflow-x: hidden;
            padding-bottom: 36px;
        }}
        .wrap {{ max-width: 1080px; margin: 0 auto; padding: 10px; }}
        .top-bar {{
            background: linear-gradient(135deg, #171b2e 0%, #101320 100%);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 14px;
            margin-bottom: 12px;
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
        }}
        .brand-title {{
            font-size: clamp(17px, 4.5vw, 22px);
            font-weight: 800;
            background: linear-gradient(90deg, #fff, #c084fc);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .top-controls {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
        .pill-info {{
            font-size: 12px;
            color: var(--muted);
            background: rgba(255,255,255,0.04);
            padding: 6px 10px;
            border-radius: 10px;
            border: 1px solid var(--border);
        }}
        .btn-sm {{
            background: var(--card-2);
            color: var(--text);
            border: 1px solid var(--border);
            padding: 6px 11px;
            border-radius: 10px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
        }}
        .btn-sm.active {{ background: var(--purple); border-color: var(--purple); color: #fff; }}
        .tabs-nav {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            margin-bottom: 14px;
        }}
        .tab-btn {{
            padding: 12px 10px;
            background: var(--card);
            color: var(--muted);
            border: 1px solid var(--border);
            border-radius: 13px;
            font-size: 13.5px;
            font-weight: 700;
            cursor: pointer;
            text-align: center;
        }}
        .tab-btn.active {{
            background: linear-gradient(135deg, #6366f1, #a855f7);
            color: #fff;
            border-color: transparent;
            box-shadow: 0 4px 16px rgba(168, 85, 247, 0.35);
        }}
        .tab-pane {{ display: none; }}
        .tab-pane.active {{ display: block; }}
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            margin-bottom: 12px;
        }}
        @media (min-width: 600px) {{ .kpi-grid {{ grid-template-columns: repeat(4, 1fr); }} }}
        .kpi-card {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 13px;
            padding: 11px 13px;
            cursor: pointer;
        }}
        .kpi-lbl {{ font-size: 12px; color: var(--muted); }}
        .kpi-val {{ font-size: 21px; font-weight: 800; margin-top: 2px; }}
        .filter-row {{
            display: flex;
            gap: 6px;
            overflow-x: auto;
            scrollbar-width: none;
            margin-bottom: 12px;
        }}
        .filter-row::-webkit-scrollbar {{ display: none; }}
        .f-chip {{
            background: var(--card);
            color: var(--muted);
            border: 1px solid var(--border);
            padding: 7px 12px;
            border-radius: 20px;
            font-size: 12.5px;
            font-weight: 600;
            white-space: nowrap;
            cursor: pointer;
        }}
        .f-chip.active {{ background: var(--text); color: var(--bg); border-color: var(--text); }}
        .profiles-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 12px;
            margin-bottom: 16px;
        }}
        .prof-card {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 14px;
            display: flex;
            flex-direction: column;
            gap: 11px;
        }}
        .prof-card.state-watching {{ border-color: rgba(168, 85, 247, 0.55); box-shadow: 0 0 20px rgba(168, 85, 247, 0.12); }}
        .prof-card.state-online {{ border-color: rgba(16, 185, 129, 0.45); }}
        .prof-card.state-offline {{ opacity: 0.88; }}
        .prof-top {{ display: flex; align-items: center; gap: 12px; }}
        .ava-wrap {{
            position: relative;
            width: 60px;
            height: 60px;
            flex-shrink: 0;
            border-radius: 50%;
            padding: 3px;
        }}
        .ava-img {{
            width: 100%;
            height: 100%;
            border-radius: 50%;
            object-fit: cover;
            display: block;
        }}
        .ava-dot {{
            position: absolute;
            bottom: 1px;
            right: 1px;
            width: 15px;
            height: 15px;
            border-radius: 50%;
            border: 2.5px solid var(--card);
        }}
        /* СЕРАЯ АВАТАРКА (ОФФЛАЙН) */
        .ava-wrap.state-offline {{ background: #334155; }}
        .ava-wrap.state-offline .ava-img {{ filter: grayscale(100%) contrast(0.95) opacity(0.72); }}
        .ava-wrap.state-offline .ava-dot {{ background: #64748b; }}
        /* ФИОЛЕТОВАЯ АВАТАРКА (СМОТРИТ) */
        .ava-wrap.state-watching {{ background: linear-gradient(135deg, #a855f7, #ec4899); box-shadow: 0 0 16px var(--purple-glow); }}
        .ava-wrap.state-watching .ava-dot {{ background: #a855f7; box-shadow: 0 0 8px #a855f7; animation: pulseDot 1.6s infinite; }}
        /* ЗЕЛЕНАЯ АВАТАРКА (ОНЛАЙН) */
        .ava-wrap.state-online {{ background: linear-gradient(135deg, #10b981, #059669); box-shadow: 0 0 14px var(--green-glow); }}
        .ava-wrap.state-online .ava-dot {{ background: #10b981; box-shadow: 0 0 8px #10b981; animation: pulseDot 2s infinite; }}
        @keyframes pulseDot {{ 0%, 100% {{ transform: scale(1); }} 50% {{ transform: scale(1.22); }} }}
        .prof-meta {{ flex: 1; min-width: 0; }}
        .prof-name-row {{ display: flex; justify-content: space-between; align-items: center; gap: 6px; flex-wrap: wrap; }}
        .prof-name {{ font-size: 16px; font-weight: 800; color: #fff; word-break: break-word; }}
        .prof-sub {{ font-size: 12px; color: var(--muted); margin-top: 3px; }}
        .status-badge {{ font-size: 11px; font-weight: 800; padding: 4px 9px; border-radius: 20px; white-space: nowrap; }}
        .badge-watching {{ background: rgba(168, 85, 247, 0.2); color: #d8b4fe; border: 1px solid rgba(168, 85, 247, 0.45); }}
        .badge-online {{ background: rgba(16, 185, 129, 0.18); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.4); }}
        .badge-offline {{ background: rgba(100, 116, 139, 0.2); color: #cbd5e1; border: 1px solid rgba(100, 116, 139, 0.35); }}
        .watching-box {{ background: rgba(255,255,255,0.03); border-left: 3px solid var(--purple); padding: 9px 11px; border-radius: 9px; }}
        .state-online .watching-box {{ border-left-color: var(--green); }}
        .state-offline .watching-box {{ border-left-color: var(--grey); }}
        .watching-lbl {{ font-size: 11px; color: var(--muted); }}
        .watching-val {{ font-size: 13.5px; font-weight: 700; color: #f8fafc; margin-top: 2px; word-break: break-word; }}
        .time-panel {{ background: rgba(0,0,0,0.26); border-radius: 10px; padding: 9px 11px; display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; }}
        .time-item {{ display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; }}
        .t-lbl {{ color: var(--muted); }}
        .t-val {{ font-weight: 700; font-variant-numeric: tabular-nums; }}
        .live-timer {{ color: #c084fc; }}
        .t-muted {{ color: #94a3b8; font-weight: 500; }}
        .box-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 14px; margin-bottom: 14px; }}
        .box-title {{ font-size: 15px; font-weight: 800; margin-bottom: 11px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }}
        .table-wrap {{ width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; }}
        table {{ width: 100%; border-collapse: collapse; min-width: 440px; font-size: 13px; }}
        th, td {{ padding: 9px 10px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.06); }}
        th {{ font-size: 11.5px; color: var(--muted); text-transform: uppercase; }}
        .td-time {{ white-space: nowrap; font-variant-numeric: tabular-nums; color: #cbd5e1; font-size: 12px; }}
        .td-det {{ word-break: break-word; }}
        .ev-pill {{ padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; white-space: nowrap; }}
        .tag-watch {{ background: rgba(168,85,247,0.2); color: #d8b4fe; }}
        .tag-onl {{ background: rgba(16,185,129,0.2); color: #6ee7b7; }}
        .tag-off {{ background: rgba(100,116,139,0.25); color: #cbd5e1; }}
        .grid-2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; margin-bottom: 14px; }}
        .chart-holder {{ position: relative; height: 220px; width: 100%; }}
        .inp {{ width: 100%; background: #0e111b; border: 1px solid var(--border); color: var(--text); padding: 10px 12px; border-radius: 10px; font-size: 14px; margin-bottom: 9px; }}
        .leader-item {{ margin-bottom: 10px; }}
        .leader-head {{ display: flex; justify-content: space-between; font-size: 12.5px; margin-bottom: 4px; flex-wrap: wrap; }}
        .leader-bar-bg {{ width: 100%; height: 8px; background: #0e111b; border-radius: 6px; overflow: hidden; }}
        .leader-bar-fill {{ height: 100%; background: linear-gradient(90deg, #6366f1, #a855f7); border-radius: 6px; }}
        .empty-state {{ padding: 24px; text-align: center; color: var(--muted); }}
    </style>
</head>
<body>
<div class="wrap">
    <header class="top-bar">
        <div>
            <div class="brand-title">⚡ AniVox Live Monitor v{VERSION}</div>
            <div style="font-size: 12px; color: var(--muted);">⚪ Оффлайн • 🟣 Смотрит аниме • 🟢 В сети</div>
        </div>
        <div class="top-controls">
            <span class="pill-info" id="syncTime">🕒 {now_str}</span>
            <button class="btn-sm" id="soundToggleBtn" onclick="toggleSoundRadar()">🔕 Звук-радар: ВЫКЛ</button>
        </div>
    </header>

    <div class="tabs-nav">
        <button class="tab-btn active" onclick="openTab('tab1', this)">📡 1. Профили & Статусы</button>
        <button class="tab-btn" onclick="openTab('tab2', this)">📊 2. Аналитика & Функции</button>
    </div>

    <div id="tab1" class="tab-pane active">
        <div class="kpi-grid">
            <div class="kpi-card" onclick="filterCards('all')">
                <div class="kpi-lbl">👥 Всего профилей</div>
                <div class="kpi-val">{len(profiles)}</div>
            </div>
            <div class="kpi-card" onclick="filterCards('watching')">
                <div class="kpi-lbl">🟣 Смотрят сейчас</div>
                <div class="kpi-val" style="color: #c084fc;">{cnt_watching}</div>
            </div>
            <div class="kpi-card" onclick="filterCards('online')">
                <div class="kpi-lbl">🟢 В сети</div>
                <div class="kpi-val" style="color: #34d399;">{cnt_online}</div>
            </div>
            <div class="kpi-card" onclick="filterCards('offline')">
                <div class="kpi-lbl">⚪ Оффлайн</div>
                <div class="kpi-val" style="color: #94a3b8;">{cnt_offline}</div>
            </div>
        </div>

        <div class="filter-row">
            <button class="f-chip active" onclick="filterCards('all', this)">Все ({len(profiles)})</button>
            <button class="f-chip" onclick="filterCards('watching', this)">🟣 Смотрят ({cnt_watching})</button>
            <button class="f-chip" onclick="filterCards('online', this)">🟢 Онлайн ({cnt_online})</button>
            <button class="f-chip" onclick="filterCards('offline', this)">⚪ Оффлайн ({cnt_offline})</button>
        </div>

        <div class="profiles-grid" id="profilesContainer">
            {profile_cards_html}
        </div>

        <div class="box-card">
            <div class="box-title">
                <span>🕒 История изменения статусов (с датой и временем)</span>
            </div>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Дата и Время</th>
                            <th>Профиль</th>
                            <th>Статус</th>
                            <th>Подробности / Тайтл</th>
                        </tr>
                    </thead>
                    <tbody>
                        {history_rows_html if history_rows_html else '<tr><td colspan="4" class="empty-state">Лог событий пока пуст</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <div id="tab2" class="tab-pane">
        <div class="box-card">
            <div class="box-title">🏆 Рейтинг активности профилей</div>
            {leaderboard_rows_html if leaderboard_rows_html else '<div class="empty-state">Недостаточно данных для рейтинга</div>'}
        </div>

        <div class="grid-2">
            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">📊 Почасовая активность (00:00 – 23:00)</div>
                <div class="chart-holder"><canvas id="hourChart"></canvas></div>
            </div>
            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">🎬 Популярные тайтлы</div>
                <div class="chart-holder"><canvas id="titleChart"></canvas></div>
            </div>
        </div>

        <div class="grid-2">
            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">🍿 Калькулятор марафона серий</div>
                <input type="number" id="epCnt" class="inp" value="12" min="1" placeholder="Сколько серий осталось?" oninput="calcMarathon()">
                <select id="epLen" class="inp" onchange="calcMarathon()">
                    <option value="20">20 мин (без опенинга и эндинга)</option>
                    <option value="24" selected>24 мин (полная серия)</option>
                    <option value="45">45 мин (OVA / Спешл)</option>
                </select>
                <div id="marathonOut" style="background:rgba(168,85,247,0.12); border:1px solid rgba(168,85,247,0.35); padding:10px; border-radius:10px; font-size:13px;"></div>
            </div>

            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">
                    <span>🔍 Поиск по базе логов</span>
                    <button class="btn-sm active" onclick="downloadCSV()">📥 Скачать CSV</button>
                </div>
                <input type="text" id="searchInp" class="inp" placeholder="Введи ник, тайтл, дату или статус..." oninput="filterHistoryTable()">
                <div style="font-size:12.5px; color:var(--muted);">
                    Найдено записей: <b id="foundCnt" style="color:#fff;">все</b>
                </div>
            </div>
        </div>

        <div class="box-card">
            <div class="box-title">📋 Отфильтрованный журнал событий</div>
            <div class="table-wrap">
                <table id="exportTable">
                    <thead>
                        <tr>
                            <th>Дата и Время</th>
                            <th>Профиль</th>
                            <th>Статус</th>
                            <th>Подробности</th>
                        </tr>
                    </thead>
                    <tbody id="searchTableBody">
                        {history_rows_html if history_rows_html else '<tr><td colspan="4">Нет записей</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>

<script>
    let soundRadarEnabled = false;

    function openTab(id, btn) {{
        document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.getElementById(id).classList.add('active');
        btn.classList.add('active');
    }}

    function filterCards(state, btn) {{
        if (btn) {{
            document.querySelectorAll('.f-chip').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
        }}
        document.querySelectorAll('#profilesContainer .prof-card').forEach(card => {{
            const st = card.getAttribute('data-state');
            if (state === 'all' || st === state || (state === 'online' && st === 'watching')) {{
                card.style.display = 'flex';
            }} else {{
                card.style.display = 'none';
            }}
        }});
    }}

    function tickTimers() {{
        const now = Math.floor(Date.now() / 1000);
        document.querySelectorAll('.live-timer').forEach(el => {{
            const ts = parseInt(el.getAttribute('data-ts'), 10);
            if (!ts) return;
            let d = Math.max(0, now - ts);
            const days = Math.floor(d / 86400); d %= 86400;
            const hrs = Math.floor(d / 3600); d %= 3600;
            const mins = Math.floor(d / 60);
            const secs = d % 60;
            let out = [];
            if (days > 0) out.push(days + 'д');
            if (hrs > 0 || days > 0) out.push(hrs + 'ч');
            out.push(mins + 'м ' + secs + 'с');
            el.textContent = out.join(' ');
        }});
    }}
    setInterval(tickTimers, 1000);
    tickTimers();

    function playBeep() {{
        if (!soundRadarEnabled) return;
        try {{
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(880, ctx.currentTime);
            gain.gain.setValueAtTime(0.1, ctx.currentTime);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.18);
        }} catch (e) {{}}
    }}

    function toggleSoundRadar() {{
        soundRadarEnabled = !soundRadarEnabled;
        const btn = document.getElementById('soundToggleBtn');
        btn.textContent = soundRadarEnabled ? '🔔 Звук-радар: ВКЛ' : '🔕 Звук-радар: ВЫКЛ';
        btn.classList.toggle('active', soundRadarEnabled);
        if (soundRadarEnabled) playBeep();
    }}

    function calcMarathon() {{
        const eps = parseInt(document.getElementById('epCnt').value) || 0;
        const len = parseInt(document.getElementById('epLen').value) || 24;
        const mins = eps * len;
        const h = Math.floor(mins / 60);
        const m = mins % 60;
        const end = new Date(Date.now() + mins * 60000).toLocaleTimeString('ru-RU', {{hour:'2-digit', minute:'2-digit'}});
        document.getElementById('marathonOut').innerHTML =
            `⏱ Непрерывный просмотр: <b>${{h}} ч ${{m}} мин</b><br>🏁 Завершишь просмотр в: <b>${{end}}</b>`;
    }}
    calcMarathon();

    function filterHistoryTable() {{
        const q = document.getElementById('searchInp').value.toLowerCase().trim();
        let cnt = 0;
        document.querySelectorAll('#searchTableBody .log-row').forEach(r => {{
            const hay = r.getAttribute('data-search') || '';
            if (!q || hay.includes(q)) {{
                r.style.display = '';
                cnt++;
            }} else {{
                r.style.display = 'none';
            }}
        }});
        document.getElementById('foundCnt').textContent = cnt;
    }}

    function downloadCSV() {{
        const rows = document.querySelectorAll('#exportTable tr');
        let csv = [];
        rows.forEach(r => {{
            if (r.style.display === 'none') return;
            let line = [];
            r.querySelectorAll('th, td').forEach(c => line.push('"' + c.innerText.replace(/"/g, '""') + '"'));
            csv.push(line.join(';'));
        }});
        const blob = new Blob(["\\uFEFF" + csv.join("\\n")], {{type: 'text/csv;charset=utf-8;'}});
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'anivox_logs.csv';
        a.click();
    }}

    new Chart(document.getElementById('hourChart'), {{
        type: 'bar',
        data: {{
            labels: Array.from({{length:24}}, (_,i) => (i<10?'0'+i:i)+':00'),
            datasets: [{{ data: {hourly_json}, backgroundColor: '#a855f7', borderRadius: 4 }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ ticks: {{ color: '#94a3b8', font: {{size: 10}} }} }},
                y: {{ ticks: {{ color: '#94a3b8', stepSize: 1 }}, beginAtZero: true }}
            }}
        }}
    }});

    const tLabels = {top_labels_json};
    const tVals = {top_vals_json};
    new Chart(document.getElementById('titleChart'), {{
        type: 'doughnut',
        data: {{
            labels: tLabels.length ? tLabels : ['Нет данных'],
            datasets: [{{
                data: tVals.length ? tVals : [1],
                backgroundColor: ['#a855f7','#10b981','#6366f1','#ec4899','#f59e0b','#3b82f6','#14b8a6','#ef4444'],
                borderWidth: 0
            }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#f1f5f9', font: {{size: 11}}, boxWidth: 11 }} }} }}
        }}
    }});
</script>
</body>
</html>"""

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        return output_path


# ==================== ФОНОВЫЙ МОНИТОРИНГ ПРОФИЛЕЙ И ОЧИСТКА ====================
def check_all_profiles_once(cfg, notify_even_if_same=False, target_chat=None):
    pids = list(cfg.get("profiles", {}).keys())
    if not pids:
        if notify_even_if_same and target_chat:
            tg_api(cfg["token"], "sendMessage", {"chat_id": target_chat, "text": "Список профилей пуст."})
        return

    now = int(time.time())

    for pid in pids:
        info = fetch_profile(pid)
        if not info.get("ok"):
            continue

        with config_lock:
            p = cfg["profiles"].get(pid, {})
            old_onl = bool(p.get("online", False))
            old_wat = bool(p.get("watching", False))
            old_ttl = p.get("current_title", "Ничего не смотрит")

            new_onl = bool(info.get("online", False))
            new_wat = bool(info.get("watching", False))
            new_ttl = info.get("current_title", "Ничего не смотрит")

            p["nickname"] = info.get("nickname", p.get("nickname", f"User #{pid}"))
            p["avatar"] = info.get("avatar", p.get("avatar", make_svg_avatar(p["nickname"])))

            if new_onl:
                p["online_seconds"] = int(p.get("online_seconds", 0)) + DEFAULT_INTERVAL

            status_diff = (old_onl != new_onl) or (old_wat != new_wat) or (old_ttl != new_ttl)
            if status_diff:
                prev_label = f"{'Смотрел: ' + old_ttl if old_wat else ('В сети' if old_onl else 'Оффлайн')}"
                p["prev_status"] = prev_label
                p["online"] = new_onl
                p["watching"] = new_wat
                p["current_title"] = new_ttl
                p["last_status_change"] = now

                if new_wat and old_ttl != new_ttl:
                    p["titles_watched"] = int(p.get("titles_watched", 0)) + 1
                    ev_type = "Смотрит"
                    details = f"Смотрит: {new_ttl}"
                elif new_onl and not old_onl:
                    ev_type = "Онлайн"
                    details = "Зашел(ла) на AniVox"
                elif not new_onl and old_onl:
                    ev_type = "Оффлайн"
                    details = f"Вышел(ла) из сети (был: {prev_label})"
                else:
                    ev_type = "Статус"
                    details = f"Активность: {new_ttl}"

                cfg["history"].append({
                    "ts": now,
                    "timestamp": now,
                    "pid": pid,
                    "name": p["nickname"],
                    "event": ev_type,
                    "details": details
                })
                if len(cfg["history"]) > 600:
                    cfg["history"] = cfg["history"][-600:]

                save_config(cfg)

                for uid_str in list(cfg.get("users", {}).keys()):
                    s = get_user_settings(cfg, uid_str)
                    ntf = s["notify"]
                    if ev_type == "Онлайн" and not ntf.get("onl", True):
                        continue
                    if ev_type == "Оффлайн" and not ntf.get("off", True):
                        continue
                    if ev_type == "Смотрит" and not ntf.get("ttl", True):
                        continue

                    dt_s = datetime.fromtimestamp(now).strftime("%d.%m.%Y %H:%M:%S")
                    badge = "🟣 СМОТРИТ АНИМЕ" if new_wat else ("🟢 В СЕТИ" if new_onl else "⚪ ОФФЛАЙН")
                    alert_text = (
                        f"🔔 <b>Изменение статуса AniVox!</b>\n\n"
                        f"👤 Профиль: <b>{p['nickname']}</b> (<code>#{pid}</code>)\n"
                        f"📡 Статус: <b>{badge}</b>\n"
                        f"🎬 Тайтл: <i>{new_ttl}</i>\n"
                        f"🕒 Время: <code>{dt_s}</code>"
                    )
                    send_or_edit(cfg, uid_str, alert_text, reply_markup=kb_main_menu(cfg, uid_str))

    generate_html_report(cfg)
    if notify_even_if_same and target_chat:
        send_or_edit(cfg, target_chat, format_status_text(cfg), reply_markup=kb_main_menu(cfg, target_chat), force_new=True)


def monitor_loop(cfg):
    while True:
        try:
            check_all_profiles_once(cfg)
            now = time.time()
            for uid_str, u in list(cfg.get("users", {}).items()):
                cln_min = u.get("settings", {}).get("cleanup_min", 0)
                if cln_min > 0:
                    q = u.get("cleanup_queue", [])
                    rem_q = []
                    for item in q:
                        if now - item["ts"] >= cln_min * 60:
                            tg_api(cfg["token"], "deleteMessage", {"chat_id": uid_str, "message_id": item["mid"]})
                        else:
                            rem_q.append(item)
                    u["cleanup_queue"] = rem_q
        except Exception as e:
            print(f"[Monitor Loop Error]: {e}")

        intervals = [u.get("settings", {}).get("interval", DEFAULT_INTERVAL) for u in cfg.get("users", {}).values()]
        sleep_sec = min(intervals) if intervals else DEFAULT_INTERVAL
        time.sleep(max(1, sleep_sec))


# ==================== ВСТРОЕННЫЙ ВЕБ-СЕРВЕР + AUTOPING ДЛЯ RENDER ====================
class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        cfg = load_config()
        if self.path.startswith("/api/status"):
            payload = json.dumps(build_api_payload(cfg), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.path.startswith("/ping"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        path = generate_html_report(cfg)
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))

    def log_message(self, format, *args):
        return


def run_web_server():
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"[Web] Дашборд запущен на http://0.0.0.0:{PORT}")
    server.serve_forever()


def autoping_loop(cfg):
    while True:
        time.sleep(240)
        url = cfg.get("autoping_url", "").strip()
        if url:
            ping_target = url.rstrip("/") + "/ping"
            try:
                req = urllib.request.Request(ping_target, headers={"User-Agent": USER_AGENT})
                urllib.request.urlopen(req, timeout=10).read()
            except Exception:
                pass


# ==================== ТОЧКА ВХОДА (LONG POLLING + ПОТОКИ) ====================
def main():
    cfg = load_config()
    generate_html_report(cfg)

    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=monitor_loop, args=(cfg,), daemon=True).start()
    threading.Thread(target=autoping_loop, args=(cfg,), daemon=True).start()

    print(f"[{APP_NAME} v{VERSION}] Запущен! Ожидание событий Telegram...")
    offset = 0
    while True:
        try:
            cfg = load_config()
            token = cfg.get("token", "")
            if not token or token == "ВСТАВЬ_ТОКЕН_БОТА":
                time.sleep(5)
                continue

            res = tg_api(token, "getUpdates", {
                "offset": offset,
                "timeout": 25,
                "allowed_updates": ["message", "callback_query"]
            })
            if res.get("ok"):
                for upd in res.get("result", []):
                    offset = upd["update_id"] + 1
                    handle_update(cfg, upd)
            else:
                time.sleep(3)
        except KeyboardInterrupt:
            print("\nОстановка бота...")
            break
        except Exception as e:
            print(f"[Polling Error]: {e}")
            time.sleep(3)
# =============================================================================
# AniVox Monitor 8.0 — production runtime, responsive dashboard and hardening
# =============================================================================
#
# The previous release contained two partially merged application runtimes.
# The old bottom-level runtime referenced undefined globals such as load_config,
# tg_api and config_lock.  Version 8 uses the Store + Monitor implementation
# consistently.  The original Telegram commands and inline menus are retained.
#
# New dashboard capabilities:
#   * responsive layout with automatic phone / tablet / desktop breakpoints;
#   * persistent mobile bottom navigation and desktop sticky navigation;
#   * live refresh from /api/status without reloading the page;
#   * search, status filters, sorting, theme preference and compact mode;
#   * CSV and JSON export, copyable profile IDs and event-history filtering;
#   * health endpoint, safe HTML escaping and optional dashboard access key.
#
# Runtime configuration:
#   BOT_TOKEN       Telegram bot token (or "token" in the state JSON)
#   CHAT_ID         optional owner Telegram ID
#   PORT            HTTP port (default 8080)
#   DASHBOARD_KEY   optional key required for dashboard/API access
#   ANIVOX_STATE_FILE, ANIVOX_REPORT_FILE, ANIVOX_LOG_FILE
#   KODIK_TOKEN     optional Kodik API key; no key is bundled in source
#
# This section intentionally avoids third-party Python dependencies.
# =============================================================================

import html as _html
import hmac as _hmac
import secrets as _secrets
import signal as _signal
import socketserver as _socketserver
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs


DASHBOARD_KEY = os.environ.get("DASHBOARD_KEY", "").strip()
ACTIVE_MONITOR = None
SERVER_INSTANCE = None
SHUTDOWN_EVENT = threading.Event()
REPORT_LOCK = threading.RLock()


def _safe_text(value: Any, limit: int = 500) -> str:
    """Return safe, bounded text for HTML output."""
    return _html.escape(str(value if value is not None else "")[:limit], quote=True)


def _profile_snapshot(profile: Profile) -> Dict[str, Any]:
    """Convert the dataclass profile into a stable dashboard data object."""
    raw = profile.last_status if isinstance(profile.last_status, dict) else {}
    presence = str(raw.get("presence") or "Нет данных")
    title = str(raw.get("title") or "")
    if not profile.last_ok:
        presence = "Ошибка"
    return {
        "id": str(profile.profile_id),
        "name": str(profile.label or f"ID {profile.profile_id}"),
        "url": str(profile.url),
        "avatar": str(profile.avatar or ""),
        "presence": presence,
        "title": title,
        "level": str(raw.get("level") or ""),
        "last_check": str(profile.last_check or ""),
        "last_error": str(profile.last_error or ""),
        "last_change": int(profile.last_status_change_ts or 0),
        "online_seconds": int(profile.online_seconds or 0),
        "watching_seconds": int(profile.watching_seconds or 0),
        "titles_count": int(profile.titles_count or 0),
        "source": str(raw.get("source") or ""),
    }


def build_dashboard_payload(monitor: Monitor) -> Dict[str, Any]:
    """Build a read-only API payload from the active Store."""
    store = monitor.store
    with store.lock:
        profiles = [_profile_snapshot(p) for p in store.profiles.values()]
        history = list(store.history[-500:])
        wall = list(store.wall_messages[-30:])
        subscriptions = list(store.anime_subs)
        settings = {
            "interval_seconds": int(store.settings.interval_seconds),
            "paused": bool(monitor.paused),
            "auto_refresh_seconds": max(5, min(60, int(store.settings.interval_seconds))),
        }
    online = sum(1 for p in profiles if p["presence"] in ("Онлайн", "Смотрит"))
    watching = sum(1 for p in profiles if p["presence"] == "Смотрит")
    errors = sum(1 for p in profiles if p["presence"] == "Ошибка")
    return {
        "app": APP_NAME,
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "timestamp": int(time.time()),
        "stats": {
            "profiles": len(profiles),
            "online": online,
            "watching": watching,
            "offline": max(0, len(profiles) - online - errors),
            "errors": errors,
            "events": len(history),
            "subscriptions": len(subscriptions),
        },
        "settings": settings,
        "profiles": profiles,
        "history": history,
        "wall": wall,
        "subscriptions": subscriptions,
    }


def _duration_label(seconds: Any) -> str:
    """Human-readable duration used in dashboard cards."""
    try:
        value = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        value = 0
    days, value = divmod(value, 86400)
    hours, value = divmod(value, 3600)
    minutes, _ = divmod(value, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours or days:
        parts.append(f"{hours} ч")
    parts.append(f"{minutes} мин")
    return " ".join(parts)


def _dashboard_profile_card(profile: Dict[str, Any]) -> str:
    """Render one profile card; all external/user strings are escaped."""
    presence = profile["presence"]
    if presence == "Смотрит":
        state, label, icon = "watching", "СМОТРИТ", "🎬"
    elif presence == "Онлайн":
        state, label, icon = "online", "В СЕТИ", "🟢"
    elif presence == "Ошибка":
        state, label, icon = "error", "ОШИБКА", "⚠️"
    else:
        state, label, icon = "offline", "ОФФЛАЙН", "⚪"

    name = _safe_text(profile["name"], 80)
    title = _safe_text(profile["title"] or "Сейчас ничего не смотрит", 180)
    pid = _safe_text(profile["id"], 40)
    avatar = profile["avatar"]
    if not avatar.startswith(("https://", "http://", "data:image/")):
        avatar = ""
    avatar = _safe_text(avatar, 1000)
    fallback = _safe_text(make_svg_avatar(profile["name"]), 1000)
    checked = _safe_text(profile["last_check"], 40)
    error = _safe_text(profile["last_error"], 180)
    changed = int(profile["last_change"] or 0)
    online_time = _safe_text(_duration_label(profile["online_seconds"]), 40)
    watched_time = _safe_text(_duration_label(profile["watching_seconds"]), 40)
    total_titles = int(profile["titles_count"] or 0)
    url = _safe_text(profile["url"], 500)

    return f"""
    <article class="profile-card state-{state}"
             data-state="{state}" data-name="{name.lower()} {title.lower()}"
             data-online="{int(profile['online_seconds'])}"
             data-watched="{int(profile['watching_seconds'])}">
      <div class="profile-heading">
        <div class="avatar-shell">
          <img class="avatar" loading="lazy" src="{avatar or fallback}"
               alt="Аватар {name}" onerror="this.onerror=null;this.src='{fallback}'">
          <span class="presence-dot" aria-hidden="true"></span>
        </div>
        <div class="profile-identity">
          <div class="name-line">
            <h3>{name}</h3>
            <span class="status status-{state}">{icon} {label}</span>
          </div>
          <div class="muted">AniVox ID: <code>#{pid}</code></div>
        </div>
      </div>
      <div class="watch-box">
        <div class="muted">Текущая активность</div>
        <div class="watch-title">{title}</div>
      </div>
      <div class="profile-metrics">
        <div><span>В сети</span><b>{online_time}</b></div>
        <div><span>Смотрит</span><b>{watched_time}</b></div>
        <div><span>Тайтлов</span><b>{total_titles}</b></div>
      </div>
      <div class="profile-footer">
        <span class="muted">Проверка: {checked or "ещё не проверялся"}</span>
        <div class="card-actions">
          <button class="mini-button" type="button"
                  onclick="copyText('{pid}')">Копировать ID</button>
          <a class="mini-button" href="{url}" target="_blank"
             rel="noopener noreferrer">Профиль ↗</a>
        </div>
      </div>
      {f'<div class="error-note">{error}</div>' if error else ''}
      <span class="hidden-change" data-ts="{changed}"></span>
    </article>
    """


def _dashboard_history_row(item: Dict[str, Any]) -> str:
    """Render one event row safely."""
    stamp = item.get("timestamp") or item.get("ts") or 0
    try:
        if isinstance(stamp, str) and not stamp.isdigit():
            stamp = int(datetime.fromisoformat(stamp).timestamp())
        stamp = int(stamp)
        when = datetime.fromtimestamp(stamp).strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        when = _safe_text(item.get("checked_at") or "—", 40)
    name = item.get("nickname") or item.get("name") or f"ID {item.get('profile_id', item.get('pid', '?'))}"
    event = item.get("presence") or item.get("event") or "Событие"
    detail = item.get("changes") or item.get("details") or item.get("title") or ""
    css = "watch"
    if "онлайн" in str(event).lower():
        css = "online"
    elif "оффлайн" in str(event).lower():
        css = "offline"
    searchable = _safe_text(f"{when} {name} {event} {detail}".lower(), 800)
    return (
        f'<tr class="history-row" data-search="{searchable}">'
        f'<td>{_safe_text(when, 40)}</td>'
        f'<td><b>{_safe_text(name, 80)}</b></td>'
        f'<td><span class="event-pill {css}">{_safe_text(event, 50)}</span></td>'
        f'<td>{_safe_text(detail, 240)}</td></tr>'
    )


def generate_dashboard_html(monitor: Monitor, output_path: str = REPORT_PATH) -> str:
    """Create the complete adaptive dashboard HTML file."""
    payload = build_dashboard_payload(monitor)
    profiles = payload["profiles"]
    history = payload["history"]
    wall = payload["wall"]
    subscriptions = payload["subscriptions"]
    stats = payload["stats"]

    cards = "".join(_dashboard_profile_card(p) for p in profiles)
    if not cards:
        cards = (
            '<div class="empty-state"><div class="empty-icon">👥</div>'
            '<h3>Пока нет профилей</h3><p>Добавь профиль через Telegram-бота, '
            'и здесь появится его статус.</p></div>'
        )
    rows = "".join(_dashboard_history_row(item) for item in reversed(history[-250:]))
    if not rows:
        rows = '<tr><td colspan="4" class="empty-cell">История пока пуста</td></tr>'

    wall_html = "".join(
        f'<article class="wall-item"><div class="wall-author">{_safe_text(x.get("author", "Пользователь"), 80)}</div>'
        f'<p>{_safe_text(x.get("text", ""), 1000)}</p>'
        f'<time>{_safe_text(x.get("time", ""), 40)}</time></article>'
        for x in reversed(wall)
    ) or '<div class="empty-cell">Сообщений пока нет</div>'

    subs_html = "".join(
        f'<div class="subscription-item"><b>{_safe_text(x.get("title", "Аниме"), 120)}</b>'
        f'<span>{_safe_text(x.get("voice_name", "Любая озвучка"), 80)}</span>'
        f'<small>Последняя серия: {_safe_text(x.get("last_episode", 0), 12)}</small></div>'
        for x in subscriptions
    ) or '<div class="empty-cell">Подписок пока нет</div>'

    safe_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    safe_payload = safe_payload.replace("</", "<\\/")
    generated = _safe_text(payload["generated_at"], 80)

    html_doc = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#10111b">
<meta name="description" content="Адаптивная панель AniVox Monitor">
<title>AniVox Monitor {VERSION}</title>
<style>
:root {{
 --bg:#0b0d16; --surface:#141827; --surface2:#1b2133; --surface3:#242c42;
 --text:#f4f6ff; --muted:#98a3bd; --line:#2a334b; --purple:#a78bfa;
 --green:#34d399; --red:#fb7185; --yellow:#fbbf24; --radius:18px;
 --shadow:0 14px 40px rgba(0,0,0,.22); color-scheme:dark;
}}
:root[data-theme="light"] {{
 --bg:#f3f5fb; --surface:#fff; --surface2:#f1f3fa; --surface3:#e5e9f4;
 --text:#182033; --muted:#68738c; --line:#dce2ef; --purple:#7652d6;
 --green:#059669; --red:#e34b68; --yellow:#b7791f;
 --shadow:0 10px 28px rgba(40,50,80,.08); color-scheme:light;
}}
* {{ box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{
 margin:0; background:var(--bg); color:var(--text);
 font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
 min-height:100vh; padding-bottom:calc(22px + env(safe-area-inset-bottom));
}}
button,input,select {{ font:inherit; }}
button,a {{ -webkit-tap-highlight-color:transparent; }}
button {{ cursor:pointer; }}
a {{ color:inherit; }}
.app-shell {{ width:min(1440px,100%); margin:auto; padding:18px; }}
.header {{
 position:sticky; top:0; z-index:20; display:flex; align-items:center;
 justify-content:space-between; gap:16px; padding:14px 18px;
 background:color-mix(in srgb,var(--surface) 90%,transparent);
 backdrop-filter:blur(16px); border:1px solid var(--line);
 border-radius:var(--radius); box-shadow:var(--shadow); margin-bottom:16px;
}}
.brand {{ display:flex; align-items:center; gap:12px; min-width:0; }}
.brand-mark {{
 width:44px; height:44px; display:grid; place-items:center; border-radius:14px;
 background:linear-gradient(135deg,#6366f1,#a855f7); font-size:23px;
 box-shadow:0 8px 24px rgba(124,58,237,.28);
}}
.brand h1 {{ font-size:clamp(17px,2vw,22px); margin:0; letter-spacing:-.5px; }}
.brand p {{ color:var(--muted); font-size:12px; margin:2px 0 0; }}
.header-actions {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
.button {{
 display:inline-flex; align-items:center; justify-content:center; gap:7px;
 min-height:40px; padding:9px 13px; border-radius:12px;
 border:1px solid var(--line); color:var(--text); background:var(--surface2);
 text-decoration:none; font-weight:650; font-size:13px; transition:.18s ease;
}}
.button:hover {{ transform:translateY(-1px); border-color:var(--purple); }}
.button.primary {{ background:linear-gradient(135deg,#635bdf,#9257d6); color:white; border:0; }}
.button:focus-visible,input:focus-visible,select:focus-visible {{
 outline:3px solid color-mix(in srgb,var(--purple) 55%,transparent); outline-offset:2px;
}}
.status-line {{ display:flex; gap:8px; align-items:center; color:var(--muted); font-size:12px; }}
.live-dot {{ width:8px; height:8px; border-radius:50%; background:var(--green); box-shadow:0 0 12px var(--green); }}
.stats-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:16px 0; }}
.stat-card {{ background:var(--surface); border:1px solid var(--line); border-radius:16px; padding:16px; min-width:0; }}
.stat-label {{ color:var(--muted); font-size:12px; }}
.stat-value {{ font-size:clamp(23px,3vw,32px); font-weight:850; letter-spacing:-1px; margin-top:3px; }}
.stat-card.online .stat-value {{ color:var(--green); }}
.stat-card.watching .stat-value {{ color:var(--purple); }}
.stat-card.errors .stat-value {{ color:var(--red); }}
.nav-tabs {{ display:flex; gap:8px; border-bottom:1px solid var(--line); margin:22px 0 16px; }}
.nav-tab {{ border:0; border-bottom:2px solid transparent; background:transparent; color:var(--muted); padding:12px 16px; font-weight:700; }}
.nav-tab.active {{ color:var(--text); border-bottom-color:var(--purple); }}
.panel {{ display:none; }}
.panel.active {{ display:block; animation:fade-in .18s ease; }}
@keyframes fade-in {{ from {{opacity:.4;transform:translateY(4px)}} to {{opacity:1;transform:none}} }}
.toolbar {{ display:flex; gap:10px; align-items:center; justify-content:space-between; flex-wrap:wrap; margin-bottom:14px; }}
.search-wrap {{ flex:1 1 240px; }}
.input {{
 width:100%; min-height:43px; padding:10px 13px; border:1px solid var(--line);
 border-radius:12px; background:var(--surface); color:var(--text);
}}
.filters {{ display:flex; gap:7px; flex-wrap:wrap; }}
.filter {{
 border:1px solid var(--line); background:var(--surface); color:var(--muted);
 border-radius:999px; padding:8px 12px; font-size:12px; font-weight:700;
}}
.filter.active {{ color:white; background:#7656d8; border-color:#7656d8; }}
.profiles-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:13px; }}
.profile-card {{
 min-width:0; display:flex; flex-direction:column; gap:13px; padding:16px;
 background:var(--surface); border:1px solid var(--line); border-radius:18px;
 box-shadow:var(--shadow); transition:transform .18s,border-color .18s;
}}
.profile-card:hover {{ transform:translateY(-2px); }}
.profile-card.state-watching {{ border-color:color-mix(in srgb,var(--purple) 65%,var(--line)); }}
.profile-card.state-online {{ border-color:color-mix(in srgb,var(--green) 55%,var(--line)); }}
.profile-card.state-error {{ border-color:color-mix(in srgb,var(--red) 55%,var(--line)); }}
.profile-heading {{ display:flex; align-items:center; gap:12px; min-width:0; }}
.avatar-shell {{ width:54px; height:54px; flex:0 0 54px; position:relative; padding:3px; border-radius:50%; background:var(--surface3); }}
.state-watching .avatar-shell {{ background:linear-gradient(135deg,#a78bfa,#ec4899); }}
.state-online .avatar-shell {{ background:linear-gradient(135deg,#34d399,#059669); }}
.avatar {{ display:block; width:100%; height:100%; object-fit:cover; border-radius:50%; background:var(--surface2); }}
.presence-dot {{ position:absolute; width:13px; height:13px; right:0; bottom:0; border:2px solid var(--surface); border-radius:50%; background:#64748b; }}
.state-online .presence-dot {{ background:var(--green); }}
.state-watching .presence-dot {{ background:var(--purple); }}
.state-error .presence-dot {{ background:var(--red); }}
.profile-identity {{ min-width:0; flex:1; }}
.name-line {{ display:flex; align-items:flex-start; justify-content:space-between; gap:7px; flex-wrap:wrap; }}
.name-line h3 {{ margin:0; font-size:16px; overflow-wrap:anywhere; }}
.muted {{ color:var(--muted); font-size:12px; }}
.status {{ white-space:nowrap; font-size:10px; font-weight:850; border-radius:99px; padding:4px 8px; background:var(--surface2); }}
.status-online {{ color:var(--green); }}
.status-watching {{ color:var(--purple); }}
.status-error {{ color:var(--red); }}
.watch-box {{ border-radius:12px; padding:11px 12px; background:var(--surface2); border-left:3px solid var(--purple); }}
.watch-title {{ font-weight:700; margin-top:4px; overflow-wrap:anywhere; }}
.profile-metrics {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }}
.profile-metrics div {{ display:flex; flex-direction:column; gap:3px; }}
.profile-metrics span {{ color:var(--muted); font-size:11px; }}
.profile-metrics b {{ font-size:13px; }}
.profile-footer {{ display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; border-top:1px solid var(--line); padding-top:10px; }}
.card-actions {{ display:flex; gap:6px; }}
.mini-button {{ display:inline-flex; align-items:center; min-height:32px; padding:6px 9px; border:1px solid var(--line); border-radius:9px; color:var(--text); background:var(--surface2); text-decoration:none; font-size:11px; font-weight:700; }}
.error-note {{ color:var(--red); font-size:11px; overflow-wrap:anywhere; }}
.empty-state {{ grid-column:1/-1; text-align:center; padding:48px 20px; border:1px dashed var(--line); border-radius:18px; color:var(--muted); }}
.empty-icon {{ font-size:40px; margin-bottom:8px; }}
.section-card {{ background:var(--surface); border:1px solid var(--line); border-radius:18px; padding:17px; margin-bottom:14px; }}
.section-title {{ font-size:16px; margin:0 0 13px; }}
.table-wrap {{ width:100%; overflow:auto; border:1px solid var(--line); border-radius:12px; }}
table {{ width:100%; border-collapse:collapse; min-width:650px; font-size:13px; }}
th,td {{ text-align:left; padding:11px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); background:var(--surface2); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }}
tr:last-child td {{ border-bottom:0; }}
.event-pill {{ display:inline-block; border-radius:99px; padding:4px 8px; background:var(--surface2); font-size:11px; font-weight:750; }}
.event-pill.online {{ color:var(--green); }}
.event-pill.watch {{ color:var(--purple); }}
.event-pill.offline {{ color:var(--muted); }}
.empty-cell {{ color:var(--muted); text-align:center; padding:25px; }}
.wall-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }}
.wall-item,.subscription-item {{ border:1px solid var(--line); background:var(--surface2); border-radius:13px; padding:13px; overflow-wrap:anywhere; }}
.wall-author {{ font-weight:800; margin-bottom:6px; }}
.wall-item time,.subscription-item small {{ display:block; color:var(--muted); margin-top:8px; font-size:11px; }}
.subscription-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }}
.subscription-item {{ display:flex; flex-direction:column; gap:5px; }}
.subscription-item span {{ color:var(--muted); font-size:12px; }}
.footer {{ color:var(--muted); text-align:center; padding:22px 10px 8px; font-size:11px; }}
.toast {{
 position:fixed; z-index:50; bottom:22px; left:50%; transform:translate(-50%,20px);
 opacity:0; pointer-events:none; background:var(--surface3); color:var(--text);
 border:1px solid var(--line); padding:11px 16px; border-radius:12px;
 box-shadow:var(--shadow); transition:.2s; max-width:90vw; text-align:center;
}}
.toast.show {{ opacity:1; transform:translate(-50%,0); }}
.mobile-nav {{ display:none; }}
.compact .profile-card {{ padding:11px; gap:8px; }}
.compact .profile-metrics {{ display:none; }}
.loading {{ opacity:.55; pointer-events:none; }}
@media (max-width:1050px) {{
 .profiles-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
}}
@media (max-width:700px) {{
 body {{ font-size:14px; padding-bottom:calc(86px + env(safe-area-inset-bottom)); }}
 .app-shell {{ padding:10px; }}
 .header {{ position:relative; padding:12px; align-items:flex-start; }}
 .brand-mark {{ width:39px; height:39px; flex-basis:39px; }}
 .header-actions {{ justify-content:flex-end; }}
 .header-actions .button {{ min-height:36px; padding:7px 9px; font-size:11px; }}
 .status-line {{ display:none; }}
 .stats-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }}
 .stat-card {{ padding:12px; }}
 .stat-value {{ font-size:25px; }}
 .nav-tabs {{ display:none; }}
 .panel {{ display:none; }}
 .panel.active {{ display:block; }}
 .toolbar {{ align-items:stretch; }}
 .filters {{ overflow-x:auto; flex-wrap:nowrap; padding-bottom:4px; }}
 .filter {{ flex:0 0 auto; }}
 .profiles-grid {{ grid-template-columns:1fr; gap:9px; }}
 .profile-card {{ padding:13px; }}
 .wall-grid,.subscription-grid {{ grid-template-columns:1fr; }}
 .mobile-nav {{
   display:grid; grid-template-columns:repeat(4,1fr); gap:5px;
   position:fixed; z-index:30; bottom:0; left:0; right:0;
   padding:8px 8px calc(8px + env(safe-area-inset-bottom));
   background:color-mix(in srgb,var(--surface) 94%,transparent);
   border-top:1px solid var(--line); backdrop-filter:blur(18px);
 }}
 .mobile-nav button {{
   display:flex; flex-direction:column; gap:3px; align-items:center;
   justify-content:center; min-height:48px; border:0; border-radius:10px;
   background:transparent; color:var(--muted); font-size:10px; font-weight:750;
 }}
 .mobile-nav button.active {{ background:var(--surface3); color:var(--text); }}
 .mobile-nav .nav-icon {{ font-size:18px; }}
 .section-card {{ padding:12px; }}
 .profile-footer {{ align-items:flex-start; }}
 .footer {{ padding-bottom:12px; }}
}}
@media (max-width:380px) {{
 .brand p {{ display:none; }}
 .header-actions .button {{ font-size:10px; }}
 .profile-metrics b {{ font-size:12px; }}
}}
@media (min-width:701px) and (max-width:900px) {{
 .profiles-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
 .app-shell {{ padding:14px; }}
}}
@media (prefers-reduced-motion:reduce) {{
 *,*::before,*::after {{ scroll-behavior:auto!important; animation-duration:.01ms!important; transition-duration:.01ms!important; }}
}}
</style>
</head>
<body>
<div class="app-shell" id="app">
<header class="header">
  <div class="brand">
    <div class="brand-mark">🎬</div>
    <div><h1>AniVox Monitor</h1><p>Версия {VERSION} · адаптивная панель</p></div>
  </div>
  <div class="header-actions">
    <div class="status-line"><span class="live-dot"></span><span id="connectionText">Подключено</span></div>
    <button class="button" id="themeButton" type="button" onclick="toggleTheme()">🌗 Тема</button>
    <button class="button" id="compactButton" type="button" onclick="toggleCompact()">▤ Вид</button>
    <button class="button primary" id="refreshButton" type="button" onclick="refreshData(true)">↻ Обновить</button>
  </div>
</header>

<section class="stats-grid" aria-label="Сводка">
  <article class="stat-card"><div class="stat-label">Всего профилей</div><div class="stat-value" id="statProfiles">{stats['profiles']}</div></article>
  <article class="stat-card online"><div class="stat-label">Сейчас в сети</div><div class="stat-value" id="statOnline">{stats['online']}</div></article>
  <article class="stat-card watching"><div class="stat-label">Смотрят аниме</div><div class="stat-value" id="statWatching">{stats['watching']}</div></article>
  <article class="stat-card errors"><div class="stat-label">Ошибки проверки</div><div class="stat-value" id="statErrors">{stats['errors']}</div></article>
</section>

<nav class="nav-tabs" aria-label="Разделы">
  <button class="nav-tab active" data-panel="profiles" onclick="openPanel('profiles',this)">👥 Профили</button>
  <button class="nav-tab" data-panel="history" onclick="openPanel('history',this)">📈 История</button>
  <button class="nav-tab" data-panel="community" onclick="openPanel('community',this)">💬 Сообщество</button>
</nav>

<main>
<section class="panel active" id="panel-profiles">
  <div class="toolbar">
    <div class="search-wrap"><input class="input" id="profileSearch" type="search" placeholder="Поиск по нику, ID или тайтлу..." oninput="applyFilters()" autocomplete="off"></div>
    <div class="filters" role="group" aria-label="Фильтр статуса">
      <button class="filter active" data-filter="all" onclick="setFilter('all',this)">Все</button>
      <button class="filter" data-filter="online" onclick="setFilter('online',this)">Онлайн</button>
      <button class="filter" data-filter="watching" onclick="setFilter('watching',this)">Смотрят</button>
      <button class="filter" data-filter="offline" onclick="setFilter('offline',this)">Оффлайн</button>
      <button class="filter" data-filter="error" onclick="setFilter('error',this)">Ошибки</button>
    </div>
  </div>
  <div class="toolbar">
    <div class="muted" id="profileCount">Профилей: {len(profiles)}</div>
    <select class="input" style="max-width:220px" id="sortSelect" onchange="sortCards()">
      <option value="default">Сортировка: по статусу</option>
      <option value="name">По имени</option>
      <option value="online">По времени онлайн</option>
      <option value="watching">По времени просмотра</option>
    </select>
  </div>
  <div class="profiles-grid" id="profilesGrid">{cards}</div>
</section>

<section class="panel" id="panel-history">
  <div class="section-card">
    <div class="toolbar">
      <h2 class="section-title">📈 Журнал событий</h2>
      <div class="header-actions">
        <button class="button" onclick="downloadCSV()">📥 CSV</button>
        <button class="button" onclick="downloadJSON()">JSON</button>
      </div>
    </div>
    <input class="input" id="historySearch" type="search" placeholder="Фильтр истории..." oninput="filterHistory()">
    <p class="muted" id="historyCount" style="margin:9px 0">Событий: {len(history)}</p>
    <div class="table-wrap">
      <table id="historyTable">
        <thead><tr><th>Время</th><th>Профиль</th><th>Статус</th><th>Подробности</th></tr></thead>
        <tbody id="historyBody">{rows}</tbody>
      </table>
    </div>
  </div>
</section>

<section class="panel" id="panel-community">
  <div class="section-card">
    <h2 class="section-title">🧱 Стена сообщений</h2>
    <div class="wall-grid" id="wallGrid">{wall_html}</div>
  </div>
  <div class="section-card">
    <h2 class="section-title">🍿 Ожидание новых серий</h2>
    <div class="subscription-grid" id="subscriptionGrid">{subs_html}</div>
  </div>
</section>
</main>

<footer class="footer">
  <div>AniVox Monitor {VERSION} · Последняя генерация: <span id="generatedAt">{generated}</span></div>
  <div>Интерфейс автоматически подстраивается под размер экрана устройства.</div>
</footer>
</div>

<nav class="mobile-nav" aria-label="Мобильная навигация">
  <button class="active" data-panel="profiles" onclick="openPanel('profiles',this)"><span class="nav-icon">👥</span>Профили</button>
  <button data-panel="history" onclick="openPanel('history',this)"><span class="nav-icon">📈</span>История</button>
  <button data-panel="community" onclick="openPanel('community',this)"><span class="nav-icon">💬</span>Стена</button>
  <button onclick="refreshData(true)"><span class="nav-icon">↻</span>Обновить</button>
</nav>
<div class="toast" id="toast" role="status" aria-live="polite"></div>

<script>
"use strict";
const INITIAL_DATA = {safe_payload};
let dashboardData = INITIAL_DATA;
let activeFilter = "all";
let refreshTimer = null;
let toastTimer = null;
let refreshBusy = false;

function byId(id) {{ return document.getElementById(id); }}

function showToast(message) {{
  const node = byId("toast");
  if (!node) return;
  node.textContent = String(message || "");
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 2300);
}}

function openPanel(name, button) {{
  document.querySelectorAll(".panel").forEach(node => node.classList.remove("active"));
  const panel = byId("panel-" + name);
  if (panel) panel.classList.add("active");
  document.querySelectorAll(".nav-tab,.mobile-nav button").forEach(node => node.classList.remove("active"));
  document.querySelectorAll('[data-panel="' + name + '"]').forEach(node => node.classList.add("active"));
  if (button) button.classList.add("active");
  window.scrollTo({{top:0,behavior:"smooth"}});
}}

function setFilter(value, button) {{
  activeFilter = value;
  document.querySelectorAll(".filter").forEach(node => node.classList.remove("active"));
  if (button) button.classList.add("active");
  applyFilters();
}}

function applyFilters() {{
  const query = (byId("profileSearch").value || "").toLocaleLowerCase("ru");
  let visible = 0;
  document.querySelectorAll(".profile-card").forEach(card => {{
    const state = card.dataset.state || "offline";
    const haystack = (card.dataset.name || "").toLocaleLowerCase("ru");
    const stateMatch = activeFilter === "all"
      || state === activeFilter
      || (activeFilter === "online" && state === "watching");
    const textMatch = !query || haystack.includes(query)
      || (card.textContent || "").toLocaleLowerCase("ru").includes(query);
    const show = stateMatch && textMatch;
    card.hidden = !show;
    if (show) visible += 1;
  }});
  byId("profileCount").textContent = "Показано: " + visible;
}}

function sortCards() {{
  const grid = byId("profilesGrid");
  const cards = Array.from(grid.querySelectorAll(".profile-card"));
  const mode = byId("sortSelect").value;
  cards.sort((a,b) => {{
    if (mode === "name") return (a.dataset.name || "").localeCompare(b.dataset.name || "", "ru");
    if (mode === "online") return Number(b.dataset.online || 0) - Number(a.dataset.online || 0);
    if (mode === "watching") return Number(b.dataset.watched || 0) - Number(a.dataset.watched || 0);
    const order = {{watching:0,online:1,offline:2,error:3}};
    return (order[a.dataset.state] ?? 4) - (order[b.dataset.state] ?? 4);
  }});
  cards.forEach(card => grid.appendChild(card));
  applyFilters();
}}

function toggleTheme() {{
  const root = document.documentElement;
  const next = root.dataset.theme === "light" ? "dark" : "light";
  root.dataset.theme = next;
  try {{ localStorage.setItem("anivox-theme", next); }} catch (_) {{}}
}}

function toggleCompact() {{
  document.body.classList.toggle("compact");
  try {{ localStorage.setItem("anivox-compact", document.body.classList.contains("compact") ? "1" : "0"); }} catch (_) {{}}
}}

async function copyText(value) {{
  try {{
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(String(value));
    else {{
      const area = document.createElement("textarea");
      area.value = String(value);
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }}
    showToast("Скопировано: " + value);
  }} catch (_) {{ showToast("Не удалось скопировать"); }}
}}

function escapeHTML(value) {{
  return String(value ?? "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[ch]));
}}

function renderProfile(profile) {{
  const status = profile.presence || "Оффлайн";
  const state = status === "Смотрит" ? "watching" : status === "Онлайн" ? "online" : status === "Ошибка" ? "error" : "offline";
  const badge = state === "watching" ? "🎬 СМОТРИТ" : state === "online" ? "🟢 В СЕТИ" : state === "error" ? "⚠️ ОШИБКА" : "⚪ ОФФЛАЙН";
  const name = escapeHTML(profile.name);
  const id = escapeHTML(profile.id);
  const title = escapeHTML(profile.title || "Сейчас ничего не смотрит");
  const safeUrl = /^https?:\\/\\//i.test(profile.url || "") ? escapeHTML(profile.url) : "#";
  const avatar = /^https?:\\/\\//i.test(profile.avatar || "") ? escapeHTML(profile.avatar) : "";
  const fallback = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><rect width="100%" height="100%" fill="#7656d8"/><text x="50%" y="55%" text-anchor="middle" fill="white" font-size="24">AV</text></svg>');
  const duration = seconds => {{
    const n = Math.max(0, Number(seconds || 0));
    return Math.floor(n/3600) + " ч " + Math.floor((n%3600)/60) + " мин";
  }};
  return `<article class="profile-card state-${{state}}" data-state="${{state}}" data-name="${{(name+" "+title).toLocaleLowerCase("ru")}}" data-online="${{Number(profile.online_seconds||0)}}" data-watched="${{Number(profile.watching_seconds||0)}}">
    <div class="profile-heading"><div class="avatar-shell"><img class="avatar" loading="lazy" src="${{avatar || fallback}}" alt="Аватар ${{name}}"></div>
    <div class="profile-identity"><div class="name-line"><h3>${{name}}</h3><span class="status status-${{state}}">${{badge}}</span></div><div class="muted">AniVox ID: <code>#${{id}}</code></div></div></div>
    <div class="watch-box"><div class="muted">Текущая активность</div><div class="watch-title">${{title}}</div></div>
    <div class="profile-metrics"><div><span>В сети</span><b>${{duration(profile.online_seconds)}}</b></div><div><span>Смотрит</span><b>${{duration(profile.watching_seconds)}}</b></div><div><span>Тайтлов</span><b>${{Number(profile.titles_count||0)}}</b></div></div>
    <div class="profile-footer"><span class="muted">Проверка: ${{escapeHTML(profile.last_check || "—")}}</span><div class="card-actions"><button class="mini-button" onclick="copyText('${{id}}')">Копировать ID</button><a class="mini-button" href="${{safeUrl}}" target="_blank" rel="noopener">Профиль ↗</a></div></div>
  </article>`;
}}

function renderHistory(items) {{
  const body = byId("historyBody");
  if (!items || !items.length) {{
    body.innerHTML = '<tr><td colspan="4" class="empty-cell">История пока пуста</td></tr>';
    return;
  }}
  body.innerHTML = items.slice(-250).reverse().map(item => {{
    const stamp = Number(item.timestamp || item.ts || 0);
    const when = stamp ? new Date(stamp*1000).toLocaleString("ru-RU") : escapeHTML(item.checked_at || "—");
    const name = escapeHTML(item.nickname || item.name || "Профиль");
    const event = escapeHTML(item.presence || item.event || "Событие");
    const detail = escapeHTML(item.changes || item.details || item.title || "");
    const lower = event.toLowerCase();
    const kind = lower.includes("онлайн") ? "online" : lower.includes("оффлайн") ? "offline" : "watch";
    const hay = escapeHTML((when+" "+name+" "+event+" "+detail).toLocaleLowerCase("ru"));
    return `<tr class="history-row" data-search="${{hay}}"><td>${{when}}</td><td><b>${{name}}</b></td><td><span class="event-pill ${{kind}}">${{event}}</span></td><td>${{detail}}</td></tr>`;
  }}).join("");
  filterHistory();
}}

function filterHistory() {{
  const query = (byId("historySearch").value || "").toLocaleLowerCase("ru");
  let count = 0;
  document.querySelectorAll(".history-row").forEach(row => {{
    const match = !query || (row.dataset.search || "").includes(query);
    row.hidden = !match;
    if (match) count++;
  }});
  byId("historyCount").textContent = "Найдено событий: " + count;
}}

function downloadBlob(name, content, type) {{
  const blob = new Blob([content], {{type}});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}}

function downloadCSV() {{
  const rows = Array.from(document.querySelectorAll("#historyTable tr")).filter(row => !row.hidden);
  const csv = rows.map(row => Array.from(row.querySelectorAll("th,td"))
    .map(cell => '"' + cell.innerText.replace(/"/g,'""') + '"').join(";")).join("\\r\\n");
  downloadBlob("anivox-history.csv", "\\uFEFF" + csv, "text/csv;charset=utf-8");
}}

function downloadJSON() {{
  downloadBlob("anivox-export.json", JSON.stringify(dashboardData,null,2), "application/json;charset=utf-8");
}}

function updateStats(data) {{
  byId("statProfiles").textContent = data.stats.profiles;
  byId("statOnline").textContent = data.stats.online;
  byId("statWatching").textContent = data.stats.watching;
  byId("statErrors").textContent = data.stats.errors;
  byId("generatedAt").textContent = new Date(data.timestamp*1000).toLocaleString("ru-RU");
}}

function renderCommunity(data) {{
  byId("wallGrid").innerHTML = (data.wall || []).slice().reverse().map(item =>
    `<article class="wall-item"><div class="wall-author">${{escapeHTML(item.author || "Пользователь")}}</div><p>${{escapeHTML(item.text || "")}}</p><time>${{escapeHTML(item.time || "")}}</time></article>`
  ).join("") || '<div class="empty-cell">Сообщений пока нет</div>';
  byId("subscriptionGrid").innerHTML = (data.subscriptions || []).map(item =>
    `<div class="subscription-item"><b>${{escapeHTML(item.title || "Аниме")}}</b><span>${{escapeHTML(item.voice_name || "Любая озвучка")}}</span><small>Последняя серия: ${{Number(item.last_episode||0)}}</small></div>`
  ).join("") || '<div class="empty-cell">Подписок пока нет</div>';
}}

async function refreshData(manual=false) {{
  if (refreshBusy) return;
  refreshBusy = true;
  const button = byId("refreshButton");
  if (button) button.classList.add("loading");
  try {{
    const response = await fetch("/api/status", {{cache:"no-store",headers:{{"Accept":"application/json"}}}});
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    dashboardData = data;
    updateStats(data);
    byId("profilesGrid").innerHTML = data.profiles.map(renderProfile).join("") ||
      '<div class="empty-state"><div class="empty-icon">👥</div><h3>Пока нет профилей</h3><p>Добавь профиль через Telegram.</p></div>';
    renderHistory(data.history || []);
    renderCommunity(data);
    sortCards();
    byId("connectionText").textContent = "Обновлено " + new Date().toLocaleTimeString("ru-RU");
    if (manual) showToast("Данные обновлены");
  }} catch (error) {{
    byId("connectionText").textContent = "Нет связи с сервером";
    if (manual) showToast("Не удалось обновить: " + error.message);
  }} finally {{
    refreshBusy = false;
    if (button) button.classList.remove("loading");
  }}
}}

function restorePreferences() {{
  try {{
    const theme = localStorage.getItem("anivox-theme");
    if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
    if (localStorage.getItem("anivox-compact") === "1") document.body.classList.add("compact");
  }} catch (_) {{}}
}}

function startAutoRefresh() {{
  if (refreshTimer) clearInterval(refreshTimer);
  const seconds = Math.max(5, Math.min(60, Number(dashboardData.settings.auto_refresh_seconds || 10)));
  refreshTimer = setInterval(() => {{
    if (!document.hidden) refreshData(false);
  }}, seconds * 1000);
}}

document.addEventListener("visibilitychange", () => {{
  if (!document.hidden) refreshData(false);
}});
restorePreferences();
renderHistory(dashboardData.history || []);
startAutoRefresh();
applyFilters();
</script>
</body>
</html>
"""
    with REPORT_LOCK:
        target = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        temporary = target + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(html_doc)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    return target


# Replace the old report method, which mixed Profile dataclasses and dictionaries
# and referenced undefined names hourly_activity/title_counts.
def _monitor_generate_html_report(self, output_path: str = REPORT_PATH) -> str:
    return generate_dashboard_html(self, output_path)


Monitor.generate_html_report = _monitor_generate_html_report


class SecureDashboardHandler(BaseHTTPRequestHandler):
    """Threaded HTTP handler for dashboard, health and read-only API."""

    server_version = "AniVoxMonitor/8"
    sys_version = ""

    def _authorized(self) -> bool:
        if not DASHBOARD_KEY:
            return True
        query = parse_qs(urlsplit(self.path).query)
        supplied = (query.get("key") or [""])[0]
        return _hmac.compare_digest(supplied, DASHBOARD_KEY)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline' https: data:; img-src 'self' https: data:; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        monitor = ACTIVE_MONITOR
        if monitor is None:
            self._send(503, b"Monitor is starting", "text/plain; charset=utf-8")
            return

        route = urlsplit(self.path).path
        if route in ("/health", "/ping"):
            self._send(200, b"OK", "text/plain; charset=utf-8")
            return

        if not self._authorized():
            self._send(401, b"Dashboard access key required", "text/plain; charset=utf-8")
            return

        if route == "/api/status":
            body = json.dumps(build_dashboard_payload(monitor), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return

        if route == "/api/health":
            body = json.dumps({
                "ok": True,
                "paused": monitor.paused,
                "profiles": len(monitor.store.profiles),
                "time": int(time.time()),
            }).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return

        if route == "/":
            path = monitor.generate_html_report()
            try:
                with open(path, "rb") as handle:
                    self._send(200, handle.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"Report generation failed", "text/plain; charset=utf-8")
            return

        self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_HEAD(self) -> None:
        route = urlsplit(self.path).path
        if route in ("/health", "/ping"):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
        else:
            self.send_error(404)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("HTTP %s - %s", self.address_string(), fmt % args)


def run_web_server() -> None:
    """Start a threaded HTTP server; each browser request gets its own thread."""
    global SERVER_INSTANCE
    server = ThreadingHTTPServer(("0.0.0.0", PORT), SecureDashboardHandler)
    server.daemon_threads = True
    SERVER_INSTANCE = server
    logger.info("Веб-панель запущена: http://0.0.0.0:%s", PORT)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        SERVER_INSTANCE = None


def monitor_worker(monitor: Monitor) -> None:
    """Run profile and anime checks using the canonical Monitor implementation."""
    logger.info("Поток мониторинга запущен")
    while monitor.running and not SHUTDOWN_EVENT.is_set():
        started = time.monotonic()
        try:
            if not monitor.paused:
                monitor.check_all(notify=True)
                monitor.check_anime_updates()
            monitor.enforce_cleanup()
        except Exception:
            logger.exception("Ошибка фонового мониторинга")
        interval = max(5, int(monitor.store.settings.interval_seconds or DEFAULT_INTERVAL))
        elapsed = time.monotonic() - started
        SHUTDOWN_EVENT.wait(max(1.0, interval - elapsed))
    logger.info("Поток мониторинга остановлен")


def telegram_polling_worker(monitor: Monitor) -> None:
    """Telegram long polling with reconnect, offset tracking and safe backoff."""
    logger.info("Telegram polling запущен")
    offset = 0
    failures = 0
    while monitor.running and not SHUTDOWN_EVENT.is_set():
        try:
            token = monitor.store.token or os.environ.get("BOT_TOKEN", "")
            if not token or token == "ВСТАВЬ_ТОКЕН_БОТА":
                SHUTDOWN_EVENT.wait(5)
                continue
            if monitor.tg.token != token:
                monitor.tg = TelegramClient(token)
            response = monitor.tg.get_updates(offset=offset, timeout=25)
            if not response.get("ok"):
                failures = min(failures + 1, 6)
                logger.warning("Telegram polling: %s", response.get("description", "unknown error"))
                SHUTDOWN_EVENT.wait(min(30, 2 ** failures))
                continue
            failures = 0
            for update in response.get("result", []):
                offset = int(update.get("update_id", offset)) + 1
                try:
                    monitor.handle_update(update)
                except Exception:
                    logger.exception("Ошибка обработки Telegram update")
        except Exception:
            failures = min(failures + 1, 6)
            logger.exception("Ошибка Telegram polling")
            SHUTDOWN_EVENT.wait(min(30, 2 ** failures))
    logger.info("Telegram polling остановлен")


def autoping_worker(monitor: Monitor) -> None:
    """Optional keep-alive ping for deployments that provide a public URL."""
    while monitor.running and not SHUTDOWN_EVENT.is_set():
        SHUTDOWN_EVENT.wait(240)
        if SHUTDOWN_EVENT.is_set():
            break
        url = (monitor.store.autoping_url or "").strip()
        if not url:
            continue
        try:
            target = url.rstrip("/") + "/ping"
            request = urllib.request.Request(target, headers={"User-Agent": DEFAULT_USER_AGENT})
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read(64)
        except Exception as exc:
            logger.debug("Autoping failed: %s", exc)


def validate_runtime() -> None:
    """Validate configuration values before opening sockets."""
    global PORT
    if not 1 <= int(PORT) <= 65535:
        raise ValueError("PORT должен быть в диапазоне 1..65535")
    PORT = int(PORT)
    if not CONFIG_PATH:
        raise ValueError("Не задан путь к файлу состояния")
    if not REPORT_PATH:
        raise ValueError("Не задан путь к HTML-отчету")


def shutdown_runtime(monitor: Monitor, threads: List[threading.Thread]) -> None:
    """Request a graceful stop and wait briefly for worker threads."""
    logger.info("Получен сигнал остановки")
    monitor.running = False
    SHUTDOWN_EVENT.set()
    server = SERVER_INSTANCE
    if server is not None:
        try:
            server.shutdown()
        except Exception:
            logger.debug("HTTP server уже остановлен")
    for thread in threads:
        if thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)
    logger.info("AniVox Monitor остановлен")


def main() -> None:
    """Canonical application entry point."""
    global ACTIVE_MONITOR
    validate_runtime()
    SHUTDOWN_EVENT.clear()

    store = Store(CONFIG_PATH)
    env_token = os.environ.get("BOT_TOKEN", "").strip()
    if env_token:
        store.token = env_token
    env_chat = os.environ.get("CHAT_ID", "").strip()
    if env_chat:
        store.chat_id = env_chat
    env_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if env_url:
        store.autoping_url = env_url
    store.save()

    monitor = Monitor(store)
    ACTIVE_MONITOR = monitor
    monitor.generate_html_report()

    workers = [
        threading.Thread(target=run_web_server, name="anivox-http", daemon=True),
        threading.Thread(target=monitor_worker, args=(monitor,), name="anivox-monitor", daemon=True),
        threading.Thread(target=telegram_polling_worker, args=(monitor,), name="anivox-telegram", daemon=True),
        threading.Thread(target=autoping_worker, args=(monitor,), name="anivox-autoping", daemon=True),
    ]
    for worker in workers:
        worker.start()

    logger.info("%s v%s запущен", APP_NAME, VERSION)
    logger.info("Файл состояния: %s", os.path.abspath(CONFIG_PATH))
    logger.info("HTML-отчет: %s", os.path.abspath(REPORT_PATH))
    logger.info("Порт панели: %s", PORT)
    if not store.token:
        logger.warning("BOT_TOKEN не задан: веб-панель работает, Telegram-бот ожидает токен.")
    if DASHBOARD_KEY:
        logger.info("Для панели включена защита DASHBOARD_KEY.")

    try:
        while not SHUTDOWN_EVENT.wait(1):
            if not any(worker.is_alive() for worker in workers):
                logger.error("Все рабочие потоки завершились")
                break
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_runtime(monitor, workers)


# =============================================================================
# Additional maintainability helpers and regression checks
# =============================================================================

def parse_profile_identifier(value: str) -> Optional[str]:
    """Extract a numeric AniVox profile identifier from supported input forms."""
    match = PROFILE_RE.search(str(value or "").strip())
    return match.group(1) if match else None


def normalize_presence(value: Any) -> str:
    """Normalize remote presence values to the three supported UI states."""
    raw = str(value or "").strip().lower()
    if raw in {"watching", "смотрит", "anime", "watch"}:
        return "Смотрит"
    if raw in {"online", "онлайн", "в сети", "true", "1"}:
        return "Онлайн"
    return "Оффлайн"


def safe_int(value: Any, default: int = 0, minimum: Optional[int] = None,
             maximum: Optional[int] = None) -> int:
    """Convert untrusted values to an integer and optionally clamp the result."""
    try:
        result = int(float(value))
    except (TypeError, ValueError, OverflowError):
        result = int(default)
    if minimum is not None:
        result = max(int(minimum), result)
    if maximum is not None:
        result = min(int(maximum), result)
    return result


def safe_float(value: Any, default: float = 0.0,
               minimum: Optional[float] = None,
               maximum: Optional[float] = None) -> float:
    """Convert untrusted values to a float and optionally clamp the result."""
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        result = float(default)
    if minimum is not None:
        result = max(float(minimum), result)
    if maximum is not None:
        result = min(float(maximum), result)
    return result


def format_local_timestamp(timestamp: Any, pattern: str = "%d.%m.%Y %H:%M:%S") -> str:
    """Format an epoch timestamp without allowing malformed data to crash a view."""
    value = safe_int(timestamp, 0, minimum=0)
    if not value:
        return "—"
    try:
        return datetime.fromtimestamp(value).strftime(pattern)
    except (OverflowError, OSError, ValueError):
        return "—"


def build_csv_export(history: List[Dict[str, Any]]) -> str:
    """Build a UTF-8 CSV representation suitable for an administrator export."""
    from io import StringIO
    output = StringIO()
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Время", "Профиль", "Статус", "Тайтл", "Подробности"])
    for item in history:
        writer.writerow([
            item.get("checked_at") or format_local_timestamp(item.get("timestamp") or item.get("ts")),
            item.get("nickname") or item.get("name") or item.get("profile_id") or item.get("pid") or "",
            item.get("presence") or item.get("event") or "",
            item.get("title") or "",
            item.get("changes") or item.get("details") or "",
        ])
    return "\ufeff" + output.getvalue()


def prune_history(history: List[Dict[str, Any]], max_items: int = 1000) -> List[Dict[str, Any]]:
    """Return the newest bounded history entries."""
    limit = safe_int(max_items, 1000, minimum=1, maximum=10000)
    return list(history[-limit:])


def summarize_profiles(profiles: Dict[str, Profile]) -> Dict[str, int]:
    """Count all supported profile states in one pass."""
    summary = {"total": len(profiles), "online": 0, "watching": 0, "offline": 0, "errors": 0}
    for profile in profiles.values():
        if not profile.last_ok:
            summary["errors"] += 1
            continue
        presence = (profile.last_status or {}).get("presence", "Оффлайн")
        if presence == "Смотрит":
            summary["watching"] += 1
            summary["online"] += 1
        elif presence == "Онлайн":
            summary["online"] += 1
        else:
            summary["offline"] += 1
    return summary


def validate_profile_url(url: str) -> bool:
    """Accept only HTTP(S) URLs on the known AniVox hosts."""
    try:
        parsed = urlsplit(str(url))
        return parsed.scheme in {"http", "https"} and parsed.hostname in {
            "anivox.fun", "www.anivox.fun", "anivox.ru", "anivox.org"
        }
    except Exception:
        return False


def redact_secret(value: str, visible: int = 4) -> str:
    """Mask a token before it is ever included in diagnostics."""
    text_value = str(value or "")
    if not text_value:
        return "(не задан)"
    if len(text_value) <= visible:
        return "*" * len(text_value)
    return "*" * (len(text_value) - visible) + text_value[-visible:]


def diagnostics_snapshot(monitor: Monitor) -> Dict[str, Any]:
    """Return non-secret diagnostics for support and troubleshooting."""
    store = monitor.store
    with store.lock:
        return {
            "app": APP_NAME,
            "version": VERSION,
            "profiles": len(store.profiles),
            "history_items": len(store.history),
            "subscriptions": len(store.anime_subs),
            "allowed_users": len(store.allowed_users),
            "telegram_configured": bool(store.token),
            "chat_configured": bool(store.chat_id),
            "monitor_paused": bool(monitor.paused),
            "interval_seconds": safe_int(store.settings.interval_seconds, DEFAULT_INTERVAL, 1, 3600),
            "state_file_exists": os.path.exists(store.path),
            "report_file_exists": os.path.exists(REPORT_PATH),
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        }


def check_store_integrity(store: Store) -> List[str]:
    """Return human-readable consistency warnings without changing user data."""
    warnings = []
    if not isinstance(store.profiles, dict):
        warnings.append("Хранилище профилей должно быть словарём.")
    if not isinstance(store.history, list):
        warnings.append("История должна быть списком.")
    if not isinstance(store.allowed_users, dict):
        warnings.append("Список разрешённых пользователей должен быть словарём.")
    for pid, profile in store.profiles.items():
        if str(pid) != str(profile.profile_id):
            warnings.append(f"Ключ профиля {pid} не совпадает с profile_id.")
        if not str(pid).isdigit():
            warnings.append(f"ID профиля {pid} не является числовым.")
    return warnings


def initialize_store_directories(store: Store) -> None:
    """Create parent directories for configured persistent paths."""
    for file_path in (store.path, REPORT_PATH, LOG_PATH):
        parent = os.path.dirname(os.path.abspath(file_path))
        if parent:
            os.makedirs(parent, exist_ok=True)


def export_state_backup(store: Store, destination: str) -> str:
    """Create a consistent JSON backup without exposing the bot token in logs."""
    with store.lock:
        data = {
            "version": VERSION,
            "created_at": datetime.now().astimezone().isoformat(),
            "state": {
                "allowed_users": store.allowed_users,
                "anime_cache": store.anime_cache,
                "anime_subs": store.anime_subs,
                "wall_messages": store.wall_messages,
                "online_snipers": store.online_snipers,
                "settings": asdict(store.settings),
                "user_settings": store.user_settings,
                "profiles": {key: asdict(value) for key, value in store.profiles.items()},
                "history": store.history,
            },
        }
    target = os.path.abspath(destination)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return target


def restore_state_backup(store: Store, backup_path: str) -> bool:
    """Restore supported application data from a backup file."""
    with open(backup_path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    state = raw.get("state") if isinstance(raw, dict) else None
    if not isinstance(state, dict):
        raise ValueError("Некорректная структура резервной копии")
    with store.lock:
        profiles = {}
        for pid, pdata in (state.get("profiles") or {}).items():
            if not isinstance(pdata, dict):
                continue
            profile_id = str(pdata.get("profile_id") or pid)
            allowed = {field.name for field in __import__("dataclasses").fields(Profile)}
            profiles[profile_id] = Profile(**{
                key: value for key, value in pdata.items() if key in allowed
            })
        store.profiles = profiles
        store.allowed_users = dict(state.get("allowed_users") or {})
        store.anime_cache = dict(state.get("anime_cache") or {})
        store.anime_subs = list(state.get("anime_subs") or [])
        store.wall_messages = list(state.get("wall_messages") or [])
        store.online_snipers = dict(state.get("online_snipers") or {})
        store.user_settings = dict(state.get("user_settings") or {})
        store.history = prune_history(list(state.get("history") or []), 1000)
        settings = state.get("settings") or {}
        for key, value in settings.items():
            if hasattr(store.settings, key):
                setattr(store.settings, key, value)
        store.save()
    return True


def run_self_tests() -> Dict[str, Any]:
    """Small offline smoke test suite for core helpers and report rendering."""
    tests = []
    tests.append(("profile id from url", parse_profile_identifier("https://anivox.fun/profile/123") == "123"))
    tests.append(("profile id from plain id", parse_profile_identifier("987") == "987"))
    tests.append(("reject invalid profile id", parse_profile_identifier("not-a-profile") is None))
    tests.append(("presence watching", normalize_presence("watching") == "Смотрит"))
    tests.append(("presence online", normalize_presence("online") == "Онлайн"))
    tests.append(("presence offline", normalize_presence("unknown") == "Оффлайн"))
    tests.append(("safe int clamp", safe_int("99", 0, 1, 10) == 10))
    tests.append(("safe float", safe_float("1.5") == 1.5))
    tests.append(("secret redaction", redact_secret("123456789") == "*****6789"))
    tests.append(("url validation", validate_profile_url("https://anivox.fun/profile/1")))
    tests.append(("reject external url", not validate_profile_url("https://example.com/")))
    tests.append(("history pruning", len(prune_history([{"i": x} for x in range(20)], 5)) == 5))
    failed = [name for name, passed in tests if not passed]
    return {"passed": len(tests) - len(failed), "total": len(tests), "failed": failed}


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        result = run_self_tests()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if not result["failed"] else 1)
    main()
