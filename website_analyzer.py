"""
Fetches a business's website and extracts what the ad-content step needs
downstream: what the business does, roughly what industry it's in. This
is stage 2 of onboarding (connect Facebook -> ANALYZE WEBSITE ->
questionnaire -> ai ad content generation -> budget -> launch).

Scraping + an LLM guess can be wrong, especially for JS-heavy sites with
little static content - always show `business_description` back to the
user for confirmation/edit before it's used to generate ad copy. Don't
chain straight into content generation without a human checkpoint here.
"""

from bs4 import BeautifulSoup
import httpx

import Grok_client


class WebsiteAnalysisError(Exception):
    pass


async def _fetch_html(url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(
            url, timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; StelleBot/1.0)"},
        )
    if resp.status_code != 200:
        raise WebsiteAnalysisError(f"Could not fetch {url}: HTTP {resp.status_code}")
    return resp.text


def _extract_signals(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    def meta(name=None, prop=None):
        tag = soup.find("meta", attrs={"name": name} if name else {"property": prop})
        return tag["content"].strip() if tag and tag.get("content") else ""

    description = meta(name="description") or meta(prop="og:description")
    og_title = meta(prop="og:title")
    og_image = meta(prop="og:image")

    # A short slice of visible body text as extra grounding for the LLM -
    # not a full scrape, just enough context to infer from.
    body_excerpt = " ".join(soup.stripped_strings)[:1500]

    return {
        "title": title or og_title,
        "meta_description": description,
        "og_image": og_image,
        "body_excerpt": body_excerpt,
    }


async def analyze_website(url: str) -> dict:
    """
    Returns:
      business_description - plain-language summary, shown to the user for confirmation
      suggested_industry   - short category label
      og_image             - a candidate image already on the site, in case it's usable as ad creative
      source_signals       - the raw extracted title/meta/body-excerpt, for transparency/debugging
    """
    html = await _fetch_html(url)
    signals = _extract_signals(html)

    context = (
        f"Page title: {signals['title']}\n"
        f"Meta description: {signals['meta_description']}\n"
        f"Visible text excerpt: {signals['body_excerpt']}\n"
    )

    analysis = await Grok_client.analyze_business_from_website(context)

    return {
        "business_description": analysis["business_description"],
        "suggested_industry": analysis["suggested_industry"],
        "og_image": signals["og_image"],
        "source_signals": signals,
    }