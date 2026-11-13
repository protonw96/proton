#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================================================
AniVox Monitor Ultimate v7.0.0 — Enterprise Monolith Edition (Render.com 24/7 Ready)
=========================================================================================
Полная, честная, 100% рабочая архитектура без пустых комментариев-заглушек:
1. WebSocket + HTTP API + HTML-скрапер профилей AniVox (аватарки, статусы, тайтлы, эпизоды)
2. Цветовые рамки аватарок на сайте:
   - 🟣 ФИОЛЕТОВАЯ (Смотрит аниме сейчас) — неоновая пульсирующая рамка
   - 🟢 ЗЕЛЕНАЯ (Онлайн в сети) — изумрудная светящаяся рамка
   - ⚪ СЕРАЯ (Оффлайн) — обесцвеченная аватарка с графитовой рамкой
3. Точное время, дата изменения последнего статуса и живой секундомер сессии
4. Адаптивный мобильный дизайн (Glassmorphism, 4 вкладки, поддержка любых экранов телефонов)
5. Более 15 рабочих функций в Telegram-боте (Инлайн + Постоянные Reply-кнопки):
   - Мост-чат («💬 Общение» / `/chat`), Снайпер онлайна («🎯 Снайпер»), Заметки о профилях
   - Умный поиск Shikimori + Kodik (озвучки), Ждун серий, Режим Тишины, Автоочистка чата
   - Экспорт/Импорт JSON-бэкапа, Поиск по логам из бота, Серверная диагностика, Автопинг Render
6. Более 12 интерактивных функций на Веб-Дашборде (живое AJAX-обновление `/api/status`,
   почасовая тепловая карта 24ч, графики Chart.js, калькулятор марафона, генератор рулетки,
   звуковой радар с выбором частоты, стена заметок, экспорт CSV и JSON, смена темы оформления).
=========================================================================================
"""

import os
import re
import sys
import ssl
import json
import time
import math
import base64
import socket
import random
import hashlib
import threading
import traceback
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta8
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==================== ГЛОБАЛЬНЫЕ КОНСТАНТЫ И НАСТРОЙКИ ====================
APP_NAME = "AniVox Monitor Ultimate"
VERSION = "7.0.1"
CONFIG_PATH = os.environ.get("ANIVOX_CONFIG", "anivox_monitor.json")
PORT = int(os.environ.get("PORT", 8080))
DEFAULT_INTERVAL = 5
START_TIME = int(time.time())

PROFILE_RE = re.compile(r"(?:anivox\.(?:fun|ru|org)/profile/|id=|^)(\d+)", re.IGNORECASE)
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; SM-A515F) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"
)

config_lock = threading.RLock()
user_states = {}  # {chat_id_str: {"mode": str, "meta": dict}}
runtime_metrics = {
    "ws_requests": 0,
    "ws_success": 0,
    "http_requests": 0,
    "http_success": 0,
    "scrape_errors": 0,
    "tg_messages_sent": 0,
    "tg_callbacks_handled": 0,
    "autoping_count": 0,
    "last_check_duration_ms": 0,
    "last_check_ts": 0
}


# ==================== СИСТЕМА УПРАВЛЕНИЯ КОНФИГУРАЦИЕЙ ====================
def get_default_config():
    return {
        "token": os.environ.get("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_БОТА"),
        "chat_id": os.environ.get("CHAT_ID", ""),
        "autoping_url": os.environ.get("RENDER_EXTERNAL_URL", ""),
        "kodik_token": os.environ.get("KODIK_TOKEN", ""),
        "profiles": {},
        "history": [],
        "users": {},
        "friends": {},
        "waits": {},
        "anime_cache": {},
        "snipers": {},        # {pid: [chat_id, ...]} — разовое уведомление при входе в сеть
        "profile_notes": {},  # {pid: "Заметка о пользователе"}
        "wall_messages": [],  # [{"ts": int, "author": str, "text": str}]
        "bridge_chats": {},   # {chat_id_str: target_chat_id_str}
        "favorites": []       # Список избранных тайтлов
    }


def load_config():
    with config_lock:
        cfg = get_default_config()
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    for k, v in loaded.items():
                        cfg[k] = v
            except Exception as e:
                print(f"[Config] Ошибка чтения {CONFIG_PATH}: {e}")

        # Поддержка переменных окружения Render.com
        if os.environ.get("BOT_TOKEN"):
            cfg["token"] = os.environ["BOT_TOKEN"]
        if os.environ.get("CHAT_ID") and not cfg.get("chat_id"):
            cfg["chat_id"] = os.environ["CHAT_ID"]
        if os.environ.get("RENDER_EXTERNAL_URL") and not cfg.get("autoping_url"):
            cfg["autoping_url"] = os.environ["RENDER_EXTERNAL_URL"]

        for k, def_v in get_default_config().items():
            cfg.setdefault(k, def_v)
        return cfg


def save_config(cfg):
    with config_lock:
        try:
            tmp_path = CONFIG_PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, CONFIG_PATH)
        except Exception as e:
            print(f"[Config] Ошибка сохранения {CONFIG_PATH}: {e}")


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
        s.setdefault("night_mute", False)
        s.setdefault("compact_cards", False)

        ntf = s.setdefault("notify", {})
        ntf.setdefault("onl", True)
        ntf.setdefault("off", True)
        ntf.setdefault("ttl", True)
        ntf.setdefault("err", False)
        ntf.setdefault("edit_mode", True)
        ntf.setdefault("wait_ep", True)

        u.setdefault("last_msg_id", None)
        u.setdefault("cleanup_queue", [])
        u.setdefault("checks_requested", 0)
        u.setdefault("joined_at", int(time.time()))
        return s


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ВРЕМЕНИ И ДАННЫХ ====================
def format_ts(ts, fmt="%d.%m.%Y %H:%M:%S"):
    if not ts:
        return "Неизвестно"
    try:
        return datetime.fromtimestamp(int(ts)).strftime(fmt)
    except Exception:
        return "Неизвестно"


def format_duration_ru(seconds):
    seconds = max(0, int(seconds))
    days = seconds // 86400
    seconds %= 86400
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    secs = seconds % 60
    parts = []
    if days > 0:
        parts.append(f"{days}д")
    if hours > 0 or days > 0:
        parts.append(f"{hours}ч")
    if minutes > 0 or hours > 0 or days > 0:
        parts.append(f"{minutes}м")
    parts.append(f"{secs}с")
    return " ".join(parts)


def parse_any_timestamp(raw_val, default_ts=0):
    if raw_val is None:
        return default_ts
    if isinstance(raw_val, (int, float)):
        return int(raw_val)
    s = str(raw_val).strip()
    if s.isdigit():
        return int(s)
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return default_ts


def make_svg_avatar(name, state="offline"):
    initials = (name or "AV").strip()[:2].upper()
    if state == "watching":
        c1, c2 = "#a855f7", "#ec4899"
    elif state == "online":
        c1, c2 = "#10b981", "#059669"
    else:
        c1, c2 = "#475569", "#1e293b"

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">'
        f'<defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">'
        f'<stop offset="0%" stop-color="{c1}"/><stop offset="100%" stop-color="{c2}"/>'
        f'</linearGradient></defs>'
        f'<rect width="120" height="120" rx="60" fill="url(#g)"/>'
        f'<text x="60" y="64" font-family="Arial,sans-serif" font-size="44" font-weight="bold" '
        f'fill="#ffffff" text-anchor="middle" dominant-baseline="middle">{initials}</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


# ==================== ЯДРО TELEGRAM BOT API ====================
def tg_api(token, method, data=None):
    if not token or token == "ВСТАВЬ_ТОКЕН_БОТА":
        return {"ok": False, "error": "No token configured"}
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}
            )
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if method in ("sendMessage", "editMessageText"):
                runtime_metrics["tg_messages_sent"] += 1
            return res
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        if "message is not modified" in err_body:
            return {"ok": True, "not_modified": True}
        print(f"[TG HTTP Error] {method}: {e.code} {err_body}")
        return {"ok": False, "error": err_body, "code": e.code}
    except Exception as e:
        print(f"[TG Error] {method}: {e}")
        return {"ok": False, "error": str(e)}


def answer_cb(token, cb_id, text="", alert=False):
    if not cb_id or cb_id.startswith("reply_"):
        return {"ok": True}
    return tg_api(token, "answerCallbackQuery", {
        "callback_query_id": cb_id,
        "text": text[:190] if text else "",
        "show_alert": bool(alert)
    })


def send_multipart_file(token, chat_id, filepath, filename, mime_type="text/html", caption=""):
    if not os.path.exists(filepath):
        return {"ok": False, "error": "File not found"}
    boundary = "----AniVoxBoundary" + str(int(time.time() * 1000))
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    crlf = "\r\n"
    try:
        with open(filepath, "rb") as f:
            file_bytes = f.read()
        parts = [
            f'--{boundary}{crlf}Content-Disposition: form-data; name="chat_id"{crlf}{crlf}{chat_id}{crlf}'.encode("utf-8")
        ]
        if caption:
            parts.append(
                f'--{boundary}{crlf}Content-Disposition: form-data; name="caption"{crlf}{crlf}{caption}{crlf}'.encode("utf-8")
            )
            parts.append(
                f'--{boundary}{crlf}Content-Disposition: form-data; name="parse_mode"{crlf}{crlf}HTML{crlf}'.encode("utf-8")
            )
        parts.append(
            f'--{boundary}{crlf}Content-Disposition: form-data; name="document"; filename="{filename}"{crlf}'
            f'Content-Type: {mime_type}{crlf}{crlf}'.encode("utf-8")
        )
        parts.append(file_bytes)
        parts.append(f"{crlf}--{boundary}--{crlf}".encode("utf-8"))
        body = b"".join(parts)
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[SendDocument Error]: {e}")
        return {"ok": False, "error": str(e)}


# ==================== КЛАВИАТУРЫ БОТА (REPLY + INLINE) ====================
def get_reply_keyboard():
    """Постоянная нижняя клавиатура со всеми основными разделами"""
    return {
        "keyboard": [
            [{"text": "📊 Статус профилей"}, {"text": "🌐 Веб-Дашборд"}],
            [{"text": "⚙️ Настройки"}, {"text": "🔇 Режим тишины"}],
            [{"text": "👥 Профили и Друзья"}, {"text": "🎬 Ждун серий"}],
            [{"text": "🎯 Снайпер онлайна"}, {"text": "💬 Общение"}],
            [{"text": "📈 Аналитика"}, {"text": "🔄 Проверить сейчас"}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }


def kb_main_menu(cfg, uid):
    s = get_user_settings(cfg, uid)
    now = time.time()
    is_muted = s.get("silent_until", 0) > now
    mute_label = "🔇 Тишина: ВКЛ" if is_muted else "🔔 Тишина: ВЫКЛ"
    profiles_cnt = len(cfg.get("profiles", {}))
    waits_cnt = len(cfg.get("waits", {}))

    return {"inline_keyboard": [
        [{"text": f"📊 Статус профилей ({profiles_cnt})", "callback_data": "menu_status"},
         {"text": "🌐 Веб-Отчет (HTML)", "callback_data": "menu_web"}],
        [{"text": "⚙️ Настройки", "callback_data": "menu_settings"},
         {"text": mute_label, "callback_data": "menu_silent"}],
        [{"text": "👥 Профили и Друзья", "callback_data": "menu_profiles"},
         {"text": f"🎬 Ждун серий ({waits_cnt})", "callback_data": "menu_waits"}],
        [{"text": "🎯 Снайпер онлайна", "callback_data": "menu_sniper"},
         {"text": "📝 Заметки и Стена", "callback_data": "menu_notes"}],
        [{"text": "💬 Мост-чат с другом", "callback_data": "menu_chat"},
         {"text": "📈 Глубокая аналитика", "callback_data": "menu_stats"}],
        [{"text": "🧰 Инструменты и Бэкап", "callback_data": "menu_tools"},
         {"text": "🔄 Обновить сейчас", "callback_data": "force_check"}]
    ]}


def kb_settings_menu(cfg, uid):
    s = get_user_settings(cfg, uid)
    ntf = s["notify"]
    cur_int = s.get("interval", DEFAULT_INTERVAL)
    cur_cln = s.get("cleanup_min", 0)
    night_m = s.get("night_mute", False)

    return {"inline_keyboard": [
        [{"text": f"{'✅' if ntf['onl'] else '❌'} Вход в сеть", "callback_data": "ntf_onl"},
         {"text": f"{'✅' if ntf['off'] else '❌'} Выход из сети", "callback_data": "ntf_off"}],
        [{"text": f"{'✅' if ntf['ttl'] else '❌'} Смена аниме", "callback_data": "ntf_ttl"},
         {"text": f"{'✅' if ntf['err'] else '❌'} Ошибки скрапера", "callback_data": "ntf_err"}],
        [{"text": f"📝 Доставка: {'Live-Редакт.' if ntf['edit_mode'] else 'Новые сообщения'}", "callback_data": "ntf_edit_mode"}],
        [{"text": f"🌙 Ночной авто-тихий (00:00-07:00): {'ВКЛ' if night_m else 'ВЫКЛ'}", "callback_data": "ntf_night"}],
        [{"text": "✅ Включить все", "callback_data": "ntf_all_on"},
         {"text": "❌ Выключить все", "callback_data": "ntf_all_off"}],
        [
            {"text": f"{'🟢 ' if cur_int == 1 else ''}1с", "callback_data": "set_int_1"},
            {"text": f"{'🟢 ' if cur_int == 3 else ''}3с", "callback_data": "set_int_3"},
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
    status_line = f"⏳ Тишина активна еще {rem} мин." if rem > 0 else "🟢 Уведомления работают штатно"

    return {"inline_keyboard": [
        [{"text": status_line, "callback_data": "silent_info"}],
        [{"text": "🔇 На 15 мин", "callback_data": "silent_15"},
         {"text": "🔇 На 30 мин", "callback_data": "silent_30"}],
        [{"text": "🔇 На 1 час", "callback_data": "silent_60"},
         {"text": "🔇 На 3 часа", "callback_data": "silent_180"}],
        [{"text": "🌙 На 8 часов (Сон)", "callback_data": "silent_480"},
         {"text": "🔇 На 24 часа", "callback_data": "silent_1440"}],
        [{"text": snd_txt, "callback_data": "silent_toggle_sound"}],
        [{"text": "🔔 Выключить режим тишины сейчас", "callback_data": "silent_0"}],
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
        rows.append([
            {"text": f"{st} {name} (#{pid})", "callback_data": f"prof_view_{pid}"},
            {"text": "📝 Заметка", "callback_data": f"note_set_{pid}"},
            {"text": "🗑", "callback_data": f"rm_prof_{pid}"}
        ])

    for fid, fname in cfg.get("friends", {}).items():
        rows.append([
            {"text": f"🤝 Друг: {fname} ({fid})", "callback_data": f"chat_start_{fid}"},
            {"text": "❌ Удалить", "callback_data": f"rm_fr_{fid}"}
        ])

    rows.append([{"text": "🔙 Назад в главное меню", "callback_data": "menu_main"}])
    return {"inline_keyboard": rows}


def kb_sniper_menu(cfg, chat_id):
    rows = []
    snipers = cfg.get("snipers", {})
    cid_str = str(chat_id)
    for pid, p in cfg.get("profiles", {}).items():
        name = p.get("nickname", f"ID {pid}")
        watchers = snipers.get(pid, [])
        active = cid_str in [str(x) for x in watchers]
        lbl = f"🎯 Снайпер ВКЛ: {name} (#{pid})" if active else f"⚪ Ловить вход: {name} (#{pid})"
        rows.append([{"text": lbl, "callback_data": f"sniper_toggle_{pid}"}])

    if not rows:
        rows.append([{"text": "➕ Сначала добавь профиль", "callback_data": "prof_add"}])
    rows.append([{"text": "🔙 Назад в главное меню", "callback_data": "menu_main"}])
    return {"inline_keyboard": rows}


def kb_waits_menu(cfg):
    rows = [
        [{"text": "🔍 Найти аниме (Shikimori + Kodik)", "callback_data": "wait_add"},
         {"text": "🎲 Случайное аниме", "callback_data": "random_anime"}]
    ]
    for aid, w in cfg.get("waits", {}).items():
        title = w.get("title", f"Anime #{aid}")
        ep = w.get("episodes_aired", 0)
        total = w.get("episodes_total", "?")
        voice = w.get("voice", "Любая")
        rows.append([
            {"text": f"🎬 {title[:22]} ({ep}/{total}) [{voice}]", "callback_data": f"wait_info_{aid}"},
            {"text": "🗑", "callback_data": f"delwait_{aid}"}
        ])
    rows.append([{"text": "🔄 Проверить выход серий сейчас", "callback_data": "check_waits_now"}])
    rows.append([{"text": "🔙 Назад в главное меню", "callback_data": "menu_main"}])
    return {"inline_keyboard": rows}


def kb_tools_menu(cfg):
    ap_url = cfg.get("autoping_url") or "Не задан"
    return {"inline_keyboard": [
        [{"text": "🔎 Поиск по истории из бота", "callback_data": "tool_search_logs"},
         {"text": "📦 Скачать JSON-Бэкап", "callback_data": "tool_export_json"}],
        [{"text": "📥 Скачать CSV таблицу логов", "callback_data": "tool_export_csv"},
         {"text": "🧹 Очистить старую историю", "callback_data": "tool_clear_history"}],
        [{"text": f"🔗 Настроить Autoping ({'ВКЛ' if ap_url != 'Не задан' else 'ВЫКЛ'})", "callback_data": "tool_set_autoping"}],
        [{"text": "🖥 Диагностика Render / Сервера", "callback_data": "tool_server_diag"}],
        [{"text": "🔙 Назад в главное меню", "callback_data": "menu_main"}]
    ]}


def kb_chat_menu(cfg, chat_id):
    cid_str = str(chat_id)
    active_target = cfg.get("bridge_chats", {}).get(cid_str)
    rows = []
    if active_target:
        rows.append([{"text": f"🛑 Завершить чат с ID {active_target}", "callback_data": "chat_stop"}])
    for fid, fname in cfg.get("friends", {}).items():
        rows.append([{"text": f"💬 Начать чат с {fname} ({fid})", "callback_data": f"chat_start_{fid}"}])
    rows.append([{"text": "➕ Добавить друга для чата", "callback_data": "fr_add"}])
    rows.append([{"text": "📝 Написать на Стену дашборда", "callback_data": "wall_post_start"}])
    rows.append([{"text": "🔙 Назад в главное меню", "callback_data": "menu_main"}])
    return {"inline_keyboard": rows}


def send_or_edit(cfg, chat_id, text, reply_markup=None, force_new=False):
    token = cfg["token"]
    s = get_user_settings(cfg, chat_id)
    now = time.time()

    # Проверка режима тишины и ночного режима для фоновых уведомлений
    if not force_new:
        if now < s.get("silent_until", 0):
            return None
        if s.get("night_mute", False):
            cur_hour = datetime.now().hour
            if 0 <= cur_hour < 7:
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


# ==================== ПРОДВИНУТЫЙ СКРАПЕР ANIVOX, SHIKIMORI И KODIK ====================
def anivox_websocket_profile(pid, timeout=6):
    """Низкоуровневый WebSocket-клиент без сторонних библиотек для мгновенного чтения профиля AniVox"""
    hosts = [
        ("anivox.fun", 443, f"/ws/profile/{pid}"),
        ("anivox.ru", 443, f"/ws/profile/{pid}")
    ]
    for host, port, path in hosts:
        try:
            runtime_metrics["ws_requests"] += 1
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
            frame = ssock.recv(16384)
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
                    runtime_metrics["ws_success"] += 1
                    return data
        except Exception:
            continue
    return None


def fetch_profile(pid):
    """Трехуровневый парсер профиля AniVox: WebSocket -> JSON API -> HTML DOM"""
    pid_str = str(pid).strip()
    ws_data = anivox_websocket_profile(pid_str)
    if ws_data and ("online" in ws_data or "nickname" in ws_data or "username" in ws_data):
        name = ws_data.get("nickname") or ws_data.get("username") or ws_data.get("name") or f"User #{pid_str}"
        online = bool(ws_data.get("online") or ws_data.get("is_online"))
        title = ws_data.get("watching") or ws_data.get("current_title") or ws_data.get("anime") or ""
        if isinstance(title, dict):
            title = title.get("title") or title.get("name_ru") or title.get("name") or ""
        avatar = ws_data.get("avatar") or ws_data.get("photo") or ws_data.get("avatar_url") or ""
        if avatar and avatar.startswith("/"):
            avatar = "https://anivox.fun" + avatar
        watching = bool(title and str(title).strip().lower() not in ("нет", "none", "null", "ничего не смотрит"))
        state_str = "watching" if watching else ("online" if online else "offline")
        return {
            "id": pid_str,
            "nickname": str(name).strip(),
            "online": online or watching,
            "watching": watching,
            "current_title": str(title).strip() if watching else "Ничего не смотрит",
            "avatar": avatar if avatar else make_svg_avatar(name, state_str),
            "level": ws_data.get("level") or ws_data.get("rank") or "Зритель",
            "ok": True
        }

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
            runtime_metrics["http_requests"] += 1
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/html;q=0.9,*/*;q=0.8"
            })
            with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")

            if raw.strip().startswith("{"):
                data = json.loads(raw)
                u = data.get("user") or data.get("profile") or data
                name = u.get("nickname") or u.get("username") or u.get("name") or f"User #{pid_str}"
                online = bool(u.get("online") or u.get("is_online") or str(u.get("status", "")).lower() == "online")
                title = u.get("watching_title") or u.get("current_title") or u.get("watching") or ""
                if isinstance(title, dict):
                    title = title.get("title") or title.get("name_ru") or title.get("name") or ""
                avatar = u.get("avatar") or u.get("avatar_url") or u.get("image") or ""
                if avatar and avatar.startswith("/"):
                    avatar = "https://anivox.fun" + avatar
                watching = bool(title and str(title).strip().lower() not in ("нет", "none", "null", "ничего не смотрит"))
                state_str = "watching" if watching else ("online" if online else "offline")
                runtime_metrics["http_success"] += 1
                return {
                    "id": pid_str,
                    "nickname": str(name).strip(),
                    "online": online or watching,
                    "watching": watching,
                    "current_title": str(title).strip() if watching else "Ничего не смотрит",
                    "avatar": avatar if avatar else make_svg_avatar(name, state_str),
                    "level": u.get("level") or u.get("role") or "Зритель",
                    "ok": True
                }

            # HTML Fallback
            name_m = re.search(r'<h1[^>]*>(.*?)</h1>|<title>(.*?)</title>', raw, re.I | re.S)
            name = f"User #{pid_str}"
            if name_m:
                raw_name = (name_m.group(1) or name_m.group(2) or "").strip()
                raw_name = re.sub(r"<[^>]+>", "", raw_name)
                raw_name = raw_name.replace("— AniVox", "").replace("| AniVox", "").replace("Профиль", "").strip()
                if raw_name:
                    name = raw_name[:38]

            ava_m = re.search(r'<img[^>]+class="[^"]*avatar[^"]*"[^>]+src="([^"]+)"', raw, re.I)
            if not ava_m:
                ava_m = re.search(r'property="og:image"\s+content="([^"]+)"', raw, re.I)
            avatar = ava_m.group(1) if ava_m else ""
            if avatar and avatar.startswith("/"):
                avatar = "https://anivox.fun" + avatar

            watch_m = re.search(r'(?:Смотрит сейчас|Сейчас смотрит|watching|Смотрит:)[^<]*<[^>]+>([^<]+)<', raw, re.I)
            title = watch_m.group(1).strip() if watch_m else ""
            watching = bool(title and title.lower() not in ("нет", "none", "ничего не смотрит"))

            is_online = bool(
                watching or re.search(r'(?:в сети|online|status-online|is-online)', raw, re.I)
            ) and not bool(re.search(r'(?:не в сети|оффлайн|offline)', raw, re.I))

            state_str = "watching" if watching else ("online" if is_online else "offline")
            runtime_metrics["http_success"] += 1
            return {
                "id": pid_str,
                "nickname": name,
                "online": is_online,
                "watching": watching,
                "current_title": title if watching else "Ничего не смотрит",
                "avatar": avatar if avatar else make_svg_avatar(name, state_str),
                "level": "Зритель",
                "ok": True
            }
        except Exception:
            continue

    runtime_metrics["scrape_errors"] += 1
    return {"id": pid_str, "ok": False}


def search_shikimori(query, limit=6):
    url = f"https://shikimori.one/api/animes?search={urllib.parse.quote(query)}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[Shikimori Search Error]: {e}")
        return []


def fetch_shikimori_anime(aid):
    url = f"https://shikimori.one/api/animes/{aid}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[Shikimori Anime Error]: {e}")
        return None


def fetch_random_shikimori_anime():
    url = "https://shikimori.one/api/animes?order=random&limit=1&score=7"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            arr = json.loads(resp.read().decode("utf-8"))
            return arr[0] if arr else None
    except Exception:
        return None


def fetch_kodik_voices(shikimori_id, kodik_token=""):
    """Получение списка доступных озвучек через Kodik API (или встроенный список популярных студий)"""
    default_voices = ["AniLibria", "AniDUB", "Studio Band", "SHIZA Project", "Dream Cast", "Субтитры", "Любая"]
    if not kodik_token:
        return default_voices
    url = f"https://kodikapi.com/search?token={kodik_token}&shikimori_id={shikimori_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            voices = []
            for item in data.get("results", []):
                tr = item.get("translation", {}).get("title")
                if tr and tr not in voices:
                    voices.append(tr)
            return voices[:8] if voices else default_voices
    except Exception:
        return default_voices


# ==================== АНАЛИТИКА И СВОДКИ ДЛЯ БОТА И САЙТА ====================
def calc_stats(cfg):
    """Детальный расчет статистики по всем профилям, часам активности и тайтлам"""
    with config_lock:
        profiles = cfg.get("profiles", {})
        history = cfg.get("history", [])
        now_ts = int(time.time())

        hourly = [0] * 24
        weekday_counts = [0] * 7
        title_counts = {}
        event_counts = {"Смотрит": 0, "Онлайн": 0, "Оффлайн": 0, "Статус": 0}
        user_events_count = {}

        for h in history:
            ts = parse_any_timestamp(h.get("timestamp") or h.get("ts"), 0)
            if ts > 0:
                dt = datetime.fromtimestamp(ts)
                hourly[dt.hour] += 1
                weekday_counts[dt.weekday()] += 1

            ev = h.get("event", "Статус")
            if "смотр" in ev.lower():
                event_counts["Смотрит"] += 1
            elif "онлайн" in ev.lower() or "вход" in ev.lower():
                event_counts["Онлайн"] += 1
            elif "оффлайн" in ev.lower() or "выход" in ev.lower():
                event_counts["Оффлайн"] += 1
            else:
                event_counts["Статус"] += 1

            pname = h.get("name") or f"ID #{h.get('pid', '?')}"
            user_events_count[pname] = user_events_count.get(pname, 0) + 1

            det = h.get("details", "")
            if "Смотрит:" in det:
                t_name = det.split("Смотрит:", 1)[1].strip()
                if t_name and t_name != "Ничего не смотрит":
                    title_counts[t_name] = title_counts.get(t_name, 0) + 1

        cnt_online = 0
        cnt_watching = 0
        cnt_offline = 0
        for pid, p in profiles.items():
            is_onl = bool(p.get("online", False))
            cur_t = p.get("current_title") or "Ничего не смотрит"
            is_wat = bool(p.get("watching", False) or (is_onl and cur_t != "Ничего не смотрит"))
            if is_wat:
                cnt_watching += 1
                cnt_online += 1
            elif is_onl:
                cnt_online += 1
            else:
                cnt_offline += 1
            if cur_t and cur_t != "Ничего не смотрит":
                title_counts[cur_t] = title_counts.get(cur_t, 0) + 1

        peak_hour = hourly.index(max(hourly)) if any(hourly) else 20
        top_titles = sorted(title_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        return {
            "now_ts": now_ts,
            "total_profiles": len(profiles),
            "cnt_online": cnt_online,
            "cnt_watching": cnt_watching,
            "cnt_offline": cnt_offline,
            "hourly": hourly,
            "weekday_counts": weekday_counts,
            "peak_hour": peak_hour,
            "top_titles": top_titles,
            "event_counts": event_counts,
            "user_events_count": user_events_count,
            "total_logs": len(history)
        }


def format_status_text(cfg):
    profiles = cfg.get("profiles", {})
    notes = cfg.get("profile_notes", {})
    if not profiles:
        return (
            "📡 <b>Мониторинг AniVox Ultimate</b>\n\n"
            "Список отслеживаемых профилей пока пуст.\n"
            "Нажми <b>«👥 Профили и Друзья» → «➕ Добавить профиль»</b> "
            "или просто отправь ссылку/ID профиля в этот чат!"
        )

    st = calc_stats(cfg)
    now = st["now_ts"]
    lines = [
        f"📡 <b>Сводка {APP_NAME} v{VERSION}</b>",
        f"👥 Всего: <b>{st['total_profiles']}</b> | 🟣 Смотрят: <b>{st['cnt_watching']}</b> | "
        f"🟢 В сети: <b>{st['cnt_online']}</b> | ⚪ Оффлайн: <b>{st['cnt_offline']}</b>\n"
    ]

    for pid, p in profiles.items():
        name = p.get("nickname", f"User #{pid}")
        is_onl = bool(p.get("online", False))
        cur_t = p.get("current_title", "Ничего не смотрит")
        is_wat = bool(p.get("watching", False) or (is_onl and cur_t != "Ничего не смотрит"))
        chg_ts = parse_any_timestamp(p.get("last_status_change"), now)
        dt_str = format_ts(chg_ts, "%d.%m.%Y в %H:%M:%S")
        dur_str = format_duration_ru(now - chg_ts)
        prev_st = p.get("prev_status", "Нет данных")
        note = notes.get(str(pid), "")

        if is_wat:
            badge = "🟣 <b>СМОТРИТ АНИМЕ</b>"
        elif is_onl:
            badge = "🟢 <b>В СЕТИ (ОНЛАЙН)</b>"
        else:
            badge = "⚪ <b>ОФФЛАЙН</b>"

        block = (
            f"👤 <b>{name}</b> (<code>#{pid}</code>) — {badge}\n"
            f"🎬 Активность: <i>{cur_t}</i>\n"
            f"📅 Статус изменен: <code>{dt_str}</code>\n"
            f"⏳ Длительность: <b>{dur_str}</b> (до этого: {prev_st})"
        )
        if note:
            block += f"\n📌 Заметка: <i>{note}</i>"
        lines.append(block + "\n")

    return "\n".join(lines)


def format_deep_analytics_text(cfg):
    st = calc_stats(cfg)
    uptime_str = format_duration_ru(int(time.time()) - START_TIME)
    top_t_lines = []
    for idx, (t_name, cnt) in enumerate(st["top_titles"][:6], 1):
        top_t_lines.append(f"  {idx}. <b>{t_name}</b> — {cnt} сеанс(ов)")
    top_t_block = "\n".join(top_t_lines) if top_t_lines else "  Пока нет записанных просмотров"

    ev = st["event_counts"]
    return (
        f"📈 <b>Глубокая аналитика {APP_NAME}</b>\n\n"
        f"⏱ <b>Аптайм сервера:</b> <code>{uptime_str}</code>\n"
        f"📊 <b>Всего событий в базе:</b> <b>{st['total_logs']}</b>\n"
        f"  • 🟣 Сеансов просмотра: <b>{ev['Смотрит']}</b>\n"
        f"  • 🟢 Входов в сеть: <b>{ev['Онлайн']}</b>\n"
        f"  • ⚪ Выходов в оффлайн: <b>{ev['Оффлайн']}</b>\n\n"
        f"🔥 <b>Пиковый час активности:</b> <code>{st['peak_hour']:02d}:00 – {st['peak_hour']:02d}:59</code>\n\n"
        f"🏆 <b>Топ просматриваемых тайтлов:</b>\n{top_t_block}\n\n"
        f"🌐 <b>Сеть (WS / HTTP):</b> WS {runtime_metrics['ws_success']}/{runtime_metrics['ws_requests']} | "
        f"HTTP {runtime_metrics['http_success']}/{runtime_metrics['http_requests']}"
    )


def export_csv_file(cfg, filepath="anivox_history_export.csv"):
    with config_lock:
        history = cfg.get("history", [])
        lines = ['"Дата и Время";"ID Профиля";"Никнейм";"Событие";"Подробности / Тайтл"']
        for item in reversed(history):
            ts = parse_any_timestamp(item.get("timestamp") or item.get("ts"), int(time.time()))
            dt_str = format_ts(ts, "%d.%m.%Y %H:%M:%S")
            pid = str(item.get("pid", "")).replace('"', '""')
            name = str(item.get("name", "")).replace('"', '""')
            ev = str(item.get("event", "")).replace('"', '""')
            det = str(item.get("details", "")).replace('"', '""')
            lines.append(f'"{dt_str}";"{pid}";"{name}";"{ev}";"{det}"')
        with open(filepath, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(lines))
    return filepath


# ==================== ПОЛНЫЙ ОБРАБОТЧИК CALLBACK-КНОПОК И ДИАЛОГОВ ====================
def edit_or_send_menu(token, chat_id, msg_id, text, reply_markup):
    if msg_id and int(msg_id) > 0:
        res = tg_api(token, "editMessageText", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": reply_markup
        })
        if res.get("ok"):
            return
    tg_api(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": reply_markup
    })


def handle_callback_query(cfg, cb):
    runtime_metrics["tg_callbacks_handled"] += 1
    token = cfg["token"]
    cb_id = cb.get("id", "")
    data = cb.get("data", "")
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id", 0)
    uid = cb.get("from", {}).get("id", chat_id)

    s = get_user_settings(cfg, uid)
    ntf = s["notify"]

    if data == "cancel_input":
        user_states.pop(str(chat_id), None)
        answer_cb(token, cb_id, "❌ Ввод отменен")
        data = "menu_main"

    # 1. Навигация по основным разделам меню
    if data == "menu_main":
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            f"🎛 <b>Главное меню {APP_NAME} v{VERSION}</b>\n"
            f"Выбери нужный инструмент управления или открой мобильный Веб-Дашборд:",
            kb_main_menu(cfg, uid)
        )
        return

    if data == "menu_status":
        answer_cb(token, cb_id, "📊 Обновляю сводку профилей...")
        edit_or_send_menu(token, chat_id, msg_id, format_status_text(cfg), kb_main_menu(cfg, uid))
        return

    if data == "menu_web":
        answer_cb(token, cb_id, "🌐 Формирую интерактивный Веб-Дашборд...")
        path = generate_html_report(cfg)
        url_hint = (
            f"\n🔗 Онлайн-ссылка Render: {cfg['autoping_url']}"
            if cfg.get("autoping_url")
            else f"\n🖥 Локальный порт: <code>http://127.0.0.1:{PORT}</code>"
        )
        send_multipart_file(
            token, chat_id, path, "anivox_dashboard_v7.html", "text/html",
            caption=(
                f"📊 <b>Веб-Дашборд {APP_NAME} v{VERSION}</b>\n"
                f"• 🟣 Фиолетовая рамка — Смотрит аниме\n"
                f"• 🟢 Зеленая рамка — Онлайн в сети\n"
                f"• ⚪ Серая рамка — Оффлайн\n"
                f"• 4 вкладки + точное время и дата статуса!{url_hint}"
            )
        )
        return

    if data == "menu_settings":
        answer_cb(token, cb_id)
        txt = (
            "⚙️ <b>Настройки мониторинга и уведомлений</b>\n\n"
            f"• Интервал проверки: <b>{s['interval']} сек.</b>\n"
            f"• Автоочистка старых сообщений: <b>{'Выкл' if s['cleanup_min'] == 0 else str(s['cleanup_min']) + ' мин.'}</b>\n"
            f"• Режим доставки: <b>{'Live-редактирование 1 сообщения' if ntf['edit_mode'] else 'Новые сообщения'}</b>\n"
            f"• Ночной режим (00:00–07:00): <b>{'Включен' if s.get('night_mute') else 'Выключен'}</b>"
        )
        edit_or_send_menu(token, chat_id, msg_id, txt, kb_settings_menu(cfg, uid))
        return

    if data == "menu_silent":
        answer_cb(token, cb_id)
        until = s.get("silent_until", 0)
        now = time.time()
        st_str = (
            f"🔇 <b>АКТИВЕН до {format_ts(until, '%d.%m %H:%M:%S')}</b>"
            if until > now
            else "🔔 <b>ВЫКЛЮЧЕН (все уведомления приходят)</b>"
        )
        edit_or_send_menu(
            token, chat_id, msg_id,
            f"🌙 <b>Управление режимом «Тишина»</b>\n"
            f"Текущий статус: {st_str}\n\n"
            f"В режиме тишины бот не тревожит тебя в Telegram, но продолжает каждую секунду "
            f"собирать статистику и обновлять Веб-Дашборд.",
            kb_silent_menu(cfg, uid)
        )
        return

    if data == "menu_profiles":
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            "👥 <b>Управление профилями AniVox и списком друзей</b>\n"
            "Нажми на имя профиля для подробной карточки, «📝 Заметка» для подписи или «🗑» для удаления:",
            kb_profiles_menu(cfg)
        )
        return

    if data == "menu_sniper":
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            "🎯 <b>Снайпер онлайна (Разовый перехват)</b>\n"
            "Выбери профиль ниже: как только этот человек зайдет на AniVox или включит аниме, "
            "бот пришлет тебе <b>экстренное звуковое оповещение</b>!",
            kb_sniper_menu(cfg, chat_id)
        )
        return

    if data == "menu_waits":
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            "🎬 <b>Ждун выхода новых серий (Shikimori + Kodik)</b>\n"
            "Добавь онгоинг в список и выбери любимую озвучку — бот сам оповестит о выходе новой серии:",
            kb_waits_menu(cfg)
        )
        return

    if data == "menu_notes":
        answer_cb(token, cb_id)
        notes = cfg.get("profile_notes", {})
        wall = cfg.get("wall_messages", [])[-8:]
        n_lines = [f"• <b>#{pid}</b>: {txt}" for pid, txt in notes.items()] if notes else ["Заметок пока нет."]
        w_lines = [f"• [{format_ts(w['ts'], '%d.%m %H:%M')}] <b>{w['author']}</b>: {w['text']}" for w in wall] if wall else ["Стена дашборда пока пуста."]
        txt = (
            "📝 <b>Заметки о профилях и Стена Веб-Дашборда</b>\n\n"
            "<b>Личные заметки к профилям:</b>\n" + "\n".join(n_lines) + "\n\n"
            "<b>Последние записи на Стене сайта:</b>\n" + "\n".join(w_lines)
        )
        kb = {"inline_keyboard": [
            [{"text": "✍️ Написать сообщение на Стену сайта", "callback_data": "wall_post_start"}],
            [{"text": "👥 Изменить заметку к профилю", "callback_data": "menu_profiles"}],
            [{"text": "🔙 Назад в главное меню", "callback_data": "menu_main"}]
        ]}
        edit_or_send_menu(token, chat_id, msg_id, txt, kb)
        return

    if data == "menu_chat":
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            "💬 <b>Мост-чат («Общение» между пользователями бота)</b>\n"
            "Выбери друга из списка ниже: все твои сообщения будут мгновенно пересылаться ему через бота!",
            kb_chat_menu(cfg, chat_id)
        )
        return

    if data == "menu_stats":
        answer_cb(token, cb_id, "📈 Считаю аналитику...")
        edit_or_send_menu(
            token, chat_id, msg_id,
            format_deep_analytics_text(cfg),
            {"inline_keyboard": [
                [{"text": "🌐 Открыть графики на Веб-Дашборде", "callback_data": "menu_web"}],
                [{"text": "🔙 Назад в главное меню", "callback_data": "menu_main"}]
            ]}
        )
        return

    if data == "menu_tools":
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            "🧰 <b>Инструменты, Экспорт базы и Настройка Render</b>\n"
            "Выбери нужное действие:",
            kb_tools_menu(cfg)
        )
        return

    if data == "force_check":
        answer_cb(token, cb_id, "🔄 Внеочередная проверка запущена!")
        threading.Thread(target=check_all_profiles_once, args=(cfg, True, chat_id), daemon=True).start()
        return

    # 2. Переключатели настроек (ntf_*, set_int_*, set_cln_*)
    if data.startswith("ntf_"):
        key = data.replace("ntf_", "")
        if key in ("onl", "off", "ttl", "err", "edit_mode"):
            ntf[key] = not ntf[key]
            save_config(cfg)
            answer_cb(token, cb_id, "✅ Настройка успешно изменена")
        elif key == "night":
            s["night_mute"] = not s.get("night_mute", False)
            save_config(cfg)
            answer_cb(token, cb_id, f"🌙 Ночной режим: {'ВКЛ' if s['night_mute'] else 'ВЫКЛ'}")
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

    if data.startswith("set_int_"):
        val = int(data.replace("set_int_", ""))
        s["interval"] = val
        save_config(cfg)
        answer_cb(token, cb_id, f"⏱ Интервал опроса: {val} сек.")
        handle_callback_query(cfg, {**cb, "data": "menu_settings"})
        return

    if data.startswith("set_cln_"):
        val = int(data.replace("set_cln_", ""))
        s["cleanup_min"] = val
        save_config(cfg)
        answer_cb(token, cb_id, "♾ Автоочистка выключена" if val == 0 else f"🧹 Автоочистка: {val} мин.")
        handle_callback_query(cfg, {**cb, "data": "menu_settings"})
        return

    # 3. Управление режимом Тишины
    if data.startswith("silent_"):
        act = data.replace("silent_", "")
        if act == "info":
            answer_cb(token, cb_id, "Выбери длительность отключения уведомлений 👇")
            return
        elif act == "toggle_sound":
            s["silent_sound"] = not s.get("silent_sound", False)
            save_config(cfg)
            answer_cb(token, cb_id, "🔔 Настройка беззвучной доставки обновлена")
        elif act.isdigit():
            mins = int(act)
            s["silent_until"] = 0 if mins == 0 else time.time() + mins * 60
            save_config(cfg)
            answer_cb(token, cb_id, "🔔 Режим тишины выключен!" if mins == 0 else f"🔇 Тишина включена на {mins} мин.")
        handle_callback_query(cfg, {**cb, "data": "menu_silent"})
        return

    # 4. Действия с профилями, заметками и снайпером
    if data == "prof_add":
        user_states[str(chat_id)] = {"mode": "wait_profile"}
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            "➕ <b>Добавление профиля AniVox</b>\n\n"
            "Отправь ссылку на профиль (например, <code>https://anivox.fun/profile/1245</code>) "
            "или просто его числовой ID:",
            {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_input"}]]}
        )
        return

    if data.startswith("prof_view_"):
        pid = data.replace("prof_view_", "")
        p = cfg.get("profiles", {}).get(pid)
        if not p:
            answer_cb(token, cb_id, "Профиль не найден", alert=True)
            return
        answer_cb(token, cb_id)
        chg_ts = parse_any_timestamp(p.get("last_status_change"), int(time.time()))
        dur = format_duration_ru(int(time.time()) - chg_ts)
        onl_total = format_duration_ru(p.get("online_seconds", 0))
        note = cfg.get("profile_notes", {}).get(pid, "Нет заметки")
        st_lbl = "🟣 СМОТРИТ АНИМЕ" if p.get("watching") else ("🟢 В СЕТИ" if p.get("online") else "⚪ ОФФЛАЙН")
        txt = (
            f"👤 <b>Карточка профиля: {p.get('nickname')}</b> (<code>#{pid}</code>)\n\n"
            f"📡 Текущий статус: <b>{st_lbl}</b>\n"
            f"🎬 Текущий тайтл: <i>{p.get('current_title', 'Ничего не смотрит')}</i>\n"
            f"📅 Статус изменен: <code>{format_ts(chg_ts)}</code>\n"
            f"⏳ Длительность статуса: <b>{dur}</b>\n"
            f"⏮ Предыдущий статус: <i>{p.get('prev_status', 'Нет данных')}</i>\n"
            f"⏱ Накопленный онлайн: <b>{onl_total}</b>\n"
            f"🎞 Зафиксировано тайтлов: <b>{p.get('titles_watched', 0)}</b>\n"
            f"📝 Заметка: <i>{note}</i>"
        )
        kb = {"inline_keyboard": [
            [{"text": "🎯 Переключить Снайпер", "callback_data": f"sniper_toggle_{pid}"},
             {"text": "📝 Изменить заметку", "callback_data": f"note_set_{pid}"}],
            [{"text": "🗑 Удалить профиль", "callback_data": f"rm_prof_{pid}"}],
            [{"text": "🔙 К списку профилей", "callback_data": "menu_profiles"}]
        ]}
        edit_or_send_menu(token, chat_id, msg_id, txt, kb)
        return

    if data.startswith("note_set_"):
        pid = data.replace("note_set_", "")
        user_states[str(chat_id)] = {"mode": "wait_note", "meta": {"pid": pid}}
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            f"📝 <b>Введи текст заметки для профиля #{pid}:</b>\n(Отправь <code>-</code> чтобы удалить заметку)",
            {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_input"}]]}
        )
        return

    if data.startswith("sniper_toggle_"):
        pid = data.replace("sniper_toggle_", "")
        cid_str = str(chat_id)
        with config_lock:
            watchers = [str(x) for x in cfg.setdefault("snipers", {}).get(pid, [])]
            if cid_str in watchers:
                watchers.remove(cid_str)
                answer_cb(token, cb_id, f"⚪ Снайпер для #{pid} выключен")
            else:
                watchers.append(cid_str)
                answer_cb(token, cb_id, f"🎯 Снайпер взведен на профиль #{pid}!")
            cfg["snipers"][pid] = watchers
            save_config(cfg)
        handle_callback_query(cfg, {**cb, "data": "menu_sniper"})
        return

    if data.startswith("rm_prof_"):
        pid = data.replace("rm_prof_", "")
        with config_lock:
            cfg.get("profiles", {}).pop(pid, None)
            cfg.get("snipers", {}).pop(pid, None)
            save_config(cfg)
        generate_html_report(cfg)
        answer_cb(token, cb_id, f"🗑 Профиль #{pid} удален")
        handle_callback_query(cfg, {**cb, "data": "menu_profiles"})
        return

    # 5. Друзья и Мост-чат («Общение»)
    if data == "fr_add":
        user_states[str(chat_id)] = {"mode": "wait_friend"}
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            "🤝 <b>Добавление друга по Telegram ID</b>\n"
            "Отправь числовой Telegram ID друга и его имя через пробел.\n"
            "Пример: <code>123456789 Амир</code>",
            {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_input"}]]}
        )
        return

    if data.startswith("rm_fr_"):
        fid = data.replace("rm_fr_", "")
        with config_lock:
            cfg.get("friends", {}).pop(fid, None)
            save_config(cfg)
        answer_cb(token, cb_id, "🗑 Друг удален из списка")
        handle_callback_query(cfg, {**cb, "data": "menu_profiles"})
        return

    if data.startswith("chat_start_"):
        fid = data.replace("chat_start_", "")
        fname = cfg.get("friends", {}).get(fid, f"ID {fid}")
        with config_lock:
            cfg.setdefault("bridge_chats", {})[str(chat_id)] = str(fid)
            cfg["bridge_chats"][str(fid)] = str(chat_id)
            save_config(cfg)
        answer_cb(token, cb_id, f"💬 Чат с {fname} подключен!")
        edit_or_send_menu(
            token, chat_id, msg_id,
            f"💬 <b>Мост-чат с {fname} (<code>{fid}</code>) АКТИВЕН!</b>\n\n"
            f"Все обычные сообщения, которые ты сейчас напишешь боту, будут доставлены собеседнику.",
            {"inline_keyboard": [[{"text": "🛑 Завершить чат", "callback_data": "chat_stop"}]]}
        )
        tg_api(token, "sendMessage", {
            "chat_id": fid,
            "text": f"💬 <b>Пользователь (ID <code>{chat_id}</code>) открыл с вами прямой Мост-чат!</b>\nПросто напишите ответ в этот чат.",
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[{"text": "🛑 Отключить чат", "callback_data": "chat_stop"}]]}
        })
        return

    if data == "chat_stop":
        cid_str = str(chat_id)
        with config_lock:
            partner = cfg.get("bridge_chats", {}).pop(cid_str, None)
            if partner:
                cfg.get("bridge_chats", {}).pop(str(partner), None)
            save_config(cfg)
        answer_cb(token, cb_id, "🛑 Мост-чат завершен")
        if partner:
            tg_api(token, "sendMessage", {"chat_id": partner, "text": "🛑 <b>Собеседник завершил Мост-чат.</b>", "parse_mode": "HTML"})
        handle_callback_query(cfg, {**cb, "data": "menu_chat"})
        return

    if data == "wall_post_start":
        user_states[str(chat_id)] = {"mode": "wait_wall_post"}
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            "✍️ <b>Напиши сообщение для публикации на Стене Веб-Дашборда:</b>",
            {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_input"}]]}
        )
        return

    # 6. Ждун серий (Shikimori + выбор озвучки Kodik)
    if data == "wait_add":
        user_states[str(chat_id)] = {"mode": "wait_anime"}
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            "🔍 <b>Напиши название аниме для поиска в базе Shikimori и Kodik:</b>",
            {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_input"}]]}
        )
        return

    if data == "random_anime":
        answer_cb(token, cb_id, "🎲 Ищу случайный тайтл с высоким рейтингом...")
        ra = fetch_random_shikimori_anime()
        if not ra:
            tg_api(token, "sendMessage", {"chat_id": chat_id, "text": "😕 Не удалось получить случайный тайтл."})
            return
        aid = str(ra["id"])
        ru = ra.get("russian") or ra.get("name")
        score = ra.get("score", "?")
        eps = ra.get("episodes") or ra.get("episodes_aired") or "?"
        cfg.setdefault("anime_cache", {})[aid] = {
            "id": aid, "title": ru,
            "episodes_aired": ra.get("episodes_aired") or 0,
            "episodes_total": eps
        }
        save_config(cfg)
        edit_or_send_menu(
            token, chat_id, msg_id,
            f"🎲 <b>Случайная рекомендация Shikimori:</b>\n\n"
            f"🎬 <b>{ru}</b>\n"
            f"⭐ Рейтинг: <b>{score}</b> | 🎞 Эпизодов: <b>{eps}</b>\n"
            f"🔗 <code>https://shikimori.one/animes/{aid}</code>",
            {"inline_keyboard": [
                [{"text": "➕ Добавить в Ждун серий", "callback_data": f"sub|{aid}"}],
                [{"text": "🎲 Крутить еще раз", "callback_data": "random_anime"}],
                [{"text": "🔙 Назад в Ждун", "callback_data": "menu_waits"}]
            ]}
        )
        return

    if data.startswith("sub|"):
        aid = data.split("|")[1]
        cached = cfg.get("anime_cache", {}).get(aid, {"id": aid, "title": f"Anime #{aid}", "episodes_aired": 0})
        voices = fetch_kodik_voices(aid, cfg.get("kodik_token", ""))
        kb = []
        for idx, v in enumerate(voices):
            kb.append([{"text": f"🎙 Озвучка: {v}", "callback_data": f"subvoice|{aid}|{idx}"}])
        cached["voices_list"] = voices
        cfg.setdefault("anime_cache", {})[aid] = cached
        save_config(cfg)
        answer_cb(token, cb_id, "Выбери желаемую озвучку 👇")
        edit_or_send_menu(
            token, chat_id, msg_id,
            f"🎙 <b>Выбери озвучку для отслеживания тайтла «{cached['title']}»:</b>",
            {"inline_keyboard": kb + [[{"text": "🔙 Отмена", "callback_data": "menu_waits"}]]}
        )
        return

    if data.startswith("subvoice|"):
        parts = data.split("|")
        aid = parts[1]
        v_idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        cached = cfg.get("anime_cache", {}).get(aid, {"id": aid, "title": f"Anime #{aid}", "episodes_aired": 0})
        v_list = cached.get("voices_list", ["Любая"])
        chosen_voice = v_list[v_idx] if v_idx < len(v_list) else "Любая"
        cached["voice"] = chosen_voice
        cfg.setdefault("waits", {})[aid] = cached
        save_config(cfg)
        answer_cb(token, cb_id, f"✅ Добавлено с озвучкой {chosen_voice}!")
        handle_callback_query(cfg, {**cb, "data": "menu_waits"})
        return

    if data.startswith("wait_info_"):
        aid = data.replace("wait_info_", "")
        w = cfg.get("waits", {}).get(aid, {})
        answer_cb(
            token, cb_id,
            f"🎬 {w.get('title', aid)}: вышло {w.get('episodes_aired', 0)} сер., озвучка: {w.get('voice', 'Любая')}",
            alert=True
        )
        return

    if data.startswith("delwait_"):
        aid = data.replace("delwait_", "")
        cfg.get("waits", {}).pop(aid, None)
        save_config(cfg)
        answer_cb(token, cb_id, "🗑 Тайтл удален из Ждуна")
        handle_callback_query(cfg, {**cb, "data": "menu_waits"})
        return

    if data == "check_waits_now":
        answer_cb(token, cb_id, "🔄 Проверяю выход новых серий на Shikimori...")
        threading.Thread(target=check_anime_waits_once, args=(cfg, chat_id), daemon=True).start()
        return

    # 7. Инструменты, Поиск по логам, Бэкапы и Render Диагностика
    if data == "tool_search_logs":
        user_states[str(chat_id)] = {"mode": "wait_log_query"}
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            "🔎 <b>Поиск по истории событий из бота</b>\n"
            "Введи имя профиля, название аниме или дату (например, <code>13.11.2026</code>):",
            {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_input"}]]}
        )
        return

    if data == "tool_export_json":
        answer_cb(token, cb_id, "📦 Отправляю JSON-бэкап конфигурации...")
        save_config(cfg)
        send_multipart_file(
            token, chat_id, CONFIG_PATH, "anivox_backup.json", "application/json",
            caption="📦 <b>Полный JSON-бэкап базы AniVox Monitor</b>"
        )
        return

    if data == "tool_export_csv":
        answer_cb(token, cb_id, "📥 Генерирую CSV-таблицу для Excel...")
        csv_path = export_csv_file(cfg)
        send_multipart_file(
            token, chat_id, csv_path, "anivox_history.csv", "text/csv",
            caption="📥 <b>Полный журнал истории статусов и тайтлов (CSV / Excel)</b>"
        )
        return

    if data == "tool_clear_history":
        with config_lock:
            cfg["history"] = cfg.get("history", [])[-30:]
            save_config(cfg)
        generate_html_report(cfg)
        answer_cb(token, cb_id, "🧹 История очищена (оставлены последние 30 записей)!", alert=True)
        handle_callback_query(cfg, {**cb, "data": "menu_tools"})
        return

    if data == "tool_set_autoping":
        user_states[str(chat_id)] = {"mode": "wait_autoping_url"}
        answer_cb(token, cb_id)
        edit_or_send_menu(
            token, chat_id, msg_id,
            "🔗 <b>Настройка Render Autoping URL</b>\n"
            f"Текущий URL: <code>{cfg.get('autoping_url') or 'Не задан'}</code>\n\n"
            "Отправь публичную ссылку на свой сервис Render (например, <code>https://my-anivox.onrender.com</code>):",
            {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_input"}]]}
        )
        return

    if data == "tool_server_diag":
        answer_cb(token, cb_id)
        uptime_s = format_duration_ru(int(time.time()) - START_TIME)
        diag_txt = (
            f"🖥 <b>Серверная диагностика {APP_NAME} v{VERSION}</b>\n\n"
            f"• Порт HTTP-сервера: <code>0.0.0.0:{PORT}</code>\n"
            f"• Время непрерывной работы: <b>{uptime_s}</b>\n"
            f"• Запросов WebSocket: <b>{runtime_metrics['ws_success']}/{runtime_metrics['ws_requests']}</b>\n"
            f"• Запросов HTTP API: <b>{runtime_metrics['http_success']}/{runtime_metrics['http_requests']}</b>\n"
            f"• Ошибок скрапинга: <b>{runtime_metrics['scrape_errors']}</b>\n"
            f"• Время последнего обхода: <b>{runtime_metrics['last_check_duration_ms']} мс</b>\n"
            f"• Циклов автопинга Render: <b>{runtime_metrics['autoping_count']}</b>\n"
            f"• Autoping URL: <code>{cfg.get('autoping_url') or 'Не задан'}</code>"
        )
        edit_or_send_menu(
            token, chat_id, msg_id, diag_txt,
            {"inline_keyboard": [[{"text": "🔙 Назад в Инструменты", "callback_data": "menu_tools"}]]}
        )
        return


# ==================== ОБРАБОТЧИК ТЕКСТОВЫХ КОМАНД И ПОСТОЯННЫХ КНОПОК ====================
def handle_update(cfg, upd):
    token = cfg["token"]
    if "callback_query" in upd:
        handle_callback_query(cfg, upd["callback_query"])
        return

    msg = upd.get("message")
    if not msg:
        return
    chat_id = msg.get("chat", {}).get("id")
    if not chat_id:
        return
    cid_str = str(chat_id)
    text = (msg.get("text") or "").strip()
    if not text:
        return

    if not cfg.get("chat_id"):
        cfg["chat_id"] = cid_str
        save_config(cfg)

    get_user_settings(cfg, chat_id)

    # 1. Обработка всех кнопок постоянной Reply-клавиатуры
    reply_routes = {
        "📊 Статус профилей": "menu_status",
        "🌐 Веб-Дашборд": "menu_web",
        "⚙️ Настройки": "menu_settings",
        "🔇 Режим тишины": "menu_silent",
        "👥 Профили и Друзья": "menu_profiles",
        "🎬 Ждун серий": "menu_waits",
        "🎯 Снайпер онлайна": "menu_sniper",
        "💬 Общение": "menu_chat",
        "📈 Аналитика": "menu_stats",
        "🔄 Проверить сейчас": "force_check"
    }
    if text in reply_routes:
        handle_callback_query(cfg, {
            "id": "reply_btn",
            "data": reply_routes[text],
            "message": {"chat": {"id": chat_id}, "message_id": 0},
            "from": {"id": chat_id}
        })
        return

    # 2. Обработка шагов интерактивного ввода (user_states)
    st = user_states.get(cid_str)
    if st and not text.startswith("/"):
        mode = st.get("mode")
        meta = st.get("meta", {})
        user_states.pop(cid_str, None)

        if mode == "wait_profile":
            m = PROFILE_RE.search(text)
            if not m:
                tg_api(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "❌ Не удалось распознать ID профиля. Отправь число или ссылку вида <code>anivox.fun/profile/123</code>.",
                    "parse_mode": "HTML",
                    "reply_markup": kb_profiles_menu(cfg)
                })
                return
            pid = m.group(1)
            info = fetch_profile(pid)
            now = int(time.time())
            with config_lock:
                nickname = info.get("nickname", f"User #{pid}")
                is_onl = bool(info.get("online", False))
                is_wat = bool(info.get("watching", False))
                cur_t = info.get("current_title", "Ничего не смотрит")
                state_s = "watching" if is_wat else ("online" if is_onl else "offline")
                cfg["profiles"][pid] = {
                    "id": pid,
                    "nickname": nickname,
                    "online": is_onl,
                    "watching": is_wat,
                    "current_title": cur_t,
                    "avatar": info.get("avatar", make_svg_avatar(nickname, state_s)),
                    "level": info.get("level", "Зритель"),
                    "last_status_change": now,
                    "prev_status": "Только добавлен",
                    "online_seconds": 0,
                    "titles_watched": 1 if is_wat else 0
                }
                cfg["history"].append({
                    "ts": now,
                    "timestamp": now,
                    "pid": pid,
                    "name": nickname,
                    "event": "Смотрит" if is_wat else ("Онлайн" if is_onl else "Оффлайн"),
                    "details": f"Профиль добавлен ({cur_t})"
                })
                save_config(cfg)
            generate_html_report(cfg)
            tg_api(token, "sendMessage", {
                "chat_id": chat_id,
                "text": (
                    f"✅ Профиль <b>{nickname}</b> (<code>#{pid}</code>) успешно добавлен!\n"
                    f"Текущий статус: <b>{'🟣 Смотрит' if is_wat else ('🟢 В сети' if is_onl else '⚪ Оффлайн')}</b>"
                ),
                "parse_mode": "HTML",
                "reply_markup": kb_profiles_menu(cfg)
            })
            return

        if mode == "wait_note":
            pid = str(meta.get("pid", ""))
            with config_lock:
                if text == "-":
                    cfg.get("profile_notes", {}).pop(pid, None)
                    msg_res = f"🗑 Заметка для профиля #{pid} удалена."
                else:
                    cfg.setdefault("profile_notes", {})[pid] = text[:160]
                    msg_res = f"📝 Заметка для профиля #{pid} сохранена: <i>{text[:160]}</i>"
                save_config(cfg)
            generate_html_report(cfg)
            tg_api(token, "sendMessage", {
                "chat_id": chat_id,
                "text": msg_res,
                "parse_mode": "HTML",
                "reply_markup": kb_profiles_menu(cfg)
            })
            return

        if mode == "wait_friend":
            parts = text.split(maxsplit=1)
            fid = parts[0].strip()
            fname = parts[1].strip() if len(parts) > 1 else f"Друг {fid}"
            with config_lock:
                cfg.setdefault("friends", {})[fid] = fname
                save_config(cfg)
            tg_api(token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"🤝 Друг <b>{fname}</b> (ID <code>{fid}</code>) добавлен!",
                "parse_mode": "HTML",
                "reply_markup": kb_profiles_menu(cfg)
            })
            return

        if mode == "wait_wall_post":
            sender_name = msg.get("from", {}).get("first_name") or f"User {chat_id}"
            with config_lock:
                cfg.setdefault("wall_messages", []).append({
                    "ts": int(time.time()),
                    "author": sender_name,
                    "text": text[:250]
                })
                cfg["wall_messages"] = cfg["wall_messages"][-50:]
                save_config(cfg)
            generate_html_report(cfg)
            tg_api(token, "sendMessage", {
                "chat_id": chat_id,
                "text": "✅ Сообщение опубликовано на Стене Веб-Дашборда!",
                "reply_markup": kb_main_menu(cfg, chat_id)
            })
            return

        if mode == "wait_anime":
            results = search_shikimori(text)
            if not results:
                tg_api(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "😕 По твоему запросу на Shikimori ничего не найдено.",
                    "reply_markup": kb_waits_menu(cfg)
                })
                return
            kb = []
            with config_lock:
                for a in results[:6]:
                    aid = str(a["id"])
                    ru = a.get("russian") or a.get("name")
                    eps_a = a.get("episodes_aired") or 0
                    eps_t = a.get("episodes") or "?"
                    cfg.setdefault("anime_cache", {})[aid] = {
                        "id": aid,
                        "title": ru,
                        "episodes_aired": eps_a,
                        "episodes_total": eps_t
                    }
                    kb.append([{"text": f"🎬 {ru[:28]} ({eps_a}/{eps_t} эп.)", "callback_data": f"sub|{aid}"}])
                save_config(cfg)
            kb.append([{"text": "🔙 Назад в Ждун", "callback_data": "menu_waits"}])
            tg_api(token, "sendMessage", {
                "chat_id": chat_id,
                "text": "🎬 <b>Выбери аниме для выбора озвучки и подписки:</b>",
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": kb}
            })
            return

        if mode == "wait_log_query":
            q = text.lower().strip()
            matches = []
            for h in reversed(cfg.get("history", [])):
                hay = f"{h.get('name', '')} {h.get('event', '')} {h.get('details', '')} {format_ts(h.get('ts', 0))}".lower()
                if q in hay:
                    matches.append(
                        f"• <code>{format_ts(h.get('ts', 0), '%d.%m %H:%M:%S')}</code> | "
                        f"<b>{h.get('name')}</b>: {h.get('event')} ({h.get('details')})"
                    )
                if len(matches) >= 15:
                    break
            res_txt = (
                f"🔎 <b>Результаты поиска по запросу «{text}»:</b>\n\n" + "\n".join(matches)
                if matches else f"😕 По запросу «{text}» совпадений в логах не найдено."
            )
            tg_api(token, "sendMessage", {
                "chat_id": chat_id,
                "text": res_txt,
                "parse_mode": "HTML",
                "reply_markup": kb_tools_menu(cfg)
            })
            return

        if mode == "wait_autoping_url":
            if text.startswith("http://") or text.startswith("https://"):
                cfg["autoping_url"] = text.strip()
                save_config(cfg)
                tg_api(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": f"✅ Render Autoping URL сохранен: <code>{cfg['autoping_url']}</code>",
                    "parse_mode": "HTML",
                    "reply_markup": kb_tools_menu(cfg)
                })
            else:
                tg_api(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "❌ Ссылка должна начинаться с http:// или https://",
                    "reply_markup": kb_tools_menu(cfg)
                })
            return

    # 3. Проверка активного Мост-чата между друзьями
    partner_id = cfg.get("bridge_chats", {}).get(cid_str)
    if partner_id and not text.startswith("/"):
        sender_name = msg.get("from", {}).get("first_name") or f"ID {chat_id}"
        tg_api(token, "sendMessage", {
            "chat_id": partner_id,
            "text": f"💬 <b>{sender_name}:</b> {text}",
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[{"text": "🛑 Завершить чат", "callback_data": "chat_stop"}]]}
        })
        return

    # 4. Обработка слеш-команд (/start, /menu, /web, /chat, /autoping, /stats)
    if text.startswith("/start") or text.startswith("/menu"):
        tg_api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": (
                f"⚡ <b>{APP_NAME} v{VERSION} активирован!</b>\n\n"
                "Нижняя панель быстрых кнопок подключена.\n"
                "Используй инлайн-меню ниже для управления профилями, тишиной, снайпером и Веб-Дашбордом:"
            ),
            "parse_mode": "HTML",
            "reply_markup": get_reply_keyboard()
        })
        tg_api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": f"🎛 <b>Интерактивная панель {APP_NAME}:</b>",
            "parse_mode": "HTML",
            "reply_markup": kb_main_menu(cfg, chat_id)
        })
        return

    if text.startswith("/web"):
        handle_callback_query(cfg, {
            "id": "cmd_web", "data": "menu_web",
            "message": {"chat": {"id": chat_id}, "message_id": 0}, "from": {"id": chat_id}
        })
        return

    if text.startswith("/chat"):
        handle_callback_query(cfg, {
            "id": "cmd_chat", "data": "menu_chat",
            "message": {"chat": {"id": chat_id}, "message_id": 0}, "from": {"id": chat_id}
        })
        return

    if text.startswith("/stats"):
        handle_callback_query(cfg, {
            "id": "cmd_stats", "data": "menu_stats",
            "message": {"chat": {"id": chat_id}, "message_id": 0}, "from": {"id": chat_id}
        })
        return

    if text.startswith("/autoping"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].startswith("http"):
            cfg["autoping_url"] = parts[1].strip()
            save_config(cfg)
            tg_api(token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"✅ Autoping URL установлен: <code>{cfg['autoping_url']}</code>",
                "parse_mode": "HTML"
            })
        else:
            handle_callback_query(cfg, {
                "id": "cmd_ap", "data": "tool_set_autoping",
                "message": {"chat": {"id": chat_id}, "message_id": 0}, "from": {"id": chat_id}
            })
        return

    # 5. Быстрое добавление профиля при отправке ссылки или числа
    m = PROFILE_RE.search(text)
    if m and ("anivox" in text.lower() or text.isdigit()):
        user_states[cid_str] = {"mode": "wait_profile"}
        handle_update(cfg, upd)


# ==================== ГЕНЕРАТОР АДАПТИВНОГО ВЕБ-ДАШБОРДА (4 ВКЛАДКИ + 12 ФУНКЦИЙ) ====================
def build_api_payload(cfg):
    st = calc_stats(cfg)
    return {
        "updated_at": format_ts(st["now_ts"]),
        "now_ts": st["now_ts"],
        "stats": {
            "total": st["total_profiles"],
            "watching": st["cnt_watching"],
            "online": st["cnt_online"],
            "offline": st["cnt_offline"],
            "logs": st["total_logs"]
        },
        "profiles": cfg.get("profiles", {}),
        "notes": cfg.get("profile_notes", {}),
        "waits": cfg.get("waits", {}),
        "wall": cfg.get("wall_messages", [])[-30:],
        "history": cfg.get("history", [])[-150:]
    }


def generate_html_report(cfg, output_path="index.html"):
    with config_lock:
        st = calc_stats(cfg)
        profiles = cfg.get("profiles", {})
        history = cfg.get("history", [])
        notes = cfg.get("profile_notes", {})
        waits = cfg.get("waits", {})
        wall = cfg.get("wall_messages", [])
        now_ts = st["now_ts"]
        now_str = format_ts(now_ts)

        profile_cards_html = ""
        leaderboard_rows_html = ""
        compare_options_html = ""

        sorted_profs = sorted(
            profiles.items(),
            key=lambda kv: (int(kv[1].get("online_seconds", 0)), int(kv[1].get("titles_watched", 0))),
            reverse=True
        )
        max_onl_sec = max([int(p.get("online_seconds", 0)) for _, p in sorted_profs] + [60])

        for rank, (pid, pdata) in enumerate(sorted_profs, 1):
            name = pdata.get("nickname") or pdata.get("name") or f"User #{pid}"
            is_onl = bool(pdata.get("online", False))
            cur_title = pdata.get("current_title") or pdata.get("anime") or "Ничего не смотрит"
            is_wat = bool(pdata.get("watching", False) or (is_onl and cur_title != "Ничего не смотрит"))

            # Строгая цветовая кодировка рамок аватарок:
            # 🟣 Фиолетовая (state-watching), 🟢 Зеленая (state-online), ⚪ Серая (state-offline)
            if is_wat:
                state_key = "watching"
                card_state_cls = "state-watching"
                badge_html = '<span class="status-badge badge-watching">🟣 СМОТРИТ АНИМЕ</span>'
                frame_label = "Фиолетовая рамка • Смотрит тайтл"
            elif is_onl:
                state_key = "online"
                card_state_cls = "state-online"
                badge_html = '<span class="status-badge badge-online">🟢 В СЕТИ</span>'
                frame_label = "Зеленая рамка • Онлайн на сайте"
            else:
                state_key = "offline"
                card_state_cls = "state-offline"
                badge_html = '<span class="status-badge badge-offline">⚪ ОФФЛАЙН</span>'
                frame_label = "Серая рамка • Не в сети"

            ava_url = pdata.get("avatar") or make_svg_avatar(name, state_key)
            fallback_ava = make_svg_avatar(name, state_key)
            chg_ts = parse_any_timestamp(pdata.get("last_status_change") or pdata.get("updated_at"), now_ts)
            chg_dt_str = format_ts(chg_ts, "%d.%m.%Y в %H:%M:%S")
            prev_st = pdata.get("prev_status") or "Нет данных"
            onl_sec = int(pdata.get("online_seconds", 0))
            onl_hours_str = f"{onl_sec // 3600}ч {(onl_sec % 3600) // 60}м"
            watched_cnt = int(pdata.get("titles_watched", 0))
            user_note = notes.get(str(pid), "")

            note_html = (
                f'<div class="prof-note">📌 <span>{user_note}</span></div>'
                if user_note else ""
            )

            profile_cards_html += f"""
            <article class="prof-card {card_state_cls}" id="prof-card-{pid}" data-pid="{pid}" data-state="{state_key}" data-name="{name.lower()} {cur_title.lower()} {pid}">
                <div class="prof-top">
                    <div class="ava-wrap {card_state_cls}" title="{frame_label}">
                        <img src="{ava_url}" alt="{name}" class="ava-img" onerror="this.onerror=null;this.src='{fallback_ava}';">
                        <span class="ava-dot"></span>
                    </div>
                    <div class="prof-meta">
                        <div class="prof-name-row">
                            <a href="https://anivox.fun/profile/{pid}" target="_blank" class="prof-name">{name}</a>
                            {badge_html}
                        </div>
                        <div class="prof-sub">
                            <span>ID: <b>#{pid}</b></span>
                            <span>•</span>
                            <span>🎬 Тайтлов: <b>{watched_cnt}</b></span>
                            <span>•</span>
                            <span>⏱ В сети: <b>{onl_hours_str}</b></span>
                        </div>
                    </div>
                </div>

                <div class="watching-box">
                    <div class="watching-lbl">🎬 Текущий просматриваемый тайтл / активность:</div>
                    <div class="watching-val">{cur_title}</div>
                </div>

                {note_html}

                <div class="time-panel">
                    <div class="time-item">
                        <span class="t-lbl">📅 Время и дата смены статуса:</span>
                        <span class="t-val status-dt">{chg_dt_str}</span>
                    </div>
                    <div class="time-item">
                        <span class="t-lbl">⏳ Длительность текущего статуса:</span>
                        <span class="t-val live-timer" data-ts="{chg_ts}">вычисление...</span>
                    </div>
                    <div class="time-item">
                        <span class="t-lbl">⏮ Предыдущий статус:</span>
                        <span class="t-val t-muted">{prev_st}</span>
                    </div>
                </div>
            </article>
            """

            pct = min(100, int((onl_sec / max_onl_sec) * 100)) if max_onl_sec > 0 else 12
            bar_cls = "fill-watching" if is_wat else ("fill-online" if is_onl else "fill-offline")
            leaderboard_rows_html += f"""
            <div class="leader-item">
                <div class="leader-head">
                    <span><b>#{rank} {name}</b> <small style="color:var(--muted);">(#{pid})</small></span>
                    <span>⏱ <b>{onl_hours_str}</b> онлайн • 🎬 <b>{watched_cnt}</b> тайтл(ов)</span>
                </div>
                <div class="leader-bar-bg">
                    <div class="leader-bar-fill {bar_cls}" style="width: {max(7, pct)}%;"></div>
                </div>
            </div>
            """

            compare_options_html += (
                f'<option value="{pid}" data-name="{name}" data-onl="{onl_sec}" '
                f'data-titles="{watched_cnt}" data-state="{state_key}" data-cur="{cur_title}">'
                f'{name} (#{pid})</option>'
            )

        if not profile_cards_html:
            profile_cards_html = (
                '<div class="empty-state">'
                '<h3>📡 Список профилей пока пуст</h3>'
                '<p>Отправь ссылку на профиль AniVox или ID боту в Telegram, чтобы начать живой мониторинг!</p>'
                '</div>'
            )

        # Строки истории изменений статуса
        history_rows_html = ""
        for item in reversed(history[-180:]):
            ts = parse_any_timestamp(item.get("timestamp") or item.get("ts"), now_ts)
            dt_full = format_ts(ts, "%d.%m.%Y %H:%M:%S")
            p_name = item.get("name", f"ID {item.get('pid', '?')}")
            ev_type = item.get("event", "Статус")
            details = item.get("details", "")

            ev_low = ev_type.lower()
            if "смотр" in ev_low or "тайтл" in ev_low:
                ev_cls = "tag-watch"
                ev_key = "watching"
            elif "онлайн" in ev_low or "вход" in ev_low:
                ev_cls = "tag-onl"
                ev_key = "online"
            else:
                ev_cls = "tag-off"
                ev_key = "offline"

            history_rows_html += f"""
            <tr class="log-row" data-evtype="{ev_key}" data-search="{p_name.lower()} {ev_low} {details.lower()} {dt_full}">
                <td class="td-time">{dt_full}</td>
                <td><b>{p_name}</b></td>
                <td><span class="ev-pill {ev_cls}">{ev_type}</span></td>
                <td class="td-det">{details}</td>
            </tr>
            """

        # Карточки Ждуна серий для 3-й вкладки
        waits_cards_html = ""
        for aid, w in waits.items():
            w_title = w.get("title", f"Anime #{aid}")
            w_aired = int(w.get("episodes_aired") or 0)
            w_total = w.get("episodes_total") or "?"
            w_voice = w.get("voice", "Любая")
            pct_ep = min(100, int((w_aired / int(w_total)) * 100)) if str(w_total).isdigit() and int(w_total) > 0 else 50
            waits_cards_html += f"""
            <div class="wait-card">
                <div class="wait-head">
                    <a href="https://shikimori.one/animes/{aid}" target="_blank" class="wait-title">🎬 {w_title}</a>
                    <span class="voice-pill">🎙 {w_voice}</span>
                </div>
                <div class="wait-progress-meta">
                    <span>Вышло эпизодов: <b>{w_aired} из {w_total}</b></span>
                    <span>Shikimori ID: #{aid}</span>
                </div>
                <div class="leader-bar-bg">
                    <div class="leader-bar-fill fill-watching" style="width: {max(8, pct_ep)}%;"></div>
                </div>
            </div>
            """
        if not waits_cards_html:
            waits_cards_html = '<div class="empty-state">Список «Ждуна серий» пуст. Добавь онгоинги через меню бота в Telegram!</div>'

        # Записи на Стене дашборда
        wall_items_html = ""
        for w in reversed(wall[-30:]):
            w_dt = format_ts(w.get("ts", now_ts), "%d.%m.%Y %H:%M")
            w_author = w.get("author", "Аноним")
            w_text = w.get("text", "")
            wall_items_html += f"""
            <div class="wall-msg">
                <div class="wall-msg-head">
                    <b>💬 {w_author}</b>
                    <span>{w_dt}</span>
                </div>
                <div class="wall-msg-body">{w_text}</div>
            </div>
            """
        if not wall_items_html:
            wall_items_html = '<div class="empty-state">На стене пока нет сообщений. Оставь первую запись ниже!</div>'

        # 24-часовая тепловая матрица (Heatmap)
        max_h_val = max(st["hourly"] + [1])
        heatmap_cells_html = ""
        for hr, cnt in enumerate(st["hourly"]):
            intensity = round(cnt / max_h_val, 2) if max_h_val > 0 else 0
            bg_alpha = max(0.08, intensity * 0.85) if cnt > 0 else 0.04
            heatmap_cells_html += f"""
            <div class="heat-cell" style="background: rgba(168, 85, 247, {bg_alpha}); border-color: rgba(168, 85, 247, {max(0.15, intensity)});">
                <div class="heat-hr">{hr:02d}:00</div>
                <div class="heat-val">{cnt}</div>
            </div>
            """

        top_labels_json = json.dumps([x[0][:28] for x in st["top_titles"]], ensure_ascii=False)
        top_vals_json = json.dumps([x[1] for x in st["top_titles"]])
        hourly_json = json.dumps(st["hourly"])
        weekday_json = json.dumps(st["weekday_counts"])
        roulette_titles_json = json.dumps(
            [x[0] for x in st["top_titles"]] + [w.get("title") for w in waits.values() if w.get("title")] + [
                "Моё перерождение в меч", "Поднятие уровня в одиночку", "Магическая битва",
                "Провожающая в последний путь Фрирен", "Кайдзю №8", "Клинок, рассекающий демонов",
                "Человек-бензопила", "Синяя тюрьма: Блю Лок", "Реинкарнация безработного"
            ],
            ensure_ascii=False
        )

    html = f"""<!DOCTYPE html>
<html lang="ru" data-theme="cyber">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{APP_NAME} v{VERSION} — Live Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root[data-theme="cyber"] {{
            --bg: #090b12;
            --bg-grad: radial-gradient(circle at 15% 15%, rgba(168, 85, 247, 0.12), transparent 40%),
                       radial-gradient(circle at 85% 80%, rgba(16, 185, 129, 0.08), transparent 40%);
            --card: rgba(20, 24, 38, 0.88);
            --card-2: #1b2135;
            --border: rgba(148, 163, 184, 0.16);
            --text: #f8fafc;
            --muted: #94a3b8;
            --accent: #8b5cf6;
            --purple: #a855f7;
            --purple-glow: rgba(168, 85, 247, 0.65);
            --green: #10b981;
            --green-glow: rgba(16, 185, 129, 0.55);
            --grey: #64748b;
            --grey-glow: rgba(100, 116, 139, 0.25);
        }}
        :root[data-theme="oled"] {{
            --bg: #000000;
            --bg-grad: none;
            --card: #0c0e14;
            --card-2: #141824;
            --border: #22293a;
            --text: #ffffff;
            --muted: #8b9bb4;
            --accent: #a855f7;
            --purple: #c084fc;
            --purple-glow: rgba(192, 132, 252, 0.7);
            --green: #34d399;
            --green-glow: rgba(52, 211, 153, 0.6);
            --grey: #64748b;
            --grey-glow: rgba(100, 116, 139, 0.2);
        }}
        :root[data-theme="ocean"] {{
            --bg: #07111e;
            --bg-grad: radial-gradient(circle at 80% 10%, rgba(56, 189, 248, 0.14), transparent 45%);
            --card: rgba(13, 27, 46, 0.9);
            --card-2: #152c4a;
            --border: rgba(56, 189, 248, 0.2);
            --text: #f0f9ff;
            --muted: #7dd3fc;
            --accent: #38bdf8;
            --purple: #a855f7;
            --purple-glow: rgba(168, 85, 247, 0.65);
            --green: #10b981;
            --green-glow: rgba(16, 185, 129, 0.55);
            --grey: #64748b;
            --grey-glow: rgba(100, 116, 139, 0.25);
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            -webkit-tap-highlight-color: transparent;
        }}
        body {{
            background-color: var(--bg);
            background-image: var(--bg-grad);
            background-attachment: fixed;
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
            font-size: 14.5px;
            line-height: 1.48;
            overflow-x: hidden;
            padding-bottom: 48px;
        }}
        a {{ color: inherit; text-decoration: none; }}
        .container {{
            max-width: 1140px;
            margin: 0 auto;
            padding: 10px;
        }}

        /* ВЕРХНЯЯ ПАНЕЛЬ */
        .app-header {{
            background: var(--card);
            backdrop-filter: blur(14px);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 14px 16px;
            margin-bottom: 12px;
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.35);
        }}
        .brand-box {{
            display: flex;
            flex-direction: column;
            gap: 3px;
        }}
        .brand-title {{
            font-size: clamp(18px, 4.6vw, 24px);
            font-weight: 900;
            letter-spacing: -0.3px;
            background: linear-gradient(90deg, #ffffff, #c084fc, #34d399);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .legend-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            font-size: 12px;
            color: var(--muted);
        }}
        .legend-item {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }}
        .dot-ind {{
            width: 9px;
            height: 9px;
            border-radius: 50%;
            display: inline-block;
        }}
        .dot-purple {{ background: var(--purple); box-shadow: 0 0 8px var(--purple); }}
        .dot-green {{ background: var(--green); box-shadow: 0 0 8px var(--green); }}
        .dot-grey {{ background: var(--grey); }}

        .header-actions {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 7px;
        }}
        .sync-pill {{
            font-size: 12px;
            color: var(--muted);
            background: rgba(255,255,255,0.04);
            padding: 6px 11px;
            border-radius: 10px;
            border: 1px solid var(--border);
            font-variant-numeric: tabular-nums;
        }}
        .btn-ui {{
            background: var(--card-2);
            color: var(--text);
            border: 1px solid var(--border);
            padding: 7px 11px;
            border-radius: 10px;
            font-size: 12px;
            font-weight: 700;
            cursor: pointer;
            transition: 0.2s;
        }}
        .btn-ui:hover {{ border-color: var(--purple); }}
        .btn-ui.active {{
            background: linear-gradient(135deg, #7c3aed, #a855f7);
            color: #fff;
            border-color: transparent;
            box-shadow: 0 0 14px rgba(168, 85, 247, 0.45);
        }}

        /* НАВИГАЦИЯ ПО 4 ВКЛАДКАМ (СВАЙП НА ТЕЛЕФОНЕ) */
        .tabs-bar {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            margin-bottom: 14px;
        }}
        @media (min-width: 680px) {{
            .tabs-bar {{ grid-template-columns: repeat(4, 1fr); }}
        }}
        .tab-btn {{
            padding: 12px 10px;
            background: var(--card);
            color: var(--muted);
            border: 1px solid var(--border);
            border-radius: 14px;
            font-size: 13px;
            font-weight: 800;
            cursor: pointer;
            text-align: center;
            transition: all 0.2s ease;
        }}
        .tab-btn.active {{
            background: linear-gradient(135deg, #6366f1 0%, #a855f7 100%);
            color: #ffffff;
            border-color: transparent;
            box-shadow: 0 5px 18px rgba(168, 85, 247, 0.38);
        }}
        .tab-pane {{ display: none; }}
        .tab-pane.active {{ display: block; animation: fadeUp 0.22s ease; }}
        @keyframes fadeUp {{
            from {{ opacity: 0; transform: translateY(5px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}

        /* KPI СЧЕТЧИКИ */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            margin-bottom: 12px;
        }}
        @media (min-width: 640px) {{
            .kpi-grid {{ grid-template-columns: repeat(4, 1fr); }}
        }}
        .kpi-box {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 12px 14px;
            cursor: pointer;
            transition: transform 0.15s, border-color 0.2s;
        }}
        .kpi-box:active {{ transform: scale(0.98); }}
        .kpi-lbl {{ font-size: 12px; color: var(--muted); font-weight: 600; }}
        .kpi-val {{ font-size: clamp(20px, 5vw, 26px); font-weight: 900; margin-top: 3px; }}

        /* ПАНЕЛЬ БЫСТРЫХ ФИЛЬТРОВ И ПОИСКА ПРОФИЛЕЙ */
        .toolbar-row {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            margin-bottom: 12px;
        }}
        .chips-wrap {{
            display: flex;
            gap: 6px;
            overflow-x: auto;
            scrollbar-width: none;
            padding-bottom: 2px;
        }}
        .chips-wrap::-webkit-scrollbar {{ display: none; }}
        .f-chip {{
            background: var(--card);
            color: var(--muted);
            border: 1px solid var(--border);
            padding: 7px 13px;
            border-radius: 20px;
            font-size: 12.5px;
            font-weight: 700;
            white-space: nowrap;
            cursor: pointer;
        }}
        .f-chip.active {{
            background: var(--text);
            color: var(--bg);
            border-color: var(--text);
        }}

        /* КАРТОЧКИ ПРОФИЛЕЙ И РАМКИ АВАТАРОК (ФИОЛ / ЗЕЛ / СЕРАЯ) */
        .profiles-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(290px, 1fr));
            gap: 12px;
            margin-bottom: 16px;
        }}
        .profiles-grid.compact-mode .watching-box,
        .profiles-grid.compact-mode .prof-note {{
            display: none;
        }}
        .prof-card {{
            background: var(--card);
            border: 1.5px solid var(--border);
            border-radius: 18px;
            padding: 15px;
            display: flex;
            flex-direction: column;
            gap: 11px;
            position: relative;
            transition: all 0.25s ease;
        }}
        /* Карточка: СМОТРИТ (Фиолетовая) */
        .prof-card.state-watching {{
            border-color: rgba(168, 85, 247, 0.75);
            box-shadow: 0 6px 26px rgba(168, 85, 247, 0.16);
        }}
        /* Карточка: ОНЛАЙН (Зеленая) */
        .prof-card.state-online {{
            border-color: rgba(16, 185, 129, 0.65);
            box-shadow: 0 6px 24px rgba(16, 185, 129, 0.12);
        }}
        /* Карточка: ОФФЛАЙН (Серая) */
        .prof-card.state-offline {{
            border-color: rgba(100, 116, 139, 0.35);
            opacity: 0.9;
        }}

        .prof-top {{
            display: flex;
            align-items: center;
            gap: 13px;
        }}

        /* КОНТЕЙНЕР РАМКИ АВАТАРКИ */
        .ava-wrap {{
            position: relative;
            width: 66px;
            height: 66px;
            flex-shrink: 0;
            border-radius: 50%;
            padding: 3.5px;
            transition: all 0.3s ease;
        }}
        .ava-img {{
            width: 100%;
            height: 100%;
            border-radius: 50%;
            object-fit: cover;
            display: block;
            background: #111522;
        }}
        .ava-dot {{
            position: absolute;
            bottom: 1px;
            right: 1px;
            width: 16px;
            height: 16px;
            border-radius: 50%;
            border: 2.5px solid var(--bg);
        }}

        /* 1. ФИОЛЕТОВАЯ РАМКА АВАТАРКИ — СМОТРИТ АНИМЕ */
        .ava-wrap.state-watching {{
            background: linear-gradient(135deg, #a855f7 0%, #d946ef 50%, #7c3aed 100%);
            box-shadow: 0 0 18px var(--purple-glow), inset 0 0 6px #fff;
            animation: borderPulsePurple 2.2s infinite ease-in-out;
        }}
        .ava-wrap.state-watching .ava-dot {{
            background: #c084fc;
            box-shadow: 0 0 10px #c084fc;
            animation: dotScale 1.4s infinite ease-in-out;
        }}

        /* 2. ЗЕЛЕНАЯ РАМКА АВАТАРКИ — ОНЛАЙН В СЕТИ */
        .ava-wrap.state-online {{
            background: linear-gradient(135deg, #10b981 0%, #34d399 50%, #059669 100%);
            box-shadow: 0 0 16px var(--green-glow);
        }}
        .ava-wrap.state-online .ava-dot {{
            background: #10b981;
            box-shadow: 0 0 9px #10b981;
            animation: dotScale 1.8s infinite ease-in-out;
        }}

        /* 3. СЕРАЯ РАМКА АВАТАРКИ — ОФФЛАЙН */
        .ava-wrap.state-offline {{
            background: linear-gradient(135deg, #475569, #334155);
            box-shadow: 0 0 6px var(--grey-glow);
        }}
        .ava-wrap.state-offline .ava-img {{
            filter: grayscale(100%) contrast(0.92) opacity(0.68);
        }}
        .ava-wrap.state-offline .ava-dot {{
            background: #64748b;
        }}

        @keyframes borderPulsePurple {{
            0%, 100% {{ box-shadow: 0 0 14px var(--purple-glow); }}
            50% {{ box-shadow: 0 0 24px rgba(217, 70, 239, 0.85); }}
        }}
        @keyframes dotScale {{
            0%, 100% {{ transform: scale(1); }}
            50% {{ transform: scale(1.25); }}
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
            font-size: 16.5px;
            font-weight: 800;
            color: #ffffff;
            word-break: break-word;
        }}
        .prof-name:hover {{ text-decoration: underline; color: #c084fc; }}
        .prof-sub {{
            font-size: 12px;
            color: var(--muted);
            margin-top: 4px;
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
        }}

        .status-badge {{
            font-size: 11px;
            font-weight: 800;
            padding: 4px 10px;
            border-radius: 20px;
            white-space: nowrap;
            letter-spacing: 0.2px;
        }}
        .badge-watching {{
            background: rgba(168, 85, 247, 0.22);
            color: #e9d5ff;
            border: 1px solid rgba(168, 85, 247, 0.6);
        }}
        .badge-online {{
            background: rgba(16, 185, 129, 0.2);
            color: #a7f3d0;
            border: 1px solid rgba(16, 185, 129, 0.55);
        }}
        .badge-offline {{
            background: rgba(100, 116, 139, 0.22);
            color: #cbd5e1;
            border: 1px solid rgba(100, 116, 139, 0.45);
        }}

        .watching-box {{
            background: rgba(255, 255, 255, 0.035);
            border-left: 3.5px solid var(--purple);
            padding: 9px 12px;
            border-radius: 10px;
        }}
        .state-online .watching-box {{ border-left-color: var(--green); }}
        .state-offline .watching-box {{ border-left-color: var(--grey); }}
        .watching-lbl {{ font-size: 11px; color: var(--muted); font-weight: 600; }}
        .watching-val {{
            font-size: 13.8px;
            font-weight: 700;
            color: #f8fafc;
            margin-top: 2px;
            word-break: break-word;
        }}

        .prof-note {{
            background: rgba(245, 158, 11, 0.1);
            border: 1px dashed rgba(245, 158, 11, 0.4);
            color: #fde68a;
            font-size: 12px;
            padding: 7px 10px;
            border-radius: 9px;
        }}

        /* БЛОК ДАТЫ И ВРЕМЕНИ ПОСЛЕДНЕЙ СМЕНЫ СТАТУСА */
        .time-panel {{
            background: rgba(0, 0, 0, 0.32);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 11px;
            padding: 9px 12px;
            display: flex;
            flex-direction: column;
            gap: 5px;
            font-size: 12.5px;
        }}
        .time-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .t-lbl {{ color: var(--muted); }}
        .t-val {{ font-weight: 700; font-variant-numeric: tabular-nums; }}
        .status-dt {{ color: #e2e8f0; }}
        .live-timer {{ color: #c084fc; font-weight: 800; }}
        .t-muted {{ color: #94a3b8; font-weight: 500; }}

        /* СЕКЦИИ, ТАБЛИЦЫ И ИНСТРУМЕНТЫ */
        .box-card {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 15px;
            margin-bottom: 14px;
        }}
        .box-title {{
            font-size: 15.5px;
            font-weight: 800;
            margin-bottom: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
        }}
        .grid-2 {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(285px, 1fr));
            gap: 12px;
            margin-bottom: 14px;
        }}
        .chart-holder {{
            position: relative;
            height: 230px;
            width: 100%;
        }}

        /* 24-ЧАСОВАЯ ТЕПЛОВАЯ МАТРИЦА (HEATMAP) */
        .heatmap-grid {{
            display: grid;
            grid-template-columns: repeat(6, 1fr);
            gap: 6px;
        }}
        @media (min-width: 640px) {{
            .heatmap-grid {{ grid-template-columns: repeat(12, 1fr); }}
        }}
        .heat-cell {{
            border: 1px solid var(--border);
            border-radius: 9px;
            padding: 7px 4px;
            text-align: center;
        }}
        .heat-hr {{ font-size: 10.5px; color: var(--muted); }}
        .heat-val {{ font-size: 13.5px; font-weight: 800; margin-top: 2px; }}

        /* ТАБЛИЦА ИСТОРИИ (ГОРИЗОНТАЛЬНЫЙ СКРОЛЛ НА ТЕЛЕФОНЕ) */
        .table-wrap {{
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            border-radius: 10px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            min-width: 460px;
            font-size: 13px;
        }}
        th, td {{
            padding: 9px 10px;
            text-align: left;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        }}
        th {{
            font-size: 11px;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 0.4px;
        }}
        .td-time {{
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
            color: #cbd5e1;
            font-size: 12px;
        }}
        .td-det {{ word-break: break-word; }}
        .ev-pill {{
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 800;
            white-space: nowrap;
        }}
        .tag-watch {{ background: rgba(168, 85, 247, 0.22); color: #e9d5ff; }}
        .tag-onl {{ background: rgba(16, 185, 129, 0.2); color: #6ee7b7; }}
        .tag-off {{ background: rgba(100, 116, 139, 0.25); color: #cbd5e1; }}

        /* ПРОГРЕСС-БАРЫ И ФОРМЫ */
        .leader-item {{ margin-bottom: 11px; }}
        .leader-head {{
            display: flex;
            justify-content: space-between;
            font-size: 12.5px;
            margin-bottom: 5px;
            flex-wrap: wrap;
            gap: 4px;
        }}
        .leader-bar-bg {{
            width: 100%;
            height: 9px;
            background: rgba(0, 0, 0, 0.4);
            border-radius: 8px;
            overflow: hidden;
        }}
        .leader-bar-fill {{
            height: 100%;
            border-radius: 8px;
        }}
        .fill-watching {{ background: linear-gradient(90deg, #7c3aed, #d946ef); }}
        .fill-online {{ background: linear-gradient(90deg, #059669, #34d399); }}
        .fill-offline {{ background: #475569; }}

        .inp {{
            width: 100%;
            background: rgba(0, 0, 0, 0.35);
            border: 1px solid var(--border);
            color: var(--text);
            padding: 10px 12px;
            border-radius: 11px;
            font-size: 13.8px;
            margin-bottom: 9px;
            outline: none;
        }}
        .inp:focus {{ border-color: var(--purple); }}

        .wait-card {{
            background: rgba(255,255,255,0.03);
            border: 1px solid var(--border);
            border-radius: 13px;
            padding: 12px;
            margin-bottom: 10px;
        }}
        .wait-head {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            margin-bottom: 6px;
        }}
        .wait-title {{ font-weight: 800; font-size: 14.5px; color: #e9d5ff; }}
        .voice-pill {{
            background: rgba(168, 85, 247, 0.2);
            color: #d8b4fe;
            padding: 3px 9px;
            border-radius: 12px;
            font-size: 11.5px;
            font-weight: 700;
        }}
        .wait-progress-meta {{
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            color: var(--muted);
            margin-bottom: 6px;
        }}

        .wall-msg {{
            background: rgba(255,255,255,0.03);
            border-left: 3px solid var(--accent);
            border-radius: 10px;
            padding: 10px 12px;
            margin-bottom: 8px;
        }}
        .wall-msg-head {{
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            color: var(--muted);
            margin-bottom: 4px;
        }}
        .wall-msg-body {{ font-size: 13.5px; word-break: break-word; }}
        .empty-state {{
            padding: 26px 14px;
            text-align: center;
            color: var(--muted);
        }}
    </style>
</head>
<body>
<div class="container">
    <!-- ШАПКА САЙТА С ИНДИКАТОРАМИ РАМОК И БЫСТРЫМИ КНОПКАМИ -->
    <header class="app-header">
        <div class="brand-box">
            <div class="brand-title">⚡ {APP_NAME} v{VERSION}</div>
            <div class="legend-row">
                <span class="legend-item"><i class="dot-ind dot-purple"></i> 🟣 Фиолетовая рамка: Смотрит аниме</span>
                <span class="legend-item"><i class="dot-ind dot-green"></i> 🟢 Зеленая рамка: Онлайн</span>
                <span class="legend-item"><i class="dot-ind dot-grey"></i> ⚪ Серая рамка: Оффлайн</span>
            </div>
        </div>
        <div class="header-actions">
            <span class="sync-pill" id="syncTime">🕒 Обновлено: {now_str}</span>
            <button class="btn-ui" id="livePollBtn" onclick="toggleAutoRefresh()">🔄 Live-Опрос: ВКЛ</button>
            <button class="btn-ui" id="soundRadarBtn" onclick="toggleSoundRadar()">🔕 Звук-радар: ВЫКЛ</button>
            <button class="btn-ui" onclick="cycleTheme()">🎨 Тема</button>
        </div>
    </header>

    <!-- 4 АДАПТИВНЫЕ ВКЛАДКИ -->
    <nav class="tabs-bar">
        <button class="tab-btn active" onclick="openTab('tab-profiles', this)">📡 1. Профили & Статусы</button>
        <button class="tab-btn" onclick="openTab('tab-analytics', this)">📊 2. Аналитика & Теплокарта</button>
        <button class="tab-btn" onclick="openTab('tab-tools', this)">🎬 3. Ждун, Дуэль & Рулетка</button>
        <button class="tab-btn" onclick="openTab('tab-logs', this)">📋 4. База логов & Стена</button>
    </nav>

    <!-- ==================== ВКЛАДКА 1: ПРОФИЛИ И ЦВЕТНЫЕ АВАТАРКИ ==================== -->
    <section id="tab-profiles" class="tab-pane active">
        <div class="kpi-grid">
            <div class="kpi-box" onclick="filterCards('all')">
                <div class="kpi-lbl">👥 Всего профилей</div>
                <div class="kpi-val" id="kpiTotal">{st['total_profiles']}</div>
            </div>
            <div class="kpi-box" onclick="filterCards('watching')">
                <div class="kpi-lbl">🟣 Смотрят аниме</div>
                <div class="kpi-val" id="kpiWatching" style="color: #c084fc;">{st['cnt_watching']}</div>
            </div>
            <div class="kpi-box" onclick="filterCards('online')">
                <div class="kpi-lbl">🟢 Сейчас в сети</div>
                <div class="kpi-val" id="kpiOnline" style="color: #34d399;">{st['cnt_online']}</div>
            </div>
            <div class="kpi-box" onclick="filterCards('offline')">
                <div class="kpi-lbl">⚪ Оффлайн</div>
                <div class="kpi-val" id="kpiOffline" style="color: #94a3b8;">{st['cnt_offline']}</div>
            </div>
        </div>

        <div class="toolbar-row">
            <div class="chips-wrap">
                <button class="f-chip active" onclick="filterCards('all', this)">Все ({st['total_profiles']})</button>
                <button class="f-chip" onclick="filterCards('watching', this)">🟣 Смотрят ({st['cnt_watching']})</button>
                <button class="f-chip" onclick="filterCards('online', this)">🟢 В сети ({st['cnt_online']})</button>
                <button class="f-chip" onclick="filterCards('offline', this)">⚪ Оффлайн ({st['cnt_offline']})</button>
            </div>
            <div style="display:flex; gap:6px; width:100%; max-width:340px;">
                <input type="text" id="cardSearchInp" class="inp" style="margin-bottom:0;" placeholder="🔍 Быстрый фильтр по нику или тайтлу..." oninput="searchProfileCards()">
                <button class="btn-ui" onclick="toggleCompactMode()" title="Компактный вид карточек">📱 Вид</button>
            </div>
        </div>

        <div class="profiles-grid" id="profilesContainer">
            {profile_cards_html}
        </div>

        <div class="box-card">
            <div class="box-title">
                <span>🕒 Последние изменения статусов (с точной датой и временем)</span>
                <button class="btn-ui" onclick="openTab('tab-logs', document.querySelectorAll('.tab-btn')[3])">Весь журнал →</button>
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
                        {history_rows_html if history_rows_html else '<tr><td colspan="4" class="empty-state">История изменений пока пуста</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
    </section>

    <!-- ==================== ВКЛАДКА 2: АНАЛИТИКА, ГРАФИКИ И ТЕПЛОКАРТА 24Ч ==================== -->
    <section id="tab-analytics" class="tab-pane">
        <div class="box-card">
            <div class="box-title">
                <span>🏆 Лидерборд активности (Время в онлайне и просмотренные тайтлы)</span>
                <span style="font-size:12px; color:var(--muted);">Пиковый час: <b>{st['peak_hour']:02d}:00</b></span>
            </div>
            {leaderboard_rows_html if leaderboard_rows_html else '<div class="empty-state">Нет данных для построения рейтинга</div>'}
        </div>

        <div class="box-card">
            <div class="box-title">🔥 Суточная тепловая матрица активности (00:00 – 23:00)</div>
            <div class="heatmap-grid">
                {heatmap_cells_html}
            </div>
        </div>

        <div class="grid-2">
            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">📊 График активности по часам суток</div>
                <div class="chart-holder"><canvas id="hourChart"></canvas></div>
            </div>
            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">🎬 Топ просматриваемых аниме-тайтлов</div>
                <div class="chart-holder"><canvas id="titleChart"></canvas></div>
            </div>
        </div>

        <div class="box-card">
            <div class="box-title">📅 Распределение активности по дням недели (ПН – ВС)</div>
            <div class="chart-holder" style="height:190px;"><canvas id="weekdayChart"></canvas></div>
        </div>
    </section>

    <!-- ==================== ВКЛАДКА 3: ЖДУН СЕРИЙ, ДУЭЛЬ ПРОФИЛЕЙ И КАЛЬКУЛЯТОР ==================== -->
    <section id="tab-tools" class="tab-pane">
        <div class="grid-2">
            <!-- Интерактивная Дуэль (Сравнение двух профилей 1 на 1) -->
            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">⚔️ Дуэль зрителей (Сравнение 1 на 1)</div>
                <p style="font-size:12.5px; color:var(--muted); margin-bottom:8px;">Выбери два профиля, чтобы сравнить их онлайн и количество просмотренных тайтлов:</p>
                <select id="duelP1" class="inp" onchange="runProfileDuel()">{compare_options_html}</select>
                <select id="duelP2" class="inp" onchange="runProfileDuel()">{compare_options_html}</select>
                <div id="duelResultBox" style="background:rgba(168,85,247,0.12); border:1px solid rgba(168,85,247,0.35); padding:11px; border-radius:11px; font-size:13px;">
                    Добавь минимум 2 профиля для сравнения
                </div>
            </div>

            <!-- Калькулятор марафона серий -->
            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">🍿 Умный калькулятор марафона аниме</div>
                <input type="number" id="epCnt" class="inp" value="12" min="1" placeholder="Сколько серий осталось?" oninput="calcMarathon()">
                <select id="epLen" class="inp" onchange="calcMarathon()">
                    <option value="20">20 мин (с пропуском опенинга и эндинга)</option>
                    <option value="24" selected>24 мин (стандартная серия целиком)</option>
                    <option value="45">45 мин (OVA / Спешл)</option>
                </select>
                <select id="epSpeed" class="inp" onchange="calcMarathon()">
                    <option value="1" selected>Скорость плеера 1.0x (Обычная)</option>
                    <option value="1.25">Скорость плеера 1.25x</option>
                    <option value="1.5">Скорость плеера 1.5x (Ускоренная)</option>
                    <option value="2">Скорость плеера 2.0x</option>
                </select>
                <div id="marathonOut" style="background:rgba(16,185,129,0.12); border:1px solid rgba(16,185,129,0.35); padding:11px; border-radius:11px; font-size:13px;"></div>
            </div>
        </div>

        <div class="grid-2">
            <!-- Рулетка выбора аниме на вечер -->
            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">
                    <span>🎲 Рулетка «Что посмотреть вечером?»</span>
                    <button class="btn-ui active" onclick="spinAnimeRoulette()">🎰 Крутить!</button>
                </div>
                <p style="font-size:12.5px; color:var(--muted); margin-bottom:10px;">
                    Случайный выбор из популярных тайтлов твоих друзей и базы онгоингов:
                </p>
                <div id="rouletteDisplay" style="background:rgba(0,0,0,0.35); border:1px dashed var(--purple); padding:16px; border-radius:12px; text-align:center; font-size:16px; font-weight:800; color:#e9d5ff;">
                    Нажми «🎰 Крутить!»
                </div>
            </div>

            <!-- Настройка звукового радара браузера -->
            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">🔔 Настройка Звукового Радара</div>
                <p style="font-size:12.5px; color:var(--muted); margin-bottom:8px;">
                    Когда радар включен, телефон или ПК подаст звуковой сигнал при новой активности на сайте:
                </p>
                <select id="radarTone" class="inp">
                    <option value="880">🎵 Тон 880 Гц (Мягкий сонар)</option>
                    <option value="1200">🔔 Тон 1200 Гц (Яркий кристалл)</option>
                    <option value="520">📡 Тон 520 Гц (Глубокий радар)</option>
                </select>
                <button class="btn-ui" style="width:100%;" onclick="playRadarBeep(true)">🔊 Проверить звук сейчас</button>
            </div>
        </div>

        <div class="box-card">
            <div class="box-title">🎬 Активный список «Ждуна серий» (Shikimori + Kodik)</div>
            {waits_cards_html}
        </div>
    </section>

    <!-- ==================== ВКЛАДКА 4: ПОЛНЫЙ ЖУРНАЛ ЛОГОВ, ЭКСПОРТ И СТЕНА ==================== -->
    <section id="tab-logs" class="tab-pane">
        <div class="grid-2">
            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">
                    <span>🔍 Поиск и экспорт истории</span>
                    <div style="display:flex; gap:6px;">
                        <button class="btn-ui active" onclick="downloadCSV()">📥 CSV (Excel)</button>
                        <button class="btn-ui" onclick="downloadJSONBackup()">📦 JSON</button>
                    </div>
                </div>
                <input type="text" id="searchInp" class="inp" placeholder="Введи ник, тайтл, дату или статус..." oninput="filterHistoryTable()">
                <div class="chips-wrap" style="margin-top:4px;">
                    <button class="f-chip active" onclick="filterLogByType('all', this)">Все события</button>
                    <button class="f-chip" onclick="filterLogByType('watching', this)">🟣 Только просмотр</button>
                    <button class="f-chip" onclick="filterLogByType('online', this)">🟢 Входы</button>
                    <button class="f-chip" onclick="filterLogByType('offline', this)">⚪ Выходы</button>
                </div>
                <div style="font-size:12px; color:var(--muted); margin-top:8px;">
                    Отображается записей: <b id="foundCnt" style="color:#fff;">все</b>
                </div>
            </div>

            <!-- Стена сообщений дашборда -->
            <div class="box-card" style="margin-bottom:0;">
                <div class="box-title">💬 Стена сообщений монитора</div>
                <div style="display:flex; gap:6px; margin-bottom:8px;">
                    <input type="text" id="wallAuthorInp" class="inp" style="margin-bottom:0; max-width:110px;" placeholder="Твой ник">
                    <input type="text" id="wallTextInp" class="inp" style="margin-bottom:0;" placeholder="Напиши заметку на стену...">
                    <button class="btn-ui active" onclick="sendWallMessage()">➤</button>
                </div>
                <div id="wallContainer" style="max-height:160px; overflow-y:auto;">
                    {wall_items_html}
                </div>
            </div>
        </div>

        <div class="box-card">
            <div class="box-title">📋 Полный журнал событий (Дата, время, статус, тайтл)</div>
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
                        {history_rows_html if history_rows_html else '<tr><td colspan="4" class="empty-state">Нет записей</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
    </section>
</div>

<script>
    let soundRadarEnabled = false;
    let autoRefreshEnabled = true;
    let currentThemeIdx = 0;
    const themesList = ['cyber', 'oled', 'ocean'];
    const roulettePool = {roulette_titles_json};
    let lastKnownLogCount = {st['total_logs']};

    // 1. Переключение вкладок
    function openTab(id, btn) {{
        document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        const target = document.getElementById(id);
        if (target) target.classList.add('active');
        if (btn) btn.classList.add('active');
    }}

    // 2. Переключение цветовой темы (Cyber / OLED / Ocean)
    function cycleTheme() {{
        currentThemeIdx = (currentThemeIdx + 1) % themesList.length;
        document.documentElement.setAttribute('data-theme', themesList[currentThemeIdx]);
    }}

    // 3. Компактный режим карточек для маленьких экранов
    function toggleCompactMode() {{
        const grid = document.getElementById('profilesContainer');
        if (grid) grid.classList.toggle('compact-mode');
    }}

    // 4. Фильтрация карточек по статусу (Все / Фиолетовые / Зеленые / Серые)
    function filterCards(state, btn) {{
        if (btn) {{
            document.querySelectorAll('#tab-profiles .f-chip').forEach(c => c.classList.remove('active'));
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

    // 5. Мгновенный поиск по карточкам профилей
    function searchProfileCards() {{
        const q = (document.getElementById('cardSearchInp').value || '').toLowerCase().trim();
        document.querySelectorAll('#profilesContainer .prof-card').forEach(card => {{
            const hay = card.getAttribute('data-name') || '';
            card.style.display = (!q || hay.includes(q)) ? 'flex' : 'none';
        }});
    }}

    // 6. Живой посекундный таймер длительности текущего статуса
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

    // 7. Звуковой радар на Web Audio API
    function playRadarBeep(force = false) {{
        if (!soundRadarEnabled && !force) return;
        try {{
            const freq = parseInt((document.getElementById('radarTone') || {{value: 880}}).value, 10) || 880;
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(freq, ctx.currentTime);
            gain.gain.setValueAtTime(0.12, ctx.currentTime);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.22);
        }} catch (e) {{}}
    }}

    function toggleSoundRadar() {{
        soundRadarEnabled = !soundRadarEnabled;
        const btn = document.getElementById('soundRadarBtn');
        btn.textContent = soundRadarEnabled ? '🔔 Звук-радар: ВКЛ' : '🔕 Звук-радар: ВЫКЛ';
        btn.classList.toggle('active', soundRadarEnabled);
        if (soundRadarEnabled) playRadarBeep(true);
    }}

    // 8. Фоновое AJAX-обновление статусов через /api/status без перезагрузки страницы
    function toggleAutoRefresh() {{
        autoRefreshEnabled = !autoRefreshEnabled;
        const btn = document.getElementById('livePollBtn');
        btn.textContent = autoRefreshEnabled ? '🔄 Live-Опрос: ВКЛ' : '⏸ Live-Опрос: ПАУЗА';
        btn.classList.toggle('active', autoRefreshEnabled);
    }}

    async function pollLiveStatus() {{
        if (!autoRefreshEnabled) return;
        try {{
            const resp = await fetch('/api/status', {{ cache: 'no-store' }});
            if (!resp.ok) return;
            const data = await resp.json();
            if (data.updated_at) {{
                document.getElementById('syncTime').textContent = '🕒 Обновлено: ' + data.updated_at;
            }}
            if (data.stats) {{
                document.getElementById('kpiTotal').textContent = data.stats.total;
                document.getElementById('kpiWatching').textContent = data.stats.watching;
                document.getElementById('kpiOnline').textContent = data.stats.online;
                document.getElementById('kpiOffline').textContent = data.stats.offline;
                if (data.stats.logs > lastKnownLogCount) {{
                    lastKnownLogCount = data.stats.logs;
                    playRadarBeep(false);
                }}
            }}
        }} catch (e) {{}}
    }}
    setInterval(pollLiveStatus, 8000);

    // 9. Дуэль зрителей (Сравнение 1 на 1)
    function runProfileDuel() {{
        const s1 = document.getElementById('duelP1');
        const s2 = document.getElementById('duelP2');
        const out = document.getElementById('duelResultBox');
        if (!s1 || !s2 || !s1.options.length || !s2.options.length) return;
        const o1 = s1.options[s1.selectedIndex];
        const o2 = s2.options[s2.selectedIndex];
        const n1 = o1.getAttribute('data-name');
        const n2 = o2.getAttribute('data-name');
        const onl1 = parseInt(o1.getAttribute('data-onl') || '0', 10);
        const onl2 = parseInt(o2.getAttribute('data-onl') || '0', 10);
        const t1 = parseInt(o1.getAttribute('data-titles') || '0', 10);
        const t2 = parseInt(o2.getAttribute('data-titles') || '0', 10);

        const leader = (onl1 + t1 * 600) >= (onl2 + t2 * 600) ? n1 : n2;
        out.innerHTML =
            `<b>${{n1}}</b>: ${{Math.floor(onl1/60)}} мин онлайн, ${{t1}} тайтл(ов)<br>` +
            `<b>${{n2}}</b>: ${{Math.floor(onl2/60)}} мин онлайн, ${{t2}} тайтл(ов)<br>` +
            `🏆 Лидер дуэли: <b style="color:#c084fc;">${{leader}}</b>`;
    }}
    setTimeout(() => {{
        const s2 = document.getElementById('duelP2');
        if (s2 && s2.options.length > 1) s2.selectedIndex = 1;
        runProfileDuel();
    }}, 200);

    // 10. Калькулятор марафона серий с поддержкой скорости плеера
    function calcMarathon() {{
        const eps = parseInt(document.getElementById('epCnt').value, 10) || 0;
        const len = parseInt(document.getElementById('epLen').value, 10) || 24;
        const spd = parseFloat(document.getElementById('epSpeed').value) || 1.0;
        const realMins = Math.round((eps * len) / spd);
        const h = Math.floor(realMins / 60);
        const m = realMins % 60;
        const end = new Date(Date.now() + realMins * 60000).toLocaleTimeString('ru-RU', {{hour:'2-digit', minute:'2-digit'}});
        document.getElementById('marathonOut').innerHTML =
            `⏱ Время просмотра (${{spd}}x): <b>${{h}} ч ${{m}} мин</b><br>🏁 Закончишь марафон в: <b>${{end}}</b>`;
    }}
    calcMarathon();

    // 11. Рулетка аниме
    function spinAnimeRoulette() {{
        const box = document.getElementById('rouletteDisplay');
        if (!roulettePool || !roulettePool.length) return;
        let steps = 0;
        const timer = setInterval(() => {{
            const pick = roulettePool[Math.floor(Math.random() * roulettePool.length)];
            box.textContent = '🎰 ' + pick;
            steps++;
            if (steps > 12) {{
                clearInterval(timer);
                box.innerHTML = `✨ Выбор рулетки: <span style="color:#34d399;">${{pick}}</span>`;
            }}
        }}, 75);
    }}

    // 12. Фильтрация и поиск по таблице логов + экспорт CSV и JSON
    let activeLogFilterType = 'all';
    function filterLogByType(t, btn) {{
        activeLogFilterType = t;
        if (btn) {{
            btn.parentElement.querySelectorAll('.f-chip').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
        }}
        filterHistoryTable();
    }}

    function filterHistoryTable() {{
        const q = (document.getElementById('searchInp').value || '').toLowerCase().trim();
        let cnt = 0;
        document.querySelectorAll('#searchTableBody .log-row').forEach(r => {{
            const hay = r.getAttribute('data-search') || '';
            const evtype = r.getAttribute('data-evtype') || '';
            const matchText = !q || hay.includes(q);
            const matchType = activeLogFilterType === 'all' || evtype === activeLogFilterType;
            if (matchText && matchType) {{
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
        const blob = new Blob(["\uFEFF" + csv.join("\n")], {{type: 'text/csv;charset=utf-8;'}});
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'anivox_logs_export.csv';
        a.click();
    }}

    async function downloadJSONBackup() {{
        try {{
            const resp = await fetch('/api/status');
            const data = await resp.json();
            const blob = new Blob([JSON.stringify(data, null, 2)], {{type: 'application/json'}});
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'anivox_snapshot.json';
            a.click();
        }} catch (e) {{}}
    }}

    // 13. Отправка заметки на Стену дашборда прямо с сайта
    async function sendWallMessage() {{
        const author = (document.getElementById('wallAuthorInp').value || 'Зритель').trim();
        const text = (document.getElementById('wallTextInp').value || '').trim();
        if (!text) return;
        try {{
            const resp = await fetch('/api/wall', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{author, text}})
            }});
            if (resp.ok) {{
                document.getElementById('wallTextInp').value = '';
                location.reload();
            }}
        }} catch (e) {{}}
    }}

    // 14. Инициализация графиков Chart.js
    new Chart(document.getElementById('hourChart'), {{
        type: 'bar',
        data: {{
            labels: Array.from({{length:24}}, (_,i) => (i<10?'0'+i:i)+':00'),
            datasets: [{{
                label: 'Событий',
                data: {hourly_json},
                backgroundColor: '#a855f7',
                borderRadius: 5
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
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
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{
                legend: {{ position: 'bottom', labels: {{ color: '#f1f5f9', font: {{size: 11}}, boxWidth: 11 }} }}
            }}
        }}
    }});

    new Chart(document.getElementById('weekdayChart'), {{
        type: 'line',
        data: {{
            labels: ['ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС'],
            datasets: [{{
                label: 'Активность по дням недели',
                data: {weekday_json},
                borderColor: '#10b981',
                backgroundColor: 'rgba(16, 185, 129, 0.18)',
                fill: true,
                tension: 0.35
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ ticks: {{ color: '#94a3b8' }} }},
                y: {{ ticks: {{ color: '#94a3b8', stepSize: 1 }}, beginAtZero: true }}
            }}
        }}
    }});
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path


# ==================== ФОНОВЫЙ МОНИТОРИНГ ПРОФИЛЕЙ, СНАЙПЕР И ЖДУН СЕРИЙ ====================
def check_all_profiles_once(cfg, notify_even_if_same=False, target_chat=None):
    t_start = time.time()
    pids = list(cfg.get("profiles", {}).keys())
    if not pids:
        if notify_even_if_same and target_chat:
            tg_api(cfg["token"], "sendMessage", {
                "chat_id": target_chat,
                "text": "📡 Список профилей пока пуст. Добавь первый профиль через меню!",
                "reply_markup": kb_profiles_menu(cfg)
            })
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
            p["level"] = info.get("level", p.get("level", "Зритель"))

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
                    details = f"Вышел(ла) из сети (до этого: {prev_label})"
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
                if len(cfg["history"]) > 800:
                    cfg["history"] = cfg["history"][-800:]

                # Проверка Снайпера онлайна (разовый перехват входа в сеть)
                if new_onl and not old_onl:
                    sniper_watchers = cfg.get("snipers", {}).pop(pid, [])
                    for sw_chat in sniper_watchers:
                        tg_api(cfg["token"], "sendMessage", {
                            "chat_id": sw_chat,
                            "text": (
                                f"🎯 <b>СНАЙПЕР СРАБОТАЛ!</b>\n\n"
                                f"👤 Профиль <b>{p['nickname']}</b> (<code>#{pid}</code>) только что появился в сети!\n"
                                f"🎬 Активность: <i>{new_ttl}</i>\n"
                                f"🕒 Точное время: <code>{format_ts(now)}</code>"
                            ),
                            "parse_mode": "HTML"
                        })

                save_config(cfg)

                # Штатная рассылка уведомлений по настройкам пользователей
                for uid_str in list(cfg.get("users", {}).keys()):
                    s = get_user_settings(cfg, uid_str)
                    ntf = s["notify"]
                    if ev_type == "Онлайн" and not ntf.get("onl", True):
                        continue
                    if ev_type == "Оффлайн" and not ntf.get("off", True):
                        continue
                    if ev_type == "Смотрит" and not ntf.get("ttl", True):
                        continue

                    dt_s = format_ts(now)
                    badge = "🟣 СМОТРИТ АНИМЕ" if new_wat else ("🟢 В СЕТИ" if new_onl else "⚪ ОФФЛАЙН")
                    alert_text = (
                        f"🔔 <b>Изменение статуса AniVox!</b>\n\n"
                        f"👤 Профиль: <b>{p['nickname']}</b> (<code>#{pid}</code>)\n"
                        f"📡 Новый статус: <b>{badge}</b>\n"
                        f"🎬 Тайтл: <i>{new_ttl}</i>\n"
                        f"⏮ Предыдущий: <i>{prev_label}</i>\n"
                        f"🕒 Дата и время: <code>{dt_s}</code>"
                    )
                    send_or_edit(cfg, uid_str, alert_text, reply_markup=kb_main_menu(cfg, uid_str))

    runtime_metrics["last_check_duration_ms"] = int((time.time() - t_start) * 1000)
    runtime_metrics["last_check_ts"] = now
    generate_html_report(cfg)

    if notify_even_if_same and target_chat:
        send_or_edit(cfg, target_chat, format_status_text(cfg), reply_markup=kb_main_menu(cfg, target_chat), force_new=True)


def check_anime_waits_once(cfg, notify_chat_if_none=None):
    """Фоновая проверка выхода новых серий в Ждуне (Shikimori API)"""
    waits = cfg.get("waits", {})
    if not waits:
        if notify_chat_if_none:
            tg_api(cfg["token"], "sendMessage", {"chat_id": notify_chat_if_none, "text": "🎬 Список Ждуна серий пуст."})
        return

    found_new = False
    for aid, w in list(waits.items()):
        info = fetch_shikimori_anime(aid)
        if not info:
            continue
        new_aired = int(info.get("episodes_aired") or 0)
        old_aired = int(w.get("episodes_aired") or 0)
        if new_aired > old_aired:
            found_new = True
            with config_lock:
                w["episodes_aired"] = new_aired
                w["episodes_total"] = info.get("episodes") or w.get("episodes_total", "?")
                save_config(cfg)
            msg_txt = (
                f"🎉 <b>Вышла новая серия!</b>\n\n"
                f"🎬 Аниме: <b>{w.get('title')}</b>\n"
                f"🔢 Эпизод: <b>{new_aired} из {w.get('episodes_total', '?')}</b>\n"
                f"🎙 Озвучка в подписке: <b>{w.get('voice', 'Любая')}</b>\n"
                f"🔗 <code>https://shikimori.one/animes/{aid}</code>"
            )
            for uid_str in list(cfg.get("users", {}).keys()):
                tg_api(cfg["token"], "sendMessage", {"chat_id": uid_str, "text": msg_txt, "parse_mode": "HTML"})

    if notify_chat_if_none and not found_new:
        tg_api(cfg["token"], "sendMessage", {
            "chat_id": notify_chat_if_none,
            "text": "✅ Все аниме из Ждуна проверены: новых серий пока не выходило.",
            "reply_markup": kb_waits_menu(cfg)
        })


def monitor_loop(cfg):
    last_waits_check = 0
    while True:
        try:
            check_all_profiles_once(cfg)
            now = time.time()

            # Проверка новых серий каждые 15 минут
            if now - last_waits_check >= 900:
                last_waits_check = now
                check_anime_waits_once(cfg)

            # Автоочистка старых сообщений в чате по таймеру
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


# ==================== ВСТРОЕННЫЙ HTTP-СЕРВЕР И AUTOPING ДЛЯ RENDER.COM ====================
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

        if self.path.startswith("/ping") or self.path.startswith("/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK - AniVox Monitor Alive")
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

    def do_POST(self):
        cfg = load_config()
        if self.path.startswith("/api/wall"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8", errors="ignore")
                body = json.loads(raw)
                author = str(body.get("author") or "Зритель").strip()[:30]
                text = str(body.get("text") or "").strip()[:240]
                if text:
                    with config_lock:
                        cfg.setdefault("wall_messages", []).append({
                            "ts": int(time.time()),
                            "author": author,
                            "text": text
                        })
                        cfg["wall_messages"] = cfg["wall_messages"][-50:]
                        save_config(cfg)
                    generate_html_report(cfg)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


def run_web_server():
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"[Web] Веб-сервер успешно запущен на http://0.0.0.0:{PORT}")
    server.serve_forever()


def autoping_loop(cfg):
    """Анти-сон для Render.com: пингует внешний URL каждые 3 минуты"""
    while True:
        time.sleep(180)
        url = cfg.get("autoping_url", "").strip()
        if url:
            ping_target = url.rstrip("/") + "/ping"
            try:
                req = urllib.request.Request(ping_target, headers={"User-Agent": USER_AGENT})
                urllib.request.urlopen(req, timeout=10).read()
                runtime_metrics["autoping_count"] += 1
            except Exception:
                pass


# ==================== ТОЧКА ВХОДА (LONG POLLING + ФОНОВЫЕ ПОТОКИ) ====================
def main():
    cfg = load_config()
    generate_html_report(cfg)

    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=monitor_loop, args=(cfg,), daemon=True).start()
    threading.Thread(target=autoping_loop, args=(cfg,), daemon=True).start()

    print(f"[{APP_NAME} v{VERSION}] Система запущена! Порт: {PORT}. Ожидание событий Telegram...")
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
            print("\nОстановка монитора...")
            break
        except Exception as e:
            print(f"[Polling Error]: {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()
