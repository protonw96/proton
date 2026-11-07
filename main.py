#!/usr/bin/env python3
"""AniVox Monitor v4.7.0 (Compact Core, GitHub OTA Auto-Updater via Telegram, Live AJAX Dashboard & Multimedia Bridge)."""

from __future__ import annotations

import base64, html, http.server, json, logging, os, re, signal, socket, socketserver, ssl, struct, sys, tempfile, threading, time
import urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any, Optional

APP_NAME, VERSION = "AniVox Monitor", "4.7.0"
SELF_PATH = Path(__file__).resolve()
CONFIG_PATH = SELF_PATH.with_name("anivox_monitor.json")
LOG_PATH = SELF_PATH.with_name("anivox_monitor.log")
PROFILE_RE = re.compile(r"^https?://(?:www\.)?anivox\.fun/profile/([0-9]+)(?:[/?#].*)?$", re.I)
CLEAN_TITLE_RE = re.compile(r"(\[.*?\]|\(.*?\)|/.*$|\b\d+\s*(?:сезон|season|part|часть|тв|tv)\b|(?:сезон|season|part|часть|тв|tv)\s*\d+\b)", re.I)
ALLOWED_INTERVALS = (1, 5, 10, 15, 20, 25, 30)
DEFAULT_INTERVAL = 10
DEFAULT_USER_AGENT = f"Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 Chrome/124.0 AniVoxMonitor/{VERSION}"
SHIKIMORI_UA = f"AniVoxMonitor/{VERSION} (https://anivox.fun)"

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
}

def almaty_tz() -> timezone:
    return timezone(timedelta(hours=5), name="ALMT")

def almaty_now() -> str:
    return datetime.now(almaty_tz()).isoformat(timespec="seconds")

def compact(val: Any) -> str:
    return re.sub(r"\s+", " ", str(val or "")).strip()

def as_bool(val: Any, default: bool = False) -> bool:
    return val if isinstance(val, bool) else (val.lower() in {"1", "true", "yes", "да", "д"} if isinstance(val, str) else default)

def save_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try: path.chmod(0o600)
    except OSError: pass

def load_json(path: Path) -> dict[str, Any]:
    if not path.exists(): return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception as e:
        logger.warning("Ошибка чтения JSON: %s", e)
        return {}

def normalize_profile_url(value: str) -> tuple[str, str]:
    c = value.strip()
    if not c.startswith(("http://", "https://")): c = "https://" + c
    p = urllib.parse.urlparse(c)
    norm = urllib.parse.urlunparse(("https", p.netloc.lower(), p.path.rstrip("/"), "", "", ""))
    m = PROFILE_RE.match(norm)
    if not m: raise ValueError("Нужна ссылка вида https://anivox.fun/profile/27788")
    return norm, m.group(1)

def clean_anime_title(raw: str) -> str:
    c = re.sub(r"\s+", " ", re.sub(r"[:\-–—]+$", " ", CLEAN_TITLE_RE.sub(" ", raw))).strip()
    return c if len(c) >= 2 else raw.strip()

def fetch_anime_info(title: str) -> dict[str, Any]:
    if not title or title.lower() in {"неизвестно", "тайтл"}: return {"genres": [], "id": ""}
    def _search(q: str) -> str:
        url = f"https://shikimori.one/api/animes?search={urllib.parse.quote(q)}&limit=1"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": SHIKIMORI_UA}), timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
            return str(d[0].get("id", "")) if d and isinstance(d, list) else ""
    try:
        time.sleep(1.0)
        cleaned = clean_anime_title(title)
        aid = _search(cleaned) or (_search(title) if cleaned != title else "")
        if not aid: return {"genres": [], "id": ""}
        url2 = f"https://shikimori.one/api/animes/{aid}"
        with urllib.request.urlopen(urllib.request.Request(url2, headers={"User-Agent": SHIKIMORI_UA}), timeout=10) as r2:
            d2 = json.loads(r2.read().decode("utf-8"))
            genres = [g.get("russian") or g.get("name") for g in d2.get("genres", []) if isinstance(g, dict) and (g.get("russian") or g.get("name"))]
            return {"genres": genres, "id": aid}
    except Exception as e:
        logger.warning("Ошибка Shikimori API для '%s': %s", title, e)
        return {"genres": [], "id": ""}

def get_anime_data(title: str, cache: dict) -> tuple[str, str]:
    c = cache.get(title)
    if isinstance(c, dict):
        return (", ".join(c.get("genres", [])) or "-"), (f"https://anivox.fun/anime/{c['id']}" if c.get("id") else "https://anivox.fun")
    return (", ".join(c) or "-" if isinstance(c, list) else "-"), "https://anivox.fun"

def format_duration_hm(seconds: float) -> str:
    m = max(1, int(round(seconds / 60)))
    return f"{m} мин." if m < 60 else (f"{m // 60} ч. {m % 60} мин." if m % 60 else f"{m // 60} ч.")

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
        self.path = path
        raw = load_json(path)
        self.token = str(raw.get("token", os.environ.get("BOT_TOKEN", ""))).strip()
        self.chat_id = str(raw.get("chat_id", os.environ.get("CHAT_ID", ""))).strip()
        self.autoping_url = str(raw.get("autoping_url", os.environ.get("RENDER_EXTERNAL_URL", ""))).strip()
        
        # Настройки GitHub автообновления
        gh = raw.get("github", {}) if isinstance(raw.get("github"), dict) else {}
        self.github_token = str(gh.get("token", os.environ.get("GITHUB_TOKEN", ""))).strip()
        self.github_repo = str(gh.get("repo", os.environ.get("GITHUB_REPO", ""))).strip()
        self.github_branch = str(gh.get("branch", os.environ.get("GITHUB_BRANCH", "main"))).strip() or "main"
        self.github_path = str(gh.get("path", os.environ.get("GITHUB_PATH", SELF_PATH.name))).strip() or SELF_PATH.name

        self.allowed_users: list[str] = [str(u) for u in raw.get("allowed_users", [])]
        if self.chat_id and self.chat_id not in self.allowed_users:
            self.allowed_users.append(self.chat_id)
        self.anime_cache: dict[str, Any] = raw.get("anime_cache", {})

        rs = raw.get("settings", {}) if isinstance(raw.get("settings"), dict) else {}
        iv = int(rs.get("interval_minutes", DEFAULT_INTERVAL))
        self.settings = Settings(
            interval_minutes=iv if iv in ALLOWED_INTERVALS else DEFAULT_INTERVAL,
            auto_cleanup_days=int(rs.get("auto_cleanup_days", 15)),
            notify_on_change=as_bool(rs.get("notify_on_change"), True),
            notify_on_online=as_bool(rs.get("notify_on_online"), True),
            notify_on_offline=as_bool(rs.get("notify_on_offline"), True),
            notify_on_title=as_bool(rs.get("notify_on_title"), True),
            notify_on_errors=as_bool(rs.get("notify_on_errors"), False),
            request_timeout=max(5, min(90, int(rs.get("request_timeout", 20)))),
            min_request_gap_seconds=max(1, min(300, int(rs.get("min_request_gap_seconds", 5)))),
        )
        self.profiles: dict[str, Profile] = {}
        for item in (raw.get("profiles", []) if isinstance(raw.get("profiles"), list) else []):
            if isinstance(item, dict) and "url" in item and "profile_id" in item:
                p = Profile(
                    url=str(item["url"]), profile_id=str(item["profile_id"]),
                    label=str(item.get("label", "")), last_signature=str(item.get("last_signature", "")),
                    last_check=str(item.get("last_check", "")), last_ok=as_bool(item.get("last_ok"), False),
                    last_error=str(item.get("last_error", "")), last_status=str(item.get("last_status", "")),
                    muted_until=float(item.get("muted_until", 0.0) or 0.0),
                    active_title=str(item.get("active_title", "")),
                    active_title_since=float(item.get("active_title_since", 0.0) or 0.0),
                )
                self.profiles[p.profile_id] = p

        self.history: dict[str, list[dict[str, Any]]] = {}
        rh = raw.get("history", {})
        if isinstance(rh, dict):
            for pid, entries in rh.items():
                if isinstance(entries, list):
                    cl: list[dict[str, Any]] = []
                    for it in entries:
                        if isinstance(it, dict) and (not cl or any(cl[-1].get(k) != it.get(k) for k in ("presence", "nickname", "level", "title"))):
                            cl.append(it)
                    self.history[str(pid)] = cl[-500:]

    def save(self) -> None:
        with self.lock:
            save_json(self.path, {
                "token": self.token, "chat_id": self.chat_id, "autoping_url": self.autoping_url,
                "github": {"token": self.github_token, "repo": self.github_repo, "branch": self.github_branch, "path": self.github_path},
                "allowed_users": self.allowed_users, "anime_cache": self.anime_cache,
                "settings": asdict(self.settings), "profiles": [asdict(p) for p in self.profiles.values()],
                "history": self.history,
            })

    def update_anime_cache(self, title: str, info: dict[str, Any]) -> None:
        with self.lock:
            self.anime_cache[title] = info
            self.save()

    def add_history(self, profile_id: str, result: "FetchResult", changes: list[str]) -> None:
        if not result.ok or not changes: return
        with self.lock:
            entries = self.history.setdefault(profile_id, [])
            if entries and all(entries[-1].get(k) == getattr(result, k) for k in ("presence", "nickname", "level", "title")):
                return
            if result.title:
                c = self.anime_cache.get(result.title)
                if not c or (isinstance(c, dict) and not c.get("id")) or isinstance(c, list):
                    self.anime_cache[result.title] = {"genres": [], "id": ""}
                    threading.Thread(target=lambda: self.update_anime_cache(result.title, fetch_anime_info(result.title)), daemon=True).start()
            entries.append({
                "checked_at": result.checked_at, "ok": result.ok, "nickname": result.nickname,
                "level": result.level, "presence": result.presence, "title": result.title,
                "signature": result.signature, "changes": changes, "error": result.error,
            })
            self.history[profile_id] = entries[-500:]

class TelegramError(RuntimeError): pass

class TelegramClient:
    def __init__(self, token: str, timeout: int = 35) -> None:
        self.token = token.strip()
        self.timeout = timeout
        self.base_url = f"https://api.telegram.org/bot{self.token}/"
        self.offset = 0

    def request(self, method: str, payload: Optional[dict[str, Any]] = None) -> Any:
        req = urllib.request.Request(self.base_url + method, data=urllib.parse.urlencode(payload or {}).encode(), headers={"User-Agent": f"{APP_NAME}/{VERSION}"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                res = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise TelegramError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
        except Exception as e:
            raise TelegramError(f"Network error: {e}") from e
        if not res.get("ok"): raise TelegramError(str(res.get("description", "ошибка Telegram")))
        return res.get("result")

    def get_me(self) -> dict[str, Any]:
        r = self.request("getMe")
        return r if isinstance(r, dict) else {}

    def send_message(self, chat_id: str, text: str, reply_markup: Optional[dict[str, Any]] = None) -> None:
        self.request("sendMessage", {
            "chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML",
            "disable_web_page_preview": "true",
            "reply_markup": json.dumps(reply_markup or TELEGRAM_KEYBOARD, ensure_ascii=False),
        })

    def copy_message(self, to_chat_id: str, from_chat_id: str, message_id: int) -> None:
        self.request("copyMessage", {"chat_id": to_chat_id, "from_chat_id": from_chat_id, "message_id": message_id})

    def edit_message_text(self, chat_id: str, text: str, message_id: int, reply_markup: Optional[dict[str, Any]] = None) -> None:
        p: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if reply_markup: p["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        self.request("editMessageText", p)

    def answer_callback_query(self, cb_id: str, text: str = "") -> None:
        try: self.request("answerCallbackQuery", {"callback_query_id": cb_id, "text": text[:190]})
        except Exception: pass

    def download_file(self, file_id: str) -> bytes:
        info = self.request("getFile", {"file_id": file_id})
        file_path = info.get("file_path") if isinstance(info, dict) else ""
        if not file_path: raise TelegramError("Не удалось получить путь файла от Telegram")
        dl_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        with urllib.request.urlopen(urllib.request.Request(dl_url, headers={"User-Agent": DEFAULT_USER_AGENT}), timeout=30) as r:
            return r.read()

    def send_document(self, chat_id: str, path: Path, caption: str = "") -> None:
        b = f"----AniVox{os.urandom(8).hex()}"
        ctype = "text/html" if path.name.endswith(".html") else "application/json"
        parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode()]
        if caption: parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption[:1024]}\r\n".encode())
        parts.extend([
            f'--{b}\r\nContent-Disposition: form-data; name="document"; filename="{path.name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode(),
            path.read_bytes(), f"\r\n--{b}--\r\n".encode()
        ])
        req = urllib.request.Request(self.base_url + "sendDocument", data=b"".join(parts), headers={"Content-Type": f"multipart/form-data; boundary={b}"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            res = json.loads(r.read().decode("utf-8"))
            if not res.get("ok"): raise TelegramError(str(res.get("description", "ошибка отправки файла")))

    def get_updates(self, timeout: int = 25) -> list[dict[str, Any]]:
        res = self.request("getUpdates", {"offset": str(self.offset), "timeout": str(max(0, min(25, timeout))), "allowed_updates": json.dumps(["message", "callback_query"])})
        updates = res if isinstance(res, list) else []
        for it in updates:
            if isinstance(it, dict) and isinstance(it.get("update_id"), int):
                self.offset = it["update_id"] + 1
        return updates

def push_to_github(store: Store, content_bytes: bytes, commit_msg: str, target_path: str = "") -> str:
    """Пушит обновленный файл напрямую в GitHub через REST API."""
    if not store.github_token or not store.github_repo:
        return "⚠️ GitHub не настроен (обновлено только локально). Для автопуша на GitHub настрой: /github"
    path_in_repo = (target_path or store.github_path).lstrip("/")
    api_url = f"https://api.github.com/repos/{store.github_repo}/contents/{urllib.parse.quote(path_in_repo)}"
    headers = {
        "Authorization": f"Bearer {store.github_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{APP_NAME}/{VERSION}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    sha = None
    try:
        req_get = urllib.request.Request(f"{api_url}?ref={urllib.parse.quote(store.github_branch)}", headers=headers)
        with urllib.request.urlopen(req_get, timeout=15) as r:
            sha = json.loads(r.read().decode("utf-8")).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise RuntimeError(f"GitHub GET HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e

    body: dict[str, Any] = {
        "message": commit_msg,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": store.github_branch,
    }
    if sha: body["sha"] = sha
    req_put = urllib.request.Request(api_url, data=json.dumps(body).encode("utf-8"), headers={**headers, "Content-Type": "application/json"}, method="PUT")
    try:
        with urllib.request.urlopen(req_put, timeout=20) as r:
            resp_data = json.loads(r.read().decode("utf-8"))
            commit_sha = str(resp_data.get("commit", {}).get("sha", ""))[:7]
            return f"✅ Запушено в GitHub (<code>{store.github_repo}@{store.github_branch}:{path_in_repo}</code>, коммит <code>{commit_sha}</code>)"
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub PUT HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:250]}") from e

class FetchResult:
    def __init__(self, ok: bool, profile_id: str, url: str, nickname: str = "неизвестно", level: str = "неизвестно", presence: str = "неизвестно", title: str = "", error: str = "", last_seen_timestamp: float = 0) -> None:
        self.ok, self.profile_id, self.url = ok, profile_id, url
        self.nickname, self.level, self.presence = nickname or "неизвестно", level or "неизвестно", presence or "неизвестно"
        self.title, self.error, self.last_seen_timestamp = title, error, last_seen_timestamp
        self.checked_at = almaty_now()

    @property
    def signature(self) -> str:
        st = "watching:" + self.title.lower() if self.presence.startswith("Смотрит:") else ("online" if self.presence.startswith("Онлайн") else "offline")
        return "|".join((self.nickname.lower(), self.level, st, self.title.lower())).lower()

def recv_exact(sock: socket.socket, length: int) -> bytes:
    buf = bytearray()
    while len(buf) < length:
        chunk = sock.recv(length - len(buf))
        if not chunk: raise ConnectionError("AniVox WebSocket закрыл соединение")
        buf.extend(chunk)
    return bytes(buf)

def ws_send_text(sock: socket.socket, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    mask = os.urandom(4)
    n = len(data)
    hdr = bytes((0x81, 0x80 | n)) if n < 126 else (bytes((0x81, 0x80 | 126)) + struct.pack("!H", n) if n < 65536 else bytes((0x81, 0x80 | 127)) + struct.pack("!Q", n))
    sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

def ws_read_message(sock: socket.socket) -> Optional[str]:
    frags: list[bytes] = []
    while True:
        h = recv_exact(sock, 2)
        op, masked, ln = h[0] & 0x0F, bool(h[1] & 0x80), h[1] & 0x7F
        if ln == 126: ln = struct.unpack("!H", recv_exact(sock, 2))[0]
        elif ln == 127: ln = struct.unpack("!Q", recv_exact(sock, 8))[0]
        mask = recv_exact(sock, 4) if masked else b""
        data = recv_exact(sock, ln)
        if masked: data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if op == 8: return None
        if op == 9: sock.sendall(bytes((0x8A, len(data))) + data); continue
        if op in (1, 0):
            frags.append(data)
            if h[0] & 0x80: return b"".join(frags).decode("utf-8", errors="replace")

def format_last_seen(ts: float) -> str:
    if ts <= 0: return "Был в сети: время неизвестно"
    dt = datetime.fromtimestamp(ts, tz=almaty_tz())
    age = max(0, int(time.time() - ts))
    rel = "только что" if age < 60 else (f"{age // 60} мин. назад" if age < 3600 else (f"{age // 3600} ч. назад" if age < 86400 else f"{age // 86400} дн. назад"))
    return f"Был в сети: {rel} ({dt.strftime('%H:%M Алматы')})"

def fetch_profile(profile: Profile, timeout: int = 20) -> FetchResult:
    host = "anivox.fun"
    try:
        raw_sock = socket.create_connection((host, 443), timeout=timeout)
        sock = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=host)
        sock.settimeout(timeout)
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            hs = f"GET /api HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: https://{host}\r\nUser-Agent: {DEFAULT_USER_AGENT}\r\n\r\n"
            sock.sendall(hs.encode("ascii"))
            resp = b""
            while b"\r\n\r\n" not in resp:
                resp += sock.recv(4096)
                if len(resp) > 65536: raise ConnectionError("Слишком длинный handshake")
            if " 101 " not in resp.decode("latin1", "replace").splitlines()[0]:
                raise ConnectionError("Ошибка WebSocket Handshake")
            ws_send_text(sock, {"type": "get_profile", "id": int(profile.profile_id)})
            deadline = time.monotonic() + timeout
            data = None
            while time.monotonic() < deadline:
                msg = ws_read_message(sock)
                if msg:
                    try:
                        parsed = json.loads(msg)
                        if parsed.get("type") == "get_profile": data = parsed; break
                    except json.JSONDecodeError: continue
            if not data: raise TimeoutError("AniVox не прислал get_profile")
        finally:
            try: sock.close()
            except OSError: pass

        c = data.get("data") if isinstance(data, dict) else None
        u = c.get("user") if isinstance(c, dict) else None
        if not isinstance(u, dict): return FetchResult(False, profile.profile_id, profile.url, error="Ответ без data.user")
        try: on_ts = float(u.get("online", 0) or 0)
        except (TypeError, ValueError): on_ts = 0.0
        st = c.get("online_status", "")
        if on_ts == 0:
            raw_t = compact(st.get("text", "") if isinstance(st, dict) else st)
            title = re.sub(r"^(?:смотрит|watching)\s*[:：-]?\s*", "", raw_t, flags=re.I).strip()
            presence = f"Смотрит: {title}" if title else "Онлайн"
        else:
            title, presence = "", format_last_seen(on_ts)
        return FetchResult(True, profile.profile_id, profile.url, compact(u.get("username")) or "неизвестно", str(u.get("level", "неизвестно")), presence, title, last_seen_timestamp=on_ts)
    except Exception as e:
        return FetchResult(False, profile.profile_id, profile.url, error=f"WebSocket ошибка: {e}")

def format_profile(res: FetchResult, cache: dict, label: str = "", session_note: str = "", muted_until: float = 0.0) -> str:
    name = html.escape(res.nickname + (f" ({label})" if label else ""))
    lines = [f"<b>👤 Профиль:</b> {name}", f"<b>⭐ Лвл:</b> {html.escape(str(res.level))}"]
    if res.presence.startswith("Смотрит:"):
        gnrs, link = get_anime_data(res.title, cache)
        lines.append(f"🍿 <b>Смотрит:</b> <a href='{link}'>{html.escape(res.title)}</a>")
        if gnrs and gnrs != "-": lines.append(f"🎭 <b>Жанры:</b> <i>{html.escape(gnrs)}</i>")
    else:
        ico = "🟢" if res.presence.startswith("Онлайн") else "🔴"
        lines.append(f"{ico} <b>Статус:</b> {html.escape(res.presence)}")
    if session_note: lines.append(session_note)
    if muted_until > time.time():
        lines.append(f"🔕 <i>Заглушен еще на {max(1, int((muted_until - time.time()) / 60))} мин.</i>")
    dt = res.checked_at[11:19] if len(res.checked_at) >= 19 else res.checked_at
    lines.extend([f"<i>⏱ Проверено: {dt} (Алматы)</i>", f"<a href='{res.url}'>🌐 Открыть профиль</a>"])
    return "\n".join(lines)

def build_profile_inline_keyboard(res: FetchResult, cache: dict, prof: Optional[Profile] = None) -> dict[str, Any]:
    r1: list[dict[str, str]] = []
    if res.presence.startswith("Смотрит:") and res.title:
        _, link = get_anime_data(res.title, cache)
        if link != "https://anivox.fun": r1.append({"text": "🍿 Смотреть тайтл", "url": link})
    r1.append({"text": "🌐 Профиль", "url": res.url})
    muted = bool(prof and prof.muted_until > time.time())
    r2 = [{"text": "🔄 Обновить", "callback_data": f"recheck_{res.profile_id}"}, {"text": "🔔 Вкл звук" if muted else "🔕 Тишина 2ч", "callback_data": f"mute_{res.profile_id}"}]
    return {"inline_keyboard": [r1, r2]}

def calc_stats(entries: list[dict], anime_cache: dict, interval_minutes: int = DEFAULT_INTERVAL) -> tuple[float, float, float, dict[str, float], dict[str, float], str, int]:
    w_t = on_t = off_t = 0.0
    titles: dict[str, float] = {}
    genres_t: dict[str, float] = {}
    hours_b: dict[int, float] = {h: 0.0 for h in range(24)}
    now = datetime.now(almaty_tz()).timestamp()
    max_gap = max(0.75, (interval_minutes * 3.5) / 60.0)

    for i, curr in enumerate(entries):
        try:
            dt1 = datetime.fromisoformat(curr["checked_at"])
            t2 = datetime.fromisoformat(entries[i + 1]["checked_at"]).timestamp() if i + 1 < len(entries) else now
            raw_h = max(0.0, (t2 - dt1.timestamp()) / 3600.0)
            pres = str(curr.get("presence", ""))
            if "Смотрит" in pres:
                dur = min(raw_h, max_gap)
                w_t += dur
                t = curr.get("title") or "Неизвестно"
                titles[t] = titles.get(t, 0.0) + dur
                hours_b[dt1.hour] += dur
                c = anime_cache.get(t)
                for g in (c.get("genres", []) if isinstance(c, dict) else (c if isinstance(c, list) else [])):
                    genres_t[g] = genres_t.get(g, 0.0) + dur
            elif "Онлайн" in pres:
                dur = min(raw_h, max_gap)
                on_t += dur
                hours_b[dt1.hour] += dur * 0.5
            else:
                off_t += min(raw_h, 24.0)
        except Exception: continue

    peak = "нет данных"
    if any(v > 0 for v in hours_b.values()):
        bh = max(hours_b, key=lambda h: hours_b[h])
        peak = f"{bh:02d}:00–{(bh + 2) % 24:02d}:00"
    return round(w_t, 1), round(on_t, 1), round(off_t, 1), titles, genres_t, peak, max(0, int(round((w_t * 60.0) / 24.0)))

def changed_fields(old: str, res: FetchResult) -> list[str]:
    if not old: return ["новый профиль"]
    p, c = (old.split("|") + ["", "", "", ""])[:4], (res.signature.split("|") + ["", "", "", ""])[:4]
    ch = [lbl for idx, lbl in ((0, "ник"), (1, "уровень"), (2, "статус")) if p[idx] != c[idx]]
    if p[2].startswith("watching:") and c[2].startswith("watching:") and p[3] != c[3]: ch.append("тайтл")
    return ch

class DashboardHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    store_ref: Optional[Store] = None
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/healthz":
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(b"OK"); return
        if path == "/api/data.json" and self.store_ref:
            with self.store_ref.lock:
                body = json.dumps({"updated_at": almaty_now(), "profiles": [asdict(p) for p in self.store_ref.profiles.values()], "anime_cache": self.store_ref.anime_cache}, ensure_ascii=False).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body); return
        if path == "/": self.path = "/anivox_report.html"
        super().do_GET()
    def log_message(self, format: str, *args: Any) -> None: pass

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def get_public_base_url(store: Store) -> str:
    return (store.autoping_url or os.environ.get("RENDER_EXTERNAL_URL", "") or f"http://78.154.103.11:{os.environ.get('PORT', 15887)}").rstrip("/")

def run_web_server(store: Store, port: int = 15887) -> None:
    actual_port = int(os.environ.get("PORT", port))
    DashboardHTTPRequestHandler.store_ref = store
    handler = partial(DashboardHTTPRequestHandler, directory=str(CONFIG_PATH.parent.resolve()))
    try:
        with ThreadedTCPServer(("0.0.0.0", actual_port), handler) as httpd:
            logger.info("Web-сервер запущен: %s/anivox_report.html", get_public_base_url(store))
            httpd.serve_forever()
    except Exception as e:
        logger.error("Ошибка Web-сервера: %s", e)

class Monitor:
    def __init__(self, store: Store, telegram: TelegramClient) -> None:
        self.store, self.telegram = store, telegram
        self.stop_event, self.check_requested, self.ping_requested = threading.Event(), threading.Event(), threading.Event()
        self.paused, self.bridge_chat_active = False, False
        self.check_lock, self.chat_lock = threading.RLock(), threading.Lock()
        self.update_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="tg-worker")

    def stop(self, *_args: Any) -> None:
        self.stop_event.set(); self.check_requested.set(); self.ping_requested.set()
        self.update_executor.shutdown(wait=False, cancel_futures=True)

    def run_autoping(self) -> None:
        while not self.stop_event.is_set():
            url = self.store.autoping_url.strip()
            if url:
                for target in (url.rstrip("/") + "/healthz", url):
                    try:
                        with urllib.request.urlopen(urllib.request.Request(target, headers={"User-Agent": DEFAULT_USER_AGENT}), timeout=15) as r:
                            logger.info("Автопинг OK: %s (%s)", target, r.getcode()); break
                    except Exception: continue
            self.ping_requested.wait(timeout=300)
            self.ping_requested.clear()

    def start_bridge_chat(self, sender_id: str, sender_name: str) -> None:
        with self.chat_lock: self.bridge_chat_active = True
        msg = (f"🚨 <b>ПРИНУДИТЕЛЬНЫЙ ОБЩИЙ ЧАТ АКТИВИРОВАН!</b>\n\n👤 <b>Инициатор:</b> {html.escape(sender_name)} (ID: <code>{sender_id}</code>)\n"
               f"👥 <b>Участников:</b> {len(self.store.allowed_users)}\n\n⚡️ Поддерживаются текст, ГС, кружки, фото и стикеры!\n<i>Для выхода нажмите «🚪 Завершить чат».</i>")
        for uid in list(self.store.allowed_users):
            try: self.telegram.send_message(uid, msg)
            except Exception: pass

    def stop_bridge_chat(self, sender_id: str, sender_name: str) -> None:
        with self.chat_lock:
            if not self.bridge_chat_active:
                try: self.telegram.send_message(sender_id, "ℹ️ Общий чат сейчас не активен.")
                except Exception: pass
                return
            self.bridge_chat_active = False
        msg = f"🛑 <b>ОБЩИЙ ЧАТ ЗАВЕРШЁН</b>\n👤 Завершил: <b>{html.escape(sender_name)}</b> (ID: <code>{sender_id}</code>)"
        for uid in list(self.store.allowed_users):
            try: self.telegram.send_message(uid, msg)
            except Exception: pass

    def relay_bridge_message(self, sender_id: str, sender_name: str, message: dict[str, Any], text: str) -> None:
        mid = message.get("message_id")
        has_media = any(k in message for k in ("photo", "voice", "video_note", "sticker", "video", "audio", "document", "animation"))
        for uid in list(self.store.allowed_users):
            if str(uid) == str(sender_id): continue
            try:
                if has_media and isinstance(mid, int):
                    self.telegram.send_message(uid, f"📎 <b>[{html.escape(sender_name)}]:</b>")
                    self.telegram.copy_message(uid, sender_id, mid)
                elif text:
                    self.telegram.send_message(uid, f"💬 <b>[{html.escape(sender_name)}]:</b>\n{html.escape(text)}")
            except Exception: pass

    def allowed_notification(self, old: str, res: FetchResult) -> bool:
        s = self.store.settings
        f = changed_fields(old, res)
        if not old or not s.notify_on_change: return bool(not old and s.notify_on_change)
        if "статус" in f:
            on = res.presence.startswith(("Онлайн", "Смотрит"))
            if (on and not s.notify_on_online) or (not on and not s.notify_on_offline): f.remove("статус")
        if "тайтл" in f and not s.notify_on_title: f.remove("тайтл")
        return bool(f)

    def check_one(self, profile: Profile, announce: bool) -> FetchResult:
        res = fetch_profile(profile, self.store.settings.request_timeout)
        with self.check_lock:
            profile.last_check, old_sig = res.checked_at, profile.last_signature
            changes = changed_fields(old_sig, res)
            profile.last_ok, profile.last_error = res.ok, res.error
            self.store.add_history(profile.profile_id, res, changes)
            if res.ok:
                now_ts, sess_note = time.time(), ""
                watching = res.presence.startswith("Смотрит:") and bool(res.title)
                if profile.active_title and (not watching or res.title != profile.active_title):
                    el = max(0.0, now_ts - (profile.active_title_since or now_ts))
                    if el >= 120:
                        sess_note = f"🎬 <b>Сессия завершена:</b> {html.escape(profile.active_title)}\n⏳ <b>Длительность:</b> {format_duration_hm(el)} (~{max(1, int(round((el / 60.0) / 24.0)))} сер.)"
                    profile.active_title, profile.active_title_since = "", 0.0
                if watching and profile.active_title != res.title:
                    profile.active_title, profile.active_title_since = res.title, now_ts

                if announce and profile.muted_until <= now_ts and self.allowed_notification(old_sig, res):
                    pref = f"<b>🔔 Изменение:</b> {', '.join(changes)}\n\n" if old_sig else ""
                    txt = pref + format_profile(res, self.store.anime_cache, profile.label, sess_note, profile.muted_until)
                    kb = build_profile_inline_keyboard(res, self.store.anime_cache, profile)
                    for uid in list(self.store.allowed_users):
                        try: self.telegram.send_message(uid, txt, reply_markup=kb)
                        except TelegramError: pass
                profile.last_signature, profile.last_status = res.signature, res.presence
            elif announce and self.store.settings.notify_on_errors and profile.muted_until <= time.time():
                for uid in list(self.store.allowed_users):
                    try: self.telegram.send_message(uid, f"⚠️ <b>Ошибка {html.escape(profile.url)}</b>\n{html.escape(res.error)}")
                    except TelegramError: pass
            self.store.save()
            return res

    def check_all(self, announce: bool = True) -> list[FetchResult]:
        profiles = list(self.store.profiles.values())
        if not profiles or self.stop_event.is_set(): return []
        res_map: dict[str, FetchResult] = {}
        with ThreadPoolExecutor(max_workers=min(len(profiles), 8)) as ex:
            futs = {ex.submit(self.check_one, p, announce): p.profile_id for p in profiles}
            for f in as_completed(futs):
                if self.stop_event.is_set(): break
                try: res_map[futs[f]] = f.result()
                except Exception as e: logger.error("Ошибка проверки %s: %s", futs[f], e)
        return [res_map[p.profile_id] for p in profiles if p.profile_id in res_map]

    def generate_html_report(self, chat_id: Optional[str] = None) -> None:
        path = CONFIG_PATH.with_name("anivox_report.html")
        with self.store.lock:
            profs = list(self.store.profiles.items())
            hist = {k: list(v) for k, v in self.store.history.items()}
            cache = dict(self.store.anime_cache)
            iv = self.store.settings.interval_minutes

        doc = [
            "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>",
            "<title>AniVox Dashboard</title><script src='https://cdn.jsdelivr.net/npm/chart.js'></script><style>",
            ":root{--bg:#f4f6f9;--card:#fff;--text:#333;--border:#e0e0e0;--primary:#007bff;--hover:#f1f1f1;--accent:#e3f2fd}",
            "@media(prefers-color-scheme:dark){:root{--bg:#121212;--card:#1e1e1e;--text:#e0e0e0;--border:#333;--primary:#bb86fc;--hover:#2c2c2c;--accent:#2c2c2c}}",
            "body{font-family:'Segoe UI',sans-serif;background:var(--bg);color:var(--text);margin:0;padding:20px;transition:.3s}",
            ".header-bar{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;margin-bottom:12px}h2{margin:8px 0;font-size:26px}",
            ".live-badge{font-size:13px;padding:6px 12px;border-radius:20px;background:var(--accent);border:1px solid var(--border)}",
            ".container{display:flex;flex-direction:column;max-width:1000px;margin:0 auto}",
            ".tabs{display:flex;flex-wrap:wrap;background:var(--card);border-radius:12px 12px 0 0;border:1px solid var(--border);border-bottom:none}",
            ".tabs button{flex:1;min-width:120px;background:inherit;color:var(--text);border:none;padding:15px;cursor:pointer;font-weight:bold;transition:.3s}",
            ".tabs button:hover{background:var(--hover)}.tabs button.active{border-bottom:3px solid var(--primary);color:var(--primary)}",
            ".tabcontent{display:none;background:var(--card);padding:25px;border:1px solid var(--border);border-radius:0 0 12px 12px}",
            ".stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:15px;margin-bottom:20px}",
            ".card{background:var(--accent);padding:16px;border-radius:10px;text-align:center;border:1px solid var(--border);font-size:15px}",
            ".card span{display:block;font-size:22px;font-weight:bold;color:var(--primary);margin-top:5px}",
            ".charts{display:flex;flex-wrap:wrap;gap:20px;justify-content:space-around;margin-bottom:20px}.chart-box{width:300px;max-width:100%}",
            "table{width:100%;border-collapse:collapse;margin-top:10px;font-size:14px}th,td{border:1px solid var(--border);padding:10px;text-align:left}",
            "th{background:var(--primary);color:#fff}.history-table th,.history-table td{padding:8px;font-size:13px}a{color:var(--primary);text-decoration:none}a:hover{text-decoration:underline}",
            "</style></head><body><div class='container' id='dashboard-root'>",
            f"<div class='header-bar'><h2>🌌 AniVox Dashboard v{VERSION}</h2><div class='live-badge'>🟢 Live-обновление ({almaty_now()[11:19]})</div></div><div class='tabs'>"
        ]
        for idx, (pid, p) in enumerate(profs, 1):
            nm = (p.last_signature.split("|")[0].capitalize() if p.last_signature else "") or p.label or pid
            doc.append(f"<button class='tablinks' data-tab='id_{pid}' onclick=\"openTab('id_{pid}')\">{idx}. {html.escape(nm)}</button>")
        doc.append("</div>")

        charts_js: list[str] = []
        for idx, (pid, p) in enumerate(profs, 1):
            nm = (p.last_signature.split("|")[0].capitalize() if p.last_signature else "") or p.label or pid
            entries = hist.get(pid, [])
            w_h, on_h, off_h, titles, genres_t, peak, eps = calc_stats(entries, cache, iv)
            doc.append(f"<div id='id_{pid}' class='tabcontent'><h3>👤 {idx}. {html.escape(nm)} <small style='font-weight:normal;opacity:.8'>({html.escape(p.last_status or 'не проверялся')})</small></h3>")
            doc.append(f"<div class='stats-grid'><div class='card'>В аниме <span>{w_h} ч. (~{eps} эп.)</span></div><div class='card'>Онлайн <span>{on_h} ч.</span></div><div class='card'>Оффлайн <span>{off_h} ч.</span></div><div class='card'>Пик активности <span>{peak}</span></div></div><div class='charts'>")
            if w_h > 0 or on_h > 0 or off_h > 0:
                doc.append(f"<div class='chart-box'><canvas id='c1_{pid}'></canvas></div>")
                charts_js.append(f"try{{new Chart(document.getElementById('c1_{pid}'),{{type:'doughnut',data:{{labels:['Смотрит','Онлайн','Оффлайн'],datasets:[{{data:[{w_h},{on_h},{off_h}],backgroundColor:['#bb86fc','#03dac6','#cf6679']}}]}},options:{{animation:false,plugins:{{legend:{{position:'bottom'}}}}}}}});}}catch(e){{}}")
            if genres_t:
                sg = sorted(genres_t.items(), key=lambda x: x[1], reverse=True)[:6]
                lbls, vals = ",".join(json.dumps(g, ensure_ascii=False) for g, _ in sg), ",".join(str(round(v, 1)) for _, v in sg)
                doc.append(f"<div class='chart-box'><canvas id='c2_{pid}'></canvas></div>")
                charts_js.append(f"try{{new Chart(document.getElementById('c2_{pid}'),{{type:'polarArea',data:{{labels:[{lbls}],datasets:[{{label:'Часов',data:[{vals}],backgroundColor:'rgba(187,134,252,0.5)'}}]}},options:{{animation:false,plugins:{{legend:{{position:'bottom'}}}},scales:{{r:{{ticks:{{display:false}}}}}}}}}});}}catch(e){{}}")
            doc.append("</div>")
            if titles:
                doc.append("<h4>🏆 Топ тайтлов:</h4><table><tr><th>Тайтл</th><th>Жанры</th><th>Часы</th><th>Серий (~)</th></tr>")
                for t, hrs in sorted(titles.items(), key=lambda x: x[1], reverse=True):
                    gnrs, link = get_anime_data(t, cache)
                    doc.append(f"<tr><td><a href='{link}' target='_blank'>{html.escape(t)}</a></td><td><small>{html.escape(gnrs)}</small></td><td><b>{round(hrs,1)}</b></td><td>~{max(1,int(round((hrs*60)/24)))}</td></tr>")
                doc.append("</table><br>")
            if entries:
                doc.append("<h4>📈 История (до 50):</h4><table class='history-table'><tr><th>#</th><th>Дата (Алматы)</th><th>Статус</th></tr>")
                for h_i, e in enumerate(reversed(entries[-50:]), 1):
                    try: dt_s = datetime.fromisoformat(e.get("checked_at", "")).strftime("%d.%m %H:%M")
                    except Exception: dt_s = e.get("checked_at", "")
                    pr = str(e.get("presence", ""))
                    if pr.startswith("Смотрит:"):
                        tp = pr.replace("Смотрит: ", "").strip()
                        _, lnk = get_anime_data(tp, cache)
                        pr_h = f"Смотрит: <a href='{lnk}' target='_blank'>{html.escape(tp)}</a>"
                    else: pr_h = html.escape(pr)
                    doc.append(f"<tr><td>{h_i}</td><td>{dt_s}</td><td>{pr_h}</td></tr>")
                doc.append("</table>")
            doc.append("</div>")

        doc.append(f"""<script id="charts-runner">function renderAllCharts(){{if(typeof Chart!=='undefined'&&Chart.instances)Object.values(Chart.instances).forEach(c=>c.destroy());{"".join(charts_js)}}}</script>
        <script>
        function openTab(id){{try{{var t=document.getElementsByClassName('tabcontent');for(var i=0;i<t.length;i++)t[i].style.display='none';var l=document.getElementsByClassName('tablinks');for(var i=0;i<l.length;i++)l[i].className=l[i].className.replace(' active','');var c=document.getElementById(id);if(c)c.style.display='block';var b=document.querySelector("button[data-tab='"+id+"']");if(b)b.className+=' active';sessionStorage.setItem('activeTab_anivox',id);}}catch(e){{}}}}
        function restoreActiveTab(){{var a=sessionStorage.getItem('activeTab_anivox');if(a&&document.getElementById(a))openTab(a);else{{var f=document.getElementsByClassName('tablinks')[0];if(f)openTab(f.getAttribute('data-tab'));}}}}
        document.addEventListener('DOMContentLoaded',function(){{restoreActiveTab();renderAllCharts();}});
        setInterval(async function(){{if(window.location.protocol==='file:')return;try{{const r=await fetch(window.location.pathname,{{cache:'no-store'}});if(!r.ok)return;const d=new DOMParser().parseFromString(await r.text(),'text/html');const nr=d.getElementById('dashboard-root'),nc=d.getElementById('charts-runner');if(nr&&document.getElementById('dashboard-root')){{const sy=window.scrollY;document.getElementById('dashboard-root').innerHTML=nr.innerHTML;if(nc)eval(nc.textContent);restoreActiveTab();renderAllCharts();window.scrollTo(0,sy);}}}}catch(e){{}}}},15000);
        </script></div></body></html>""")

        tmp = path.with_suffix(".html.tmp")
        tmp.write_text("\n".join(doc), encoding="utf-8")
        os.replace(tmp, path)
        if chat_id:
            try: self.telegram.send_document(chat_id, path, f"🌐 Live Нейро-Отчет обновлен! Ссылка: {get_public_base_url(self.store)}/anivox_report.html")
            except Exception as e: self.telegram.send_message(chat_id, f"❌ Ошибка отправки отчета: {e}")

    def handle_document_update(self, chat_id: str, doc: dict[str, Any]) -> None:
        """Обрабатывает загруженный в Telegram файл (.py для автообновления кода и GitHub, .json для импорта базы)."""
        fname = str(doc.get("file_name", ""))
        fid = str(doc.get("file_id", ""))
        if not fid: return

        if fname.endswith(".py"):
            self.telegram.send_message(chat_id, f"⏳ Получен файл <code>{html.escape(fname)}</code>. Проверяю синтаксис и обновляю...")
            try:
                raw_bytes = self.telegram.download_file(fid)
                code_str = raw_bytes.decode("utf-8")
                compile(code_str, fname, "exec")  # Защита от синтаксических ошибок
            except SyntaxError as e:
                self.telegram.send_message(chat_id, f"❌ <b>Отмена обновления! Ошибка синтаксиса в файле:</b>\nСтрока {e.lineno}: <code>{html.escape(str(e.msg))}</code>")
                return
            except Exception as e:
                self.telegram.send_message(chat_id, f"❌ Ошибка загрузки/чтения файла: {html.escape(str(e))}")
                return

            gh_status = ""
            try:
                gh_status = push_to_github(self.store, raw_bytes, f"🔄 OTA Update {fname} via Telegram ({almaty_now()})")
            except Exception as e:
                gh_status = f"⚠️ Ошибка пуша на GitHub: <code>{html.escape(str(e))}</code>"

            try:
                tmp_py = SELF_PATH.with_suffix(".py.tmp")
                tmp_py.write_bytes(raw_bytes)
                os.replace(tmp_py, SELF_PATH)
                self.store.save()
                self.telegram.send_message(
                    chat_id,
                    f"🚀 <b>Код успешно обновлен!</b>\n{gh_status}\n\n🔄 Перезапускаю бота на новой версии...",
                )
                time.sleep(1.0)
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception as e:
                self.telegram.send_message(chat_id, f"{gh_status}\n❌ Ошибка локального перезапуска: {html.escape(str(e))}")
            return

        if fname.endswith(".json"):
            try:
                data = json.loads(self.telegram.download_file(fid).decode("utf-8"))
                if not isinstance(data, dict): raise ValueError("Корень JSON должен быть объектом")
                with self.store.lock:
                    for item in (data.get("profiles", []) if isinstance(data.get("profiles"), list) else []):
                        if isinstance(item, dict) and "url" in item and "profile_id" in item:
                            pid = str(item["profile_id"])
                            if pid not in self.store.profiles:
                                self.store.profiles[pid] = Profile(url=str(item["url"]), profile_id=pid, label=str(item.get("label", "")))
                    if isinstance(data.get("history"), dict):
                        for pid, entries in data["history"].items():
                            if isinstance(entries, list):
                                cur = self.store.history.setdefault(str(pid), [])
                                seen = {e.get("checked_at") for e in cur if isinstance(e, dict)}
                                cur.extend([e for e in entries if isinstance(e, dict) and e.get("checked_at") not in seen])
                                self.store.history[str(pid)] = cur[-500:]
                    if isinstance(data.get("anime_cache"), dict):
                        self.store.anime_cache.update(data["anime_cache"])
                    self.store.save()
                self.telegram.send_message(chat_id, f"✅ База из <code>{html.escape(fname)}</code> успешно импортирована и объединена!")
            except Exception as e:
                self.telegram.send_message(chat_id, f"❌ Ошибка импорта JSON: {html.escape(str(e))}")

    def send_settings_menu(self, chat_id: str, msg_id: Optional[int] = None) -> None:
        if chat_id != self.store.chat_id:
            self.telegram.send_message(chat_id, "❌ Настройки меняет только владелец."); return
        s = self.store.settings
        kb = {"inline_keyboard": [
            [{"text": f"⏱ Интервал: {s.interval_minutes} мин", "callback_data": "ignore"}],
            [{"text": str(x), "callback_data": f"set_int_{x}"} for x in (1, 5, 10, 30)],
            [{"text": f"🧹 Очистка истории: {s.auto_cleanup_days} дн.", "callback_data": "ignore"}],
            [{"text": lbl, "callback_data": f"set_cln_{v}"} for lbl, v in (("5 дн", 5), ("15 дн", 15), ("30 дн", 30), ("Выкл", 0))],
        ]}
        txt = "⚙️ <b>Панель управления (Владелец)</b>\nМеняйте параметры в один клик:"
        if msg_id: self.telegram.edit_message_text(chat_id, txt, msg_id, kb)
        else: self.telegram.send_message(chat_id, txt, kb)

    def send_notifications_menu(self, chat_id: str, msg_id: Optional[int] = None) -> None:
        if chat_id != self.store.chat_id:
            self.telegram.send_message(chat_id, "❌ Настройки уведомлений меняет только владелец."); return
        s = self.store.settings
        kb = {"inline_keyboard": [
            [{"text": f"{'✅' if s.notify_on_online else '❌'} Онлайн", "callback_data": "ntf_onl"}, {"text": f"{'✅' if s.notify_on_offline else '❌'} Оффлайн", "callback_data": "ntf_off"}],
            [{"text": f"{'✅' if s.notify_on_title else '❌'} Смена тайтла", "callback_data": "ntf_ttl"}, {"text": f"{'✅' if s.notify_on_errors else '❌'} Ошибки", "callback_data": "ntf_err"}],
            [{"text": f"{'🔔' if s.notify_on_change else '🔕'} ГЛАВНЫЙ РУБИЛЬНИК ({'ВКЛ' if s.notify_on_change else 'ВЫКЛ'})", "callback_data": "ntf_all"}],
        ]}
        txt = "🔕 <b>Настройка уведомлений</b>\nВыберите, о чем бот должен писать:"
        if msg_id: self.telegram.edit_message_text(chat_id, txt, msg_id, kb)
        else: self.telegram.send_message(chat_id, txt, kb)

    def command(self, chat_id: str, is_owner: bool, cmd: str, args: str) -> str:
        if cmd in {"/start", "/help"}:
            return (
                f"<b>AniVox Monitor v{VERSION}</b>\n\n"
                "<code>/add &lt;ссылка&gt; [метка]</code> — добавить профиль\n"
                "<code>/list</code> — список профилей\n"
                "<code>/remove &lt;id&gt;</code> — удалить профиль\n"
                "<code>/check [id|all]</code> — параллельная проверка\n"
                "<code>/history [id] [кол-во]</code> — история и пик активности\n"
                "<code>/report</code> — Live HTML-отчет\n"
                "<code>/find &lt;текст&gt;</code> — поиск в истории\n"
                "<code>/friends</code> — управление друзьями\n"
                "<code>/autoping &lt;ссылка&gt;</code> — автопинг Render.com\n"
                "<code>/github</code> — настройка автообновления на GitHub\n"
                "<code>/chat</code> | <code>/stopchat</code> — общий мультимедиа-чат\n\n"
                "💡 <i>Отправьте боту файл <code>.py</code>, чтобы автоматически обновить код на GitHub и перезапустить бота!</i>"
            )
        if cmd in {"/github", "/update"}:
            if not is_owner: return "Только владелец может настраивать GitHub."
            parts = args.split()
            if len(parts) >= 2:
                self.store.github_token = parts[0].strip()
                self.store.github_repo = parts[1].strip()
                if len(parts) >= 3: self.store.github_branch = parts[2].strip()
                if len(parts) >= 4: self.store.github_path = parts[3].strip()
                self.store.save()
                return (f"✅ <b>GitHub привязан!</b>\n• Репозиторий: <code>{html.escape(self.store.github_repo)}</code>\n"
                        f"• Ветка: <code>{html.escape(self.store.github_branch)}</code>\n• Файл: <code>{html.escape(self.store.github_path)}</code>\n\n"
                        "Теперь просто отправьте обновленный <code>.py</code> файл в этот чат — он сам зальется на GitHub и обновит бота!")
            t_mask = f"{self.store.github_token[:4]}…{self.store.github_token[-4:]}" if len(self.store.github_token) > 8 else ("задан" if self.store.github_token else "не задан")
            return (f"🐙 <b>Настройки автообновления GitHub:</b>\n• Токен: <code>{t_mask}</code>\n"
                    f"• Репозиторий: <code>{html.escape(self.store.github_repo or 'не задан')}</code>\n"
                    f"• Ветка: <code>{html.escape(self.store.github_branch)}</code>\n"
                    f"• Путь к файлу: <code>{html.escape(self.store.github_path)}</code>\n\n"
                    "<b>Как привязать:</b>\n<code>/github &lt;ТОКЕН&gt; &lt;ник/репо&gt; [ветка] [путь_к_файлу.py]</code>\n"
                    "После этого просто кидайте файл <code>.py</code> в чат с ботом!")
        if cmd == "/autoping":
            if not is_owner: return "Только владелец."
            arg = args.strip()
            if not arg:
                return f"🌐 <b>Автопинг:</b> <code>{html.escape(self.store.autoping_url or 'выключен')}</code>\nЗадать: <code>/autoping https://проект.onrender.com</code>\nВыкл: <code>/autoping off</code>"
            if arg.lower() in {"off", "stop", "0", "выкл"}:
                self.store.autoping_url = ""; self.store.save(); return "🛑 Автопинг отключен."
            self.store.autoping_url = (arg if arg.startswith(("http://", "https://")) else "https://" + arg).rstrip("/")
            self.store.save(); self.ping_requested.set()
            return f"✅ <b>Автопинг запущен!</b>\n🔗 <code>{html.escape(self.store.autoping_url)}</code>\n📊 Отчет: {html.escape(self.store.autoping_url)}/anivox_report.html"
        if cmd == "/list":
            if not self.store.profiles: return "Профилей нет. Добавьте через /add."
            return "<b>Мои профили:</b>\n" + "\n".join(f"<b>{i}.</b> <code>{p.profile_id}</code> — {html.escape(p.label or p.url)} — {html.escape(p.last_status or 'не проверялся')}{' 🔕' if p.muted_until > time.time() else ''}" for i, p in enumerate(self.store.profiles.values(), 1))
        if cmd == "/add":
            if not is_owner: return "Только владелец."
            parts = args.split(maxsplit=1)
            if not parts: return "Пример: /add https://anivox.fun/profile/27788 метка"
            try: url, pid = normalize_profile_url(parts[0])
            except ValueError as e: return str(e)
            ex = self.store.profiles.get(pid)
            p = ex or Profile(url=url, profile_id=pid)
            p.url = url
            if len(parts) > 1: p.label = parts[1].strip()
            self.store.profiles[pid] = p; self.store.save(); self.check_requested.set()
            return f"Профиль {pid} {'обновлён' if ex else 'добавлен'}."
        if cmd == "/remove":
            if not is_owner: return "Только владелец."
            pid = args.split()[0] if args else ""
            if pid in self.store.profiles:
                del self.store.profiles[pid]; self.store.save(); return f"Профиль {pid} удалён."
            return "Профиль не найден."
        if cmd == "/check":
            t = (args or "all").lower()
            res_list = self.check_all(False) if t == "all" else ([self.check_one(self.store.profiles[t], False)] if t in self.store.profiles else [])
            if not res_list: return "Профиль не найден или список пуст."
            for r in res_list:
                p = self.store.profiles.get(r.profile_id)
                if r.ok and p:
                    self.telegram.send_message(chat_id, format_profile(r, self.store.anime_cache, p.label, muted_until=p.muted_until), reply_markup=build_profile_inline_keyboard(r, self.store.anime_cache, p))
                else:
                    self.telegram.send_message(chat_id, f"❌ {html.escape(r.url)}\n{html.escape(r.error)}")
            return ""
        if cmd == "/history":
            parts = args.split()
            pid = parts[0] if parts and parts[0].isdigit() else ""
            try: lim = max(1, min(30, int(parts[1] if pid and len(parts) > 1 else parts[0]))) if parts else 15
            except ValueError: lim = 15
            blocks = []
            for idx, cid in enumerate([pid] if pid else list(self.store.profiles), 1):
                p, entries = self.store.profiles.get(cid), self.store.history.get(cid, [])
                w_h, _, _, _, _, peak, eps = calc_stats(entries, self.store.anime_cache, self.store.settings.interval_minutes)
                rows = [f"👤 <b>{idx}. {html.escape(p.label if p and p.label else cid)}</b> (ID {cid})", f"📊 <i>Просмотр: {w_h} ч. (~{eps} сер.) | Пик: {peak}</i>"]
                if not entries: rows.append("<i>История пуста</i>")
                else:
                    for h_i, e in enumerate(entries[-lim:][::-1], 1):
                        dt_s = e.get("checked_at", "")[5:16].replace("T", " ")
                        rows.append(f"<b>{h_i}.</b> {dt_s} — {html.escape(str(e.get('presence', '?')))}")
                blocks.append("\n".join(rows))
            return "\n\n".join(blocks)[:4000] if blocks else "История пуста."
        if cmd == "/summary":
            if not self.store.profiles: return "Профилей нет."
            rows = ["🧾 <b>Сводка по пользователям:</b>"]
            for i, p in enumerate(self.store.profiles.values(), 1):
                rows.append(f"<b>{i}.</b> {html.escape(p.label or p.profile_id)}: {html.escape(p.last_status or 'не проверялся')} (записей: {len(self.store.history.get(p.profile_id, []))})")
            return "\n".join(rows)[:4000]
        if cmd in {"/html", "/report", "/site"}:
            self.telegram.send_message(chat_id, "⏳ Генерирую Live-дашборд..."); self.generate_html_report(chat_id); return ""
        if cmd in {"/find", "/search"}:
            q = args.strip().lower()
            if not q: return "Пример: /find текст"
            found = [f"• {html.escape((self.store.history.get(p.profile_id, [{}])[-1]).get('nickname', p.label or p.profile_id))} (ID {p.profile_id}) — {html.escape(p.last_status)}" for p in self.store.profiles.values() if q in f"{p.profile_id} {p.label} {p.last_status} {p.last_signature}".lower()]
            return "\n".join(["Найдено:"] + found) if found else "Ничего не найдено."
        if cmd in {"/probe", "/test"}:
            p = next(iter(self.store.profiles.values()), Profile("https://anivox.fun/profile/27788", "27788"))
            t0 = time.monotonic(); r = fetch_profile(p, self.store.settings.request_timeout); el = time.monotonic() - t0
            return f"✅ Ответ за {el:.2f} сек.\n{format_profile(r, self.store.anime_cache)}" if r.ok else f"❌ Ошибка за {el:.2f} сек.: {html.escape(r.error)}"
        if cmd == "/export":
            path = CONFIG_PATH.with_name("anivox_history_export.json")
            with self.store.lock: save_json(path, {"exported_at": almaty_now(), "profiles": [asdict(p) for p in self.store.profiles.values()], "history": self.store.history, "anime_cache": self.store.anime_cache})
            self.telegram.send_document(chat_id, path, "Экспорт истории AniVox (JSON)"); return ""
        if cmd == "/settings": self.send_settings_menu(chat_id); return ""
        if cmd in {"/notifications", "/notif"}: self.send_notifications_menu(chat_id); return ""
        if cmd == "/pause":
            if not is_owner: return "Только владелец."
            self.paused = True; return "⏸ Проверки приостановлены."
        if cmd == "/resume":
            if not is_owner: return "Только владелец."
            self.paused = False; self.check_requested.set(); return "▶️ Проверки возобновлены."
        if cmd in {"/friends", "/friend"}:
            if not is_owner: return "Только владелец."
            parts = args.split()
            if len(parts) == 2 and parts[0] == "add":
                fid = parts[1].strip()
                if fid not in self.store.allowed_users: self.store.allowed_users.append(fid); self.store.save()
                return f"✅ Пользователь <code>{fid}</code> добавлен!"
            if len(parts) == 2 and parts[0] == "remove":
                fid = parts[1].strip()
                if fid in self.store.allowed_users and fid != self.store.chat_id: self.store.allowed_users.remove(fid); self.store.save()
                return f"✅ Пользователь <code>{fid}</code> удален."
            return "🎭 <b>Друзья с доступом:</b>\n" + "\n".join(f"• <code>{u}</code>" for u in self.store.allowed_users) + "\n\n<code>/friend add ID</code> | <code>/friend remove ID</code>"
        return "Неизвестная команда. Используйте /help."

    def _handle_callback(self, cb: dict[str, Any]) -> None:
        chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
        mid = cb.get("message", {}).get("message_id")
        data, qid = str(cb.get("data", "")), str(cb.get("id", ""))
        if chat_id not in self.store.allowed_users:
            self.telegram.answer_callback_query(qid, "⛔️ Доступ ограничен."); return

        if data.startswith("recheck_"):
            pid = data.split("_", 1)[1]
            prof = self.store.profiles.get(pid)
            if not prof: self.telegram.answer_callback_query(qid, "Не найден"); return
            if chat_id != self.store.chat_id:
                try: self.telegram.send_message(self.store.chat_id, f"🕵️‍♂️ Друг (<code>{chat_id}</code>) обновил карточку <code>{pid}</code>")
                except Exception: pass
            res = self.check_one(prof, False)
            if res.ok and mid:
                try: self.telegram.edit_message_text(chat_id, format_profile(res, self.store.anime_cache, prof.label, muted_until=prof.muted_until), mid, build_profile_inline_keyboard(res, self.store.anime_cache, prof))
                except Exception: pass
                self.telegram.answer_callback_query(qid, f"✅ {res.presence}")
            else: self.telegram.answer_callback_query(qid, f"⚠️ {res.error[:100]}")
            return

        if chat_id != self.store.chat_id:
            self.telegram.answer_callback_query(qid, "Только владелец!")
            try: self.telegram.send_message(self.store.chat_id, f"🕵️‍♂️ Друг (<code>{chat_id}</code>) нажал кнопку: <b>{html.escape(data)}</b>")
            except Exception: pass
            return

        if data.startswith("mute_"):
            prof = self.store.profiles.get(data.split("_", 1)[1])
            if not prof: return
            prof.muted_until = 0.0 if prof.muted_until > time.time() else time.time() + 7200
            self.store.save()
            res = self.check_one(prof, False)
            if res.ok and mid:
                try: self.telegram.edit_message_text(chat_id, format_profile(res, self.store.anime_cache, prof.label, muted_until=prof.muted_until), mid, build_profile_inline_keyboard(res, self.store.anime_cache, prof))
                except Exception: pass
            self.telegram.answer_callback_query(qid, "🔕 Заглушено на 2ч" if prof.muted_until else "🔔 Звук включен")
        elif data.startswith("set_int_"):
            self.store.settings.interval_minutes = int(data.split("_")[-1]); self.store.save()
            self.send_settings_menu(chat_id, mid); self.telegram.answer_callback_query(qid, "Интервал обновлен"); self.check_requested.set()
        elif data.startswith("set_cln_"):
            self.store.settings.auto_cleanup_days = int(data.split("_")[-1]); self.store.save()
            self.send_settings_menu(chat_id, mid); self.telegram.answer_callback_query(qid, "Очистка обновлена")
        elif data.startswith("ntf_"):
            s = self.store.settings
            if data == "ntf_onl": s.notify_on_online = not s.notify_on_online
            elif data == "ntf_off": s.notify_on_offline = not s.notify_on_offline
            elif data == "ntf_ttl": s.notify_on_title = not s.notify_on_title
            elif data == "ntf_err": s.notify_on_errors = not s.notify_on_errors
            elif data == "ntf_all": s.notify_on_change = not s.notify_on_change
            self.store.save(); self.send_notifications_menu(chat_id, mid); self.telegram.answer_callback_query(qid, "Обновлено")

    def handle_update(self, update: dict[str, Any]) -> None:
        try:
            if "callback_query" in update: self._handle_callback(update["callback_query"]); return
            msg = update.get("message", {})
            if not isinstance(msg, dict): return
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if not chat_id: return
            u = msg.get("from", {})
            uname = u.get("username") or u.get("first_name") or "Без имени"

            if chat_id not in self.store.allowed_users:
                try: self.telegram.send_message(self.store.chat_id, f"🚨 <b>Новый пользователь:</b> {html.escape(str(uname))} (<code>{chat_id}</code>)\nДать доступ: <code>/friend add {chat_id}</code>")
                except Exception: pass
                self.telegram.send_message(chat_id, f"⛔️ Доступ ограничен. Ваш ID: <code>{chat_id}</code>."); return

            is_owner = (chat_id == self.store.chat_id)
            text = compact(msg.get("text") or msg.get("caption", ""))

            # Если владелец прислал файл .py или .json (вне режима общего чата)
            if is_owner and isinstance(msg.get("document"), dict) and not self.bridge_chat_active:
                self.handle_document_update(chat_id, msg["document"])
                return

            if text in {"💬 Связать всех в чат", "/chat"}: self.start_bridge_chat(chat_id, uname); return
            if text in {"🚪 Завершить чат", "/stopchat"}: self.stop_bridge_chat(chat_id, uname); return

            btn_map = {
                "📊 Проверить всех": "/check all", "👥 Мои профили": "/list", "🌐 Открыть сайт-отчет": "/report",
                "📈 История": "/history", "⚙️ Настройки": "/settings", "🔕 Уведомления": "/notifications",
                "⏸ Пауза": "/pause", "▶️ Продолжить": "/resume", "🎭 Друзья": "/friends",
            }
            if self.bridge_chat_active and not text.startswith("/") and text not in btn_map and text not in {"➕ Добавить", "🗑 Удалить"}:
                self.relay_bridge_message(chat_id, uname, msg, text); return

            if not is_owner and text and not self.bridge_chat_active:
                try: self.telegram.send_message(self.store.chat_id, f"🕵️‍♂️ Друг (<code>{chat_id}</code>) нажал/написал:\n<b>{html.escape(text)}</b>")
                except Exception: pass

            if text in btn_map: text = btn_map[text]
            if text == "➕ Добавить": self.telegram.send_message(chat_id, "Отправьте:\n<code>/add https://anivox.fun/profile/27788 метка</code>"); return
            if text == "🗑 Удалить": self.telegram.send_message(chat_id, "Отправьте:\n<code>/remove ID</code>"); return
            if not text: return

            cmd, _, args = text.partition(" ")
            resp = self.command(chat_id, is_owner, cmd.lower().split("@", 1)[0], args.strip())
            if resp: self.telegram.send_message(chat_id, resp)
        except Exception as e:
            logger.error("Ошибка апдейта: %s", e, exc_info=True)

    def enforce_cleanup(self) -> None:
        days = self.store.settings.auto_cleanup_days
        if days <= 0: return
        cut = time.time() - days * 86400
        with self.store.lock:
            for pid, entries in self.store.history.items():
                self.store.history[pid] = [e for e in entries if (datetime.fromisoformat(e.get("checked_at", almaty_now())).timestamp() if e.get("checked_at") else time.time()) >= cut]
            self.store.save()

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                for u in self.telegram.get_updates(timeout=25):
                    if self.stop_event.is_set(): break
                    self.update_executor.submit(self.handle_update, u)
            except Exception as e:
                logger.warning("Сбой Telegram polling: %s", e); self.stop_event.wait(3)

    def run_scheduler(self) -> None:
        last_cln = 0.0
        while not self.stop_event.is_set():
            try:
                if time.time() - last_cln > 3600: self.enforce_cleanup(); last_cln = time.time()
                if not self.paused:
                    self.check_all(True)
                    self.generate_html_report(None)
            except Exception as e: logger.error("Ошибка планировщика: %s", e)
            self.check_requested.wait(timeout=max(10, self.store.settings.interval_minutes * 60))
            self.check_requested.clear()

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_PATH, encoding="utf-8")])
    store = Store(CONFIG_PATH)
    if not store.token: store.token = input("Токен Telegram-бота: ").strip()
    if not store.chat_id: store.chat_id = input("Ваш chat ID: ").strip()
    store.save()
    tg = TelegramClient(store.token)
    acc = tg.get_me()
    print(f"Бот запущен: @{acc.get('username', 'bot')}")
    mon = Monitor(store, tg)
    signal.signal(signal.SIGINT, mon.stop); signal.signal(signal.SIGTERM, mon.stop)
    try:
        tg.send_message(store.chat_id, f"✅ <b>AniVox Monitor v{VERSION} запущен!</b>\n• GitHub OTA автообновление: отправьте <code>.py</code> файл в чат\n• Live AJAX сайт-отчет и мультимедиа мост активны")
    except Exception: pass
    threading.Thread(target=run_web_server, args=(store,), daemon=True).start()
    threading.Thread(target=mon.run_autoping, daemon=True).start()
    sched = threading.Thread(target=mon.run_scheduler, daemon=True)
    sched.start()
    try: mon.run()
    finally: mon.stop(); sched.join(timeout=5); store.save()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
