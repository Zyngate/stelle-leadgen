"""
Uses Pollinations.ai's free image generation endpoint to produce the ad
background, then composites the actual headline/offer on top as real
text using Pillow - no API key, no billing account for either step.

Why compose the text instead of asking the model to render it: every
current image-generation model (this one, Gemini, Grok/xAI, all of
them) is fundamentally unreliable at spelling out real words - it's
approximating letterforms statistically, not actually writing text, so
headlines routinely come out as garbled near-English ("oicblial",
"meIHcla"). Asking for on-image text was the previous approach here and
it doesn't hold up. Instead, this generates a clean background image
and draws the real headline/offer on top afterward - the text is
always spelled correctly because it isn't AI-guessed, it's drawn.

Font handling: tries a short list of common system font paths (DejaVu
on most Linux boxes, Liberation as a second try, Arial on macOS/Windows)
and falls back to Pillow's built-in default font if none are found -
so this doesn't hard-fail on a machine without those fonts installed,
it just looks plainer.
"""

import asyncio
import io
import random
import urllib.parse
import httpx
from PIL import Image, ImageDraw, ImageFont

from gemini_client import save_generated_image  # shared helper: bytes -> static/generated/ -> servable URL

POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt"

_BOLD_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
]
_REGULAR_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]


class PollinationsError(Exception):
    pass


def _load_font(candidates: list, size: int):
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()  # small bitmap font, but never fails


def _build_prompt(business_description: str, suggested_industry: str, goal: str,
                   offer: str = "", extra_instructions: str = "") -> str:
    """
    Deliberately does NOT ask the model to render the headline as
    on-image text (see module docstring for why) - just asks for a
    clean background with open space near the bottom, where the real
    text gets composited afterward.

    Steers toward a plain photographic style and away from "sticker
    collage" / "3D mockup" compositions specifically - those styles
    correlate heavily in training data with images that have fake
    lettering baked into the scene (props, badges, signage), so even a
    strong "no text" instruction gets overridden by that stronger
    stylistic pull. A plain photo of a real scene has no such pull.
    """
    prompt = (
        f"A realistic photograph, not an illustration or 3D render, for a "
        f"{suggested_industry} business advertisement. Business: "
        f"{business_description}. Campaign goal: {goal}. Style: clean "
        f"editorial photography, natural lighting, shallow depth of field, "
        f"like a professional stock photo. Leave the bottom third of the "
        f"frame relatively plain/uncluttered so text can be added on top "
        f"afterward. Absolutely no text, letters, words, numbers, signage, "
        f"typography, stickers, badges, or logos anywhere in the image - "
        f"if any object would naturally have writing on it (a sign, a "
        f"screen, a label, a badge), simplify or omit that object instead."
    )
    if offer:
        prompt += f" Visually hint at this offer without writing it out anywhere: {offer}."
    if extra_instructions:
        prompt += f" Additional direction: {extra_instructions}."
    return prompt


async def _fetch_background(prompt: str) -> bytes:
    encoded_prompt = urllib.parse.quote(prompt, safe="")
    url = f"{POLLINATIONS_BASE_URL}/{encoded_prompt}"
    params = {
        "width": 1024,
        "height": 1024,
        "nologo": "true",
        "seed": random.randint(0, 999_999_999),
    }
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url, params=params, timeout=60.0)

    if resp.status_code != 200:
        raise PollinationsError(f"Pollinations error {resp.status_code}: {resp.text[:300]}")

    content_type = resp.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise PollinationsError(f"Pollinations did not return an image (content-type: {content_type})")

    return resp.content


def _wrap_to_width(draw, text: str, font, max_width: int) -> list:
    """Greedy word-wrap using actual measured text width, not a guessed character count."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _compose_text_overlay(background_bytes: bytes, headline: str, offer: str = "") -> bytes:
    """
    Draws a translucent dark bar across the bottom of the image and
    renders the headline (bold, larger) and offer (regular, smaller) on
    top of it as real, correctly-spelled text.
    """
    img = Image.open(io.BytesIO(background_bytes)).convert("RGB")
    width, height = img.size

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    headline_font = _load_font(_BOLD_FONT_CANDIDATES, size=int(width * 0.06))
    offer_font = _load_font(_REGULAR_FONT_CANDIDATES, size=int(width * 0.035))

    padding = int(width * 0.05)
    max_text_width = width - (padding * 2)

    headline_lines = _wrap_to_width(draw, headline, headline_font, max_text_width)
    offer_lines = _wrap_to_width(draw, offer, offer_font, max_text_width) if offer else []

    line_height_headline = int(width * 0.075)
    line_height_offer = int(width * 0.045)
    bar_height = (
        padding * 2
        + len(headline_lines) * line_height_headline
        + (len(offer_lines) * line_height_offer if offer_lines else 0)
    )
    bar_height = min(bar_height, int(height * 0.45))

    draw.rectangle(
        [(0, height - bar_height), (width, height)],
        fill=(15, 15, 20, 190),
    )

    text_y = height - bar_height + padding
    for line in headline_lines:
        draw.text((padding, text_y), line, font=headline_font, fill=(255, 255, 255, 255))
        text_y += line_height_headline

    for line in offer_lines:
        draw.text((padding, text_y), line, font=offer_font, fill=(240, 210, 150, 255))
        text_y += line_height_offer

    composed = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    buf = io.BytesIO()
    composed.save(buf, format="PNG")
    return buf.getvalue()


async def generate_ad_image(business_description: str, suggested_industry: str,
                             goal: str, headline: str, offer: str = "",
                             extra_instructions: str = "") -> tuple:
    """
    Returns (image_bytes, extension). Generates a clean background via
    Pollinations, then composites the real headline/offer on top with
    Pillow - always returns PNG since the compositing step re-encodes
    the image regardless of what format Pollinations sent back.
    """
    prompt = _build_prompt(business_description, suggested_industry, goal, offer, extra_instructions)
    background_bytes = await _fetch_background(prompt)
    final_bytes = await asyncio.to_thread(_compose_text_overlay, background_bytes, headline, offer)
    return final_bytes, "png"