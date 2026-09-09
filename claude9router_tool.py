# -*- coding: utf-8 -*-
# ============================================================
#  ابزار بکاپ/بازیابی Claude و 9router — ویندوز
#  خروجی نهایی: EXE مستقل (PyInstaller) با GUI شیشه‌ای (PySide6)
#  سرویس: Google Drive (حساب سرویس + پوشه اشتراکی در Drive کاربر)
#    هر جزء بکاپ (زیپ/تکه/مانیفست/لاگ/شاخص) به‌صورت فایل در پوشه پشتیبان ذخیره می‌شود
#    بازیابی با دانلود اجزای ثبت‌شده در شاخص انجام می‌شود
#  بکاپ سه مسیر پیش‌فرض:
#    AppData\Local\Claude-3p
#    AppData\Roaming\9router
#    %USERPROFILE%\.claude
#  + مسیرهای سفارشی (پوشه/فایل) انتخابی کاربر: %LOCALAPPDATA%\Claude9RouterTool\custom_paths.json
#  فایل تکی بزرگ‌تر از ۱۰۰MB به تکه‌های ۹۸MB شکسته می‌شود
#  هر بکاپ: ابتدا بکاپ محلی کامل و کنترل ← آپلود اجزای جدید به Drive ←
#           پس از موفقیت کامل، فایل‌های بکاپ قبلی پوشه حذف می‌شوند (فقط آخرین بکاپ می‌ماند)
#  فایل‌های بازِ در حال استفاده با Snapshot (VSS) خوانده می‌شوند؛ فاصله بکاپ خودکار قابل تنظیم است
#  کلید حساب سرویس با DPAPI ویندوز (متناسب با کاربر) رمزنگاری و ذخیره می‌شود
# ============================================================

import base64
import ctypes
import ctypes.wintypes
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

APP_NAME = "Claude9RouterTool"
APP_VERSION = "2.5.1"

USER_AGENT = APP_NAME + "/" + APP_VERSION

# --- Google Drive ---
GD_DRIVE_API = "https://www.googleapis.com/drive/v3"
GD_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
GD_TOKEN_URL = "https://oauth2.googleapis.com/token"
GD_SCOPE = "https://www.googleapis.com/auth/drive"
GD_CHUNK = 8 * 1024 * 1024              # حجم تکه در آپلود resumable
PART_TARGET = 98 * 1024 * 1024          # حداکثر حجم محتوای هر بخش زیپ (۹۸MB)
CHUNK_THRESHOLD = 100 * 1024 * 1024     # فایل تکی بزرگ‌تر از این به تکه‌های خام شکسته می‌شود
CHUNK_SIZE = 98 * 1024 * 1024           # حجم هر تکه برای فایل‌های بزرگ

SKIP_SUFFIXES = (".lock", ".tmp", ".part")
SKIP_FILENAMES = {"SingletonCookie", "SingletonLock", "SingletonSocket"}

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

# ------------------------------------------------------------
#  مسیرهای منابع (فونت، setup جاسازی‌شده)
# ------------------------------------------------------------

def resource_dir():
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def asset_path(name):
    return os.path.join(resource_dir(), "assets", name)


# ------------------------------------------------------------
#  ابزارهای عمومی
# ------------------------------------------------------------

def localappdata():
    return os.environ.get("LOCALAPPDATA", os.path.join(userprofile(), "AppData", "Local"))


def appdata():
    return os.environ.get("APPDATA", os.path.join(userprofile(), "AppData", "Roaming"))


def userprofile():
    return os.environ.get("USERPROFILE", "")


def hostname():
    return os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "unknown"


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def longpath(p):
    r"""برای مسیرهای بلند ویندوز (>۲۳۰ نویسه) پیشوند \\?\ اضافه می‌کند."""
    p = os.path.abspath(p)
    if os.name == "nt" and not p.startswith("\\\\?\\") and len(p) > 230:
        p = "\\\\?\\" + p
    return p


def format_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024


def sha256_file(path):
    h = hashlib.sha256()
    with open(longpath(path), "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def path_in(a, b):
    a = os.path.normcase(os.path.normpath(os.path.abspath(a)))
    b = os.path.normcase(os.path.normpath(os.path.abspath(b)))
    return a == b or a.startswith(b + os.sep)


class Logger:
    r"""لاگ کنسول + فایل در %LOCALAPPDATA%\Claude9RouterTool\logs"""

    def __init__(self, kind, run_id_):
        base = os.path.join(localappdata(), APP_NAME, "logs")
        os.makedirs(base, exist_ok=True)
        self.path = os.path.join(base, f"{kind}_{run_id_}.log")
        self.f = open(self.path, "a", encoding="utf-8")
        self.f.write(f"=== {kind.upper()}  {now_iso()} ===\n")
        self.f.flush()

    def line(self, msg, level="INFO"):
        self.f.write(f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {msg}\n")
        self.f.flush()

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


# ------------------------------------------------------------
#  دسترسی ادمین (UAC)
# ------------------------------------------------------------

def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    """اجرای مجدد خودمان با سطح ادمین."""
    frozen = getattr(sys, "frozen", False)
    exe = sys.executable
    params = []
    if not frozen:
        params.append(f'"{os.path.abspath(sys.argv[0])}"')
    params.extend(sys.argv[1:])
    cmdline = " ".join(params)
    try:
        res = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, cmdline, None, 1)
        return res > 32
    except Exception:
        return False


# ------------------------------------------------------------
#  Volume Shadow Copy (VSS) — خواندن فایل‌های بازِ در حال استفاده
# ------------------------------------------------------------

class VssSnapshot:
    """Snapshot لحظه‌ای از درایوهای موردنیاز با VSS ویندوز (از طریق diskshadow).
    برنامه‌های در حال اجرا لازم نیست بسته شوند؛ فایل‌های قفل‌شده هم خوانده می‌شوند.
    fallback: اگر VSS در دسترس نبود، خواندن معمولی انجام می‌شود."""

    DEV_RE = re.compile(r"(\\\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy\d+)", re.I)
    ID_RE = re.compile(r"(\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\})")

    def __init__(self, rep=None):
        self.rep = rep
        self.active = False
        self._map = {}      # 'C:' -> '\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopyN'
        self._sid = None    # شناسه Snapshot برای پاک‌سازی

    def _log(self, msg):
        if self.rep:
            self.rep(msg)

    def _diskshadow(self, script_text, timeout=300):
        scr = tempfile.NamedTemporaryFile("w", suffix=".dsh", delete=False, encoding="ascii")
        scr.write(script_text)
        scr.close()
        try:
            r = subprocess.run(
                ["diskshadow", "/s", scr.name],
                capture_output=True, text=True, timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
            return (r.stdout or "") + "\n" + (r.stderr or "")
        finally:
            try:
                os.unlink(scr.name)
            except OSError:
                pass

    def start(self, drives):
        """ساخت Snapshot برای درایوهای لازم. برمی‌گرداند True اگر فعال شد."""
        if os.environ.get("C9R_SELFTEST"):
            return False
        if not drives:
            return False
        try:
            out = self._diskshadow(
                "set context persistent nowriters\nset verbose off\n"
                + "".join(f"add volume {d}\n" for d in drives)
                + "create\nlist shadows all\n"
            )
        except Exception as ex:
            self._log("  ⚠ VSS در دسترس نیست (" + str(ex)[:120] + ")؛ خواندن معمولی انجام می‌شود.")
            return False
        devs = self.DEV_RE.findall(out)
        ids = self.ID_RE.findall(out)
        if not devs:
            self._log("  ⚠ Snapshot ساخته نشد؛ خواندن معمولی انجام می‌شود.")
            return False
        for d in drives:
            self._map[d.upper().rstrip("\\")] = devs[-1]
        self._sid = ids[-1] if ids else None
        self.active = True
        self._log("VSS Snapshot فعال شد؛ فایل‌های بازِ در حال استفاده هم خوانده می‌شوند.")
        return True

    def map_path(self, path):
        """مسیر فایل را (در صورت فعال بودن Snapshot) به مسیر داخل Snapshot نگاشت می‌کند."""
        if not self.active or not self._map:
            return path
        d, rest = os.path.splitdrive(os.path.abspath(path))
        if not d:
            return path
        key = d.upper()
        if key in self._map:
            return self._map[key] + rest.replace("/", "\\")
        return path

    def delete(self):
        """حذف Snapshot (اگر شناسه‌اش را داریم)."""
        if not self._sid:
            self.active = False
            return
        try:
            self._diskshadow(f"set context persistent nowriters\ndelete shadows set {self._sid}\n", timeout=120)
        except Exception:
            if self.rep:
                self.rep("  ⚠ حذف Snapshot ناموفق بود (با Reboot پاک می‌شود).")
        self.active = False


# ------------------------------------------------------------
#  مسیرهای سفارشی (config.json در %LOCALAPPDATA%)
# ------------------------------------------------------------

def config_path():
    return os.path.join(localappdata(), APP_NAME, "custom_paths.json")


def load_custom_paths():
    try:
        with open(longpath(config_path()), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(p) for p in data if isinstance(p, str) and p.strip()]
    except Exception:
        pass
    return []


def save_custom_paths(paths):
    try:
        os.makedirs(os.path.dirname(longpath(config_path())), exist_ok=True)
        with open(longpath(config_path()), "w", encoding="utf-8") as f:
            json.dump([str(p) for p in paths], f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def settings_path():
    return os.path.join(localappdata(), APP_NAME, "settings.json")


def load_settings():
    try:
        with open(longpath(settings_path()), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(d):
    try:
        os.makedirs(os.path.dirname(longpath(settings_path())), exist_ok=True)
        with open(longpath(settings_path()), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# ------------------------------------------------------------
#  رمزنگاری DPAPI ویندوز (حفاظت توکن ربات در دیسک)
# ------------------------------------------------------------

class CryptProtect:
    """رمزنگاری/فارغ‌سازی متن با CryptProtectData (DPAPI — فقط همین کاربر ویندوز)."""

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    @classmethod
    def protect(cls, text):
        data = text.encode("utf-8")
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = cls._BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = cls._BLOB()
        if not ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(blob_in), "C9R", None, None, None, 0, ctypes.byref(blob_out)):
            raise OSError("CryptProtectData ناموفق بود")
        try:
            return base64.b64encode(ctypes.string_at(blob_out.pbData, blob_out.cbData)).decode("ascii")
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)

    @classmethod
    def unprotect(cls, b64_text):
        raw = base64.b64decode(b64_text)
        buf = ctypes.create_string_buffer(raw, len(raw))
        blob_in = cls._BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = cls._BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
            raise OSError("CryptUnprotectData ناموفق بود (فایل روی این حساب ساخته نشده؟)")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)


# ------------------------------------------------------------
#  اتصال Google Drive (OAuth حساب خود کاربر)
#    settings.json: gd_folder_id (شناسه پوشهٔ مقصد)
#    secrets.dat:   client_json (OAuth Client) + refresh_token با DPAPI رمزنگاری‌شده
# ------------------------------------------------------------

SECRETS_PATH = os.path.join(localappdata(), APP_NAME, "secrets.dat")


def load_secrets():
    """اعتبارنامه ذخیره‌شده — یا مقادیر خالی."""
    try:
        with open(longpath(SECRETS_PATH), "r", encoding="utf-8") as f:
            enc = json.load(f)
        return {
            "client_json": CryptProtect.unprotect(enc["client_b64"]) if enc.get("client_b64") else "",
            "refresh_token": CryptProtect.unprotect(enc["refresh_b64"]) if enc.get("refresh_b64") else "",
        }
    except Exception:
        return {"client_json": "", "refresh_token": ""}


def save_secrets(client_json, refresh_token):
    enc = {
        "client_b64": CryptProtect.protect(client_json) if client_json else "",
        "refresh_b64": CryptProtect.protect(refresh_token) if refresh_token else "",
    }
    os.makedirs(os.path.dirname(longpath(SECRETS_PATH)), exist_ok=True)
    with open(longpath(SECRETS_PATH), "w", encoding="utf-8") as f:
        json.dump(enc, f, indent=1)


def gd_credentials_ready():
    """اعتبارنامه را در حافظه ثبت و آمادگی را برمی‌گرداند.
    اگر فایل محلی (مخصوص همین ویندوز) نبود، از اعتبارنامهٔ جاسازی‌شده استفاده می‌شود
    تا روی هر ویندوز جدید بدون لاگین کار کند."""
    s = load_settings()
    secrets = load_secrets()
    client = secrets.get("client_json")
    refresh = secrets.get("refresh_token")
    if not (client and refresh) and _EMBEDDED_CLIENT_JSON and _EMBEDDED_REFRESH_TOKEN:
        client, refresh = _EMBEDDED_CLIENT_JSON, _EMBEDDED_REFRESH_TOKEN
    if client and refresh:
        gd_set_credentials(client, refresh)
    ready = bool(client and refresh and (s.get("gd_folder_id") or _EMBEDDED_FOLDER_ID))
    return ready, {"client_json": client or "", "refresh_token": refresh or ""}


# ------------------------------------------------------------
#  ساخت مجموعه بکاپ (سه مسیر پیش‌فرض + مسیرهای سفارشی)
# ------------------------------------------------------------

def build_backup_set(warnings, vss=None):
    """برمی‌گرداند: (sources, files, dirs). مسیرها: Claude-3p، 9router، ‎.claude و سفارشی‌ها."""
    sources = []
    files = []
    dirs = []

    claude = os.path.join(localappdata(), "Claude-3p")
    nine = os.path.join(appdata(), "9router")
    dot_claude = os.path.join(userprofile(), ".claude")

    if os.path.isdir(claude):
        sources.append({"label": "Claude (Claude-3p)", "arcroot": "Claude-3p", "path": claude})
    else:
        warnings.append("پوشه Claude-3p پیدا نشد؛ بخش کلود در بکاپ ثبت نشد.")

    if os.path.isdir(nine):
        sources.append({"label": "9router", "arcroot": "9router", "path": nine})
    else:
        warnings.append("پوشه 9router پیدا نشد؛ بخش 9router در بکاپ ثبت نشد.")

    if os.path.isdir(dot_claude):
        sources.append({"label": "Claude (.claude)", "arcroot": ".claude", "path": dot_claude})
    else:
        warnings.append("پوشه .claude (در پروفایل کاربر) پیدا نشد؛ این بخش در بکاپ ثبت نشد.")

    # مسیرهای سفارشی انتخابی کاربر (پوشه یا فایل) — هر منبع ریشه آرشیو یکتا می‌گیرد
    used_roots = {s["arcroot"] for s in sources}

    def uniq_root(base):
        if base not in used_roots:
            used_roots.add(base)
            return base
        i = 2
        while f"{base}_{i}" in used_roots:
            i += 1
        used_roots.add(f"{base}_{i}")
        return f"{base}_{i}"

    for cpath in load_custom_paths():
        if os.path.isdir(cpath):
            if any(path_in(cpath, s["path"]) or path_in(s["path"], cpath) for s in sources):
                warnings.append(f"مسیر سفارشی «{cpath}» با منابع دیگر همپوشانی دارد؛ نادیده گرفته شد.")
                continue
            sources.append({"label": "سفارشی (پوشه): " + cpath, "arcroot": uniq_root("custom"), "path": cpath})
        elif os.path.isfile(cpath):
            if any(path_in(cpath, s["path"]) for s in sources):
                warnings.append(f"فایل سفارشی «{cpath}» داخل منابع دیگر است؛ نادیده گرفته شد.")
                continue
            sources.append({"label": "سفارشی (فایل): " + cpath, "arcroot": uniq_root("custom"), "path": cpath})
        else:
            warnings.append(f"مسیر سفارشی «{cpath}» وجود ندارد؛ نادیده گرفته شد.")

    def pth(p):
        # مسیر «خواندن»: اگر Snapshot فعال باشد از داخل Snapshot می‌خوانیم؛
        # مسیر «منطقی» (مانیفست/بازیابی) همیشه مسیر اصلی است.
        return vss.map_path(p) if vss else p

    for src in sources:
        src_path = src["path"]
        arcroot = src["arcroot"]
        if os.path.isfile(src_path):
            # منبع فایل تکی (فقط از مسیرهای سفارشی ممکن است)
            try:
                sz = os.path.getsize(longpath(pth(src_path)))
                mt = int(os.path.getmtime(longpath(pth(src_path))))
            except OSError:
                warnings.append("فایل سفارشی قابل خواندن نیست: " + src_path)
                continue
            fname = os.path.basename(src_path)
            files.append({
                "path": os.path.abspath(src_path),
                "arcname": f"{arcroot}/{fname}",
                "size": sz, "mtime": mt, "archive": None, "status": "pending",
            })
            continue
        for root, _dirs, fnames in os.walk(
            pth(src_path), onerror=lambda e: warnings.append("خطای خواندن پوشه: " + str(e))
        ):
            # root ممکن است داخل Snapshot باشد؛ مسیر منطقی واقعی را بساز:
            # مسیر اصلیِ منبع + بخش نسبیِ زیر درخت منبع
            tail = os.path.relpath(root, pth(src_path))
            real_root = os.path.abspath(src_path) if tail == "." else os.path.join(os.path.abspath(src_path), tail)
            rel = os.path.relpath(root, pth(src_path))
            reld = "." if rel == "." else rel.replace(os.sep, "/")
            if reld != ".":
                dirs.append({"path": os.path.abspath(real_root), "arcname": f"{arcroot}/{reld}"})
            for fname in sorted(fnames):
                low = fname.lower()
                if fname in SKIP_FILENAMES or low.endswith(SKIP_SUFFIXES):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    sz = os.path.getsize(longpath(fpath))
                    mt = int(os.path.getmtime(longpath(fpath)))
                except OSError:
                    continue
                arc = f"{arcroot}/{reld}/{fname}" if reld != "." else f"{arcroot}/{fname}"
                files.append({
                    "path": os.path.join(os.path.abspath(real_root), fname),
                    "arcname": arc,
                    "size": sz, "mtime": mt, "archive": None, "status": "pending",
                })

    files.sort(key=lambda e: e["arcname"])
    dirs.sort(key=lambda e: e["arcname"])
    return sources, files, dirs


# ------------------------------------------------------------
#  آرشیو و مانیفست
# ------------------------------------------------------------

def split_big_file(path, size, out_dir, run_id_, rep=None):
    """فایل بزرگ‌تر از CHUNK_THRESHOLD را به تکه‌های خام CHUNK_SIZE می‌شکند.
    خروجی: (chunks, warnings) — chunks لیستی از (chunk_path, start, length)."""
    chunks = []
    try:
        with open(longpath(path), "rb") as f:
            idx = 0
            start = 0
            while start < size:
                length = min(CHUNK_SIZE, size - start)
                cname = f"{run_id_}_big{idx}of{n_chunks_of(size)}.c9chunk"
                cpath = os.path.join(out_dir, cname)
                remaining = length
                with open(longpath(cpath), "wb") as out:
                    f.seek(start)
                    while remaining > 0:
                        buf = f.read(min(1024 * 1024, remaining))
                        if not buf:
                            break
                        out.write(buf)
                        remaining -= len(buf)
                chunks.append((cpath, start, length))
                if rep:
                    rep(f"  تکه {idx}: {format_bytes(length)}")
                start += length
                idx += 1
    except OSError as ex:
        return chunks, str(ex)
    return chunks, None


def n_chunks_of(size):
    return max(1, (size + CHUNK_SIZE - 1) // CHUNK_SIZE)


def assign_parts(entries_files, target):
    parts = []
    cur, cur_size = [], 0
    for e in entries_files:
        sz = e["size"]
        if cur and cur_size + sz > target:
            parts.append(cur)
            cur, cur_size = [], 0
        cur.append(e)
        cur_size += sz
    if cur or not parts:
        parts.append(cur)
    return parts


def write_archives(run_id_, parts, dir_entries, out_dir, warnings, logger, rep=None, vss=None):
    created = []
    for i, part in enumerate(parts, 1):
        name = f"{run_id_}_part{i}.zip"
        path = os.path.join(out_dir, name)
        zipped, size = 0, 0
        with zipfile.ZipFile(longpath(path), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            if i == 1:
                for d in dir_entries:
                    z.writestr(d["arcname"] + "/", "")
            for e in part:
                e["archive"] = name
                try:
                    z.write(longpath(vss.map_path(e["path"]) if vss else e["path"]), e["arcname"])
                    e["status"] = "ok"
                    zipped += 1
                    size += e["size"]
                except OSError as ex:
                    e["status"] = "error"
                    e["error"] = str(ex)
                    warnings.append(f"فایل باز نشد (در حال استفاده؟): {e['path']} — {ex}")
        created.append((name, path, zipped, size))
        if rep:
            rep(f"آرشیو {name}: {zipped} فایل")
        logger.line(f"آرشیو {name}: {zipped} فایل")
    return created


def build_log_text(run_id_, sources, files, dirs, warnings, parts, chunk_artifacts=None):
    total = sum(e["size"] for e in files if e.get("status", "ok") == "ok")
    lines = []
    lines.append("=== لاگ بکاپ Claude و 9router ===")
    lines.append(f"تاریخ: {now_iso()}")
    lines.append(f"شناسه بکاپ: {run_id_}")
    lines.append(f"میزبان: {hostname()} | کاربر: {os.environ.get('USERNAME', '?')}")
    lines.append("")
    lines.append("منابع:")
    for s in sources:
        lines.append(f"  [{s['label']}] {s['path']}")
    lines.append("")
    lines.append(f"فایل‌ها: {len(files)} (جمع {format_bytes(total)})")
    lines.append(f"پوشه‌ها: {len(dirs)}")
    lines.append(f"بخش‌های آرشیو: {len(parts)}")
    lines.append("")
    for i, part in enumerate(parts, 1):
        lines.append(f"  بخش {i}: {len(part)} فایل")
    lines.append("")
    if chunk_artifacts:
        lines.append(f"تکه‌های فایل‌های بزرگ: {len(chunk_artifacts)}")
        for name, _p, sz in chunk_artifacts:
            lines.append(f"  {name} ({format_bytes(sz)})")
        lines.append("")
    if warnings:
        lines.append("هشدارها:")
        for w in warnings:
            lines.append("  - " + w)
        lines.append("")
    lines.append("=== پایان لاگ ===")
    return "\n".join(lines) + "\n"


def build_manifest(run_id_, sources, files, dirs, warnings, parts):
    return {
        "schema_version": 1,
        "tool": f"{APP_NAME} v{APP_VERSION}",
        "run_id": run_id_,
        "created_at": now_iso(),
        "hostname": hostname(),
        "user": os.environ.get("USERNAME", "?"),
        "sources": sources,
        "warnings": warnings,
        "files": files,
        "dirs": dirs,
        "total_files": len(files),
        "total_size": sum(e["size"] for e in files),
        "parts": len(parts),
    }


# ------------------------------------------------------------
#  ارتباط با Google Drive (حساب سرویس + REST ساده)
#  هر جزء بکاپ (زیپ/تکه/مانیفست/لاگ) فایلی در پوشهٔ مقصد است؛
#  شاخص بکاپ (backup_index.json) نگاشت نام جزء به شناسه فایل Drive را نگه می‌دارد.
# ------------------------------------------------------------

# ------------------------------------------------------------
#  احراز هویت Google Drive با OAuth2 حساب خود کاربر — پایتون خالص
#  (حساب سرویس سهمیهٔ فضای ندارد؛ با OAuth فایل‌ها مالکیت حساب خود کاربر
#   هستند و از ۱۵GB او کم می‌شوند. scope محدود drive.file: برنامه فقط به
#   فایل‌هایی که خودش می‌سازد دسترسی دارد — بدون نیاز به تأییدیهٔ گوگل)
#  Refresh Token با DPAPI رمزنگاری و ذخیره می‌شود؛ منقضی نمی‌شود.
# ------------------------------------------------------------

_GD_AUTH = {"client_json": "", "refresh_token": "", "token": "", "exp": 0.0}
_GD_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GD_OAUTH_SCOPE = "https://www.googleapis.com/auth/drive.file"

# اعتبارنامهٔ جاسازی‌شده (خواستهٔ کاربر: صفر لاگین روی هر ویندوز جدید).
# با DPAPI محدود به یک ماشین نیست؛ چون ریپو خصوصی است و scope فقط drive.file
# (فایل‌های ساختهٔ خود برنامه) است، این مبادله برای ابزار شخصی قابل قبول است.
# اگر کاربر روزی دسترسی برنامه را در گوگل لغو کند، این مقادیر بی‌اثر می‌شوند و
# باید دوباره از دیالوگ «اتصال گوگل درایو» وارد شود. فایل gd_embedded.json در
# سمت بیلد کنار سورس قرار می‌گیرد، داخل EXE جاسازی می‌شود و در ریپو کامیت نمی‌شود.
_EMBEDDED_CLIENT_JSON = ""
_EMBEDDED_REFRESH_TOKEN = ""
_EMBEDDED_FOLDER_ID = ""

try:
    _emb_path = os.path.join(resource_dir(), "gd_embedded.json")
    if os.path.isfile(_emb_path):
        with open(_emb_path, "r", encoding="utf-8") as _f:
            _emb = json.load(_f)
        _EMBEDDED_CLIENT_JSON = _emb.get("client_json", "")
        _EMBEDDED_REFRESH_TOKEN = _emb.get("refresh_token", "")
        _EMBEDDED_FOLDER_ID = _emb.get("folder_id", "")
except Exception:
    pass


def gd_get_folder_id():
    """شناسه پوشهٔ مقصد: از تنظیمات محلی؛ نبود → مقدار جاسازی‌شده."""
    return load_settings().get("gd_folder_id") or (_EMBEDDED_FOLDER_ID or None)


def gd_set_credentials(client_json, refresh_token):
    """ثبت اعتبارنامه در حافظه (بعد از خواندن از secrets.dat)."""
    _GD_AUTH.update({
        "client_json": client_json or "",
        "refresh_token": refresh_token or "",
        "token": "", "exp": 0.0,
    })


def _gd_client_info(client_json):
    """خواندن فیلدهای کلاینت؛ ساختار {installed: {...}} یا مسطح هر دو پذیرفته می‌شود."""
    client = json.loads(client_json)
    if "installed" in client and isinstance(client["installed"], dict):
        client = client["installed"]
    return client


def gd_get_access_token():
    """دریافت access token با Refresh Token (خودکار و بی‌صدا)."""
    now = time.time()
    if _GD_AUTH["token"] and now < _GD_AUTH["exp"] - 60:
        return _GD_AUTH["token"]
    if not _GD_AUTH["client_json"] or not _GD_AUTH["refresh_token"]:
        raise RuntimeError("اعتبارنامه گوگل تنظیم نشده است")
    client = _gd_client_info(_GD_AUTH["client_json"])
    data = urllib.parse.urlencode({
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "refresh_token": _GD_AUTH["refresh_token"],
        "grant_type": "refresh_token",
    }).encode("utf-8")
    req = urllib.request.Request(GD_TOKEN_URL, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode("utf-8"))
    if "access_token" not in res:
        raise RuntimeError("دریافت توکن گوگل ناموفق: " + str(res)[:200])
    _GD_AUTH["token"] = res["access_token"]
    _GD_AUTH["exp"] = now + float(res.get("expires_in", 3600))
    return _GD_AUTH["token"]


def gd_authorize_interactive(client_json, rep=None):
    """جریان OAuth مرورگری: سرور محلی موقت + باز شدن مرورگر برای تأیید کاربر.
    Refresh Token را برمی‌گرداند (و اعتبارنامه را در حافظه ثبت می‌کند)."""
    client = _gd_client_info(client_json)

    class _Handler(BaseHTTPRequestHandler):
        code = None
        error = None

        def do_GET(self):
            q = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(q.query)
            if "code" in params:
                _Handler.code = params["code"][0]
                body = ("<html><body dir=rtl style='font-family:sans-serif;text-align:center;padding-top:60px'>"
                        "<h2>✓ تأیید شد</h2><p>می‌توانی این تب را ببندی و به برنامه برگردی.</p></body></html>").encode("utf-8")
            else:
                _Handler.error = params.get("error", ["unknown"])[0]
                body = ("<html><body dir=rtl style='font-family:sans-serif;text-align:center;padding-top:60px'>"
                        "<h2>✗ تأیید نشد</h2></body></html>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.timeout = 300
    port = srv.server_address[1]
    # کلاینت از نوع Desktop app: گوگل هر پورت loopback را می‌پذیرد (URI ثبت‌شده لازم نیست)
    redirect_uri = "http://127.0.0.1:" + str(port)
    state = uuid.uuid4().hex
    auth_url = (_GD_OAUTH_AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _GD_OAUTH_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }))
    if rep:
        rep("مرورگر برای تأیید دسترسی باز می‌شود...")
    webbrowser.open(auth_url)

    deadline = time.time() + 300
    while _Handler.code is None and _Handler.error is None and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if _Handler.error or not _Handler.code:
        raise RuntimeError("تأیید گوگل انجام نشد: " + str(_Handler.error or "مهلت تمام شد"))

    data = urllib.parse.urlencode({
        "code": _Handler.code,
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    req = urllib.request.Request(GD_TOKEN_URL, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode("utf-8"))
    refresh = res.get("refresh_token", "")
    if not refresh:
        raise RuntimeError("Refresh Token دریافت نشد؛ دوباره تلاش کن (گوگل هر بار با prompt=consent آن را می‌دهد).")
    gd_set_credentials(client_json, refresh)
    return refresh


def gd_request(method, url, params=None, data=None, headers=None, timeout=120):
    """فراخوانی REST به Drive با توکن حساب کاربر (از حافظه)."""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    tok = gd_get_access_token()
    h = {"Authorization": "Bearer " + tok, "User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return json.loads(body.decode("utf-8")) if body else {}


def gd_test_connection(client_json=None, refresh_token=None):
    """آزمون اتصال: Refresh Token در حافظه ثبت و دربارهٔ حساب پرسیده می‌شود."""
    try:
        if client_json and refresh_token:
            gd_set_credentials(client_json, refresh_token)
        tok = gd_get_access_token()
        req = urllib.request.Request(
            GD_DRIVE_API + "/about?fields=user",
            headers={"Authorization": "Bearer " + tok, "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            about = json.loads(r.read().decode("utf-8"))
        email = about.get("user", {}).get("emailAddress", "?")
        return True, "✓ به حساب Google «" + email + "» وصل است"
    except urllib.error.HTTPError as ex:
        try:
            detail = json.loads(ex.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            detail = str(ex)
        return False, "خطا: " + (detail or str(ex))
    except Exception as ex:
        return False, "خطا: " + str(ex)


def gd_upload_file(folder_id, path, timeout=1800):
    """آپلود فایل به پوشهٔ مقصد (multipart ساده؛ برای اجزای ≤۹۸MB کافی است).
    (file_id, name, size) برمی‌گرداند."""
    size = os.path.getsize(longpath(path))
    metadata = {"name": os.path.basename(path), "parents": [folder_id]}
    boundary = "----c9rbnd" + uuid.uuid4().hex
    meta_part = ("--" + boundary + "\r\n"
                 "Content-Type: application/json; charset=UTF-8\r\n\r\n"
                 + json.dumps(metadata) + "\r\n"
                 "--" + boundary + "\r\n"
                 "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
    tail = ("\r\n--" + boundary + "--\r\n").encode("utf-8")
    with open(longpath(path), "rb") as f:
        body = meta_part + f.read() + tail
    url = GD_UPLOAD_API + "/files?uploadType=multipart&fields=id,name,size"
    tok = gd_get_access_token()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer " + tok,
        "Content-Type": "multipart/related; boundary=" + boundary,
        "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read().decode("utf-8"))
    return res["id"], res.get("name", ""), size


def gd_download_file(file_id, dest, timeout=1800):
    """دانلود فایل Drive با alt=media و ذخیرهٔ استریمی."""
    tok = gd_get_access_token()
    url = GD_DRIVE_API + "/files/" + urllib.parse.quote(file_id, safe="") + "?alt=media"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok, "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(longpath(dest), "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 256)


def gd_delete_file(file_id):
    """حذف فایل از Drive (idempotent — خطای 404 نادیده گرفته می‌شود)."""
    tok = gd_get_access_token()
    url = GD_DRIVE_API + "/files/" + urllib.parse.quote(file_id, safe="")
    req = urllib.request.Request(url, method="DELETE", headers={"Authorization": "Bearer " + tok, "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()
    except urllib.error.HTTPError as ex:
        if ex.code != 404:
            raise


def gd_list_folder(folder_id):
    """فهرست فایل‌های پوشهٔ مقصد؛ لیستی از {id, name, size}."""
    items = []
    page_token = None
    while True:
        params = {"q": "'" + folder_id + "' in parents and trashed = false",
                  "fields": "nextPageToken, files(id,name,size)",
                  "pageSize": "1000"}
        if page_token:
            params["pageToken"] = page_token
        res = gd_request("GET", GD_DRIVE_API + "/files", params=params)
        for it in res.get("files", []):
            try:
                sz = int(it.get("size") or 0)
            except (TypeError, ValueError):
                sz = 0
            items.append({"id": it["id"], "name": it.get("name", ""), "size": sz})
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return items


# ------------------------------------------------------------
#  وضعیت Drive (gd_state.json — شاخص آخرین بکاپ برای حذف/بازیابی)
# ------------------------------------------------------------

def gd_state_path():
    return os.path.join(localappdata(), APP_NAME, "gd_state.json")


def load_gd_state():
    try:
        with open(longpath(gd_state_path()), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_gd_state(state):
    try:
        os.makedirs(os.path.dirname(longpath(gd_state_path())), exist_ok=True)
        with open(longpath(gd_state_path()), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def build_backup_index(run_id_, folder_id, files_map, total_files, total_size):
    """شاخص بکاپ: نگاشت نام جزء به شناسه فایل Drive (برای بازیابی و حذف بکاپ قبلی)."""
    return {
        "schema_version": 1,
        "tool": APP_NAME + " v" + APP_VERSION,
        "run_id": run_id_,
        "created_at": now_iso(),
        "folder_id": folder_id,
        "files": files_map,   # {نام جزء: {file_id, size}}
        "total_files": total_files,
        "total_size": total_size,
    }


def collect_backup_file_ids(last_index):
    """همه شناسه فایل‌های Drive یک بکاپ را برمی‌گرداند."""
    ids = []
    for entry in (last_index.get("files") or {}).values():
        fid = entry.get("file_id")
        if fid:
            ids.append(fid)
    return ids


def extract_zip_safe(path, dest_dir):
    dest_dir = os.path.abspath(dest_dir)
    with zipfile.ZipFile(longpath(path)) as z:
        for info in z.infolist():
            target = os.path.join(dest_dir, info.filename)
            t_abs = os.path.abspath(target)
            if os.path.normcase(t_abs) != os.path.normcase(dest_dir) and \
               not os.path.normcase(t_abs).startswith(os.path.normcase(dest_dir) + os.sep):
                raise RuntimeError("مسیر نامعتبر در آرشیو: " + info.filename)
            if info.is_dir():
                os.makedirs(longpath(t_abs), exist_ok=True)
                continue
            os.makedirs(longpath(os.path.dirname(t_abs)), exist_ok=True)
            with z.open(info) as src, open(longpath(t_abs), "wb") as dst:
                shutil.copyfileobj(src, dst)
            ts = time.mktime(info.date_time + (0, 0, -1))
            try:
                os.utime(longpath(t_abs), (ts, ts))
            except OSError:
                pass


# ------------------------------------------------------------
#  بکاپ
# ------------------------------------------------------------

def run_backup(upload=True, out_dir=None, rep=None):
    if os.environ.get("C9R_SELFTEST"):
        upload = False

    if rep:
        rep("آماده‌سازی بکاپ...")
    run_id_ = "backup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    if not out_dir:
        out_dir = os.path.join(localappdata(), APP_NAME, "backups", run_id_)
    os.makedirs(out_dir, exist_ok=True)

    logger = Logger("backup", run_id_)
    warnings = []

    # Snapshot (VSS) برای خواندن فایل‌های بازِ در حال استفاده — در پایان حذف می‌شود
    vss = VssSnapshot(rep=rep)
    drives = sorted({os.path.splitdrive(p)[0].upper() for p in (
        os.path.join(localappdata(), "Claude-3p"),
        os.path.join(appdata(), "9router"),
        os.path.join(userprofile(), ".claude"),
    ) + tuple(load_custom_paths()) if os.path.splitdrive(p)[0]})
    if not vss.start(drives):
        vss = None
    sources, files, dirs = build_backup_set(warnings, vss=vss)

    if not sources:
        if rep:
            rep("هیچ منبعی برای بکاپ پیدا نشد؛ عملیات لغو شد.")
        if vss:
            vss.delete()
        logger.close()
        return 1

    # فایل‌های بزرگ‌تر از CHUNK_THRESHOLD به تکه‌های خام شکسته می‌شوند (نه زیپ)
    big_entries = [e for e in files if e["size"] > CHUNK_THRESHOLD]
    small_entries = [e for e in files if e["size"] <= CHUNK_THRESHOLD]
    chunk_artifacts = []  # (name, path, size)
    for e in big_entries:
        n = n_chunks_of(e["size"])
        if rep:
            rep(f"فایل بزرگ: {e['path']} ({format_bytes(e['size'])}) → {n} تکه")
        chunks, err = split_big_file(vss.map_path(e["path"]) if vss else e["path"], e["size"], out_dir, run_id_, rep=rep)
        if err or len(chunks) != n:
            e["status"] = "error"
            e["error"] = err or "شکستن ناقص فایل بزرگ"
            warnings.append(f"شکستن فایل بزرگ ناموفق: {e['path']} — {err or 'ناقص'}")
            continue
        e["chunked"] = True
        e["chunk_size"] = CHUNK_SIZE
        e["chunks"] = [{"key": os.path.basename(cp), "start": st, "length": ln} for cp, st, ln in chunks]
        for cp, _st, ln in chunks:
            chunk_artifacts.append((os.path.basename(cp), cp, ln))

    parts = assign_parts(small_entries, PART_TARGET)
    if rep:
        rep(f"فشرده‌سازی {len(files)} فایل در {len(parts)} بخش...")
    archives = write_archives(run_id_, parts, dirs, out_dir, warnings, logger, rep=rep, vss=vss)

    log_text = build_log_text(run_id_, sources, files, dirs, warnings, parts, chunk_artifacts=chunk_artifacts)
    manifest = build_manifest(run_id_, sources, files, dirs, warnings, parts)

    manifest_path = os.path.join(out_dir, run_id_ + "_manifest.json")
    log_path = os.path.join(out_dir, run_id_ + "_log.txt")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(log_text)

    try:
        with zipfile.ZipFile(longpath(archives[0][1]), "a", zipfile.ZIP_DEFLATED) as z:
            # خلاصهٔ مانیفست (بدون فهرست کامل فایل‌ها) تا زیپ بخش اول از سقف دانلود ربات (۲۰MB) رد نشود؛
            # مانیفست کامل به‌صورت Document جداگانه ارسال می‌شود
            summary = {k: v for k, v in manifest.items() if k not in ("files", "dirs")}
            summary["files_count"] = len(files)
            summary["dirs_count"] = len(dirs)
            z.writestr("backup_manifest_summary.json", json.dumps(summary, ensure_ascii=False, indent=1).encode("utf-8"))
            z.writestr("backup.log", log_text.encode("utf-8"))
    except Exception as ex:
        warnings.append("افزودن لاگ به بخش اول ناموفق: " + str(ex))

    total = sum(e["size"] for e in files)
    if rep:
        rep(f"بکاپ محلی: {len(files)} فایل، {format_bytes(total)}")
        for name, path, cnt, sz in archives:
            rep(f"  آرشیو {name}: {cnt} فایل، {format_bytes(sz)}")
        rep(f"مسیر: {out_dir}")
        for w in warnings:
            rep("هشدار: " + w)

    if not upload:
        if rep:
            rep("آپلود غیرفعال است (حالت محلی).")
        if vss:
            vss.delete()
        logger.close()
        return 0

    # --- اعتبارسنجی بکاپ محلی (پیش از هر تماس با Drive) ---
    uploads = [(name, path, sz) for (name, path, _cnt, sz) in archives] + list(chunk_artifacts)
    verify = list(uploads) + [
        (run_id_ + "_manifest.json", manifest_path, 0),
        (run_id_ + "_log.txt", log_path, 0),
    ]
    missing = [vname for vname, vpath, _sz in verify if not os.path.isfile(longpath(vpath))]
    if missing:
        if rep:
            for mname in missing:
                rep("  ✗ جزء محلی یافت نشد: " + mname)
            rep("بکاپ محلی ناقص است؛ بکاپ قبلی Drive دست‌نخورده ماند.")
        if vss:
            vss.delete()
        logger.close()
        return 1
    if rep:
        rep("✓ بکاپ محلی کامل و سالم است (" + str(len(verify)) + " جزء)؛ آماده آپلود به Google Drive.")

    # --- اعتبارنامه Google Drive ---
    ready, _secrets = gd_credentials_ready()
    if not ready:
        if rep:
            rep("✗ اتصال Google Drive تنظیم نشده است؛ ابتدا از دکمه «اتصال گوگل درایو» حساب خودت را وصل کن.")
        if vss:
            vss.delete()
        logger.close()
        return 1
    folder_id = gd_get_folder_id()

    if rep:
        rep("✓ اعتبارنامه Google Drive آماده است.")

    # --- آپلود اجزای بکاپ به Drive (بکاپ قبلی هنوز دست‌نخورده) ---
    if rep:
        rep("آپلود بکاپ جدید به Google Drive...")
    all_ok = True
    files_map = {}  # نام جزء → {file_id, size}
    uploaded_ids = []
    for name, path, sz in uploads:
        ok_part = False
        for attempt in range(1, 4):
            try:
                if rep:
                    rep("  آپلود " + name + " (" + format_bytes(sz) + ") — تلاش " + str(attempt))
                fid, _fn, _fs = gd_upload_file(folder_id, path)
                files_map[name] = {"file_id": fid, "size": sz}
                uploaded_ids.append(fid)
                if rep:
                    rep("  ✓ " + name)
                ok_part = True
                break
            except Exception as ex:
                if rep:
                    rep("  خطا: " + str(ex))
                if attempt < 3:
                    time.sleep(2 * attempt)
        if not ok_part:
            if rep:
                rep("  ✗ آپلود " + name + " ناموفق بود")
            all_ok = False

    # --- آپلود مانیفست و لاگ (بازیابی به مانیفست نیاز دارد) ---
    for key, path in ((run_id_ + "_manifest.json", manifest_path),
                      (run_id_ + "_log.txt", log_path)):
        if not all_ok:
            break
        try:
            fid, _fn, _fs = gd_upload_file(folder_id, path)
            files_map[key] = {"file_id": fid, "size": os.path.getsize(longpath(path))}
            uploaded_ids.append(fid)
            if rep:
                rep("  ✓ " + key)
        except Exception as ex:
            if rep:
                rep("  ✗ آپلود " + key + " ناموفق: " + str(ex))
            all_ok = False

    if not all_ok:
        if rep:
            rep("برخی اجزا آپلود نشدند؛ شاخص ساخته نمی‌شود تا بکاپ ناقص بازیابی نشود و بکاپ قبلی حفظ می‌ماند.")
        # پاک‌سازی اجزای نیمه‌کاره از پوشه (تا شلوغ نشود)
        if uploaded_ids:
            cleaned = 0
            for fid in uploaded_ids:
                try:
                    gd_delete_file(fid)
                    cleaned += 1
                except Exception:
                    pass
            if rep:
                rep("  " + str(cleaned) + " جزء نیمه‌کاره از Drive پاک شد.")
        if vss:
            vss.delete()
        logger.close()
        return 1

    # --- آپلود شاخص بکاپ (آخرین گام؛ فقط پس از موفقیت همه اجزا) ---
    index = build_backup_index(run_id_, folder_id, files_map,
                               sum(1 for e in files if e.get("status", "ok") == "ok"),
                               sum(e["size"] for e in files))
    index_path = os.path.join(out_dir, run_id_ + "_index.json")
    try:
        with open(longpath(index_path), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=1)
    except OSError as ex:
        if rep:
            rep("✗ نوشتن شاخص ناموفق بود: " + str(ex))
        if vss:
            vss.delete()
        logger.close()
        return 1

    try:
        fid_index, _fn, _fs = gd_upload_file(folder_id, index_path)
    except Exception as ex:
        if rep:
            rep("  ✗ آپلود شاخص ناموفق: " + str(ex))
        for fid in uploaded_ids:
            try:
                gd_delete_file(fid)
            except Exception:
                pass
        if rep:
            rep("  اجزای نیمه‌کاره پاک شدند.")
        if vss:
            vss.delete()
        logger.close()
        return 1

    if rep:
        rep("  ✓ شاخص بکاپ آپلود شد")
    files_map[run_id_ + "_index.json"] = {"file_id": fid_index, "size": os.path.getsize(longpath(index_path))}

    # --- ثبت وضعیت محلی (فقط پس از موفقیت کامل) ---
    # سابقهٔ بکاپ‌ها نگه داشته می‌شود (جدید → قدیم) تا نگهداشت ۲ نسخهٔ آخر ممکن باشد
    prev_state = load_gd_state()
    history = list(prev_state.get("history") or []) if isinstance(prev_state, dict) else []
    prev_index = history[0] if history and isinstance(history[0], dict) else None
    if prev_index is None and isinstance(prev_state, dict) and isinstance(prev_state.get("last_index"), dict):
        prev_index = prev_state["last_index"]  # سازگاری با state نسخه‌های قبل
    history.insert(0, {
        "run_id": run_id_,
        "index_file_id": fid_index,
        "created_at": index["created_at"],
        "files": files_map,
        "total_files": index["total_files"],
        "total_size": index["total_size"],
    })
    save_gd_state({"history": history})

    # --- حذف بکاپ‌های قدیمی‌تر از ۲ نسخهٔ آخر (مرور خود پوشهٔ Drive) ---
    # با مرور پوشه، پسماندهای ناقص نسخه‌های قبل هم خودکار پاک می‌شوند.
    keep = 2
    try:
        items = gd_list_folder(folder_id)
        runs = {}
        for it in items:
            mm = re.match(r"^(backup_\d{8}_\d{6})_", it["name"])
            if mm:
                runs.setdefault(mm.group(1), []).append(it["id"])
        kept_runs = set(sorted(runs.keys(), reverse=True)[:keep])
        old_items = []
        for rid, ids in runs.items():
            if rid not in kept_runs:
                old_items.extend(ids)
        if old_items:
            if rep:
                rep("حذف بکاپ‌های قدیمی‌تر از " + str(keep) + " نسخهٔ اخیر (" + str(len(old_items)) + " فایل)...")
            removed_total = 0
            for oid in old_items:
                try:
                    gd_delete_file(oid)
                    removed_total += 1
                except Exception as ex:
                    if rep:
                        rep("  حذف " + str(oid)[:16] + "… ناموفق: " + str(ex)[:100])
            if rep:
                rep("  🗑 " + str(removed_total) + " فایل قدیمی حذف شد؛ " + str(keep) + " نسخهٔ اخیر باقی ماند.")
    except Exception as ex:
        if rep:
            rep("  ⚠ مرور پوشه برای حذف بکاپ‌های قدیمی ناموفق بود (در بکاپ بعدی دوباره تلاش می‌شود): " + str(ex)[:120])
    if rep:
        rep("بکاپ کامل شد و به Google Drive آپلود گردید (۲ نسخهٔ اخیر در پوشه می‌مانند).")
    if vss:
        vss.delete()
    logger.close()
    return 0 if all_ok else 1


# ------------------------------------------------------------
#  اجرای دستورها و نصب‌کننده در cmd (با انتظار برای پایان)
# ------------------------------------------------------------

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_cmd_wait(cmdtext, rep=None, timeout=None):
    """دستور را در cmd اجرا و خروجی آن را خط‌به‌خط گزارش می‌کند؛ منتظر پایان می‌ماند."""
    proc = subprocess.Popen(
        ["cmd", "/c", cmdtext],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    lines = []
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.rstrip("\n")
        if line:
            lines.append(line)
            if rep:
                rep("    " + line)
    proc.wait(timeout=timeout)
    return proc.returncode, lines


def setup_exe_path():
    """مسیر محلی نصب‌کننده جاسازی‌شده (کپی از باندل به %LOCALAPPDATA%)."""
    bundled = asset_path("Claude Setup.exe")
    if not os.path.isfile(bundled):
        return None
    base = os.path.join(localappdata(), APP_NAME)
    os.makedirs(base, exist_ok=True)
    target = os.path.join(base, "Claude Setup.exe")
    try:
        if not os.path.isfile(target) or sha256_file(target) != sha256_file(bundled):
            shutil.copyfile(bundled, target)
    except Exception:
        return bundled
    return target


def run_installer(rep=None):
    """نصب‌کننده Claude Setup.exe را اجرا و منتظر پایان آن می‌ماند."""
    exe = setup_exe_path()
    if not exe:
        if rep:
            rep("نصب‌کننده جاسازی‌شده (Claude Setup.exe) یافت نشد؛ رد شد.")
        return 1
    if rep:
        rep("اجرای نصب‌کننده Claude Setup.exe...")
    try:
        proc = subprocess.Popen([exe])
        proc.wait()
        if rep:
            rep("نصب‌کننده به پایان رسید.")
        return proc.returncode or 0
    except Exception as ex:
        if rep:
            rep("خطا در اجرای نصب‌کننده: " + str(ex))
        return 1


def run_post_restore(rep=None):
    """پس از بازگرداندن فایل‌ها: installer ← npm i -g npm ← npm i -g 9router."""
    if os.environ.get("C9R_SELFTEST"):
        if rep:
            rep("(حالت خودآزمایی: گام‌های نصب اجرا نمی‌شوند)")
        return 0
    rc1 = run_installer(rep=rep)
    if rep:
        rep("اجرای 'npm i -g npm' ...")
    rc2, _lines2 = run_cmd_wait("npm i -g npm", rep=rep)
    if rc2 != 0 and rep:
        rep("  ⚠ 'npm i -g npm' با کد " + str(rc2) + " پایان یافت.")
    if rep:
        rep("اجرای 'npm i -g 9router' ...")
    rc3, _lines3 = run_cmd_wait("npm i -g 9router", rep=rep)
    if rc3 != 0 and rep:
        rep("  ⚠ 'npm i -g 9router' با کد " + str(rc3) + " پایان یافت.")
    return 0 if (rc1 == 0 and rc2 == 0 and rc3 == 0) else 1


# ------------------------------------------------------------
#  بازیابی
# ------------------------------------------------------------

def run_restore(parts_dir=None, rep=None):
    if rep:
        rep("شروع بازیابی...")
    staging = os.path.join(tempfile.gettempdir(), "c9r_restore_" + uuid.uuid4().hex[:8])
    os.makedirs(staging, exist_ok=True)
    logger = Logger("restore", datetime.now().strftime("%Y%m%d_%H%M%S"))

    manifest = None
    run_id_ = None

    if parts_dir:
        cands = sorted(glob.glob(os.path.join(parts_dir, "*_manifest.json")))
        if not cands:
            if rep:
                rep(f"هیچ مانیفست بکاپی در {parts_dir} پیدا نشد.")
            logger.close()
            return 1
        with open(cands[-1], "r", encoding="utf-8") as f:
            manifest = json.load(f)
        run_id_ = manifest.get("run_id", "?")
        dl_dir = parts_dir
        if rep:
            rep(f"حالت محلی: بکاپ {run_id_} از {parts_dir} بازیابی می‌شود.")
    else:
        ready, _secrets = gd_credentials_ready()
        if not ready:
            if rep:
                rep("✗ اتصال Google Drive تنظیم نشده است؛ ابتدا از دکمه «اتصال گوگل درایو» حساب خودت را وصل کن.")
            logger.close()
            return 1
        folder_id = gd_get_folder_id()

        # شاخص بکاپ از خود Drive خوانده می‌شود (نه از فایل محلی) تا بازیابی
        # روی ویندوز دیگر هم بدون هیچ تنظیم اضافه‌ای کار کند.
        if rep:
            rep("خواندن شاخص آخرین بکاپ از Google Drive...")
        last_index = None
        try:
            items = gd_list_folder(folder_id)
            index_items = sorted(
                (it for it in items if it["name"].endswith("_index.json")),
                key=lambda it: it["name"], reverse=True)
            for it in index_items:
                try:
                    ipath = os.path.join(staging, "idx_" + it["name"])
                    gd_download_file(it["id"], ipath)
                    with open(ipath, "r", encoding="utf-8") as f:
                        cand = json.load(f)
                    os.remove(ipath)
                    if isinstance(cand, dict) and cand.get("run_id") and \
                            cand.get("folder_id") == folder_id and isinstance(cand.get("files"), dict):
                        if last_index is None or cand["run_id"] > last_index["run_id"]:
                            last_index = cand
                except Exception:
                    continue
        except Exception as ex:
            if rep:
                rep("خطا در خواندن پوشهٔ Drive: " + str(ex))
        if not last_index:
            if rep:
                rep("هیچ بکاپ کاملی در پوشهٔ Drive پیدا نشد (شاخص معتبر یافت نشد).")
            logger.close()
            return 1
        # شاخص محلی هم به‌روز می‌شود (برای حذف بکاپ قبلی در بکاپ بعدی همین ماشین)
        save_gd_state({"last_index": {
            "run_id": last_index["run_id"],
            "index_file_id": last_index.get("files", {}).get(last_index["run_id"] + "_index.json", {}).get("file_id", ""),
            "created_at": last_index.get("created_at", ""),
            "files": last_index["files"],
            "total_files": last_index.get("total_files", 0),
            "total_size": last_index.get("total_size", 0),
        }})
        run_id_ = last_index["run_id"]
        if rep:
            rep("جدیدترین بکاپ: " + run_id_)

        dl_dir = os.path.join(staging, "parts")
        os.makedirs(dl_dir, exist_ok=True)

        # مانیفست: دانلود با شناسه فایل ثبت‌شده در شاخص
        m_entry = (last_index.get("files") or {}).get(run_id_ + "_manifest.json")
        if not m_entry or not m_entry.get("file_id"):
            if rep:
                rep("شناسه فایل مانیفست در شاخص یافت نشد؛ شاخص ناقص است.")
            logger.close()
            return 1
        try:
            mpath = os.path.join(dl_dir, run_id_ + "_manifest.json")
            gd_download_file(m_entry["file_id"], mpath)
            with open(mpath, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as ex:
            if rep:
                rep("خطا در دانلود مانیفست: " + str(ex))
            logger.close()
            return 1

    if not isinstance(manifest.get("files"), list):
        if rep:
            rep("مانیفست نامعتبر است.")
        logger.close()
        return 1

    if manifest.get("user") and manifest["user"] != os.environ.get("USERNAME"):
        if rep:
            rep(f"این بکاپ متعلق به کاربر «{manifest['user']}» است ولی اکنون «{os.environ.get('USERNAME')}» هستید؛ مسیرهای مطلق بازیابی می‌شوند.")

    part_keys = {
        e["archive"] for e in manifest["files"]
        if e.get("archive") and e.get("status", "ok") == "ok"
    }
    for e in manifest["files"]:
        if e.get("chunked") and isinstance(e.get("chunks"), list):
            part_keys.update(str(c.get("key", "")) for c in e["chunks"])
    part_keys.discard("")
    part_keys = sorted(part_keys)
    if not part_keys:
        if rep:
            rep("بکاپ هیچ فایلی ندارد (خالی).")
        logger.close()
        return 1

    if not parts_dir:
        for pk in part_keys:
            entry = (last_index.get("files") or {}).get(pk)
            if not entry or not entry.get("file_id"):
                if rep:
                    rep("  ✗ جزء " + pk + " در شاخص ثبت نشده؛ بکاپ ناقص است.")
                logger.close()
                return 1
            if rep:
                rep("دانلود " + pk + "...")
            try:
                gd_download_file(entry["file_id"], os.path.join(dl_dir, pk))
                if rep:
                    rep("  ✓ " + pk)
            except Exception as ex:
                if rep:
                    rep("  ✗ دانلود " + pk + " ناموفق: " + str(ex))
                logger.close()
                return 1

    extracted = os.path.join(staging, "extracted")
    for pk in part_keys:
        if pk.endswith(".c9chunk"):
            # تکه خام فایل بزرگ — استخراج نمی‌شود، در dl_dir می‌ماند
            continue
        if rep:
            rep(f"استخراج {pk}...")
        try:
            extract_zip_safe(os.path.join(dl_dir, pk), extracted)
        except Exception as ex:
            if rep:
                rep(f"استخراج {pk} ناموفق: {ex}")
            logger.close()
            return 1

    if rep:
        rep("بازگردانی فایل‌ها به مسیرهای اصلی...")
    results = {"files_ok": 0, "dirs_ok": 0, "failed": [], "skipped": []}

    for d in manifest.get("dirs", []):
        try:
            os.makedirs(longpath(d["path"]), exist_ok=True)
            results["dirs_ok"] += 1
        except OSError as ex:
            results["failed"].append((d["path"], str(ex)))

    for e in manifest["files"]:
        if e.get("status") == "error":
            results["skipped"].append((e["path"], "در بکاپ خطا داشت"))
            continue
        dst = e["path"]
        if e.get("chunked") and isinstance(e.get("chunks"), list):
            # فایل تکه‌شده: تکه‌ها را به‌ترتیب به هم بچسبان و بازگردان
            try:
                tmpf = os.path.join(staging, "chunk_" + uuid.uuid4().hex[:8])
                with open(longpath(tmpf), "wb") as out:
                    for c in sorted(e["chunks"], key=lambda c: c.get("start", 0)):
                        cp = os.path.join(dl_dir, str(c.get("key", "")))
                        if not os.path.isfile(longpath(cp)):
                            raise OSError("تکه یافت نشد: " + str(c.get("key", "?")))
                        with open(longpath(cp), "rb") as fh:
                            shutil.copyfileobj(fh, out)
                if os.path.getsize(longpath(tmpf)) != e["size"]:
                    raise OSError("اندازه فایل بازسازی‌شده از تکه‌ها تطابق ندارد")
                os.makedirs(longpath(os.path.dirname(dst)), exist_ok=True)
                shutil.copy2(longpath(tmpf), longpath(dst))
                try:
                    os.remove(longpath(tmpf))
                except OSError:
                    pass
                results["files_ok"] += 1
            except Exception as ex:
                results["failed"].append((dst, str(ex)))
                if rep:
                    rep(f"  ✗ {dst} — {ex}")
            continue
        src = os.path.join(extracted, e["arcname"])
        try:
            if not os.path.isfile(longpath(src)):
                raise OSError("فایل در آرشیو یافت نشد")
            os.makedirs(longpath(os.path.dirname(dst)), exist_ok=True)
            shutil.copy2(longpath(src), longpath(dst))
            if os.path.getsize(longpath(dst)) != e["size"]:
                raise OSError("اندازه فایل پس از کپی تطابق ندارد")
            results["files_ok"] += 1
        except Exception as ex:
            results["failed"].append((dst, str(ex)))
            if rep:
                rep(f"  ✗ {dst} — {ex}")

    if rep:
        rep(f"بازیابی فایل‌ها: {results['files_ok']} فایل + {results['dirs_ok']} پوشه بازسازی شد.")
        if results["skipped"]:
            rep(f"{len(results['skipped'])} فایل به دلیل خطا در بکاپ نادیده گرفته شد.")

    # گام‌های پس از بازگرداندن فایل‌ها
    if rep:
        rep("--- گام‌های پس از بازگردانی ---")
    b = run_post_restore(rep=rep)

    if not results["failed"] and b == 0:
        if rep:
            rep("بازگردانی با موفقیت انجام شد")
    else:
        if rep:
            rep(f"بازگردانی با {len(results['failed'])} خطا + کد گام نصب {b} پایان یافت. جزئیات: {logger.path}")
            for p, msg in results["failed"][:10]:
                rep(f"    {p} — {msg}")

    shutil.rmtree(staging, ignore_errors=True)
    logger.close()
    return 0 if (not results["failed"] and b == 0) else 1


# ------------------------------------------------------------
#  خودآزمایی (بدون شبکه)
# ------------------------------------------------------------

def _selftest_rep(msg):
    print(msg)


def selftest():
    print("=== خودآزمایی (بدون شبکه، در پوشه موقت) ===")
    sandbox = os.path.join(tempfile.gettempdir(), "c9r_selftest_" + uuid.uuid4().hex[:6])
    L = os.path.join(sandbox, "Local")
    R = os.path.join(sandbox, "Roaming")
    U = os.path.join(sandbox, "Profile")
    out = os.path.join(sandbox, "out")
    C = os.path.join(sandbox, "Custom")
    for d in (L, R, U, C, out):
        os.makedirs(d, exist_ok=True)

    expected = {}

    def mk(root, rel, data):
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if isinstance(data, bytes):
            with open(p, "wb") as f:
                f.write(data)
        else:
            with open(p, "w", encoding="utf-8") as f:
                f.write(data)
        return p

    def note(root, rel, data):
        p = mk(root, rel, data)
        expected[p] = sha256_file(p)
        return p

    note(L, "Claude-3p/settings.json", '{"theme":"dark"}\n')
    note(L, "Claude-3p/data/session.db", b"S" * 2048 + b"\x00\xff")
    mk(L, "Claude-3p/app.lock", b"LOCK")           # باید نادیده گرفته شود
    os.makedirs(os.path.join(L, "Claude-3p", "empty_dir"))  # باید بازسازی شود
    note(R, "9router/package.json", '{"name":"9router"}\n')
    note(R, "9router/index.js", "module.exports = {};\n")
    note(U, ".claude/settings.json", '{"model":"claude-fable-5"}\n')
    note(U, ".claude/projects/demo/history.jsonl", '{"role":"user"}\n')
    mk(U, ".claude/some.tmp", b"TMP")             # باید نادیده گرفته شود
    # فایل بزرگ مصنوعی (تست شکستن به تکه) — ۷۰ مگابایت الگودار
    big_size = 70 * 1024 * 1024
    big_hash = None
    try:
        import hashlib as _h
        h = _h.sha256()
        with open(os.path.join(sandbox, "Custom", "big_file.bin"), "wb") as f:
            written = 0
            block = bytes(range(256)) * (64 * 1024)   # 16MB
            while written < big_size:
                n = min(len(block), big_size - written)
                f.write(block[:n])
                h.update(block[:n])
                written += n
        big_hash = h.hexdigest()
    except OSError:
        pass

    os.environ["C9R_SELFTEST"] = "1"
    old_L, old_R = os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")
    old_U = os.environ.get("USERPROFILE")
    os.environ["LOCALAPPDATA"] = L
    os.environ["APPDATA"] = R
    os.environ["USERPROFILE"] = U
    # ثبت مسیر سفارشی — پس از سندباکس شدن LOCALAPPDATA تا در همان جا ذخیره شود
    save_custom_paths([os.path.join(sandbox, "Custom")])

    failures = []

    def check(name, cond):
        if cond:
            print("  ✓ " + name)
        else:
            print("  ✗ " + name)
            failures.append(name)

    try:
        # ثابت‌ها برای سرعت خودآزمایی کوچک‌سازی می‌شوند (نه در عملیات واقعی)
        global CHUNK_THRESHOLD, CHUNK_SIZE, PART_TARGET
        CHUNK_THRESHOLD = 32 * 1024 * 1024   # ۳۲MB
        CHUNK_SIZE = 10 * 1024 * 1024        # ۱۰MB
        PART_TARGET = 20 * 1024 * 1024       # ۲۰MB

        rc = run_backup(upload=False, out_dir=out, rep=_selftest_rep)
        check("اجرای بکاپ", rc == 0)

        mpaths = sorted(glob.glob(os.path.join(out, "*_manifest.json")))
        check("وجود مانیفست محلی", len(mpaths) == 1)
        manifest = json.load(open(mpaths[0], encoding="utf-8"))
        files = manifest["files"]
        check("تعداد فایل‌های بکاپ = ۷", len(files) == 7)
        check("فایل قفل (app.lock) نادیده گرفته شده", not any("app.lock" in f["arcname"] for f in files))
        check("فایل موقت (.tmp) نادیده گرفته شده", not any(".tmp" in f["arcname"] for f in files))
        check("منابع چهار مسیر", {s["arcroot"] for s in manifest["sources"]} == {"Claude-3p", "9router", ".claude", "custom"})

        big_e = [f for f in files if f["arcname"].endswith("big_file.bin")]
        check("فایل بزرگ تکه‌تکه شده", len(big_e) == 1 and big_e[0].get("chunked") is True)
        if big_e and big_e[0].get("chunked"):
            check("تعداد تکه‌های فایل بزرگ درست است",
                  len(big_e[0]["chunks"]) == n_chunks_of(big_e[0]["size"]))

        archives = sorted({f["archive"] for f in files if f.get("archive")})
        check("همه فایل‌های معمولی به آرشیو اختصاص یافته‌اند", all(a for a in archives) and all(
            os.path.isfile(os.path.join(out, a)) for a in archives))
        chunk_keys = [c["key"] for f in files if f.get("chunked") for c in f.get("chunks", [])]
        check("تکه‌های فایل بزرگ روی دیسک موجودند", all(
            os.path.isfile(os.path.join(out, k)) for k in chunk_keys))
        with zipfile.ZipFile(longpath(os.path.join(out, archives[0]))) as z:
            names = z.namelist()
        check("بخش اول شامل خلاصهٔ مانیفست و لاگ است",
              "backup_manifest_summary.json" in names and "backup.log" in names)
        check("پوشه خالی در آرشیو ثبت شده", any(n == "Claude-3p/empty_dir/" for n in names))

        for p in (L, R, U, C):
            shutil.rmtree(p)
            os.makedirs(p)

        rc = run_restore(parts_dir=out, rep=_selftest_rep)
        check("اجرای بازیابی (گام نصب در خودآزمایی شبیه‌سازی شد)", rc == 0)

        for p, h in expected.items():
            check("بازسازی " + os.path.relpath(p, sandbox), os.path.isfile(longpath(p)) and sha256_file(p) == h)
        check("بازسازی پوشه خالی", os.path.isdir(longpath(os.path.join(L, "Claude-3p", "empty_dir"))))
        check("فایل قفل بازسازی نشده", not os.path.exists(longpath(os.path.join(L, "Claude-3p", "app.lock"))))
        big_restored = os.path.join(C, "big_file.bin")
        check("بازسازی فایل بزرگ از تکه‌ها (هش تطابق دارد)",
              os.path.isfile(longpath(big_restored)) and big_hash is not None
              and sha256_file(big_restored) == big_hash)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
        os.environ.pop("C9R_SELFTEST", None)
        for name, old in (("LOCALAPPDATA", old_L), ("APPDATA", old_R), ("USERPROFILE", old_U)):
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old

    if failures:
        print(f"خودآزمایی: {len(failures)} مورد ناموفق")
        return 1
    print("خودآزمایی: PASS (همه موارد تأیید شد)")
    return 0


# ------------------------------------------------------------
#  رابط گرافیکی (PySide6 — تم Liquid Glass + فونت وزیر)
# ------------------------------------------------------------

def _try_sys_imports():
    try:
        import PySide6  # noqa
        return True
    except Exception:
        return False


if _try_sys_imports():
    from PySide6.QtCore import Qt, QThread, Signal, QPointF, QTimer
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient, QPainter, QRadialGradient, QBrush, QPen
    from PySide6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
        QPlainTextEdit, QFrame, QSizePolicy, QDialog, QListWidget, QFileDialog,
    )

    FONT_FAMILY = "Vazirmatn"

    def load_fonts():
        for w in ("Regular", "Medium", "Bold"):
            p = asset_path(f"fonts/Vazirmatn-{w}.ttf")
            if os.path.isfile(p):
                QFontDatabase.addApplicationFont(p)

    def app_font(size, weight="Regular"):
        f = QFont(FONT_FAMILY, size)
        weights = {"Regular": QFont.Weight.Normal, "Medium": QFont.Weight.Medium, "Bold": QFont.Weight.DemiBold}
        f.setWeight(weights.get(weight, QFont.Weight.Normal))
        f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return f

    GLASS_QSS = """
    * { font-family: 'Vazirmatn'; }

    QWidget { color: #e9f1ff; }

    #Root {
      background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                  stop:0 #0b1220, stop:0.45 #12202f, stop:0.75 #1a2c44, stop:1 #162b3a);
    }

    QFrame#cardGlass {
      background-color: rgba(255,255,255,0.055);
      border: 1px solid rgba(255,255,255,0.16);
      border-radius: 22px;
    }

    QFrame#cardHeader {
      background-color: rgba(255,255,255,0.04);
      border: 1px solid rgba(255,255,255,0.12);
      border-radius: 26px;
    }

    QLabel#appTitle {
      color: #ffffff;
      font-size: 26px;
      font-weight: 600;
      letter-spacing: 0.5px;
    }

    QLabel#appSub {
      color: #a9c4e6;
      font-size: 14px;
    }

    QLabel#badge {
      background-color: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                     stop:0 #4facfe, stop:1 #00f2fe);
      color: #06121f;
      border-radius: 34px;
      font-size: 22px;
      font-weight: 700;
    }

    QFrame#cardBtn {
      background-color: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.12);
      border-radius: 18px;
    }

    QPushButton#btnBackup {
      background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                     stop:0 #4facfe, stop:1 #00c9ff);
      color: #062838;
      border: 1px solid rgba(255,255,255,0.35);
      border-radius: 16px;
      font-size: 18px;
      font-weight: 600;
      padding: 14px 18px;
    }
    QPushButton#btnBackup:hover  { background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #71c1ff, stop:1 #2ad4ff); }
    QPushButton#btnBackup:pressed{ padding-top: 16px; }

    QPushButton#btnRestore {
      background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                     stop:0 #00c9ff, stop:1 #92fe9d);
      color: #062838;
      border: 1px solid rgba(255,255,255,0.35);
      border-radius: 16px;
      font-size: 18px;
      font-weight: 600;
      padding: 14px 18px;
    }
    QPushButton#btnRestore:hover  { background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2ad4ff, stop:1 #b3ffbe); }
    QPushButton#btnRestore:pressed{ padding-top: 16px; }

    QPushButton#btnSecondary {
      background-color: rgba(255,255,255,0.06);
      color: #cfe0f5;
      border: 1px solid rgba(255,255,255,0.15);
      border-radius: 14px;
      font-size: 15px;
      padding: 10px 16px;
    }
    QPushButton#btnSecondary:hover { background-color: rgba(255,255,255,0.14); }

    QPushButton#btnExit {
      background-color: rgba(255,255,255,0.06);
      color: #cfe0f5;
      border: 1px solid rgba(255,255,255,0.15);
      border-radius: 14px;
      font-size: 15px;
      padding: 10px 16px;
    }
    QPushButton#btnExit:hover { background-color: rgba(255,255,255,0.14); }

    QPlainTextEdit#log {
      background-color: rgba(5,10,20,0.35);
      border: 1px solid rgba(255,255,255,0.12);
      border-radius: 16px;
      color: #dcebff;
      font-size: 13.5px;
      padding: 12px;
      selection-background-color: #4facfe;
    }

    QLabel#status {
      color: #ffd27f;
      font-size: 14px;
      font-weight: 500;
    }
    """

    class TaskWorker(QThread):
        log = Signal(str)
        done = Signal(bool, str)

        def __init__(self, fn, parent=None):
            super().__init__(parent)
            self.fn = fn

        def run(self):
            ok = True
            msg = ""

            def rep(text):
                self.log.emit(text)

            try:
                rc = self.fn(rep=rep)
                ok = (rc == 0)
                if not ok:
                    msg = "عملیات با خطا پایان یافت (کد " + str(rc) + ")"
            except Exception as ex:
                ok = False
                msg = "خطا: " + str(ex)
            self.done.emit(ok, msg)

    class GlassRoot(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("Root")

        def paintEvent(self, event):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self.rect()

            bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
            bg.setColorAt(0.0, QColor("#0b1220"))
            bg.setColorAt(0.45, QColor("#12202f"))
            bg.setColorAt(0.75, QColor("#1a2c44"))
            bg.setColorAt(1.0, QColor("#162b3a"))
            p.fillRect(rect, bg)

            # هاله‌های نور (اثر شیشه)
            glow = QRadialGradient(QPointF(rect.width() * 0.18, rect.height() * 0.12), rect.width() * 0.5)
            glow.setColorAt(0.0, QColor(79, 172, 254, 90))
            glow.setColorAt(0.5, QColor(79, 172, 254, 0))
            p.fillRect(rect, QBrush(glow))

            glow2 = QRadialGradient(QPointF(rect.width() * 0.85, rect.height() * 0.85), rect.width() * 0.5)
            glow2.setColorAt(0.0, QColor(0, 242, 254, 70))
            glow2.setColorAt(0.5, QColor(0, 242, 254, 0))
            p.fillRect(rect, QBrush(glow2))

            p.end()
            super().paintEvent(event)

    class MainWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("بکاپ و بازگردانی کلود و 9router")
            self.setFixedSize(780, 760)
            self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
            self.worker = None
            self.auto_timer = None
            self._build_ui()

        def _build_ui(self):
            root = QVBoxLayout(self)
            root.setContentsMargins(26, 26, 26, 26)
            root.setSpacing(16)

            # هدر
            header = QFrame()
            header.setObjectName("cardHeader")
            hl = QHBoxLayout(header)
            hl.setContentsMargins(20, 18, 20, 18)
            hl.setSpacing(18)

            badge = QLabel("C9R")
            badge.setObjectName("badge")
            badge.setFixedSize(68, 68)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hl.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

            titles = QVBoxLayout()
            titles.setSpacing(2)
            title = QLabel("بکاپ و بازگردانی کلود و 9router")
            title.setObjectName("appTitle")
            sub = QLabel("بکاپ سه مسیر پیش‌فرض + مسیرهای سفارشی؛ فایل بزرگ خودکار تکه‌تکه می‌شود")
            sub.setObjectName("appSub")
            titles.addWidget(title)
            titles.addWidget(sub)
            hl.addLayout(titles, 1)
            root.addWidget(header)

            # دکمه‌های اصلی
            card = QFrame()
            card.setObjectName("cardGlass")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(22, 22, 22, 22)
            cl.setSpacing(12)

            self.btn_backup = QPushButton("بکاپ‌گیری (Backup)")
            self.btn_backup.setObjectName("btnBackup")
            self.btn_restore = QPushButton("بازگردانی بکاپ (Restore)")
            self.btn_restore.setObjectName("btnRestore")
            self.btn_auto = QPushButton("بکاپ خودکار: خاموش")
            self.btn_auto.setObjectName("btnSecondary")
            self.btn_paths = QPushButton("مسیرهای سفارشی...")
            self.btn_paths.setObjectName("btnSecondary")
            self.btn_gd = QPushButton("اتصال گوگل درایو...")
            self.btn_gd.setObjectName("btnSecondary")
            self.btn_exit = QPushButton("خروج")
            self.btn_exit.setObjectName("btnExit")

            hint = QLabel("بکاپ به Google Drive آپلود می‌شود؛ ۲ نسخهٔ اخیر در پوشه می‌ماند و قدیمی‌ترها خودکار حذف می‌شوند. پس از بازگردانی، نصب‌کننده Claude اجرا و npm جهانی بازنصب می‌شود. فایل‌های باز با Snapshot (VSS) خوانده می‌شوند.")
            hint.setObjectName("appSub")
            hint.setWordWrap(True)

            cl.addWidget(self.btn_backup)
            cl.addWidget(self.btn_restore)
            row = QHBoxLayout()
            row.addWidget(self.btn_auto, 1)
            row.addWidget(self.btn_paths)
            row.addWidget(self.btn_gd)
            cl.addLayout(row)
            cl.addWidget(hint)

            bottom = QHBoxLayout()
            bottom.addStretch(1)
            bottom.addWidget(self.btn_exit)
            cl.addLayout(bottom)

            root.addWidget(card)

            # وضعیت
            self.lbl_status = QLabel("")
            self.lbl_status.setObjectName("status")
            root.addWidget(self.lbl_status)

            # لاگ
            self.log = QPlainTextEdit()
            self.log.setObjectName("log")
            self.log.setReadOnly(True)
            root.addWidget(self.log, 1)

            self.btn_backup.clicked.connect(self.do_backup)
            self.btn_restore.clicked.connect(self.do_restore)
            self.btn_paths.clicked.connect(self.manage_paths)
            self.btn_auto.clicked.connect(self.toggle_auto)
            self.btn_gd.clicked.connect(self.manage_drive)

            self._refresh_gd_button()

        def toggle_auto(self):
            if self.auto_timer is None:
                dlg = IntervalDialog(self)
                if dlg.exec() != QDialog.DialogCode.Accepted:
                    return
                minutes = dlg.minutes
                self.auto_timer = QTimer(self)
                self.auto_timer.timeout.connect(self.run_auto_backup)
                self.auto_timer.start(int(minutes * 60 * 1000))
                self.btn_auto.setText(f"بکاپ خودکار: روشن (هر {minutes} دقیقه)")
                self._append(f"بکاپ خودکار فعال شد (هر {minutes} دقیقه)؛ اولین بکاپ اکنون اجرا می‌شود.")
                self.run_auto_backup()
            else:
                self.auto_timer.stop()
                self.auto_timer = None
                self.btn_auto.setText("بکاپ خودکار: خاموش")
                self._append("بکاپ خودکار غیرفعال شد.")

        def run_auto_backup(self):
            if self.worker is not None:
                self._append("بکاپ خودکار این دوره رد شد (عملیات قبلی هنوز در حال اجراست).")
                return
            self._start(lambda rep: run_backup(upload=True, rep=rep), "بکاپ خودکار در حال اجرا...")

        def manage_paths(self):
            dlg = QDialog(self)
            dlg.setWindowTitle("مسیرهای سفارشی بکاپ")
            dlg.setFixedSize(560, 400)
            v = QVBoxLayout(dlg)
            v.addWidget(QLabel("پوشه یا فایل دلخواه اضافه کنید (فایل بزرگ‌تر از ۱۰۰MB به تکه‌های ۹۸MB شکسته می‌شود):"))
            lst = QListWidget()
            for p in load_custom_paths():
                lst.addItem(p)
            v.addWidget(lst, 1)
            h = QHBoxLayout()
            b_add_d = QPushButton("افزودن پوشه")
            b_add_f = QPushButton("افزودن فایل")
            b_del = QPushButton("حذف انتخاب")
            b_save = QPushButton("ذخیره")
            for b in (b_add_d, b_add_f, b_del, b_save):
                h.addWidget(b)
            v.addLayout(h)

            def add_dir():
                d = QFileDialog.getExistingDirectory(dlg, "انتخاب پوشه")
                if d:
                    lst.addItem(os.path.abspath(d))

            def add_file():
                f_, _fl = QFileDialog.getOpenFileName(dlg, "انتخاب فایل")
                if f_:
                    lst.addItem(os.path.abspath(f_))

            def rm_sel():
                for it in lst.selectedItems():
                    lst.takeItem(lst.row(it))

            def save():
                save_custom_paths([lst.item(i).text() for i in range(lst.count())])
                dlg.accept()

            b_add_d.clicked.connect(add_dir)
            b_add_f.clicked.connect(add_file)
            b_del.clicked.connect(rm_sel)
            b_save.clicked.connect(save)
            dlg.exec()

        def _append(self, text):
            self.log.appendPlainText(text)
            sb = self.log.verticalScrollBar()
            sb.setValue(sb.maximum())

        def _refresh_gd_button(self):
            ready, _sec = gd_credentials_ready()
            if ready:
                self.btn_gd.setText("گوگل درایو: ✓ فعال")
            elif load_secrets().get("key_json") or load_settings().get("gd_folder_id"):
                self.btn_gd.setText("گوگل درایو: ناقص!")
            else:
                self.btn_gd.setText("اتصال گوگل درایو...")

        def manage_drive(self):
            dlg = DriveDialog(self)
            dlg.exec()
            self._refresh_gd_button()

        def _set_busy(self, busy, status=""):
            self.btn_backup.setEnabled(not busy)
            self.btn_restore.setEnabled(not busy)
            self.btn_paths.setEnabled(not busy)
            self.btn_gd.setEnabled(not busy)
            self.btn_exit.setEnabled(not busy)
            self.lbl_status.setText(status)

        def _start(self, fn, status):
            if self.worker is not None:
                return
            self.log.clear()
            self._set_busy(True, status)
            self.worker = TaskWorker(fn, self)
            self.worker.log.connect(self._append)
            self.worker.done.connect(self._on_done)
            self.worker.start()

        def _on_done(self, ok, msg):
            self.worker = None
            self._set_busy(False)
            if not ok:
                self._append(msg)

        def do_backup(self):
            self._start(lambda rep: run_backup(upload=True, rep=rep), "در حال بکاپ‌گیری...")

        def do_restore(self):
            self._start(lambda rep: run_restore(rep=rep), "در حال بازگردانی بکاپ...")

        def closeEvent(self, event):
            if self.worker is not None:
                try:
                    self.worker.wait(3000)
                except Exception:
                    pass
            event.accept()

    class IntervalDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("فاصله بکاپ خودکار")
            self.setFixedSize(360, 150)
            self.minutes = 60
            v = QVBoxLayout(self)
            v.addWidget(QLabel("بکاپ خودکار هر چند دقیقه یکبار اجرا شود؟"))
            from PySide6.QtWidgets import QSpinBox
            self.spin = QSpinBox()
            self.spin.setRange(1, 1440)
            self.spin.setValue(int(load_settings().get("auto_interval_minutes", 60)))
            self.spin.setSuffix(" دقیقه")
            v.addWidget(self.spin)
            h = QHBoxLayout()
            b_ok = QPushButton("شروع")
            b_cancel = QPushButton("انصراف")
            h.addStretch(1)
            h.addWidget(b_ok)
            h.addWidget(b_cancel)
            v.addLayout(h)
            b_ok.clicked.connect(self.accept)
            b_cancel.clicked.connect(self.reject)

        def accept(self):
            self.minutes = self.spin.value()
            s = load_settings()
            s["auto_interval_minutes"] = self.minutes
            save_settings(s)
            super().accept()

    class DriveDialog(QDialog):
        """اتصال Google Drive: انتخاب فایل OAuth Client JSON + مرورگر تأیید گوگل +
        انتخاب پوشهٔ مقصد (نام یا شناسه). Refresh Token رمزنگاری‌شده ذخیره می‌شود."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("اتصال گوگل درایو")
            self.setFixedSize(600, 380)
            v = QVBoxLayout(self)
            v.addWidget(QLabel("۱) فایل OAuth Client JSON (نوع Desktop app) را انتخاب کن — با DPAPI رمزنگاری می‌شود."))
            h_key = QHBoxLayout()
            from PySide6.QtWidgets import QLineEdit
            self.ed_key = QLineEdit()
            self.ed_key.setEchoMode(QLineEdit.EchoMode.Password)
            self.ed_key.setPlaceholderText("مسیر فایل JSON کلاینت OAuth")
            if load_secrets().get("client_json"):
                self.ed_key.setPlaceholderText("کلاینت فعلی ذخیره شده — برای تغییر، مسیر جدید بده")
            self.b_browse = QPushButton("انتخاب...")
            h_key.addWidget(self.ed_key, 1)
            h_key.addWidget(self.b_browse)
            v.addLayout(h_key)
            self.b_auth = QPushButton("۲) ورود به گوگل و تأیید دسترسی")
            v.addWidget(self.b_auth)
            v.addWidget(QLabel("۳) پوشهٔ مقصد — نام یا شناسه پوشهٔ Drive خودت:"))
            self.ed_folder = QLineEdit()
            s = load_settings()
            if s.get("gd_folder_id"):
                self.ed_folder.setText(str(s.get("gd_folder_id")))
            v.addWidget(self.ed_folder)
            h = QHBoxLayout()
            self.b_save = QPushButton("ذخیره و فعال‌سازی")
            self.b_cancel = QPushButton("انصراف")
            h.addStretch(1)
            h.addWidget(self.b_save)
            h.addWidget(self.b_cancel)
            v.addLayout(h)
            self.lbl_result = QLabel("")
            self.lbl_result.setWordWrap(True)
            v.addWidget(self.lbl_result)

            self.b_browse.clicked.connect(self.do_browse)
            self.b_auth.clicked.connect(self.do_auth)
            self.b_save.clicked.connect(self.do_save)
            self.b_cancel.clicked.connect(self.reject)
            self._client_json = ""
            self._refresh_token = ""

        def do_browse(self):
            path, _fl = QFileDialog.getOpenFileName(self, "انتخاب فایل کلاینت OAuth", "", "JSON (*.json)")
            if path:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    # فایل رسمی گوگل: {"installed": {...}} — فیلد type داخل ندارد؛
                    # کلید بیرونی خودش نشان‌گر نوع است. ساختار مسطح هم پذیرفته می‌شود.
                    if isinstance(data.get("web"), dict):
                        self.lbl_result.setText("✗ این فایل کلاینت از نوع Web است؛ در کنسول کلاینت با نوع Desktop app بساز.")
                        return
                    if isinstance(data.get("service_account"), dict) or data.get("type") == "service_account":
                        self.lbl_result.setText("✗ این فایل کلید حساب سرویس است، نه کلاینت OAuth.")
                        return
                    inner = data.get("installed") if isinstance(data.get("installed"), dict) else data
                    if not inner.get("client_id") or not inner.get("client_secret"):
                        self.lbl_result.setText("✗ این فایل کلاینت OAuth معتبر نیست (client_id/client_secret ندارد).")
                        return
                    self._client_json = json.dumps(data)
                    self.ed_key.setText(path)
                    self.lbl_result.setText("کلاینت خوانده شد؛ گام ۲ (ورود به گوگل) را بزن.")
                except Exception as ex:
                    self.lbl_result.setText("✗ خواندن کلاینت ناموفق: " + str(ex))

        def do_auth(self):
            client = self._client_json or load_secrets().get("client_json", "")
            if not client:
                self.lbl_result.setText("✗ ابتدا فایل کلاینت OAuth را انتخاب کن.")
                return
            try:
                self._refresh_token = gd_authorize_interactive(client, rep=self.lbl_result.setText)
                self.lbl_result.setText("✓ تأیید شد؛ حالا گام ۳ (پوشهٔ مقصد) و «ذخیره» را انجام بده.")
            except Exception as ex:
                self.lbl_result.setText("✗ تأیید گوگل ناموفق: " + str(ex))

        def _resolve_folder(self, value):
            """ورودی می‌تواند شناسه پوشه یا نام پوشه باشد؛ شناسه واقعی برمی‌گرداند.
            با scope محدود drive.file فقط فایل‌های ساختهٔ خود برنامه دیده می‌شوند —
            اگر پوشهٔ هم‌نام پیدا نشد، خود برنامه آن را در Drive می‌سازد."""
            if re.fullmatch(r"[A-Za-z0-9_-]{20,}", value):
                try:
                    fld = gd_request("GET", GD_DRIVE_API + "/files/" + urllib.parse.quote(value, safe=""),
                                     params={"fields": "id,name"})
                    return fld["id"], "پوشهٔ «" + fld.get("name", value) + "» تأیید شد"
                except Exception:
                    pass
            params = {"q": "mimeType = 'application/vnd.google-apps.folder' and name = "
                           "'" + value.replace("'", "\\'") + "' and trashed = false",
                      "fields": "files(id,name)", "pageSize": "10"}
            res = gd_request("GET", GD_DRIVE_API + "/files", params=params)
            found = res.get("files", [])
            if found:
                return found[0]["id"], "پوشهٔ «" + found[0].get("name", value) + "» پیدا شد"
            # پوشه را خود برنامه می‌سازد (فقط با drive.file این ممکن است)
            created = gd_request("POST", GD_DRIVE_API + "/files",
                                 params={"fields": "id,name"},
                                 data=json.dumps({"name": value,
                                                  "mimeType": "application/vnd.google-apps.folder"}).encode("utf-8"),
                                 headers={"Content-Type": "application/json; charset=UTF-8"})
            return created["id"], "پوشهٔ «" + created.get("name", value) + "» در Drive تو ساخته شد"

        def do_save(self):
            client = self._client_json or load_secrets().get("client_json", "")
            refresh = self._refresh_token or load_secrets().get("refresh_token", "")
            if not client or not refresh:
                self.lbl_result.setText("✗ ابتدا ورود به گوگل (گام ۲) را کامل کن.")
                return
            folder = self.ed_folder.text().strip()
            if not folder:
                self.lbl_result.setText("✗ نام یا شناسه پوشهٔ مقصد را وارد کن.")
                return
            try:
                fid, msg = self._resolve_folder(folder)
            except Exception as ex:
                self.lbl_result.setText("✗ " + str(ex))
                return
            s = load_settings()
            s["gd_folder_id"] = fid
            save_settings(s)
            save_secrets(client, refresh)
            self.lbl_result.setText("✓ اتصال ذخیره شد (" + msg + ") — از این پس بکاپ‌ها به Google Drive آپلود می‌شود.")
            self.accept()

    def run_gui():
        """رندر پنجره به فایل PNG (وضعیت --shot) و سپس حلقه رویداد."""
        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion(APP_VERSION)
        load_fonts()
        app.setFont(app_font(15))
        w = MainWindow()
        w.setStyleSheet(GLASS_QSS)
        w.show()

        if "--shot" in sys.argv:
            i = sys.argv.index("--shot")
            target = sys.argv[i + 1] if i + 1 < len(sys.argv) else "shot.png"
            from PySide6.QtWidgets import QApplication as QA
            QA.processEvents()
            QTimer.singleShot(600, lambda: _save_shot(w, target))
            QTimer.singleShot(1200, lambda: app.quit())
        return app.exec()

    def _save_shot(w, target):
        pm = w.grab()
        pm.save(target)
        print("saved " + target, file=sys.stderr)


# ------------------------------------------------------------
#  ورودی
# ------------------------------------------------------------

USAGE = """کاربرد:
  Claude9RouterTool.exe                  → رابط گرافیکی (GUI)
  Claude9RouterTool.exe --selftest      → خودآزمایی داخلی (بدون شبکه)
  Claude9RouterTool.exe --shot <PNG>    → رندر پنجره و خروج (تست ظاهر)
"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if "--help" in sys.argv or "-h" in sys.argv:
        print(USAGE)
        return 0

    if "--selftest" in sys.argv:
        return selftest()

    if not _try_sys_imports():
        print("PySide6 نصب نیست؛ نمی‌توان GUI را اجرا کرد.", file=sys.stderr)
        return 1

    if not (os.environ.get("C9R_SELFTEST") or is_admin()):
        # ارتقا به ادمین و خروج (GUI بالا با سطح ادمین باز می‌شود)
        print("ارتقا به سطح Administrator...")
        if elevate():
            return 0
        print("اجرای سطح Administrator رد شد.", file=sys.stderr)
        return 1

    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
