# سیاست حریم خصوصی — Claude9RouterTool

**آخرین به‌روزرسانی:** ۱۶ سپتامبر ۲۰۲۶

## خلاصه

Claude9RouterTool یک ابزار شخصی برای بکاپ‌گیری و بازیابی تنظیمات روی ویندوز است. این برنامه **هیچ داده‌ای را به هیچ سروری جز حساب Google Drive خودِ کاربر ارسال نمی‌کند**. هیچ سرور واسط، تحلیل‌گر، تلمتری یا ردیاب در آن وجود ندارد.

## چه داده‌ای خوانده می‌شود

برنامه فقط زمانی اجرا می‌شود که کاربر خودش دکمهٔ بکاپ‌گیری یا بازگردانی را بزند. در آن صورت، این مسیرهای روی همان رایانه خوانده می‌شوند:

- پوشهٔ تنظیمات و تاریخچهٔ Claude Code
- پوشهٔ تنظیمات 9Router
- پوشهٔ Downloads کاربر
- پروفایل مرورگر Google Chrome
- هر مسیر سفارشی که کاربر خودش اضافه کرده باشد

## داده‌ها کجا می‌روند

- **هیچ‌کجا جز حساب Google Drive خودِ کاربر.** دسترسی برنامه با محدودهٔ `drive.file` انجام می‌شود؛ یعنی برنامه فقط به فایل‌هایی که خودش ساخته دسترسی دارد و بقیهٔ Drive کاربر برایش نامرئی است.
- فایل‌های بکاپ در همان پوشه‌ای قرار می‌گیرند که کاربر هنگام اتصال انتخاب کرده است.
- بکاپ‌های موقت پیش از آپلود، روی همان رایانه می‌مانند و پس از موفقیت پاک می‌شوند.

## چه چیزی به اشتراک گذاشته می‌شود

**هیچ‌چیز.** داده‌ها نه فروخته می‌شوند، نه به اشتراک گذاشته می‌شوند و نه به هیچ شخص ثالثی ارسال می‌شوند. برنامه به هیچ سروری جز `googleapis.com` (برای ارسال و دریافت فایل‌های بکاپ) متصل نمی‌شود.

## اعتبارنامه و امنیت

- `client_id` و `client_secret` کلاینت OAuth داخل خود برنامه جاسازی شده‌اند.
- `refresh_token` گوگل با ChaCha20 و کلید مشتق‌شده از PBKDF2 (۲۰۰٬۰۰۰ دور) رمزنگاری می‌شود.
- روی هر رایانه، اعتبارنامهٔ محلی با DPAPI ویندوز محافظت می‌شود.

## نگهداری و حذف داده‌ها

کاربر در هر لحظه می‌تواند:

- فایل‌های بکاپ را از پوشهٔ Google Drive خودش پاک کند؛
- دسترسی برنامه را در صفحهٔ <https://myaccount.google.com/permissions> لغو کند؛
- تمام فایل‌های محلی برنامه را از `%LOCALAPPDATA%\Claude9RouterTool` حذف کند.

با لغو دسترسی، برنامه دیگر به هیچ داده‌ای دسترسی نخواهد داشت.

## کودکان

این برنامه برای استفادهٔ شخصی بزرگسالان ساخته شده و برای کودکان زیر ۱۳ سال طراحی نشده است.

## تماس

برای هر پرسشی دربارهٔ این سیاست یا دربارهٔ داده‌ها، از طریق بخش Issues مخزن پروژه درخواست بدهید:

<https://github.com/0smjp0-afk/Claude9RouterTool/issues>

---

# Privacy Policy — Claude9RouterTool (English)

**Last updated:** 16 September 2026

Claude9RouterTool is a personal Windows backup and restore utility. **It sends no data to any server other than the user's own Google Drive account.** There is no intermediary server, no analytics, no telemetry and no tracking.

**Data read.** Only when the user clicks Backup or Restore, the app reads the Claude Code settings, 9Router settings, the Downloads folder, the Google Chrome profile, and any custom path the user has added — all on the local machine.

**Where data goes.** Only to the user's own Google Drive, using the `drive.file` scope, which limits access to files this app itself created. No other part of the user's Drive is visible to it.

**Sharing.** None. Data is never sold, shared or sent to any third party. The app contacts no host other than `googleapis.com`.

**Credentials.** The OAuth `refresh_token` is encrypted with ChaCha20 using a key derived via PBKDF2 (200,000 iterations); on each machine, local credentials are protected with the Windows DPAPI.

**Deletion.** The user can delete backup files from their own Drive at any time, revoke access at <https://myaccount.google.com/permissions>, or remove all local app data from `%LOCALAPPDATA%\Claude9RouterTool`.

**Contact.** <https://github.com/0smjp0-afk/Claude9RouterTool/issues>