#Requires -Version 5.1
<#
.SYNOPSIS
  اضافه کردن کیبورد فارسی + فعال‌سازی Alt+Shift برای تعویض زبان + فعال‌سازی تم دارک ویندوز
.DESCRIPTION
  هر وقت اجراش کنی:
  1) زبان فارسی (fa-IR) را به لیست زبان‌های ویندوز اضافه می‌کند (انگلیسی حفظ می‌شود)
  2) کلید تعویض زبان را روی Alt+Shift می‌گذارد
  3) تم دارک ویندوز (Apps + System) را فعال می‌کند
.NOTES
  اجرا: روی فایل راست‌کلیک > Run with PowerShell
  یا: powershell -ExecutionPolicy Bypass -File Setup-FaKeyboard-DarkTheme.ps1
#>

$ErrorActionPreference = 'Stop'

# --- تابع فورس‌رفرش: اعلام تغییر تنظیمات به همه برنامه‌ها (بدون لاگ‌اوت) ---
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class NativeMethods {
    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lResult);
}
"@

function Send-SettingChange([string]$Area) {
    $HWND_BROADCAST = [IntPtr]0xffff
    $WM_SETTINGCHANGE = 0x001A
    $SMTO_ABORTIFHUNG = 0x0002
    $result = [UIntPtr]::Zero
    [NativeMethods]::SendMessageTimeout($HWND_BROADCAST, $WM_SETTINGCHANGE, [UIntPtr]::Zero, $Area, $SMTO_ABORTIFHUNG, 5000, [ref]$result) | Out-Null
}

Write-Host "1/3: Adding Persian (Farsi) keyboard..." -ForegroundColor Cyan

# --- 1) اضافه کردن کیبورد فارسی ---
$LangList = Get-WinUserLanguageList

# مطمئن شو انگلیسی هست
if (-not ($LangList.LanguageTag -contains 'en-US')) {
    $LangList.Add('en-US')
}

# اضافه کردن فارسی اگر نیست
if (-not ($LangList.LanguageTag -contains 'fa-IR')) {
    $LangList.Add('fa-IR')
    Write-Host "  fa-IR added." -ForegroundColor Green
} else {
    Write-Host "  fa-IR already exists." -ForegroundColor Yellow
}

Set-WinUserLanguageList $LangList -Force
Write-Host "  Current languages: $($LangList.LanguageTag -join ', ')" -ForegroundColor Gray

# فورس‌رفرش لیست زبان (بدون نیاز به لاگ‌اوت)
Send-SettingChange 'intl'
# مطمئن شو سرویس زبان (Language Bar) در حال اجراست
Start-Process "$env:SystemRoot\System32\ctfmon.exe" -ErrorAction SilentlyContinue
Write-Host "  Language list refreshed (no logout needed)." -ForegroundColor Green

Write-Host "2/3: Setting Alt+Shift for language switching..." -ForegroundColor Cyan

# --- 2) تنظیم Alt+Shift برای جابه‌جایی بین زبان‌ها ---
# مقادیر Hotkey:
#   1 = Alt+Shift
#   2 = Ctrl+Shift
#   3 = None (غیرفعال)
$TogglePath = 'HKCU:\Keyboard Layout\Toggle'
if (-not (Test-Path $TogglePath)) {
    New-Item -Path $TogglePath -Force | Out-Null
}
Set-ItemProperty -Path $TogglePath -Name 'Language Hotkey' -Value '1' -Type String
Set-ItemProperty -Path $TogglePath -Name 'Layout Hotkey' -Value '1' -Type String
Send-SettingChange 'Keyboard Layout'
Write-Host "  Alt+Shift enabled." -ForegroundColor Green

Write-Host "3/3: Applying Windows Dark Theme..." -ForegroundColor Cyan

# --- 3) فعال کردن تم دارک ---
$PersonalizePath = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize'
if (-not (Test-Path $PersonalizePath)) {
    New-Item -Path $PersonalizePath -Force | Out-Null
}
Set-ItemProperty -Path $PersonalizePath -Name 'AppsUseLightTheme' -Value 0 -Type DWord
Set-ItemProperty -Path $PersonalizePath -Name 'SystemUsesLightTheme' -Value 0 -Type DWord

# فورس‌رفرش تم دارک (بدون نیاز به لاگ‌اوت)
Send-SettingChange 'ImmersiveColorSet'

Write-Host "  Dark theme applied." -ForegroundColor Green
Write-Host ""
Write-Host "Done! Alt+Shift را بزن تا بین EN و FA جابه‌جا شوی." -ForegroundColor Green
Write-Host "همه‌چیز بدون لاگ‌اوت فورس شد." -ForegroundColor Gray
