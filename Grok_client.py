"""
Uses Grok (xAI) to generate ad headline/description copy AND a matching
ad image, so nothing has to be typed or sourced by hand.

Grok's API is OpenAI-SDK-compatible, so auth is a single static API key -
same simple pattern as Mailchimp, no OAuth involved.

Get a key at: https://console.x.ai (Console -> API Keys -> Create Key)
Model names change over time - check console.x.ai for the current
recommended text and image models rather than trusting a hardcoded
default long-term.
"""

import os
import json
import httpx

GROQ_BASE_URL ="https://api.groq.com/openai/v1"


class GrokError(Exception):
    pass


def _get_config() -> dict:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise GrokError("GROQ_API_KEY not set in environment")
    text_model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    image_model = os.environ.get("GROK_IMAGE_MODEL", "grok-2-image")  # confirm current image model name at console.x.ai
    return {"api_key": api_key, "text_model": text_model, "image_model": image_model}


async def generate_ad_copy(business_description: str, offer: str = "") -> dict:
    """
    Given a plain description of the business (and optionally a specific
    offer/promo), returns a dict with "headline" and "description" ready
    to drop straight into a campaign (mock or real).
    """
    config = _get_config()

    prompt = (
        "Write a short display ad for the following business. "
        "Return ONLY valid JSON with two keys: 'headline' (under 8 words) "
        "and 'description' (under 20 words). No preamble, no markdown fences.\n\n"
        f"Business: {business_description}\n"
    )
    if offer:
        prompt += f"Active offer to highlight: {offer}\n"

    payload = {
        "model": config["text_model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GROQ_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=20.0,
        )

    if resp.status_code != 200:
        raise GrokError(f"Grok API error {resp.status_code}: {resp.text}")

    data = resp.json()
    raw_text = data["choices"][0]["message"]["content"].strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        raise GrokError(f"Grok returned non-JSON output: {raw_text}")

    if "headline" not in parsed or "description" not in parsed:
        raise GrokError(f"Grok response missing expected keys: {parsed}")

    return {"headline": parsed["headline"], "description": parsed["description"]}


async def generate_ad_image(business_description: str, offer: str = "") -> str:
    """
    Generates a single ad image and returns a URL to it. The image is
    hosted temporarily by xAI - download/re-host it yourself if you need
    it to persist past that URL's expiry window.
    """
    config = _get_config()

    prompt = (
        f"A clean, professional advertising photo for: {business_description}. "
        "Bright, appealing, product-focused, suitable for a small banner ad. "
        "No text or words in the image."
    )
    if offer:
        prompt += f" Related to this offer: {offer}."

    payload = {
        "model": config["image_model"],
        "prompt": prompt,
        "n": 1,
    }
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GROQ_BASE_URL}/images/generations",
            json=payload,
            headers=headers,
            timeout=40.0,
        )

    if resp.status_code != 200:
        raise GrokError(f"Grok image API error {resp.status_code}: {resp.text}")

    data = resp.json()
    try:
        return data["data"][0]["url"]
    except (KeyError, IndexError):
        raise GrokError(f"Grok image response missing expected data: {data}")


async def analyze_business_from_website(page_context: str) -> dict:
    """
    Given scraped title/meta-description/body-excerpt signals pulled from
    a business's website, infers what the business does. This is the
    seed used later for ad copy generation - always show the result back
    to the user for confirmation or edit before it feeds anything else,
    since a scrape + LLM guess can be wrong (especially for JS-heavy
    sites with little static text).
    """
    config = _get_config()

    prompt = (
        "Based on the following website signals, infer what this business "
        "does. Return ONLY valid JSON with two keys: 'business_description' "
        "(one sentence, under 25 words) and 'suggested_industry' (2-3 words). "
        "No preamble, no markdown fences.\n\n"
        f"{page_context}\n"
    )

    payload = {
        "model": config["text_model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
    }
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GROQ_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=20.0,
        )

    if resp.status_code != 200:
        raise GrokError(f"Grok API error {resp.status_code}: {resp.text}")

    data = resp.json()
    raw_text = data["choices"][0]["message"]["content"].strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        raise GrokError(f"Grok returned non-JSON output: {raw_text}")

    if "business_description" not in parsed or "suggested_industry" not in parsed:
        raise GrokError(f"Grok response missing expected keys: {parsed}")

    return parsed


async def generate_ad_creative(business_description: str, offer: str = "") -> dict:
    """
    Convenience wrapper - generates copy and image together and returns
    everything needed to create a campaign in one call.
    """
    copy = await generate_ad_copy(business_description, offer)
    image_url = await generate_ad_image(business_description, offer)
    return {
        "headline": copy["headline"],
        "description": copy["description"],
        "image_url": image_url,
    }