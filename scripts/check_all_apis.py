import asyncio
import sys
import time
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from companion.config import settings

async def check_local_fastapi():
    print("=" * 65)
    print("1. LOCAL FASTAPI / MINI APP BACKEND (http://127.0.0.1:8080)")
    print("=" * 65)
    async with httpx.AsyncClient(timeout=5.0) as client:
        # Check /health
        try:
            t0 = time.perf_counter()
            res = await client.get("http://127.0.0.1:8080/health")
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"[+] /health -> {res.status_code} ({elapsed:.1f}ms): {res.json()}")
        except Exception as e:
            print(f"[-] /health failed: {e}")

        # Check /api/health
        try:
            t0 = time.perf_counter()
            res = await client.get("http://127.0.0.1:8080/api/health")
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"[+] /api/health -> {res.status_code} ({elapsed:.1f}ms): {res.json()}")
        except Exception as e:
            print(f"[-] /api/health failed: {e}")

async def check_telegram_bot_api():
    print("\n" + "=" * 65)
    print("2. TELEGRAM BOT API")
    print("=" * 65)
    token = settings.ADMIN_BOT_TOKEN
    if not token:
        print("[-] ADMIN_BOT_TOKEN is not set!")
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        # getMe
        try:
            t0 = time.perf_counter()
            res = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            elapsed = (time.perf_counter() - t0) * 1000
            data = res.json()
            if data.get("ok"):
                bot = data["result"]
                print(f"[+] Bot Online: @{bot.get('username')} (ID: {bot.get('id')}, Name: '{bot.get('first_name')}') [{elapsed:.1f}ms]")
            else:
                print(f"[-] Telegram getMe Error: {data}")
        except Exception as e:
            print(f"[-] Telegram Connection Error: {e}")

        # getWebhookInfo
        try:
            res_wh = await client.get(f"https://api.telegram.org/bot{token}/getWebhookInfo")
            wh_data = res_wh.json()
            if wh_data.get("ok"):
                wh = wh_data["result"]
                wh_url = wh.get("url")
                if not wh_url:
                    print(f"[+] Polling Mode: No active webhook (Long Polling in bot engine is ACTIVE)")
                else:
                    print(f"[!] Webhook active at: {wh_url} (pending updates: {wh.get('pending_update_count')})")
        except Exception as e:
            print(f"[-] Telegram Webhook Info Error: {e}")

async def check_elevenlabs_api():
    print("\n" + "=" * 65)
    print("3. ELEVENLABS TTS API")
    print("=" * 65)
    key = settings.ELEVENLABS_API_KEY
    if not key:
        print("[-] ELEVENLABS_API_KEY is not set!")
        return
    headers = {"xi-api-key": key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Check Subscription / Quota
        try:
            t0 = time.perf_counter()
            res = await client.get("https://api.elevenlabs.io/v1/user/subscription", headers=headers)
            elapsed = (time.perf_counter() - t0) * 1000
            if res.status_code == 200:
                sub = res.json()
                tier = sub.get("tier", "unknown")
                used = sub.get("character_count", 0)
                limit = sub.get("character_limit", 0)
                rem = limit - used
                status = sub.get("status", "unknown")
                print(f"[+] Subscription: {tier.upper()} | Status: {status.upper()} [{elapsed:.1f}ms]")
                print(f"[+] Quota: {used:,} used / {limit:,} limit (Remaining: {rem:,} chars)")
            else:
                print(f"[-] Subscription check failed: {res.status_code} {res.text}")
        except Exception as e:
            print(f"[-] ElevenLabs Connection Error: {e}")

        # Check Active Voice
        voice_id = settings.ELEVENLABS_VOICE_ID or "WmNeiBsHQpj7dNcrBhAX"
        try:
            t0 = time.perf_counter()
            v_res = await client.get(f"https://api.elevenlabs.io/v1/voices/{voice_id}", headers=headers)
            elapsed = (time.perf_counter() - t0) * 1000
            if v_res.status_code == 200:
                v_data = v_res.json()
                print(f"[+] Active Voice: '{v_data.get('name')}' (ID: {voice_id}) | Category: {v_data.get('category')} [{elapsed:.1f}ms]")
            else:
                print(f"[-] Voice ID {voice_id} check: {v_res.status_code} {v_res.text}")
        except Exception as e:
            print(f"[-] Voice Check Error: {e}")

async def check_gemini_api():
    print("\n" + "=" * 65)
    print("4. GOOGLE GEMINI AI API")
    print("=" * 65)
    key = settings.GEMINI_API_KEY
    if not key:
        print("[-] GEMINI_API_KEY is not set!")
        return
    print(f"[+] API Key Configured: {key[:8]}...{key[-6:]} (Length: {len(key)})")
    
    from companion.ai.gemini_client import GeminiClient
    try:
        t0 = time.perf_counter()
        reply = await GeminiClient.generate_content(
            contents="Скажи 'Система работает нормально' и сегодняшнюю дату без лишних слов.",
            max_output_tokens=60,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"[+] Gemini Generation SUCCESS [{elapsed:.1f}ms]!")
        print(f"[+] Model response: «{reply.strip()}»")
    except Exception as e:
        print(f"[-] Gemini Generation Error: {type(e).__name__}: {e}")

async def check_database():
    print("\n" + "=" * 65)
    print("5. SQLITE DATABASE (companion.db)")
    print("=" * 65)
    try:
        from sqlalchemy import text
        from companion.models.db import async_session_maker
        t0 = time.perf_counter()
        async with async_session_maker() as session:
            res_u = (await session.execute(text("SELECT count(*) FROM users"))).scalar()
            res_a = (await session.execute(text("SELECT count(*) FROM account_sessions"))).scalar()
            res_c = (await session.execute(text("SELECT count(*) FROM contacts"))).scalar()
            res_m = (await session.execute(text("SELECT count(*) FROM contact_messages"))).scalar()
            res_p = (await session.execute(text("SELECT count(*) FROM personas"))).scalar()
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"[+] Database Connection: OK [{elapsed:.1f}ms]")
            print(f"[+] Users: {res_u} | Account Sessions: {res_a} | Personas: {res_p}")
            print(f"[+] Contacts: {res_c} | Contact Messages Stored: {res_m}")
    except Exception as e:
        print(f"[-] Database Error: {e}")

async def main():
    await check_local_fastapi()
    await check_telegram_bot_api()
    await check_elevenlabs_api()
    await check_gemini_api()
    await check_database()
    print("\n" + "=" * 65)
    print("ALL API CHECKS FINISHED")
    print("=" * 65)

if __name__ == "__main__":
    asyncio.run(main())
