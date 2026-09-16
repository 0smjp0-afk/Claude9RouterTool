# -*- coding: utf-8 -*-
# به‌روزرسانی gd_embedded.json از اعتبارنامهٔ ذخیره‌شدهٔ محلی (secrets.dat)
#
# چرا لازم است: پس از هر «اتصال گوگل درایو» توکن تازه در secrets.dat می‌نشیند،
# ولی نسخهٔ جاسازی‌شدهٔ داخل EXE همان توکن قدیمی می‌ماند. این اسکریپت توکن تازه را
# با همان رمز برنامه (ChaCha20 + PBKDF2) رمزنگاری و در gd_embedded.json می‌نویسد
# تا ویندوزهای تازه هم بدون لاگین کار کنند.
#
# اجرا:  python tools/embed_credentials.py
# سپس:  python -m PyInstaller --noconfirm --clean Claude9RouterTool.spec

import base64
import getpass
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import claude9router_tool as t  # noqa: E402


def main():
    secrets = t.load_secrets()
    client = secrets.get("client_json") or ""
    refresh = secrets.get("refresh_token") or ""
    folder = t.load_settings().get("gd_folder_id") or ""

    if not client or not refresh:
        print("✗ secrets.dat اعتبارنامهٔ معتبری ندارد؛ اول در برنامه «اتصال گوگل درایو» را کامل کن.")
        return 1
    if not folder:
        print("✗ شناسهٔ پوشهٔ مقصد در settings.json نیست؛ «ذخیره و فعال‌سازی» را در همان دیالوگ بزن.")
        return 1

    pw = getpass.getpass("رمز برنامه: ")
    if not t.auth_verify_password(pw):
        print("✗ رمز برنامه نادرست است.")
        return 1

    salt = base64.b64encode(os.urandom(16)).decode("ascii")
    payload = json.dumps({"client_json": client, "refresh_token": refresh, "folder_id": folder},
                         ensure_ascii=False)
    blob = t.auth_encrypt(pw, salt, payload, t.AUTH_PBKDF2_ITERATIONS)

    out = os.path.join(ROOT, "gd_embedded.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"enc": blob, "salt": salt, "iterations": t.AUTH_PBKDF2_ITERATIONS}, f, indent=1)

    print("✓ gd_embedded.json به‌روز شد:", out)
    print("  طول refresh_token: %d کاراکتر | طول بلوب رمزنگاری‌شده: %d" % (len(refresh), len(blob)))
    print("  حالا بیلد کن: python -m PyInstaller --noconfirm --clean Claude9RouterTool.spec")
    print("  و یادت باشد اندازهٔ EXE باید حدود ۵۲ مگابایت باشد.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())