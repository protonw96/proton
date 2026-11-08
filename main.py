#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AniVox Monitor v5.5.0 — Complete Edition
- WebSocket + HTTP парсер профилей AniVox (с аватарками и тайтлами)
- Полностью рабочие inline-кнопки Telegram (Настройки, Тишина, Профили, Ждун серий)
- Адаптивный мобильный Веб-Дашборд (Серая / Фиолетовая / Зеленая аватарка)
- Живое обновление через /api/status + 2-я вкладка с аналитикой и инструментами
"""

import os
import re
import sys
import ssl
import json
import time
import base64
import socket
import hashlib
import threading
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==================== ГЛОБАЛЬНЫЕ КОНСТАНТЫ ====================
APP_NAME = "AniVox Monitor"
VERSION = "5.5.0"
CONFIG_PATH = os.environ.get("ANIVOX_CONFIG", "anivox_config.json")
PORT = int(os.environ.get("PORT", 8080))
DEFAULT_INTERVAL = 5
PROFILE_RE = re.compile(r"(?:anivox\.(?:fun|ru|org)/profile/|id=|^)(\d+)", re.IGNORECASE)
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; SM-A515F) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)

config_lock = threading.RLock()
user_states = {}  # {chat_id: {"mode": "wait_profile" | "wait_anime" | "wait_friend"}}


# ==================== РАБОТА С КОНФИГОМ ====================
def load_config():
    with config_lock:
        cfg = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception as e:
                print(f"[Config] Ошибка чтения {CONFIG_PATH}: {e}")

        cfg.setdefault("token", os.environ.get("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_БОТА"))
        cfg.setdefault("chat_id", os.environ.get("CHAT_ID", ""))
        cfg.setdefault("autoping_url", os.environ.get("RENDER_EXTERNAL_URL", ""))
        cfg.setdefault("profiles", {})
        cfg.setdefault("history", [])
        cfg.setdefault("users", {})
        cfg.setdefault("friends", {})
        cfg.setdefault("waits", {})
        cfg.setdefault("anime_cache", {})
        return cfg


def save_config(cfg):
    with config_lock:
        try:
            tmp_path = CONFIG_PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, CONFIG_PATH)
        except Exception as e:
            print(f"[Config] Ошибка сохранения: {e}")


def get_user_settings(cfg, uid):
    uid_str = str(uid)
    with config_lock:
        users = cfg.setdefault("users", {})
        u = users.setdefault(uid_str, {})
        s = u.setdefault("settings", {})
        s.setdefault("interval", DEFAULT_INTERVAL)
        s.setdefault("cleanup_min", 0)
        s.setdefault("silent_until", 0)
        s.setdefault("silent_sound", False)
        ntf = s.setdefault("notify", {})
        ntf.setdefault("onl", True)
        ntf.setdefault("off", True)
        ntf.setdefault("ttl", True)
        ntf.setdefault("err", False)
        ntf.setdefault("edit_mode", True)
        u.setdefault("last_msg_id", None)
        u.setdefault("cleanup_queue", [])
        return s


# ==================== TELEGRAM BOT API ====================
def tg_api(token, method, data=None):
    if not token or token == "ВСТАВЬ_ТОКЕН_БОТА":
        return {"ok": False, "error": "No token"}
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}
            )
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        if "message is not modified" in err_body:
            return {"ok": True}
        print(f"[TG HTTP Error] {method}: {e.code} {err_body}")
        return {"ok": False, "error": err_body}
    except Exception as e:
        print(f"[TG Error] {method}: {e}")
        return {"ok": False, "error": str(e)}


def answer_cb(token, cb_id, text="", alert=False):
    return tg_api(token, "answerCallbackQuery", {
        "callback_query_id": cb_id,
        "text": text,
        "show_alert": alert
    })


def send_or_edit(cfg, chat_id, text, reply_markup=None, force_new=False):
    token = cfg["token"]
    s = get_user_settings(cfg, chat_id)
    now = time.time()

    # Проверка режима тишины для автоматических уведомлений
    if not force_new and now < s.get("silent_until", 0):
        return None

    u = cfg["users"][str(chat_id)]
    edit_mode = s["notify"].get("edit_mode", True)
    last_mid = u.get("last_msg_id")

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": bool(s.get("silent_sound", False))
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    if edit_mode and last_mid and not force_new:
        edit_payload = dict(payload)
        edit_payload["message_id"] = last_mid
        res = tg_api(token, "editMessageText", edit_payload)
        if res.get("ok"):
            return last_mid

    res = tg_api(token, "sendMessage", payload)
    if res.get("ok") and "result" in res:
        mid = res["result"]["message_id"]
        u["last_msg_id"] = mid
        if s.get("cleanup_min", 0) > 0:
            u.setdefault("cleanup_queue", []).append({"mid": mid, "ts": now})
        save_config(cfg)
        return mid
    return None


def send_html_document(token, chat_id, filepath, caption=""):
    """Отправка сгенерированного index.html прямо в чат Telegram"""
    if not os.path.exists(filepath):
        return
    boundary = "----AniVoxBoundary" + str(int(time.time() * 1000))
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        with open(filepath, "rb") as f:
            file_bytes = f.read()
        parts = []
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode("utf-8"))
        if caption:
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode("utf-8"))
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML\r\n".encode("utf-8"))
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"anivox_dashboard.html\"\r\n"
            f"Content-Type: text/html\r\n\r\n".encode("utf-8")
        )
        parts.append(file_bytes)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT
        })
        urllib.request.urlopen(req, timeout=25).read()
    except Exception as e:
        print(f"[SendDoc Error]: {e}")


# ==================== СКРАПЕР ANIVOX (WEBSOCKET + HTTP) ====================
def make_svg_avatar(name):
    """Генерация красивой встроенной SVG-аватарки, если у профиля нет фото"""
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


def anivox_websocket_profile(pid, timeout=6):
    """Попытка получить live-данные профиля через сырой WebSocket-хендшейк"""
    hosts = [("anivox.fun", 443, f"/ws/profile/{pid}")]
    for host, port, path in hosts:
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            raw_sock = socket.create_connection((host, port), timeout=timeout)
            ssock = ctx.wrap_socket(raw_sock, server_hostname=host)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            handshake = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"User-Agent: {USER_AGENT}\r\n\r\n"
            )
            ssock.sendall(handshake.encode("utf-8"))
            resp = ssock.recv(4096).decode("utf-8", errors="ignore")
            if "101 Switching Protocols" not in resp:
                ssock.close()
                continue
            frame = ssock.recv(8192)
            ssock.close()
            if len(frame) > 2:
                length = frame[1] & 127
                offset = 2
                if length == 126:
                    offset = 4
                elif length == 127:
                    offset = 10
                payload = frame[offset:].decode("utf-8", errors="ignore")
                data = json.loads(payload)
                if isinstance(data, dict):
                    return data
        except Exception:
            continue
    return None


def fetch_profile(pid):
    """Комплексный парсер профиля AniVox (WebSocket -> API -> HTML)"""
    pid_str = str(pid).strip()
    ws_data = anivox_websocket_profile(pid_str)
    if ws_data and ("online" in ws_data or "nickname" in ws_data or "username" in ws_data):
        name = ws_data.get("nickname") or ws_data.get("username") or ws_data.get("name") or f"User #{pid_str}"
        online = bool(ws_data.get("online") or ws_data.get("is_online"))
        title = ws_data.get("watching") or ws_data.get("current_title") or ws_data.get("anime") or ""
        avatar = ws_data.get("avatar") or ws_data.get("photo") or ""
        if avatar and avatar.startswith("/"):
            avatar = "https://anivox.fun" + avatar
        return {
            "id": pid_str,
            "nickname": str(name),
            "online": online,
            "watching": bool(title and title.lower() not in ("нет", "none", "ничего не смотрит")),
            "current_title": title if title else "Ничего не смотрит",
            "avatar": avatar if avatar else make_svg_avatar(name),
            "ok": True
        }

    # HTTP API / HTML Fallback
    urls = [
        f"https://anivox.fun/api/profile/{pid_str}",
        f"https://anivox.fun/api/users/{pid_str}",
        f"https://anivox.fun/profile/{pid_str}"
    ]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for url in urls:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/html;q=0.9,*/*;q=0.8"
            })
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")

            # Если пришел JSON
            if raw.strip().startswith("{"):
                data = json.loads(raw)
                u = data.get("user") or data.get("profile") or data
                name = u.get("nickname") or u.get("username") or u.get("name") or f"User #{pid_str}"
                online = bool(u.get("online") or u.get("is_online") or u.get("status") == "online")
                title = u.get("watching_title") or u.get("current_title") or u.get("watching") or ""
                if isinstance(title, dict):
                    title = title.get("title") or title.get("name_ru") or title.get("name") or ""
                avatar = u.get("avatar") or u.get("avatar_url") or u.get("image") or ""
                if avatar and avatar.startswith("/"):
                    avatar = "https://anivox.fun" + avatar
                return {
                    "id": pid_str,
                    "nickname": str(name),
                    "online": online or bool(title),
                    "watching": bool(title),
                    "current_title": str(title) if title else "Ничего не смотрит",
                    "avatar": avatar if avatar else make_svg_avatar(name),
                    "ok": True
                }

            # Парсинг HTML-страницы профиля
            name_m = re.search(r'<h1[^>]*>(.*?)</h1>|<title>(.*?)</title>', raw, re.I | re.S)
            name = f"User #{pid_str}"
            if name_m:
                raw_name = (name_m.group(1) or name_m.group(2) or "").strip()
                raw_name = re.sub(r"<[^>]+>", "", raw_name).replace("— AniVox", "").replace("| AniVox", "").strip()
                if raw_name:
                    name = raw_name[:35]

            ava_m = re.search(r'<img[^>]+class="[^"]*avatar[^"]*"[^>]+src="([^"]+)"', raw, re.I)
            if not ava_m:
                ava_m = re.search(r'property="og:image"\s+content="([^"]+)"', raw, re.I)
            avatar = ava_m.group(1) if ava_m else ""
            if avatar and avatar.startswith("/"):
                avatar = "https://anivox.fun" + avatar

            watch_m = re.search(r'(?:Смотрит сейчас|Сейчас смотрит|watching)[^<]*<[^>]+>([^<]+)<', raw, re.I)
            title = watch_m.group(1).strip() if watch_m else ""

            is_online = bool(
                title or re.search(r'(?:в сети|online|status-online|is-online)', raw, re.I)
            ) and not bool(re.search(r'(?:не в сети|оффлайн|offline)', raw, re.I))

            return {
                "id": pid_str,
                "nickname": name,
                "online": is_online,
                "watching": bool(title),
                "current_title": title if title else "Ничего не смотрит",
                "avatar": avatar if avatar else make_svg_avatar(name),
                "ok": True
            }
        except Exception:
            continue

    return {"id": pid_str, "ok": False}


# ==================== ПОИСК АНИМЕ SHIKIMORI (ДЛЯ ЖДУНА СЕРИЙ) ====================
def search_shikimori(query):
    url = "https://shikimori.one/api/animes? search=" + urllib.parse.quote(query) + "&limit=5"
    url = url.replace("? search=", "?search=")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[Shikimori Error]: {e}")
        return []


# ==================== ИНЛАЙН-КЛАВИАТУРЫ TELEGRAM ====================
def kb_main_menu(cfg, uid):
    s = get_user_settings(cfg, uid)
    is_muted = s.get("silent_until", 0) > time.time()
    mute_btn = "🔇 Тишина: ВКЛ" if is_muted else "🔔 Тишина: ВЫКЛ"
    return {"inline_keyboard": [
        [{"text": "📊 Статус профилей", "callback_data": "menu_status"},
         {"text": "🌐 Веб-Отчет (HTML)", "callback_data": "menu_web"}],
        [{"text": "⚙️ Настройки", "callback_data": "menu_settings"},
         {"text": mute_btn, "callback_data": "menu_silent"}],
        [{"text": "👥 Профили и Друзья", "callback_data": "menu_profiles"},
         {"text": "🎬 Ждун серий", "callback_data": "menu_waits"}],
        [{"text": "🔄 Принудительная проверка", "callback_data": "force_check"}]
    ]}


def kb_settings_menu(cfg, uid):
    s = get_user_settings(cfg, uid)
    ntf = s["notify"]
    cur_int = s.get("interval", 5)
    cur_cln = s.get("cleanup_min", 0)

    return {"inline_keyboard": [
        [{"text": f"{'✅' if ntf['onl'] else '❌'} Онлайн", "callback_data": "ntf_onl"},
         {"text": f"{'✅' if ntf['off'] else '❌'} Оффлайн", "callback_data": "ntf_off"}],
        [{"text": f"{'✅' if ntf['ttl'] else '❌'} Смена тайтла", "callback_data": "ntf_ttl"},
         {"text": f"{'✅' if ntf['err'] else '❌'} Ошибки сети", "callback_data": "ntf_err"}],
        [{"text": f"📝 Режим: {'Live-Редакт.' if ntf['edit_mode'] else 'Новые сообщения'}", "callback_data": "ntf_edit_mode"}],
        [{"text": "✅ Включить все", "callback_data": "ntf_all_on"},
         {"text": "❌ Выключить все", "callback_data": "ntf_all_off"}],
        [
            {"text": f"{'🟢 ' if cur_int == 1 else ''}1с", "callback_data": "set_int_1"},
            {"text": f"{'🟢 ' if cur_int == 5 else ''}5с", "callback_data": "set_int_5"},
            {"text": f"{'🟢 ' if cur_int == 10 else ''}10с", "callback_data": "set_int_10"},
            {"text": f"{'🟢 ' if cur_int == 30 else ''}30с", "callback_data": "set_int_30"}
        ],
        [
            {"text": f"{'🧹 ' if cur_cln == 5 else ''}5м", "callback_data": "set_cln_5"},
            {"text": f"{'🧹 ' if cur_cln == 15 else ''}15м", "callback_data": "set_cln_15"},
            {"text": f"{'🧹 ' if cur_cln == 30 else ''}30м", "callback_data": "set_cln_30"},
            {"text": f"{'♾️ ' if cur_cln == 0 else ''}Не удалять", "callback_data": "set_cln_0"}
        ],
        [{"text": "🔙 Назад в главное меню", "callback_data": "menu_main"}]
    ]}


def kb_silent_menu(cfg, uid):
    s = get_user_settings(cfg, uid)
    now = time.time()
    rem = int((s.get("silent_until", 0) - now) / 60)
    snd_txt = "🔕 Без звука в ТГ: ДА" if s.get("silent_sound") else "🔊 Без звука в ТГ: НЕТ"
    status_line = f"⏳ Тишина еще {rem} мин." if rem > 0 else "🟢 Все уведомления активны"

    return {"inline_keyboard": [
        [{"text": status_line, "callback_data": "silent_info"}],
        [{"text": "🔇 На 30 мин", "callback_data": "silent_30"},
         {"text": "🔇 На 1 час", "callback_data": "silent_60"}],
        [{"text": "🔇 На 3 часа", "callback_data": "silent_180"},
         {"text": "🌙 На 8 часов (Сон)", "callback_data": "silent_480"}],
        [{"text": snd_txt, "callback_data": "silent_toggle_sound"}],
        [{"text": "🔔 Выключить режим тишины", "callback_data": "silent_0"}],
        [{"text": "🔙 Назад в главное меню", "callback_data": "menu_main"}]
    ]}


def kb_profiles_menu(cfg):
    rows = [
        [{"text": "➕ Добавить профиль AniVox", "callback_data": "prof_add"},
         {"text": "🤝 Добавить друга (TG ID)", "callback_data": "fr_add"}]
    ]
    for pid, p in cfg.get("profiles", {}).items():
        name = p.get("nickname", f"ID {pid}")
        st = "🟣" if p.get("watching") else ("🟢" if p.get("online") else "⚪")
        rows.append([{"text": f"{st} {name} (#{pid}) — 🗑 Удалить", "callback_data": f"rm_prof_{pid}"}])

    for fid, fname in cfg.get("friends", {}).items():
        rows.append([{"text": f"🤝 Друг: {fname} ({fid}) — ❌", "callback_data": f"rm_fr_{fid}"}])

    rows.append([{"text": "🔙 Назад в главное меню", "callback_data": "menu_main"}])
    return {"inline_keyboard": rows}


def kb_waits_menu(cfg):
    rows = [[{"text": "🔍 Найти и добавить аниме в Ждун", "callback_data": "wait_add"}]]
    for aid, w in cfg.get("waits", {}).items():
        title = w.get("title", f"Anime #{aid}")
        ep = w.get("episodes_aired", 0)
        rows.append([{"text": f"🎬 {title} ({ep} сер.) — 🗑", "callback_data": f"delwait_{aid}"}])
    rows.append([{"text": "🔙 Назад в главное меню", "callback_data": "menu_main"}])
    return {"inline_keyboard": rows}


# ==================== ФОРМАТИРОВАНИЕ СТАТУСА В ТЕЛЕГРАМ ====================
def format_status_text(cfg):
    profiles = cfg.get("profiles", {})
    if not profiles:
        return (
            "📡 <b>Мониторинг AniVox</b>\n\n"
            "Список профилей пока пуст.\n"
            "Нажми <b>«👥 Профили и Друзья» → «➕ Добавить профиль»</b> или просто отправь боту ID/ссылку на профиль!"
        )

    lines = [f"📡 <b>Сводка AniVox Monitor v{VERSION}</b>\n"]
    now = int(time.time())
    for pid, p in profiles.items():
        name = p.get("nickname", f"User #{pid}")
        is_onl = p.get("online", False)
        is_wat = p.get("watching", False)
        title = p.get("current_title", "Ничего не смотрит")
        chg_ts = p.get("last_status_change", now)
        dt_str = datetime.fromtimestamp(chg_ts).strftime("%d.%m.%Y %H:%M:%S")
        dur_min = max(0, (now - chg_ts) // 60)

        if is_wat and title != "Ничего не смотрит":
            icon = "🟣 <b>СМОТРИТ АНИМЕ</b>"
        elif is_onl:
            icon = "🟢 <b>В СЕТИ</b>"
        else:
            icon = "⚪ <b>ОФФЛАЙН</b>"

        lines.append(
            f"👤 <b>{name}</b> (<code>#{pid}</code>) — {icon}\n"
            f"🎬 Тайтл: <i>{title}</i>\n"
            f"🕒 Изменено: <code>{dt_str}</code> ({dur_min} мин. назад)\n"
        )
    return "\n".join(lines)


# ==================== ОБРАБОТЧИК ВСЕХ INLINE-КНОПОК И КОМАНД ====================
def handle_callback_query(cfg, cb):
    token = cfg["token"]
    cb_id = cb["id"]
    data = cb.get("data", "")
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")
    uid = cb.get("from", {}).get("id", chat_id)

    s = get_user_settings(cfg, uid)
    ntf = s["notify"]

    if data == "cancel_input":
        user_states.pop(chat_id, None)
        answer_cb(token, cb_id, "Действие отменено")
        data = "menu_main"

    if data == "menu_main":
        answer_cb(token, cb_id)
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": msg_id,
            "text": f"🎛 <b>Главное меню {APP_NAME} v{VERSION}</b>\nВыбери нужный раздел:",
            "parse_mode": "HTML", "reply_markup": kb_main_menu(cfg, uid)
        })
        return

    if data == "menu_status":
        answer_cb(token, cb_id, "Загружаю статусы...")
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": msg_id,
            "text": format_status_text(cfg),
            "parse_mode": "HTML", "reply_markup": kb_main_menu(cfg, uid)
        })
        return

    if data == "menu_web":
        answer_cb(token, cb_id, "Генерирую мобильный веб-отчет...")
        path = generate_html_report(cfg)
        url_line = f"\n🌐 Ссылка: {cfg['autoping_url']}" if cfg.get("autoping_url") else f"\n🌐 Локальный сервер: http://127.0.0.1:{PORT}"
        send_html_document(token, chat_id, path, caption=f"📊 <b>Актуальный Веб-Дашборд AniVox</b>{url_line}")
        return

    if data == "menu_settings":
        answer_cb(token, cb_id)
        txt = (
            "⚙️ <b>Настройки мониторинга и уведомлений</b>\n\n"
            f"• Интервал опроса: <b>{s['interval']} сек.</b>\n"
            f"• Автоочистка чата: <b>{'Выкл' if s['cleanup_min'] == 0 else str(s['cleanup_min']) + ' мин.'}</b>\n"
            f"• Доставка: <b>{'Live-редактирование' if ntf['edit_mode'] else 'Новые сообщения'}</b>"
        )
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": msg_id,
            "text": txt, "parse_mode": "HTML", "reply_markup": kb_settings_menu(cfg, uid)
        })
        return

    if data == "menu_silent":
        answer_cb(token, cb_id)
        until = s.get("silent_until", 0)
        now = time.time()
        st = f"🔇 <b>Активен до {datetime.fromtimestamp(until).strftime('%H:%M:%S')}</b>" if until > now else "🔔 <b>Выключен (уведомления идут)</b>"
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": msg_id,
            "text": f"🌙 <b>Режим «Тишина»</b>\nСтатус: {st}\n\nВ режиме тишины скрапер продолжает писать всю историю и обновлять сайт.",
            "parse_mode": "HTML", "reply_markup": kb_silent_menu(cfg, uid)
        })
        return

    if data == "menu_profiles":
        answer_cb(token, cb_id)
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": msg_id,
            "text": "👥 <b>Управление профилями AniVox и друзьями</b>\nНажми на профиль для удаления или добавь новый:",
            "parse_mode": "HTML", "reply_markup": kb_profiles_menu(cfg)
        })
        return

    if data == "menu_waits":
        answer_cb(token, cb_id)
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": msg_id,
            "text": "🎬 <b>Ждун выхода новых серий</b>\nБот автоматически проверяет выход серий и присылает оповещение:",
            "parse_mode": "HTML", "reply_markup": kb_waits_menu(cfg)
        })
        return

    if data == "force_check":
        answer_cb(token, cb_id, "🔄 Запущена внеочередная проверка профилей!")
        threading.Thread(target=check_all_profiles_once, args=(cfg, True, chat_id), daemon=True).start()
        return

    # Переключатели уведомлений
    if data.startswith("ntf_"):
        key = data.replace("ntf_", "")
        if key in ("onl", "off", "ttl", "err", "edit_mode"):
            ntf[key] = not ntf[key]
            save_config(cfg)
            answer_cb(token, cb_id, "✅ Настройка изменена")
        elif key == "all_on":
            for k in ("onl", "off", "ttl", "err"):
                ntf[k] = True
            save_config(cfg)
            answer_cb(token, cb_id, "✅ Все уведомления включены")
        elif key == "all_off":
            for k in ("onl", "off", "ttl", "err"):
                ntf[k] = False
            save_config(cfg)
            answer_cb(token, cb_id, "❌ Все уведомления выключены")
        handle_callback_query(cfg, {**cb, "data": "menu_settings"})
        return

    # Интервал опроса
    if data.startswith("set_int_"):
        val = int(data.replace("set_int_", ""))
        s["interval"] = val
        save_config(cfg)
        answer_cb(token, cb_id, f"⏱ Интервал: {val} сек.")
        handle_callback_query(cfg, {**cb, "data": "menu_settings"})
        return

    # Таймер очистки
    if data.startswith("set_cln_"):
        val = int(data.replace("set_cln_", ""))
        s["cleanup_min"] = val
        save_config(cfg)
        answer_cb(token, cb_id, "♾ Очистка выключена" if val == 0 else f"🧹 Очистка: {val} мин.")
        handle_callback_query(cfg, {**cb, "data": "menu_settings"})
        return

    # Режим тишины
    if data.startswith("silent_"):
        act = data.replace("silent_", "")
        if act == "info":
            answer_cb(token, cb_id, "Выбери время ниже 👇")
            return
        elif act == "toggle_sound":
            s["silent_sound"] = not s.get("silent_sound", False)
            save_config(cfg)
            answer_cb(token, cb_id, "Звук уведомлений переключен")
        elif act.isdigit():
            mins = int(act)
            s["silent_until"] = 0 if mins == 0 else time.time() + mins * 60
            save_config(cfg)
            answer_cb(token, cb_id, "🔔 Тишина снята!" if mins == 0 else f"🔇 Тишина на {mins} мин.")
        handle_callback_query(cfg, {**cb, "data": "menu_silent"})
        return

    # Добавление и удаление профилей / друзей
    if data == "prof_add":
        user_states[chat_id] = {"mode": "wait_profile"}
        answer_cb(token, cb_id)
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": msg_id,
            "text": "➕ <b>Отправь ссылку на профиль AniVox или просто его числовой ID</b> (например: <code>1245</code>):",
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_input"}]]}
        })
        return

    if data == "fr_add":
        user_states[chat_id] = {"mode": "wait_friend"}
        answer_cb(token, cb_id)
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": msg_id,
            "text": "🤝 <b>Отправь Telegram ID друга и его имя через пробел</b>\nПример: <code>123456789 Друг</code>",
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_input"}]]}
        })
        return

    if data.startswith("rm_prof_"):
        pid = data.replace("rm_prof_", "")
        with config_lock:
            cfg.get("profiles", {}).pop(pid, None)
            save_config(cfg)
        generate_html_report(cfg)
        answer_cb(token, cb_id, f"🗑 Профиль #{pid} удален")
        handle_callback_query(cfg, {**cb, "data": "menu_profiles"})
        return

    if data.startswith("rm_fr_"):
        fid = data.replace("rm_fr_", "")
        with config_lock:
            cfg.get("friends", {}).pop(fid, None)
            save_config(cfg)
        answer_cb(token, cb_id, "🗑 Друг удален")
        handle_callback_query(cfg, {**cb, "data": "menu_profiles"})
        return

    # Ждун серий
    if data == "wait_add":
        user_states[chat_id] = {"mode": "wait_anime"}
        answer_cb(token, cb_id)
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": msg_id,
            "text": "🔍 <b>Напиши название аниме для отслеживания серий:</b>",
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_input"}]]}
        })
        return

    if data.startswith("sub|"):
        aid = data.split("|")[1]
        cached = cfg.get("anime_cache", {}).get(aid)
        if cached:
            cfg.setdefault("waits", {})[aid] = cached
            save_config(cfg)
            answer_cb(token, cb_id, f"✅ Добавлено: {cached['title']}")
        else:
            answer_cb(token, cb_id, "Аниме добавлено в список")
        handle_callback_query(cfg, {**cb, "data": "menu_waits"})
        return

    if data.startswith("delwait_"):
        aid = data.replace("delwait_", "")
        cfg.get("waits", {}).pop(aid, None)
        save_config(cfg)
        answer_cb(token, cb_id, "🗑 Удалено из Ждуна")
        handle_callback_query(cfg, {**cb, "data": "menu_waits"})
        return


def handle_update(cfg, upd):
    token = cfg["token"]
    if "callback_query" in upd:
        handle_callback_query(cfg, upd["callback_query"])
        return

    msg = upd.get("message")
    if not msg:
        return
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not text:
        return

    if not cfg.get("chat_id"):
        cfg["chat_id"] = str(chat_id)
        save_config(cfg)

    get_user_settings(cfg, chat_id)

    # Проверка активного состояния ввода
    st = user_states.get(chat_id)
    if st and not text.startswith("/"):
        mode = st.get("mode")
        user_states.pop(chat_id, None)

        if mode == "wait_profile":
            m = PROFILE_RE.search(text)
            if not m:
                tg_api(token, "sendMessage", {"chat_id": chat_id, "text": "❌ Не удалось распознать ID профиля.", "reply_markup": kb_profiles_menu(cfg)})
                return
            pid = m.group(1)
            info = fetch_profile(pid)
            now = int(time.time())
            with config_lock:
                cfg["profiles"][pid] = {
                    "id": pid,
                    "nickname": info.get("nickname", f"User #{pid}"),
                    "online": info.get("online", False),
                    "watching": info.get("watching", False),
                    "current_title": info.get("current_title", "Ничего не смотрит"),
                    "avatar": info.get("avatar", make_svg_avatar(f"U{pid}")),
                    "last_status_change": now,
                    "prev_status": "Добавлен в мониторинг",
                    "online_seconds": 0,
                    "titles_watched": 1 if info.get("watching") else 0
                }
                cfg["history"].append({
                    "ts": now, "pid": pid, "name": cfg["profiles"][pid]["nickname"],
                    "event": "Смотрит" if info.get("watching") else ("Онлайн" if info.get("online") else "Оффлайн"),
                    "details": f"Профиль добавлен ({cfg['profiles'][pid]['current_title']})"
                })
                save_config(cfg)
            generate_html_report(cfg)
            tg_api(token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"✅ Профиль <b>{cfg['profiles'][pid]['nickname']}</b> (<code>#{pid}</code>) добавлен в мониторинг!",
                "parse_mode": "HTML",
                "reply_markup": kb_profiles_menu(cfg)
            })
            return

        if mode == "wait_friend":
            parts = text.split(maxsplit=1)
            fid = parts[0]
            fname = parts[1] if len(parts) > 1 else f"Friend {fid}"
            cfg.setdefault("friends", {})[fid] = fname
            save_config(cfg)
            tg_api(token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"🤝 Друг <b>{fname}</b> (<code>{fid}</code>) успешно добавлен!",
                "parse_mode": "HTML",
                "reply_markup": kb_profiles_menu(cfg)
            })
            return

        if mode == "wait_anime":
            results = search_shikimori(text)
            if not results:
                tg_api(token, "sendMessage", {"chat_id": chat_id, "text": "😕 Ничего не найдено на Shikimori.", "reply_markup": kb_waits_menu(cfg)})
                return
            kb = []
            for a in results[:5]:
                aid = str(a["id"])
                ru = a.get("russian") or a.get("name")
                eps = a.get("episodes_aired") or 0
                cfg.setdefault("anime_cache", {})[aid] = {"id": aid, "title": ru, "episodes_aired": eps}
                kb.append([{"text": f"➕ {ru} ({eps} эп.)", "callback_data": f"sub|{aid}"}])
            save_config(cfg)
            kb.append([{"text": "🔙 Назад", "callback_data": "menu_waits"}])
            tg_api(token, "sendMessage", {
                "chat_id": chat_id,
                "text": "🎬 <b>Выбери тайтл для отслеживания новых серий:</b>",
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": kb}
            })
            return

    # Команды
    if text.startswith("/start") or text.startswith("/menu"):
        tg_api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": (
                f"👋 Привет! Это <b>{APP_NAME} v{VERSION}</b>\n\n"
                "• Все инлайн-кнопки работают мгновенно\n"
                "• На сайте аватарки меняют цвет: ⚪ Серая (оффлайн), 🟣 Фиолетовая (смотрит аниме), 🟢 Зеленая (в сети)\n"
                "• Доступно точное время смены статуса и 2-я вкладка с аналитикой!"
            ),
            "parse_mode": "HTML",
            "reply_markup": kb_main_menu(cfg, chat_id)
        })
        return

    if text.startswith("/web"):
        path = generate_html_report(cfg)
        send_html_document(token, chat_id, path, caption="📊 <b>Веб-дашборд AniVox Monitor</b>")
        return

    # Быстрое добавление профиля просто отправкой ссылки
    m = PROFILE_RE.search(text)
    if m and ("anivox" in text.lower() or text.isdigit()):
        user_states[chat_id] = {"mode": "wait_profile"}
        handle_update(cfg, upd)


# ==================== ГЕНЕРАТОР АДАПТИВНОГО САЙТА (2 ВКЛАДКИ + ЦВЕТНЫЕ АВАТАРКИ) ====================
def build_api_payload(cfg):
    """Формирует JSON-слепок для live-обновления сайта на телефоне без перезагрузки"""
    return {
        "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "now_ts": int(time.time()),
        "profiles": cfg.get("profiles", {}),
        "history": cfg.get("history", [])[-120:]
    }


def generate_html_report(cfg, output_path="index.html"):
    with config_lock:
        profiles = cfg.get("profiles", {})
        history = cfg.get("history", [])
        now_ts = int(time.time())
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        hourly_activity = [0] * 24
        title_counts = {}
        for h in history:
            ts = h.get("ts", 0)
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
            name = pdata.get("nickname") or f"ID #{pid}"
            is_onl = bool(pdata.get("online", False))
            cur_title = pdata.get("current_title") or "Ничего не смотрит"
            is_wat = bool(pdata.get("watching", False) or (is_onl and cur_title != "Ничего не смотрит"))

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
            chg_ts = pdata.get("last_status_change") or now_ts
            chg_dt_str = datetime.fromtimestamp(chg_ts).strftime("%d.%m.%Y в %H:%M:%S")
            prev_st = pdata.get("prev_status") or "Нет данных"
            onl_sec = pdata.get("online_seconds", 0)
            onl_hours_str = f"{onl_sec // 3600}ч {(onl_sec % 3600) // 60}м"
            watched_cnt = pdata.get("titles_watched", 0)

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
                        <span class="t-lbl">⏳ Длительность статуса:</span>
                        <span class="t-val live-timer" data-ts="{chg_ts}">считаем...</span>
                    </div>
                    <div class="time-item">
                        <span class="t-lbl">⏮ Предыдущее:</span>
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
                    <span>⏱ {onl_hours_str} онлайн • 🎬 {watched_cnt} тайтл(ов)</span>
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
            ts = item.get("ts", now_ts)
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
            --accent: #6366f1;
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

        /* Шапка */
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

        /* Вкладки */
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
            transition: 0.2s;
        }}
        .tab-btn.active {{
            background: linear-gradient(135deg, #6366f1, #a855f7);
            color: #fff;
            border-color: transparent;
            box-shadow: 0 4px 16px rgba(168, 85, 247, 0.35);
        }}
        .tab-pane {{ display: none; }}
        .tab-pane.active {{ display: block; }}

        /* Счетчики статусов */
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

        /* Фильтр-чипсы на 1-й вкладке */
        .filter-row {{
            display: flex;
            gap: 6px;
            overflow-x: auto;
            scrollbar-width: none;
            margin-bottom: 12px;
            padding-bottom: 2px;
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

        /* Карточки профилей и 3 ЦВЕТА АВАТАРОК */
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
            transition: transform 0.2s, border-color 0.2s;
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
            transition: filter 0.3s ease;
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

        /* 1. СЕРАЯ АВАТАРКА (ОФФЛАЙН) */
        .ava-wrap.state-offline {{
            background: #334155;
            box-shadow: none;
        }}
        .ava-wrap.state-offline .ava-img {{
            filter: grayscale(100%) contrast(0.95) opacity(0.72);
        }}
        .ava-wrap.state-offline .ava-dot {{
            background: #64748b;
        }}

        /* 2. ФИОЛЕТОВАЯ АВАТАРКА (СМОТРИТ АНИМЕ) */
        .ava-wrap.state-watching {{
            background: linear-gradient(135deg, #a855f7, #ec4899);
            box-shadow: 0 0 16px var(--purple-glow);
        }}
        .ava-wrap.state-watching .ava-dot {{
            background: #a855f7;
            box-shadow: 0 0 8px #a855f7;
            animation: pulseDot 1.6s infinite;
        }}

        /* 3. ЗЕЛЕНАЯ АВАТАРКА (ОНЛАЙН) */
        .ava-wrap.state-online {{
            background: linear-gradient(135deg, #10b981, #059669);
            box-shadow: 0 0 14px var(--green-glow);
        }}
        .ava-wrap.state-online .ava-dot {{
            background: #10b981;
            box-shadow: 0 0 8px #10b981;
            animation: pulseDot 2s infinite;
        }}

        @keyframes pulseDot {{
            0%, 100% {{ transform: scale(1); }}
            50% {{ transform: scale(1.22); }}
        }}

        .prof-meta {{ flex: 1; min-width: 0; }}
        .prof-name-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
        }}
        .prof-name {{
            font-size: 16px;
            font-weight: 800;
            color: #fff;
            word-break: break-word;
        }}
        .prof-sub {{ font-size: 12px; color: var(--muted); margin-top: 3px; }}

        .status-badge {{
            font-size: 11px;
            font-weight: 800;
            padding: 4px 9px;
            border-radius: 20px;
            white-space: nowrap;
        }}
        .badge-watching {{ background: rgba(168, 85, 247, 0.2); color: #d8b4fe; border: 1px solid rgba(168, 85, 247, 0.45); }}
        .badge-online {{ background: rgba(16, 185, 129, 0.18); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.4); }}
        .badge-offline {{ background: rgba(100, 116, 139, 0.2); color: #cbd5e1; border: 1px solid rgba(100, 116, 139, 0.35); }}

        .watching-box {{
            background: rgba(255,255,255,0.03);
            border-left: 3px solid var(--purple);
            padding: 9px 11px;
            border-radius: 9px;
        }}
        .state-online .watching-box {{ border-left-color: var(--green); }}
        .state-offline .watching-box {{ border-left-color: var(--grey); }}
        .watching-lbl {{ font-size: 11px; color: var(--muted); }}
        .watching-val {{ font-size: 13.5px; font-weight: 700; color: #f8fafc; margin-top: 2px; word-break: break-word; }}

        .time-panel {{
            background: rgba(0,0,0,0.26);
            border-radius: 10px;
            padding: 9px 11px;
            display: flex;
            flex-direction: column;
            gap: 5px;
            font-size: 12.5px;
        }}
        .time-item {{ display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; }}
        .t-lbl {{ color: var(--muted); }}
        .t-val {{ font-weight: 700; font-variant-numeric: tabular-nums; }}
        .live-timer {{ color: #c084fc; }}
        .t-muted {{ color: #94a3b8; font-weight: 500; }}

        /* Блоки и таблицы */
        .box-card {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 14px;
            margin-bottom: 14px;
        }}
        .box-title {{
            font-size: 15px;
            font-weight: 800;
            margin-bottom: 11px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
        }}
        .table-wrap {{
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }}
        table {{ width: 100%; border-collapse: collapse; min-width: 440px; font-size: 13px; }}
        th, td {{ padding: 9px 10px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.06); }}
        th {{ font-size: 11.5px; color: var(--muted); text-transform: uppercase; }}
        .td-time {{ white-space: nowrap; font-variant-numeric: tabular-nums; color: #cbd5e1; font-size: 12px; }}
        .td-det {{ word-break: break-word; }}
        .ev-pill {{ padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; white-space: nowrap; }}
        .tag-watch {{ background: rgba(168,85,247,0.2); color: #d8b4fe; }}
        .tag-onl {{ background: rgba(16,185,129,0.2); color: #6ee7b7; }}
        .tag-off {{ background: rgba(100,116,139,0.25); color: #cbd5e1; }}

        /* Вкладка 2: Инструменты и Аналитика */
        .grid-2 {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 12px;
            margin-bottom: 14px;
        }}
        .chart-holder {{ position: relative; height: 220px; width: 100%; }}
        .inp {{
            width: 100%;
            background: #0e111b;
            border: 1px solid var(--border);
            color: var(--text);
            padding: 10px 12px;
            border-radius: 10px;
            font-size: 14px;
            margin-bottom: 9px;
        }}
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

    <!-- ПЕРЕКЛЮЧАТЕЛЬ ВКЛАДОК -->
    <div class="tabs-nav">
        <button class="tab-btn active" onclick="openTab('tab1', this)">📡 1. Профили & Статусы</button>
        <button class="tab-btn" onclick="openTab('tab2', this)">📊 2. Аналитика & Функции</button>
    </div>

    <!-- ВКЛАДКА 1: ПРОФИЛИ, АВАТАРКИ И ИСТОРИЯ -->
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

    <!-- ВКЛАДКА 2: НОВЫЕ ФУНКЦИИ, РЕЙТИНГ И ГРАФИКИ -->
    <div id="tab2" class="tab-pane">
        <!-- Битва зрителей / Рейтинг активности -->
        <div class="box-card">
            <div class="box-title">🏆 Рейтинг активности профилей (Кто больше в сети и смотрит)</div>
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
            <!-- Калькулятор марафона серий -->
            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">🍿 Калькулятор просмотра аниме</div>
                <input type="number" id="epCnt" class="inp" value="12" min="1" placeholder="Сколько серий осталось?" oninput="calcMarathon()">
                <select id="epLen" class="inp" onchange="calcMarathon()">
                    <option value="20">20 мин (с пропуском опенинга и эндинга)</option>
                    <option value="24" selected>24 мин (полная стандартная серия)</option>
                    <option value="45">45 мин (OVA / Спешл)</option>
                </select>
                <div id="marathonOut" style="background:rgba(168,85,247,0.12); border:1px solid rgba(168,85,247,0.35); padding:10px; border-radius:10px; font-size:13px;"></div>
            </div>

            <!-- Живой поиск и выгрузка в Excel/CSV -->
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

    // Живой секундомер длительности статуса
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

    // Звуковой радар (Web Audio API)
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

    // Калькулятор марафона
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

    // Поиск по логам
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

    // Экспорт в CSV
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

    // Графики Chart.js
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

    changed_any = False
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
                p["online_seconds"] = p.get("online_seconds", 0) + DEFAULT_INTERVAL

            status_diff = (old_onl != new_onl) or (old_wat != new_wat) or (old_ttl != new_ttl)
            if status_diff:
                changed_any = True
                prev_label = f"{'Смотрел: ' + old_ttl if old_wat else ('В сети' if old_onl else 'Оффлайн')}"
                p["prev_status"] = prev_label
                p["online"] = new_onl
                p["watching"] = new_wat
                p["current_title"] = new_ttl
                p["last_status_change"] = now

                if new_wat and old_ttl != new_ttl:
                    p["titles_watched"] = p.get("titles_watched", 0) + 1
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
                    "pid": pid,
                    "name": p["nickname"],
                    "event": ev_type,
                    "details": details
                })
                if len(cfg["history"]) > 600:
                    cfg["history"] = cfg["history"][-600:]

                save_config(cfg)

                # Рассылка уведомлений согласно настройкам
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
            # Автоочистка старых сообщений
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

        # Берем минимальный выбранный интервал среди пользователей
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
        return  # Отключаем лишний спам HTTP-сервера в консоль


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


if __name__ == "__main__":
    main()
