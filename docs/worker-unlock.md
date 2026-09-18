# باز کردن قفل با ورکر کلادفلر — راهنمای گام‌به‌گام

## ایده در یک خط

کلید رمزگشایی اعتبارنامه (`DATA_KEY`) **فقط روی ورکر** می‌ماند. برنامه برای باز کردن
`gd_embedded.json` باید از ورکر کلید بگیرد، و ورکر فقط با رمز درست آن را می‌دهد.
پس کسی که EXE عمومی را دارد ولی رمز را نمی‌داند، هیچ راهی برای رمزگشایی آفلاین ندارد.

> توجه: باز شدن مرورگر به‌تنهایی امنیت نمی‌آورد. امنیت از این می‌آید که **کلید روی سرور است**،
> نه داخل EXE. اگر کلید داخل برنامه بماند، مهاجم صفحهٔ قفل را دور می‌زند.

## گام ۱ — ساختن مقادیر (روی سیستم خودت)

```bash
python -c "import os;print('APP_PW_SALT =', os.urandom(16).hex())"
python -c "import os,base64;print('TICKET_SECRET =', base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"
python -c "import os,base64;print('DATA_KEY =', base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"
```

هش رمز (رمز جدید و قوی را جای `RAMZ_JADID` و نمک بالا را جای `SALT_HEX` بگذار):

```bash
python -c "import hashlib;print(hashlib.pbkdf2_hmac('sha256',b'RAMZ_JADID',bytes.fromhex('SALT_HEX'),600000).hex())"
```

## گام ۲ — دیپلوی ورکر

1. `dash.cloudflare.com` → Workers & Pages → Create → Worker → نام (مثلاً `c9r-lock`) → Deploy.
2. Edit code → کل محتوای `worker.js` را جای‌گذاری کن → Deploy.
3. Settings → Variables and Secrets:

| نام | نوع | مقدار |
|---|---|---|
| `APP_PW_SALT` | Text | نمک هگز مرحلهٔ ۱ |
| `APP_PW_HASH` | Secret | هش هگز مرحلهٔ ۱ |
| `TICKET_SECRET` | Secret | مقدار مرحلهٔ ۱ |
| `DATA_KEY` | Secret | مقدار مرحلهٔ ۱ (همان کلید K) |
| `TURNSTILE_SITEKEY` | Text (اختیاری) | از Turnstile |
| `TURNSTILE_SECRET` | Secret (اختیاری) | از Turnstile |

4. تست سلامت: `https://<name>.<sub>.workers.dev/healthz` باید `ok` بدهد.

## گام ۳ — بازتولید فایل جاسازی‌شده با کلید ورکر

```bash
set DATA_KEY=<همان مقدار مرحلهٔ ۱>
python tools/embed_with_key.py
python -m PyInstaller --noconfirm --clean Claude9RouterTool.spec
```

## گام ۴ — تنظیم برنامه

در `settings.json` کلید `WORKER_URL` را بگذار: `https://<name>.<sub>.workers.dev`.
برنامه هنگام اجرا `run_worker_unlock(WORKER_URL)` را صدا می‌زند، مرورگر باز می‌شود،
رمز را در صفحه می‌زنی، و کلید K برمی‌گردد.

## نکتهٔ آفلاین (اختیاری)

اگر می‌خواهی برنامه بدون اینترنت هم باز شود، می‌توان کلید K را بعد از اولین باز شدنِ موفق،
با DPAPI روی همان ماشین ذخیره کرد (مثل `secrets.dat`). این کار حملهٔ آفلاین از روی EXE عمومی
را هنوز می‌بندد، ولی اگر خودِ ماشین آلوده شود، K در دسترس است. پیش‌فرض: بدون کش.