import asyncio
import sys
import time
from pathlib import Path
from google import genai
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from companion.ai.gemini_client import GeminiClient

async def check_key(label: str, api_key: str):
    masked = api_key[:10] + "..." + api_key[-6:] if len(api_key) > 16 else api_key
    print(f"Testing {label} | Key: {masked}")
    t0 = time.perf_counter()
    try:
        client = genai.Client(api_key=api_key)
        resp = await client.aio.models.generate_content(
            model="gemini-flash-lite-latest",
            contents="Скажи одно слово: Работает",
            config=types.GenerateContentConfig(max_output_tokens=20)
        )
        elapsed = (time.perf_counter() - t0) * 1000
        reply = resp.text.strip() if resp.text else "(empty)"
        print(f"  [+] STATUS: 200 OK | Latency: {elapsed:.1f}ms | Reply: «{reply}»")
        return True, elapsed, reply
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  [-] STATUS: FAILED ({type(e).__name__}) | Latency: {elapsed:.1f}ms | Error: {e}")
        return False, elapsed, str(e)

async def main():
    print("=" * 65)
    print("DYNAMIC GEMINI POOL VERIFICATION (From .env GEMINI_API_KEYS)")
    print("=" * 65)
    keys = GeminiClient.get_api_keys()
    if not keys:
        print("[-] No GEMINI_API_KEYS found in configuration.")
        return

    print(f"Found {len(keys)} unique key(s) in pool.\n")
    valid = 0
    for i, k in enumerate(keys, 1):
        ok, _, _ = await check_key(f"Key #{i}", k)
        if ok:
            valid += 1
        await asyncio.sleep(0.5)

    print("\n" + "=" * 65)
    print(f"RESULT: {valid}/{len(keys)} keys returned 200 OK and are active.")
    print("=" * 65)

if __name__ == "__main__":
    asyncio.run(main())
