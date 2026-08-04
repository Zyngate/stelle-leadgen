"""
Uses Google Gemini's image generation model to produce a custom ad image
that actually reflects the business and the campaign goal - instead of
just reusing whatever og:image (usually the site's logo) happened to be
scraped off the page.

Gemini's image model (gemini-2.5-flash-image, aka "nano banana") can
render legible on-image text directly in the image itself, which is the
main reason it's used here over just generating a generic stock photo:
the headline/offer actually shows up baked into the creative, not as a
caption bolted on afterward.

Auth is a single static API key, same simple pattern as Groq/Mailchimp -
no OAuth involved. Get one at https://aistudio.google.com/apikey

Model names for Gemini's generation capabilities shift somewhat often -
confirm the current recommended image model at ai.google.dev before
trusting the hardcoded default long-term.
"""

import os
import base64
import uuid
import httpx

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Where generated images are written to disk so they have a real,
# Meta-fetchable URL. main.py mounts this directory as static files.
GENERATED_IMAGE_DIR = "static/generated"


class GeminiError(Exception):
    pass


def _get_config() -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiError("GEMINI_API_KEY not set in environment")
    image_model = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    return {"api_key": api_key, "image_model": image_model}


def _build_prompt(business_description: str, suggested_industry: str, goal: str,
                   headline: str, offer: str = "", extra_instructions: str = "") -> str:
    """
    Builds one prompt from everything already known about the business
    and the campaign, so the image reflects both - not a generic stock
    photo, and not just a reused site logo.
    """
    prompt = (
        f"Create a polished, professional advertising image for a "
        f"{suggested_industry} business. Business: {business_description}. "
        f"Campaign goal: {goal}. "
        f"Render the following headline as clean, legible on-image text, "
        f"integrated naturally into the design (not a caption bar bolted "
        f"on top): \"{headline}\". "
    )
    if offer:
        prompt += f"Also feature this offer prominently in the image: {offer}. "
    if extra_instructions:
        prompt += f"Additional direction: {extra_instructions}. "
    prompt += (
        "Bright, modern, suitable for a small social media ad banner. "
        "No placeholder text, no watermarks, no unrelated logos."
    )
    return prompt


async def generate_ad_image(business_description: str, suggested_industry: str,
                             goal: str, headline: str, offer: str = "",
                             extra_instructions: str = "") -> bytes:
    """
    Returns the raw PNG bytes of a generated image. Unlike Grok/xAI,
    Gemini returns image data inline in the response (base64), not a
    hosted URL - so callers need save_generated_image() below to turn
    this into something Meta can actually fetch.
    """
    config = _get_config()
    prompt = _build_prompt(business_description, suggested_industry, goal,
                            headline, offer, extra_instructions)

    url = f"{GEMINI_BASE_URL}/models/{config['image_model']}:generateContent"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": config["api_key"],
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers, timeout=60.0)

    if resp.status_code != 200:
        raise GeminiError(f"Gemini API error {resp.status_code}: {resp.text}")

    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        raise GeminiError(f"Gemini response missing expected structure: {data}")

    for part in parts:
        inline_data = part.get("inlineData") or part.get("inline_data")
        if inline_data and inline_data.get("data"):
            return base64.b64decode(inline_data["data"])

    raise GeminiError(f"Gemini response contained no image data: {data}")


def save_generated_image(image_bytes: bytes, base_url: str, extension: str = "png") -> str:
    """
    Writes the generated image to disk under static/generated/ and
    returns a full URL Meta (or anything else) can fetch it from.
    Filename is a fresh uuid every call, so regenerating never collides
    with or silently overwrites a previous attempt - old ones just sit
    there unused. Clean the folder out periodically in a real deployment.

    extension should match the actual image format being written (e.g.
    "jpg" for Pollinations' typical output) - StaticFiles serves the
    Content-Type header based on the filename extension, so a mismatch
    here (bytes vs. claimed extension) can make some strict fetchers,
    including Meta's ad creative ingestion, reject the file.
    """
    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{extension}"
    path = os.path.join(GENERATED_IMAGE_DIR, filename)
    with open(path, "wb") as f:
        f.write(image_bytes)
    return f"{base_url}/static/generated/{filename}"