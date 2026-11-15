import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath("src"))

from companion.services.voice_service import VoiceService

async def main():
    test_phrase = (
        "Слушай, я сейчас кофе в кафе пью, тут такой трек классный играет. "
        "Скинь мне потом гс в тг пожалуйста, поболтаем как освободишься."
    )
    print("Generating A/B/C voice variants for text:")
    print(f"«{test_phrase}»\n")

    res = await VoiceService.generate_ab_test_samples(
        text=test_phrase,
        output_dir="assets/voice_pack/ab_test",
    )

    print("\n--- Audio Generation Finished Successfully ---")
    for variant, path in res.items():
        file_size = Path(path).stat().st_size if Path(path).exists() else 0
        print(f"[{variant}] -> {path} ({file_size} bytes)")

if __name__ == "__main__":
    asyncio.run(main())
