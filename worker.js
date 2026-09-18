/**
 * worker.js — «قفل مرکزی» Claude9RouterTool روی Cloudflare Workers
 * ---------------------------------------------------------------
 * این ورکر کلید رمزگشایی اعتبارنامه (DATA_KEY) را در Secret خودش نگه می‌دارد.
 * برنامه برای باز کردن gd_embedded.json باید از این ورکر کلید بگیرد، و ورکر
 * فقط وقتی کلید را می‌دهد که رمز درست وارد شده باشد. نتیجه: کسی که EXE عمومی
 * را دارد ولی رمز را نمی‌داند، هیچ راهی برای رمزگشایی آفلاین ندارد.
 *
 * نحوهٔ نصب (بدون wrangler — از همان dash.cloudflare.com):
 *  1) Workers & Pages → Create → Worker → یک نام بده (مثلاً c9r-lock) → Deploy.
 *  2) Edit code → کل این فایل را جای‌گذاری کن → Deploy.
 *  3) Settings → Variables and Secrets → این‌ها را بساز:
 *       APP_PW_HASH      (Secret)  هش PBKDF2 رمز برنامه  (دستور ساخت پایین‌تر)
 *       APP_PW_SALT      (Text)    نمک هش، هگز ۳۲ کاراکتر
 *       TICKET_SECRET    (Secret)  ۳۲ بایت تصادفی base64url
 *       DATA_KEY         (Secret)  ۳۲ بایت تصادفی base64url  ← همان کلید K
 *     اختیاری (برای مقابله با ربات‌ها و brute-force آنلاین):
 *       TURNSTILE_SITEKEY (Text)   از Cloudflare Turnstile
 *       TURNSTILE_SECRET  (Secret) از Cloudflare Turnstile
 *  4) در برنامه، آدرس ورکر را در تنظیمات بگذار: WORKER_URL = https://<name>.<sub>.workers.dev
 *
 * دستور ساخت مقادیر (روی سیستم خودت، پایتون):
 *   APP_PW_SALT:    python -c "import os,base64;print(os.urandom(16).hex())"
 *   APP_PW_HASH:    python -c "import hashlib;print(hashlib.pbkdf2_hmac('sha256',b'RAMZ_JADID',bytes.fromhex('SALT_HEX'),600000).hex())"
 *   TICKET_SECRET:  python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"
 *   DATA_KEY:       python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"
 */

const CONFIG = {
  // تعداد دور PBKDF2 برای هش رمز — باید با مقداری که در APP_PW_HASH استفاده کردی یکی باشد.
  DEFAULT_ITERS: 100000,

  // عمر «تیکت» یکبارمصرف (ثانیه). کوتاه بماند تا پنجرهٔ سوءاستفاده کم شود.
  TICKET_TTL: 60,

  // فقط callback محلی مجاز است تا از open-redirect جلوگیری شود.
  ALLOW_REDIRECT_HOSTS: ["127.0.0.1", "localhost"],

  // سقف درخواست /verify از هر IP در دقیقه (فقط اگر KV با نام RL بایند شده باشد).
  RL_PER_MIN: 20,
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    switch (url.pathname) {
      case "/":
      case "/unlock":
        return htmlPage(url, env);
      case "/verify":
        return handleVerify(request, env);
      case "/exchange":
        return handleExchange(request, env);
      case "/healthz":
        return new Response("ok");
      default:
        return json({ error: "not_found" }, 404);
    }
  },
};

// ------------------------------------------------------------
//  صفحهٔ رمز (RTL فارسی) — Turnstile اختیاری است
// ------------------------------------------------------------
function htmlPage(url, env) {
  const n = url.searchParams.get("n") || "";
  const s = url.searchParams.get("s") || "";
  const r = url.searchParams.get("r") || "";
  const sitekey = env.TURNSTILE_SITEKEY || "";
  const turnstile = sitekey
    ? `<div class="cf-turnstile" data-sitekey="${sitekey}" data-theme="light"></div>
       <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>`
    : "";

  const html = `<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>باز کردن Claude9RouterTool</title>
<style>
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       font-family:-apple-system,"Segoe UI",Tahoma,sans-serif;background:#f6f7f9;color:#1a1a1a}
  .card{background:#fff;border:1px solid #e2e2e2;border-radius:14px;padding:32px;max-width:380px;width:90%;
        box-shadow:0 8px 30px rgba(0,0,0,.06)}
  h1{font-size:1.25rem;margin:0 0 6px}
  p{color:#5a5a5a;font-size:.9rem;margin:0 0 20px;line-height:1.7}
  input{width:100%;padding:12px 14px;border:1px solid #cfd6dd;border-radius:9px;font-size:1rem;
        box-sizing:border-box;direction:ltr}
  button{margin-top:16px;width:100%;padding:12px;border:0;border-radius:9px;background:#2b6cb0;color:#fff;
         font-size:1rem;cursor:pointer}
  button:disabled{opacity:.6;cursor:default}
  .err{color:#c0392b;font-size:.88rem;margin-top:12px;min-height:1.2em}
</style></head><body>
<div class="card">
  <h1>باز کردن Claude9RouterTool</h1>
  <p>رمز برنامه را وارد کن تا کلید رمزگشایی برای همین نشست آزاد شود.</p>
  <input id="pw" type="password" autocomplete="current-password" placeholder="رمز برنامه" autofocus>
  ${turnstile}
  <button id="go">باز کردن</button>
  <div class="err" id="err"></div>
</div>
<script>
  const N=${JSON.stringify(n)}, S=${JSON.stringify(s)}, R=${JSON.stringify(r)};
  const btn=document.getElementById('go'), err=document.getElementById('err');
  btn.onclick=async()=>{
    err.textContent=''; btn.disabled=true;
    try{
      const ts=document.querySelector('[name="cf-turnstile-response"]');
      const body={pw:document.getElementById('pw').value, n:N, s:S, r:R,
                  token: ts ? ts.value : ""};
      const res=await fetch('/verify',{method:'POST',headers:{'Content-Type':'application/json'},
                                       body:JSON.stringify(body)});
      const j=await res.json();
      if(!res.ok){ err.textContent=j.error||'ناموفق'; btn.disabled=false; return; }
      location.href=j.redirect;
    }catch(e){ err.textContent='خطای شبکه'; btn.disabled=false; }
  };
  document.getElementById('pw').addEventListener('keydown',e=>{if(e.key==='Enter')btn.click();});
</script>
</body></html>`;
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

// ------------------------------------------------------------
//  /verify — بررسی رمز، سپس ساخت تیکت امضاشده و ریدایرکت به callback محلی
// ------------------------------------------------------------
async function handleVerify(request, env) {
  if (request.method !== "POST") return json({ error: "method" }, 405);

  const ip = request.headers.get("CF-Connecting-IP") || "0.0.0.0";
  if (env.RL && !(await rateLimitOk(env, ip))) return json({ error: "تلاش بیش از حد؛ کمی بعد امتحان کن." }, 429);

  let body;
  try { body = await request.json(); } catch { return json({ error: "bad_json" }, 400); }

  const pw = String(body.pw || "");
  const n = String(body.n || "");
  const s = String(body.s || "");
  const r = String(body.r || "");
  const token = String(body.token || "");

  if (!pw || !n || !s || !r) return json({ error: "پارامتر ناقص" }, 400);

  // فقط callback محلی مجاز است
  let redirectHost;
  try { redirectHost = new URL(r).hostname; } catch { return json({ error: "redirect نامعتبر" }, 400); }
  if (!CONFIG.ALLOW_REDIRECT_HOSTS.includes(redirectHost)) return json({ error: "redirect غیرمجاز" }, 400);

  // Turnstile (اختیاری)
  if (env.TURNSTILE_SECRET) {
    if (!(await turnstileOk(env, token, ip))) return json({ error: "تأیید امنیتی ناموفق" }, 403);
  }

  // بررسی رمز (مقایسهٔ زمان‌ثابت)
  const iters = parseInt(env.APP_PW_ITERS || CONFIG.DEFAULT_ITERS, 10);
  const salt = env.APP_PW_SALT || "";
  if (!env.APP_PW_HASH || !salt) return json({ error: "ورکر تنظیم نشده است" }, 500);
  const got = await pbkdf2Hex(pw, salt, iters);
  if (!timingSafeEqualStr(got, env.APP_PW_HASH)) return json({ error: "رمز نادرست است" }, 401);

  // تیکت امضاشده: payload = base64url(json{n,exp}) ، امضا = HMAC(TICKET_SECRET, payload)
  const exp = Math.floor(Date.now() / 1000) + CONFIG.TICKET_TTL;
  const payload = b64urlEncode(new TextEncoder().encode(JSON.stringify({ n, exp })));
  const sig = await hmacB64(env.TICKET_SECRET, payload);
  const ticket = payload + "." + sig;

  const sep = r.includes("?") ? "&" : "?";
  return json({ redirect: r + sep + "ticket=" + encodeURIComponent(ticket) + "&s=" + encodeURIComponent(s) });
}

// ------------------------------------------------------------
//  /exchange — برنامه تیکت را می‌فرستد و کلید K را می‌گیرد
// ------------------------------------------------------------
async function handleExchange(request, env) {
  if (request.method !== "POST") return json({ error: "method" }, 405);
  let body;
  try { body = await request.json(); } catch { return json({ error: "bad_json" }, 400); }

  const ticket = String(body.ticket || "");
  const nonce = String(body.n || "");
  const dot = ticket.lastIndexOf(".");
  if (dot < 0) return json({ error: "bad_ticket" }, 400);

  const payload = ticket.slice(0, dot);
  const sig = ticket.slice(dot + 1);
  const expect = await hmacB64(env.TICKET_SECRET, payload);
  if (!timingSafeEqualStr(sig, expect)) return json({ error: "bad_sig" }, 401);

  let data;
  try { data = JSON.parse(new TextDecoder().decode(b64urlDecode(payload))); }
  catch { return json({ error: "bad_payload" }, 400); }

  if (Math.floor(Date.now() / 1000) > (data.exp || 0)) return json({ error: "ticket منقضی شد" }, 401);
  if (nonce && data.n !== nonce) return json({ error: "nonce ناهم‌خوان" }, 401);
  if (!env.DATA_KEY) return json({ error: "DATA_KEY تنظیم نشده" }, 500);

  return json({ key: env.DATA_KEY });
}

// ------------------------------------------------------------
//  ابزارهای کمکی
// ------------------------------------------------------------
function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

async function pbkdf2Hex(password, saltHex, iterations) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password),
    { name: "PBKDF2" }, false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: hexToBytes(saltHex), iterations, hash: "SHA-256" }, key, 256);
  return bytesToHex(new Uint8Array(bits));
}

async function hmacB64(secretB64, message) {
  const key = await crypto.subtle.importKey("raw", b64urlDecode(secretB64),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return b64urlEncode(new Uint8Array(sig));
}

async function turnstileOk(env, token, ip) {
  if (!token) return false;
  const form = new FormData();
  form.append("secret", env.TURNSTILE_SECRET);
  form.append("response", token);
  form.append("remoteip", ip);
  const res = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify",
    { method: "POST", body: form });
  const out = await res.json().catch(() => ({}));
  return !!out.success;
}

async function rateLimitOk(env, ip) {
  const key = "rl:" + ip;
  const cur = parseInt((await env.RL.get(key)) || "0", 10);
  if (cur >= CONFIG.RL_PER_MIN) return false;
  await env.RL.put(key, String(cur + 1), { expirationTtl: 60 });
  return true;
}

function timingSafeEqualStr(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

function bytesToHex(bytes) {
  let s = "";
  for (const b of bytes) s += b.toString(16).padStart(2, "0");
  return s;
}
function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}
function b64urlEncode(bytes) {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function b64urlDecode(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}