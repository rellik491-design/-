import logging
from typing import Optional, Dict, Any, Tuple
import httpx
from google import genai
from google.genai import types
from companion.config import settings

logger = logging.getLogger(__name__)


class VisionService:
    """
    Multimodal Vision Service for analyzing photos, stickers, GIFs (animations),
    and contextual replies from Telegram interlocutors.
    """

    @classmethod
    async def download_file_bytes(cls, bot_token: str, file_id: str) -> Optional[bytes]:
        """Downloads file from Telegram servers using getFile."""
        if not bot_token or bot_token in ("mock_token", "test_token") or not file_id:
            return None
        url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.get(url)
                data = res.json()
                if data.get("ok"):
                    file_path = data["result"]["file_path"]
                    download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
                    d_res = await client.get(download_url)
                    if d_res.status_code == 200:
                        return d_res.content
        except Exception as e:
            logger.warning("Error downloading file %s from Telegram: %s", file_id, e)
        return None

    @classmethod
    async def describe_image_bytes(
        cls,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        media_type: str = "photo",
    ) -> Optional[str]:
        """
        Uses Gemini Vision API to extract a human-like description of what is depicted in the image.
        """
        api_key = settings.GEMINI_API_KEY
        if not api_key or not image_bytes:
            return None

        prompt = (
            "Ты — модуль визуального распознавания для диалогового AI-компаньона (молодой девушки) в Telegram.\n"
            "Опиши, что изображено на картинке/фото/стикере/меме кратко и емко (1-2 предложения) на русском языке.\n"
            "- Если это стикер, мем, сцена из фильма или известный актер/персонаж "
            "(например, Леонардо Ди Каприо с бокалом из Великого Гэтсби или Волка с Уолл-стрит, Райан Гослинг, Киану Ривз, мемный кот и т.д.) — "
            "ОБЯЗАТЕЛЬНО четко назови имя актера/персонажа, фильм/мем и его действие или эмоцию!\n"
            "- Если это селфи или фото мужчины — опиши внешность, обстановку (авто, спортзал, ресторан, улица, квартира) и позу.\n"
            "- Если это фото предмета, еды, кофе, машины, домашнего питомца, природы, вида из окна — назови конкретные интересные детали.\n"
            "Пиши только суть происходящего на картинке, без лишних вводных слов."
        )

        try:
            from companion.ai.gemini_client import GeminiClient
            part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            desc = await GeminiClient.generate_content(
                contents=[part, prompt],
                temperature=0.3,
                max_output_tokens=200,
                api_key=api_key,
            )
            if desc:
                clean_desc = desc.strip().replace("\n", " ")
                logger.info("Vision analysis succeeded: %s", clean_desc[:100])
                return clean_desc
        except Exception as e:
            logger.error("Vision API description error: %s", e)
        return None

    @classmethod
    async def extract_media_context(
        cls,
        bot_token: str,
        message_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Extracts full visual, media, and reply context from an incoming Telegram message.
        Returns:
            {
                "has_media": bool,
                "media_type": str, # "photo", "sticker", "animation", "none"
                "description": str,
                "text_augmented": str,
                "reply_to_text": Optional[str],
            }
        """
        raw_text = message_data.get("text", "").strip()
        caption = message_data.get("caption", "").strip()

        # 1. Check for Sticker
        sticker = message_data.get("sticker")
        if sticker:
            emoji = sticker.get("emoji", "")
            set_name = sticker.get("set_name", "")
            thumb = sticker.get("thumbnail")
            file_id = (thumb.get("file_id") if thumb else None) or sticker.get("file_id")
            
            desc = None
            if file_id and bot_token:
                img_bytes = await cls.download_file_bytes(bot_token, file_id)
                if img_bytes:
                    # Sticker thumbs are usually webp or jpeg
                    mime = "image/webp" if sticker.get("is_animated") or sticker.get("is_video") else "image/webp"
                    desc = await cls.describe_image_bytes(img_bytes, mime_type=mime, media_type="sticker")
                    if not desc:
                        # Retry with jpeg mime if webp failed
                        desc = await cls.describe_image_bytes(img_bytes, mime_type="image/jpeg", media_type="sticker")

            sticker_label = f"стикер/мем {emoji}".strip()
            if desc:
                text_content = f"[Собеседник прислал {sticker_label}: {desc}]"
            elif set_name or emoji:
                text_content = f"[Собеседник прислал {sticker_label}{f' из набора {set_name}' if set_name else ''}]"
            else:
                text_content = "[Собеседник прислал стикер]"

            if caption:
                text_content += f" {caption}"

            reply_prefix = await cls._extract_reply_context(bot_token, message_data.get("reply_to_message"))
            return {
                "has_media": True,
                "media_type": "sticker",
                "description": desc or emoji or set_name or "стикер",
                "text_augmented": f"{reply_prefix}{text_content}".strip(),
            }

        # 2. Check for Photo
        photo_list = message_data.get("photo")
        if photo_list and isinstance(photo_list, list) and len(photo_list) > 0:
            largest = photo_list[-1]
            file_id = largest.get("file_id")
            desc = None
            if file_id and bot_token:
                img_bytes = await cls.download_file_bytes(bot_token, file_id)
                if img_bytes:
                    desc = await cls.describe_image_bytes(img_bytes, mime_type="image/jpeg", media_type="photo")

            if desc:
                text_content = f"[Собеседник прислал фото: {desc}]"
            else:
                text_content = "[Собеседник прислал фото]"

            if caption:
                text_content += f" {caption}"

            reply_prefix = await cls._extract_reply_context(bot_token, message_data.get("reply_to_message"))
            return {
                "has_media": True,
                "media_type": "photo",
                "description": desc or "фото",
                "text_augmented": f"{reply_prefix}{text_content}".strip(),
            }

        # 3. Check for Animation (GIF)
        animation = message_data.get("animation")
        if animation:
            thumb = animation.get("thumbnail")
            file_id = (thumb.get("file_id") if thumb else None) or animation.get("file_id")
            desc = None
            if file_id and bot_token:
                img_bytes = await cls.download_file_bytes(bot_token, file_id)
                if img_bytes:
                    desc = await cls.describe_image_bytes(img_bytes, mime_type="image/jpeg", media_type="gif")

            if desc:
                text_content = f"[Собеседник прислал GIF-анимацию/мем: {desc}]"
            else:
                text_content = "[Собеседник прислал GIF-анимацию/мем]"

            if caption:
                text_content += f" {caption}"

            reply_prefix = await cls._extract_reply_context(bot_token, message_data.get("reply_to_message"))
            return {
                "has_media": True,
                "media_type": "animation",
                "description": desc or "гифка",
                "text_augmented": f"{reply_prefix}{text_content}".strip(),
            }

        # 4. Check for reply to previous message (e.g. reply to sticker "а ты знаешь кто это?")
        reply_prefix = await cls._extract_reply_context(bot_token, message_data.get("reply_to_message"))
        combined_text = f"{reply_prefix}{raw_text or caption}".strip()

        return {
            "has_media": False,
            "media_type": "none",
            "description": "",
            "text_augmented": combined_text,
        }

    @classmethod
    async def _extract_reply_context(cls, bot_token: str, reply_msg: Optional[Dict[str, Any]]) -> str:
        """Helper to extract what message the user is replying to."""
        if not reply_msg:
            return ""

        # Check if replied to sticker
        if "sticker" in reply_msg:
            st = reply_msg["sticker"]
            emoji = st.get("emoji", "")
            set_name = st.get("set_name", "")
            thumb = st.get("thumbnail")
            file_id = (thumb.get("file_id") if thumb else None) or st.get("file_id")
            desc = None
            if file_id and bot_token:
                img_bytes = await cls.download_file_bytes(bot_token, file_id)
                if img_bytes:
                    desc = await cls.describe_image_bytes(img_bytes, mime_type="image/webp", media_type="sticker")
                    if not desc:
                        desc = await cls.describe_image_bytes(img_bytes, mime_type="image/jpeg", media_type="sticker")
            if desc:
                return f"[В ответ на стикер/мем {emoji}: «{desc}»] "
            return f"[В ответ на стикер {emoji} {f'из {set_name}' if set_name else ''}] "

        # Check if replied to photo
        if "photo" in reply_msg:
            photos = reply_msg["photo"]
            if photos and isinstance(photos, list):
                largest = photos[-1]
                file_id = largest.get("file_id")
                desc = None
                if file_id and bot_token:
                    img_bytes = await cls.download_file_bytes(bot_token, file_id)
                    if img_bytes:
                        desc = await cls.describe_image_bytes(img_bytes, mime_type="image/jpeg", media_type="photo")
                if desc:
                    return f"[В ответ на фото: «{desc}»] "
                return "[В ответ на фото] "

        # Check if replied to voice
        if "voice" in reply_msg or "audio" in reply_msg:
            return "[В ответ на голосовое сообщение] "

        # Check if replied to text
        reply_text = reply_msg.get("text", "").strip()
        if reply_text:
            short = reply_text[:70] + ("..." if len(reply_text) > 70 else "")
            return f"[В ответ на: «{short}»] "

        return ""
