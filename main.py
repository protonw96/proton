#!/usr/bin/env python3
"""AniVox Monitor — компактная версия с рабочими кнопками и живым закреплённым табло."""
from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import signal
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

APP_NAME = "AniVox Monitor"
VERSION = "6.0.0"
BASE = Path(__file__).resolve().parent
CONFIG = BASE / "anivox_monitor.json"
PORT = int(os.environ.get("PORT", "15887"))
ALMATY = timezone(timedelta(hours=5), name="ALMT")
PROFILE_RE = re.compile(r"^https?://(?:www\.)?anivox\.fun/profile/(\d+)(?:[/?#].*)?$", re.I)
UA = f"AniVoxMonitor/{VERSION}"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(APP_NAME)

KEYBOARD = {
    "keyboard": [
        ["📊 Проверить всех", "📌 Табло"],
        ["👥 Мои профили", "➕ Добавить", "🗑 Удалить"],
        ["⚙️ Настройки", "🔕 Уведомления"],
        ["⏸ Пауза", "▶️ Продолжить"],
    ],
    "resize_keyboard": True,
}


def now_iso() -> str:
    return datetime.now(ALMATY).isoformat(timespec="seconds")


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def normalize_profile(value: str) -> tuple[str, str]:
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    normalized = urllib.parse.urlunparse(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))
    match = PROFILE_RE.fullmatch(normalized)
    if not match:
        raise ValueError("Укажите ссылку вида https://anivox.fun/profile/27788")
    return normalized, match.group(1)


def status_time(value: str) -> str:
    if not value:
        return "ещё не менялся"
    try:
        return datetime.fromisoformat(value).astimezone(ALMATY).strftime("%d.%m %H:%M:%S")
    except (ValueError, TypeError):
        return value


@dataclass
class Profile:
    url: str
    profile_id: str
    label: str = ""
    nickname: str = ""
    level: str = "?"
    status: str = "не проверялся"
    title: str = ""
    last_check: str = ""
    status_changed_at: str = ""
    last_error: str = ""
    muted_until: float = 0.0


class Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.data = self._read()
        self.token = str(self.data.get("token") or os.getenv("BOT_TOKEN", "")).strip()
        self.chat_id = str(self.data.get("chat_id") or os.getenv("CHAT_ID", "")).strip()
        self.allowed = {str(x) for x in self.data.get("allowed_users", [])}
        if self.chat_id:
            self.allowed.add(self.chat_id)
        self.board_id = int(self.data.get("board_message_id") or 0)
        self.interval = max(1, min(30, int(self.data.get("interval_minutes", 10) or 10)))
        self.paused = bool(self.data.get("paused", False))
        self.notifications = bool(self.data.get("notifications", True))
        self.profiles: dict[str, Profile] = {}
        for item in self.data.get("profiles", []):
            if isinstance(item, dict) and item.get("profile_id") and item.get("url"):
                fields = {k: item[k] for k in Profile.__dataclass_fields__ if k in item}
                p = Profile(**fields)
                self.profiles[p.profile_id] = p

    @staticmethod
    def _read() -> dict[str, Any]:
        try:
            value = json.loads(CONFIG.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            log.exception("Не удалось прочитать конфигурацию")
            return {}

    def save(self) -> None:
        with self.lock:
            value = {
                "token": self.token,
                "chat_id": self.chat_id,
                "allowed_users": sorted(self.allowed),
                "board_message_id": self.board_id,
                "interval_minutes": self.interval,
                "paused": self.paused,
                "notifications": self.notifications,
                "profiles": [asdict(p) for p in self.profiles.values()],
            }
            tmp = CONFIG.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, CONFIG)
            try:
                CONFIG.chmod(0o600)
            except OSError:
                pass


class Telegram:
    def __init__(self, token: str) -> None:
        self.base = f"https://api.telegram.org/bot{token}/"
        self.offset = 0

    def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        body = urllib.parse.urlencode({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v) for k, v in (payload or {}).items()}).encode()
        req = urllib.request.Request(self.base + method, data=body, headers={"User-Agent": UA}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"Telegram API {e.code}: {detail}") from e
        if not result.get("ok"):
            raise RuntimeError(str(result.get("description", "Telegram API error")))
        return result.get("result")

    def send(self, chat: str, text: str, markup: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.call("sendMessage", {"chat_id": chat, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": True, "reply_markup": markup or KEYBOARD})
        return result if isinstance(result, dict) else {}

    def edit(self, chat: str, message_id: int, text: str, markup: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat, "message_id": message_id, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": True}
        if markup is not None:
            payload["reply_markup"] = markup
        self.call("editMessageText", payload)

    def answer(self, callback_id: str, text: str = "") -> None:
        try:
            self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})
        except Exception:
            log.debug("Callback answer failed", exc_info=True)

    def pin(self, chat: str, message_id: int) -> None:
        try:
            self.call("pinChatMessage", {"chat_id": chat, "message_id": message_id, "disable_notification": True})
        except Exception:
            log.warning("Не удалось закрепить табло", exc_info=True)

    def updates(self) -> list[dict[str, Any]]:
        result = self.call("getUpdates", {"offset": self.offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]})
        updates = result if isinstance(result, list) else []
        if updates:
            self.offset = max(int(u.get("update_id", 0)) for u in updates if isinstance(u, dict)) + 1
        return updates


def read_exact(sock: socket.socket, count: int) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        block = sock.recv(count - len(buf))
        if not block:
            raise ConnectionError("WebSocket соединение закрыто")
        buf.extend(block)
    return bytes(buf)


def ws_send(sock: socket.socket, value: dict[str, Any]) -> None:
    data = json.dumps(value, separators=(",", ":")).encode()
    mask = os.urandom(4)
    size = len(data)
    if size < 126:
        header = bytes((0x81, 0x80 | size))
    elif size < 65536:
        header = bytes((0x81, 0x80 | 126)) + struct.pack("!H", size)
    else:
        header = bytes((0x81, 0x80 | 127)) + struct.pack("!Q", size)
    sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def ws_read(sock: socket.socket) -> str | None:
    chunks: list[bytes] = []
    while True:
        head = read_exact(sock, 2)
        opcode, masked, size = head[0] & 15, bool(head[1] & 128), head[1] & 127
        if size == 126:
            size = struct.unpack("!H", read_exact(sock, 2))[0]
        elif size == 127:
            size = struct.unpack("!Q", read_exact(sock, 8))[0]
        mask = read_exact(sock, 4) if masked else b""
        data = read_exact(sock, size)
        if masked:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if opcode == 8:
            return None
        if opcode == 9:
            sock.sendall(bytes((0x8A, len(data))) + data)
            continue
        if opcode in (0, 1):
            chunks.append(data)
            if head[0] & 128:
                return b"".join(chunks).decode("utf-8", "replace")


def fetch_profile(profile: Profile) -> tuple[str, str, str]:
    host = "anivox.fun"
    raw = socket.create_connection((host, 443), timeout=20)
    sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    sock.settimeout(20)
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (f"GET /api HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: https://{host}\r\nUser-Agent: {UA}\r\n\r\n")
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            response += sock.recv(4096)
            if len(response) > 65536:
                raise ConnectionError("WebSocket handshake слишком велик")
        if " 101 " not in response.decode("latin1", "replace").splitlines()[0]:
            raise ConnectionError("AniVox отклонил WebSocket подключение")
        ws_send(sock, {"type": "get_profile", "id": int(profile.profile_id)})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            message = ws_read(sock)
            if not message:
                continue
            try:
                parsed = json.loads(message)
            except json.JSONDecodeError:
                continue
            if parsed.get("type") != "get_profile":
                continue
            user = (parsed.get("data") or {}).get("user")
            if not isinstance(user, dict):
                raise ValueError("В ответе AniVox нет data.user")
            online = user.get("online", 0)
            try:
                is_online = float(online or 0) == 0
            except (TypeError, ValueError):
                is_online = True
            raw_status = (parsed.get("data") or {}).get("online_status", "")
            if isinstance(raw_status, dict):
                raw_status = raw_status.get("text", "")
            title = re.sub(r"^(смотрит|watching)\s*[:：-]?\s*", "", str(raw_status or ""), flags=re.I).strip() if is_online else ""
            status = f"Смотрит: {title}" if is_online and title else ("Онлайн" if is_online else "Оффлайн")
            return str(user.get("username") or "неизвестно"), str(user.get("level", "?")), status
        raise TimeoutError("Истёк таймаут ответа AniVox")
    finally:
        sock.close()


class Monitor:
    def __init__(self, store: Store, tg: Telegram) -> None:
        self.store, self.tg = store, tg
        self.stop_event = threading.Event()
        self.last_board_update = 0.0
        self.update_lock = threading.RLock()

    def board_text(self) -> str:
        rows = [f"📌 <b>ТАБЛО AniVox · {datetime.now(ALMATY).strftime('%H:%M:%S')} Алматы</b>"]
        if not self.store.profiles:
            rows.append("<i>Профилей пока нет. Добавьте профиль кнопкой «➕ Добавить».</i>")
        for index, p in enumerate(list(self.store.profiles.values()), 1):
            status = p.status or "не проверялся"
            icon = "🍿" if status.startswith("Смотрит:") else ("🟢" if status == "Онлайн" else "🔴")
            changed = f" · статус с {status_time(p.status_changed_at)}" if p.status_changed_at else ""
            checked = f" · проверка {status_time(p.last_check)}" if p.last_check else ""
            label = p.label or p.nickname or p.profile_id
            rows.append(f"<b>{index}. {esc(label)}</b>: {icon} {esc(status)}{changed}{checked}")
            if p.last_error:
                rows.append(f"   ⚠️ {esc(p.last_error[:120])}")
        rows.append(f"\n⏱ Интервал: {self.store.interval} мин. · {'⏸ Пауза' if self.store.paused else '▶️ Мониторинг активен'}")
        return "\n".join(rows)[:4000]

    def ensure_board(self) -> None:
        """Создаёт табло, если его нет, иначе редактирует существующее; закрепляет в чате владельца."""
        if not self.store.chat_id:
            return
        with self.update_lock:
            text = self.board_text()
            if self.store.board_id:
                try:
                    self.tg.edit(self.store.chat_id, self.store.board_id, text)
                    self.last_board_update = time.time()
                    return
                except Exception as exc:
                    log.info("Старое табло недоступно, создаю заново: %s", exc)
                    self.store.board_id = 0
            try:
                sent = self.tg.send(self.store.chat_id, text)
                mid = sent.get("message_id")
                if isinstance(mid, int):
                    self.store.board_id = mid
                    self.store.save()
                    self.tg.pin(self.store.chat_id, mid)
                    self.last_board_update = time.time()
            except Exception:
                log.exception("Не удалось создать/обновить табло")

    def check_all(self) -> None:
        changed = False
        with self.store.lock:
            profiles = list(self.store.profiles.values())
        for p in profiles:
            try:
                nickname, level, status = fetch_profile(p)
                checked = now_iso()
                if status != p.status:
                    p.status_changed_at = checked
                    changed = True
                    if self.store.notifications and p.muted_until <= time.time():
                        try:
                            self.tg.send(self.store.chat_id, f"🔔 <b>Изменение статуса</b>\n{esc(p.label or nickname)}: {esc(status)}\nВремя: {status_time(checked)} (Алматы)")
                        except Exception:
                            log.exception("Не удалось отправить уведомление о статусе")
                p.nickname, p.level, p.status, p.last_check, p.last_error = nickname, level, status, checked, ""
            except Exception as exc:
                p.last_check = now_iso()
                p.last_error = str(exc)[:300]
                log.warning("Ошибка проверки профиля %s: %s", p.profile_id, exc)
        self.store.save()
        # Обновляем доску после каждой проверки. Это также добавляет новые профили в то же сообщение.
        self.ensure_board()

    def settings_markup(self) -> dict[str, Any]:
        return {"inline_keyboard": [
            [{"text": f"⏱ Интервал: {self.store.interval} мин", "callback_data": "settings_noop"}],
            [{"text": f"{'✅ ' if self.store.interval == n else ''}{n} мин", "callback_data": f"interval:{n}"} for n in (1, 5, 10, 15, 20, 30)],
            [{"text": f"Уведомления: {'ВКЛ ✅' if self.store.notifications else 'ВЫКЛ ❌'}", "callback_data": "toggle_notifications"}],
            [{"text": f"Мониторинг: {'ПАУЗА ⏸' if not self.store.paused else 'ПРОДОЛЖИТЬ ▶️'}", "callback_data": "toggle_pause"}],
        ]}

    def show_settings(self, chat: str, message_id: int | None = None) -> None:
        text = "⚙️ <b>Настройки AniVox Monitor</b>\nНажмите кнопку — это сообщение и клавиатура обновятся сразу."
        markup = self.settings_markup()
        if message_id:
            try:
                self.tg.edit(chat, message_id, text, markup)
                return
            except Exception:
                log.debug("Settings edit failed", exc_info=True)
        sent = self.tg.send(chat, text, markup)
        # Remember settings message only for callback edit; it is not the pinned board.
        self.settings_message_id = sent.get("message_id")

    def callback(self, query: dict[str, Any]) -> None:
        callback_id = str(query.get("id", ""))
        message = query.get("message") or {}
        chat = str((message.get("chat") or {}).get("id", ""))
        sender = str((query.get("from") or {}).get("id", ""))
        data = str(query.get("data") or "")
        if sender not in self.store.allowed:
            self.tg.answer(callback_id, "Нет доступа")
            return
        msg_id = message.get("message_id")
        if data.startswith("interval:") and sender == self.store.chat_id:
            try:
                value = int(data.split(":", 1)[1])
                if value not in (1, 5, 10, 15, 20, 30):
                    raise ValueError
                self.store.interval = value
                self.store.save()
                self.tg.answer(callback_id, f"Интервал установлен: {value} мин.")
                if isinstance(msg_id, int):
                    self.show_settings(chat, msg_id)
                return
            except ValueError:
                self.tg.answer(callback_id, "Недопустимый интервал")
                return
        if data == "toggle_notifications" and sender == self.store.chat_id:
            self.store.notifications = not self.store.notifications
            self.store.save()
            self.tg.answer(callback_id, "Настройка уведомлений сохранена")
            if isinstance(msg_id, int):
                self.show_settings(chat, msg_id)
            return
        if data == "toggle_pause" and sender == self.store.chat_id:
            self.store.paused = not self.store.paused
            self.store.save()
            self.tg.answer(callback_id, "Мониторинг приостановлен" if self.store.paused else "Мониторинг возобновлён")
            if isinstance(msg_id, int):
                self.show_settings(chat, msg_id)
            self.ensure_board()
            return
        self.tg.answer(callback_id, "Готово")

    def add_profile(self, args: str) -> str:
        parts = args.split(maxsplit=1)
        if not parts:
            return "Пример: <code>/add https://anivox.fun/profile/27788 Друг</code>"
        url, pid = normalize_profile(parts[0])
        profile = self.store.profiles.get(pid) or Profile(url=url, profile_id=pid)
        profile.url = url
        if len(parts) > 1:
            profile.label = parts[1].strip()[:60]
        self.store.profiles[pid] = profile
        self.store.save()
        # Promptly update the same pinned board; the profile appears there without waiting for polling.
        self.ensure_board()
        threading.Thread(target=self.check_all, daemon=True).start()
        return f"✅ Профиль <code>{pid}</code> добавлен. Табло обновлено; проверка запущена."

    def handle_message(self, message: dict[str, Any]) -> None:
        chat = str((message.get("chat") or {}).get("id", ""))
        if not chat:
            return
        sender = str((message.get("from") or {}).get("id", chat))
        text = str(message.get("text") or "").strip()
        if sender not in self.store.allowed:
            # Keep the owner informed; never automatically grant a new user access.
            if self.store.chat_id:
                try:
                    self.tg.send(self.store.chat_id, f"👤 Новый пользователь запросил доступ: {esc(sender)}\nИмя: {esc((message.get('from') or {}).get('first_name', ''))}\nДобавить вручную: <code>/friends add {esc(sender)}</code>")
                except Exception:
                    log.exception("Failed to notify owner of new user")
            return
        owner = sender == self.store.chat_id
        if text in ("/start", "/help"):
            self.tg.send(chat, "🌌 <b>AniVox Monitor</b>\nКоманды: /add URL [метка], /remove ID, /check, /board, /settings.\nТабло закрепляется в чате владельца и автоматически обновляется.")
        elif text == "➕ Добавить" or text.startswith("/add"):
            args = text[4:].strip() if text.startswith("/add") else ""
            if not owner:
                self.tg.send(chat, "Добавлять профили может только владелец.")
            elif args:
                try:
                    self.tg.send(chat, self.add_profile(args))
                except ValueError as exc:
                    self.tg.send(chat, esc(exc))
            else:
                self.tg.send(chat, "Пришлите команду: <code>/add https://anivox.fun/profile/27788 Метка</code>")
        elif text.startswith("/remove") or text == "🗑 Удалить":
            if not owner:
                self.tg.send(chat, "Удалять профили может только владелец.")
            else:
                pid = text.split(maxsplit=1)[1].strip() if " " in text else ""
                if pid in self.store.profiles:
                    del self.store.profiles[pid]
                    self.store.save()
                    self.ensure_board()
                    self.tg.send(chat, f"🗑 Профиль <code>{esc(pid)}</code> удалён.")
                else:
                    buttons = [[{"text": f"🗑 {p.label or p.profile_id}", "callback_data": f"remove:{p.profile_id}"}] for p in self.store.profiles.values()]
                    self.tg.send(chat, "Выберите профиль:", {"inline_keyboard": buttons} if buttons else KEYBOARD)
        elif text in ("📊 Проверить всех", "/check"):
            if not owner:
                self.tg.send(chat, "Проверку может запустить только владелец.")
            else:
                self.tg.send(chat, "⏳ Проверяю профили; закреплённое табло обновится автоматически.")
                threading.Thread(target=self.check_all, daemon=True).start()
        elif text in ("📌 Табло", "/board"):
            if not owner:
                self.tg.send(chat, "Табло доступно владельцу.")
            else:
                self.ensure_board()
        elif text in ("⚙️ Настройки", "/settings", "🔕 Уведомления"):
            if owner:
                self.show_settings(chat)
            else:
                self.tg.send(chat, "Настройки доступны владельцу.")
        elif text in ("⏸ Пауза", "/pause"):
            if owner:
                self.store.paused = True
                self.store.save()
                self.ensure_board()
                self.tg.send(chat, "⏸ Мониторинг приостановлен.")
        elif text in ("▶️ Продолжить", "/resume"):
            if owner:
                self.store.paused = False
                self.store.save()
                self.ensure_board()
                self.tg.send(chat, "▶️ Мониторинг возобновлён.")
        elif text in ("👥 Мои профили", "/list"):
            if not self.store.profiles:
                self.tg.send(chat, "Профилей пока нет.")
            else:
                self.tg.send(chat, "👥 <b>Профили:</b>\n" + "\n".join(f"• <code>{esc(p.profile_id)}</code> — {esc(p.label or p.url)} — {esc(p.status)}" for p in self.store.profiles.values()))
        elif text.startswith("/friends add ") and owner:
            uid = text.split()[-1]
            self.store.allowed.add(uid)
            self.store.save()
            self.ensure_board()
            self.tg.send(chat, f"✅ Пользователь <code>{esc(uid)}</code> добавлен в список доступа.")
        elif text.startswith("/friends remove ") and owner:
            uid = text.split()[-1]
            if uid != self.store.chat_id:
                self.store.allowed.discard(uid)
                self.store.save()
            self.tg.send(chat, "Права доступа обновлены.")
        elif text:
            self.tg.send(chat, "Неизвестная команда. Нажмите /help.")

    def run(self) -> None:
        # Ensure board exists at startup and re-create it if it was manually deleted.
        self.ensure_board()
        next_check = 0.0
        while not self.stop_event.is_set():
            try:
                for update in self.tg.updates():
                    if "callback_query" in update:
                        self.callback(update["callback_query"])
                    elif isinstance(update.get("message"), dict):
                        self.handle_message(update["message"])
            except Exception:
                log.exception("Telegram polling error")
                self.stop_event.wait(3)
            now = time.time()
            if now - self.last_board_update >= 60:
                self.ensure_board()
            if not self.store.paused and now >= next_check and self.store.profiles:
                threading.Thread(target=self.check_all, daemon=True).start()
                next_check = now + self.store.interval * 60


def main() -> int:
    store = Store()
    if not store.token:
        store.token = input("Токен Telegram-бота: ").strip()
    if not store.chat_id:
        store.chat_id = input("Ваш Telegram chat ID: ").strip()
    store.allowed.add(store.chat_id)
    store.save()
    tg = Telegram(store.token)
    monitor = Monitor(store, tg)
    signal.signal(signal.SIGINT, lambda *_: monitor.stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: monitor.stop_event.set())
    try:
        me = tg.call("getMe")
        log.info("Бот запущен: @%s", (me or {}).get("username", "bot"))
        monitor.run()
    finally:
        store.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
