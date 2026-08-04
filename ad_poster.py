"""
A minimal, self-hosted "ad network" for demo purposes.

This is NOT a real ad network - it doesn't get your ad shown on other
people's real websites. What it DOES do is simulate the entire loop so
you can demo it end to end today:

  1. Create a campaign (headline, image, destination link, budget)
  2. Get back a small embeddable HTML snippet - the "ad unit"
  3. Drop that snippet onto any page (a mock "publisher site" is
     included) to simulate the ad appearing on someone else's website
  4. When someone clicks it, we log the click and redirect them to
     your real landing page - which then feeds into the existing
     /leads endpoint and Mailchimp

Storage is in-memory (a Python dict) - fine for a demo, resets when the
server restarts. Swap this for a real database before this goes anywhere
near production.
"""

import uuid
from datetime import datetime, timezone

# In-memory "database" - campaign_id -> campaign data
_CAMPAIGNS: dict[str, dict] = {}


class CampaignNotFound(Exception):
    pass


def create_campaign(name: str, headline: str, description: str,
                     image_url: str, destination_url: str,
                     daily_budget: float) -> dict:
    campaign_id = str(uuid.uuid4())[:8]
    _CAMPAIGNS[campaign_id] = {
        "id": campaign_id,
        "name": name,
        "headline": headline,
        "description": description,
        "image_url": image_url,
        "destination_url": destination_url,
        "daily_budget": daily_budget,
        "impressions": 0,
        "clicks": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return _CAMPAIGNS[campaign_id]


def get_campaign(campaign_id: str) -> dict:
    if campaign_id not in _CAMPAIGNS:
        raise CampaignNotFound(campaign_id)
    return _CAMPAIGNS[campaign_id]


def record_impression(campaign_id: str) -> None:
    if campaign_id in _CAMPAIGNS:
        _CAMPAIGNS[campaign_id]["impressions"] += 1


def record_click(campaign_id: str) -> str:
    """Logs the click and returns the destination URL to redirect to."""
    campaign = get_campaign(campaign_id)
    campaign["clicks"] += 1
    return campaign["destination_url"]


def render_ad_snippet(campaign_id: str, base_url: str) -> str:
    """
    Returns a small, embeddable HTML block - this is the "ad" as it
    would appear on a publisher's website. Every view hits our
    /ad/{id}/impression endpoint (counted as an impression), and the
    whole block links through /ad/{id}/click (counted as a click, then
    redirected to the real destination).
    """
    campaign = get_campaign(campaign_id)
    return f"""
<div style="border:1px solid #ddd;border-radius:8px;max-width:300px;
            font-family:Arial,sans-serif;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.08);">
  <a href="{base_url}/ad/{campaign_id}/click" style="text-decoration:none;color:inherit;">
    <img src="{campaign['image_url']}" style="width:100%;display:block;" alt="{campaign['headline']}">
    <div style="padding:12px;">
      <div style="font-weight:bold;font-size:15px;">{campaign['headline']}</div>
      <div style="font-size:13px;color:#555;margin-top:4px;">{campaign['description']}</div>
      <div style="font-size:11px;color:#999;margin-top:8px;">Sponsored</div>
    </div>
  </a>
</div>
<img src="{base_url}/ad/{campaign_id}/impression" width="1" height="1" style="display:none;">
"""


def list_campaigns() -> list[dict]:
    return list(_CAMPAIGNS.values())
