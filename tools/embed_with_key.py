# -*- coding: utf-8 -*-
# رمزنگاری gd_embedded.json با کلید K (همان DATA_KEY روی ورکر) — نه با رمز.
#
# چرا: در طرح جدید، رمز فقط درِ ورکر را باز می‌کند و کلید رمزگشایی (K) روی
# ورکر می‌ماند. پس فایل جاسازی‌شده باید با K رمز شود، نه با PBKDF2(رمز).
# این کار حملهٔ آفلاین را کامل بی‌اثر می‌کند.
#
# اجرا:
#   set DATA_KEY=<همان secret روی ورکر>
#   python tools/embed_with_key.py
# سپس بیلد:  python -m PyInstaller --noconfirm --clean Claude9RouterTool.spec

import base64
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import claude9router_tool as t  # noqa: E402


def main():
    data_key_b64 = os.environ.get("DATA_KEY", "").strip()
    if not data_key_b64:
        print("✗ متغیر محیطی DATA_KEY را تنظیم کن (همان مقدار Secret روی ورکر).")
        return 1
    key = base64.urlsafe_b64decode(data_key_b64 + "=" * (-len(data_key_b64) % 4))
    if len(key) != 32:
        print("✗ DATA_KEY باید ۳۲ بایت (base64url) باشد.")
        return 1

    secrets = t.load_secrets()
    client = secrets.get("client_json") or ""
    refresh = secrets.get("refresh_token") or ""
    folder = t.load_settings().get("gd_folder_id") or ""
    if not client or not refresh:
        print("✗ secrets.dat اعتبارنامهٔ معتبری ندارد؛ اول در برنامه «اتصال گوگل درایو» را کامل کن.")
        return 1
    if not folder:
        print("✗ شناسهٔ پوشهٔ مقصد در settings.json نیست.")
        return 1

    payload = json.dumps({"client_json": client, "refresh_token": refresh, "folder_id": folder},
                         ensure_ascii=False)
    blob = t.auth_encrypt_raw(key, payload)

    out = os.path.join(ROOT, "gd_embedded.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"v": 2, "raw_enc": blob}, f, indent=1)

    print("✓ gd_embedded.json با کلید ورکر بازتولید شد:", out)
    print("  طول refresh_token: %d | طول بلوب: %d" % (len(refresh), len(blob)))
    print("  حالا بیلد کن و یادت باشد اندازهٔ EXE حدود ۵۲ مگابایت باشد.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())