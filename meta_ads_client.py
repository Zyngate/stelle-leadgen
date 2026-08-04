"""
Client for the Meta Marketing API (Facebook/Instagram ads) - the real
integration that gets an ad shown to actual people and generates a click
back to your landing page.

This is a genuinely heavier integration than Mailchimp or even Bing Ads:

  1. App-level credentials  - META_APP_ID + META_APP_SECRET, from your
                              app in developers.facebook.com (created
                              once, shared across all your users)
  2. Per-user access token  - obtained by sending the business owner
                              through a Facebook OAuth consent screen
                              (Facebook Login for Business) once. That
                              exchange returns a SHORT-lived token
                              (~1-2 hours); this client exchanges it for
                              a LONG-lived token (~60 days) that you
                              store per user and refresh before expiry.
  3. Ad account + Page      - one Facebook login can control several ad
                              accounts and Pages. The user picks which
                              ad account to spend from and which Page
                              the ad is posted as, during onboarding.

IMPORTANT - App Review gate: a newly created Meta app can only manage ad
accounts that you or your added test users control (this is why the
"Stelle API Test" account works today). To create/run ads on behalf of
OTHER users' ad accounts, Meta requires your app to pass App Review for
the `ads_management` permission, plus Business Verification. That review
is done in Meta's Business/Developer portal, not in code - start it
early, it can take days. Until it's approved, this client only works
against ad accounts you own or have added as test accounts.

Object chain: every real ad is four separate objects, created in order,
each referencing the previous one's id:

  Campaign (objective, e.g. OUTCOME_LEADS)
    -> Ad Set (daily_budget, targeting, optimization_goal, schedule)
        -> Ad Creative (the actual headline/description/image/link)
            -> Ad (ties an Ad Set to a Creative - this is what actually runs)

Everything is created with status="PAUSED" by default. Nothing spends
money or goes live until you explicitly call `activate()` on the
campaign - that's a deliberate safety gate, not an oversight, so a bug
partway through the chain can't accidentally launch a live ad.

Meta reviews every new ad before it serves (usually within a few hours,
occasionally up to 24h, and it can be rejected) - `activate()` returns
immediately, use `get_status()` / `get_insights()` to poll afterward.

API version: this client targets Graph API v21.0. Meta deprecates old
versions on a rolling schedule - confirm the current version and exact
field names against the Marketing API reference before shipping, this
is one of the faster-moving parts of their platform.
"""

import os
import httpx

GRAPH_API_VERSION = "v25.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


class MetaAdsError(Exception):
    pass


class MetaAuthError(MetaAdsError):
    pass


async def exchange_for_long_lived_token(app_id: str, app_secret: str,
                                         short_lived_token: str) -> dict:
    """
    Step 2 of onboarding a user: after the Facebook OAuth consent screen
    hands you back a short-lived user access token, call this once to
    trade it for a long-lived one (~60 days). Store the result
    (access_token + expires_in) encrypted, per user, and re-run this
    exchange before it expires - Meta does not silently refresh it for you.
    """
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_lived_token,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/oauth/access_token", params=params, timeout=10.0)

    if resp.status_code != 200:
        raise MetaAuthError(f"Token exchange failed: {resp.status_code} {resp.text}")

    return resp.json()  # {"access_token": "...", "token_type": "bearer", "expires_in": 5184000}


# Meta's call-to-action button presets (there are more; these cover the
# common small-business cases). Surfaced to the user as an override in
# the questionnaire step - if they don't pick one, the `default_cta` on
# OBJECTIVE_MAP below is used instead based on the campaign goal, so the
# button always matches the objective instead of Meta's generic default.
CALL_TO_ACTION_OPTIONS = {
    "LEARN_MORE": "Learn More",
    "SIGN_UP": "Sign Up",
    "SHOP_NOW": "Shop Now",
    "GET_QUOTE": "Get Quote",
    "CONTACT_US": "Contact Us",
    "SUBSCRIBE": "Subscribe",
    "DOWNLOAD": "Download",
    "BOOK_TRAVEL": "Book Now",
}

# Maps a plain-language questionnaire answer to the Meta enums an ad set
# actually needs. Extend this if the questionnaire grows more goals.
OBJECTIVE_MAP = {
    "leads": {"objective": "OUTCOME_LEADS", "optimization_goal": "LEAD_GENERATION", "billing_event": "IMPRESSIONS", "default_cta": "SIGN_UP"},
    "traffic": {"objective": "OUTCOME_TRAFFIC", "optimization_goal": "LINK_CLICKS", "billing_event": "LINK_CLICKS", "default_cta": "LEARN_MORE"},
    "sales": {"objective": "OUTCOME_SALES", "optimization_goal": "OFFSITE_CONVERSIONS", "billing_event": "IMPRESSIONS", "default_cta": "SHOP_NOW"},
    "awareness": {"objective": "OUTCOME_AWARENESS", "optimization_goal": "REACH", "billing_event": "IMPRESSIONS", "default_cta": "LEARN_MORE"},
    "engagement": {"objective": "OUTCOME_ENGAGEMENT", "optimization_goal": "POST_ENGAGEMENT", "billing_event": "IMPRESSIONS", "default_cta": "LEARN_MORE"},
}


def objective_settings(goal: str) -> dict:
    key = goal.strip().lower()
    if key not in OBJECTIVE_MAP:
        raise MetaAdsError(f"Unknown goal '{goal}', expected one of {list(OBJECTIVE_MAP)}")
    return OBJECTIVE_MAP[key]


async def get_short_lived_token(app_id: str, app_secret: str, redirect_uri: str, code: str) -> str:
    """Step 1 of the OAuth callback: trades the `code` Facebook redirected back with for a short-lived token."""
    params = {
        "client_id": app_id,
        "client_secret": app_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/oauth/access_token", params=params, timeout=10.0)
    if resp.status_code != 200:
        raise MetaAuthError(f"Code exchange failed: {resp.status_code} {resp.text}")
    return resp.json()["access_token"]


async def list_ad_accounts(access_token: str) -> list:
    """Ad accounts this Facebook login can access - let the user pick which one to spend from."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/me/adaccounts",
                                 params={"fields": "id,name", "access_token": access_token}, timeout=10.0)
    if resp.status_code != 200:
        raise MetaAdsError(f"Could not list ad accounts: {resp.status_code} {resp.text}")
    return resp.json().get("data", [])


async def list_pages(access_token: str) -> list:
    """Pages this Facebook login manages - the ad is posted as one of these."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/me/accounts",
                                 params={"fields": "id,name", "access_token": access_token}, timeout=10.0)
    if resp.status_code != 200:
        raise MetaAdsError(f"Could not list pages: {resp.status_code} {resp.text}")
    return resp.json().get("data", [])


class MetaAdsClient:
    def __init__(self, access_token: str, ad_account_id: str, page_id: str):
        """
        access_token: the long-lived per-user token from exchange_for_long_lived_token
        ad_account_id: e.g. "act_983072688098855" (must include the "act_" prefix)
        page_id: the Facebook Page the ad will be posted as
        """
        self.access_token = access_token
        self.ad_account_id = ad_account_id
        self.page_id = page_id

    async def _post(self, path: str, payload: dict) -> dict:
        payload = {**payload, "access_token": self.access_token}
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{BASE_URL}/{path}", data=payload, timeout=20.0)
        if resp.status_code != 200:
            raise MetaAdsError(f"Meta API error {resp.status_code}: {resp.text}")
        return resp.json()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        params = {**(params or {}), "access_token": self.access_token}
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{BASE_URL}/{path}", params=params, timeout=15.0)
        if resp.status_code != 200:
            raise MetaAdsError(f"Meta API error {resp.status_code}: {resp.text}")
        return resp.json()

    # -----------------------------------------------------------------
    # 1. Campaign
    # -----------------------------------------------------------------
    async def create_campaign(self, name: str, objective: str = "OUTCOME_LEADS",
                               status: str = "PAUSED") -> dict:
        """
        objective is one of Meta's OUTCOME_* enums - the common ones map
        roughly to your questionnaire's "what's your goal" answer:
          OUTCOME_LEADS      - form fills / lead capture (your main case)
          OUTCOME_TRAFFIC    - clicks to the website
          OUTCOME_SALES      - purchases (needs a Pixel/Conversions API set up)
          OUTCOME_AWARENESS  - reach/impressions
          OUTCOME_ENGAGEMENT - post engagement
        """
        payload = {
            "name": name,
            "objective": objective,
            "status": status,
            "special_ad_categories": "[]",  # required field; set per Meta's housing/credit/employment/political rules if applicable
        }
        return await self._post(f"{self.ad_account_id}/campaigns", payload)

    # -----------------------------------------------------------------
    # 2. Ad Set - budget, targeting, schedule live here
    # -----------------------------------------------------------------
    async def create_ad_set(self, campaign_id: str, name: str, daily_budget: float,
                             targeting: dict, optimization_goal: str = "LEAD_GENERATION",
                             billing_event: str = "IMPRESSIONS",
                             status: str = "PAUSED") -> dict:
        """
        daily_budget is passed in whole currency units (e.g. 15.00 for
        $15/day) and converted here to the minor-unit integer string Meta
        expects (cents). targeting is a dict matching Meta's targeting
        spec, e.g.:
          {"geo_locations": {"countries": ["US"]}, "age_min": 18, "age_max": 65}
        Build this from your questionnaire answers (location, age range).
        """
        payload = {
            "name": name,
            "campaign_id": campaign_id,
            "daily_budget": str(int(round(daily_budget * 100))),
            "billing_event": billing_event,
            "optimization_goal": optimization_goal,
            "targeting": _to_json(targeting),
            "status": status,
            "is_adset_budget_sharing_enabled": "true",
        }
        print(f"[DEBUG] ADSET PAYLOAD: {payload}", flush=True)
        return await self._post(f"{self.ad_account_id}/adsets", payload)

    # -----------------------------------------------------------------
    # 3. Ad Creative - the actual headline/description/image/link
    # -----------------------------------------------------------------
    async def create_ad_creative(self, name: str, headline: str, description: str,
                                  link: str, image_url: str,
                                  call_to_action: str = "LEARN_MORE") -> dict:
        """
        link should point at the landing page that captures the lead
        (your existing /leads form) - ideally with a query param
        identifying this ad/campaign so the resulting lead can be
        attributed back (matches the `source_post` field already in
        LeadIn). image_url must be a URL Meta can fetch directly; for
        images you want to persist, host them yourself rather than
        relying on Grok's temporary URL.

        call_to_action should be one of CALL_TO_ACTION_OPTIONS above.
        Left unset, Meta falls back to a generic button - explicitly
        matching it to the goal (e.g. SIGN_UP for a leads campaign
        instead of a bare LEARN_MORE) is a real, if small, lift on
        click-through.
        """
        payload = {
            "name": name,
            "object_story_spec": _to_json({
                "page_id": self.page_id,
                "link_data": {
                    "link": link,
                    "message": description,
                    "name": headline,
                    "picture": image_url,
                    "call_to_action": {
                        "type": call_to_action,
                        "value": {"link": link},
                    },
                },
            }),
        }
        return await self._post(f"{self.ad_account_id}/adcreatives", payload)

    # -----------------------------------------------------------------
    # 4. Ad - ties an Ad Set to a Creative; this is what actually runs
    # -----------------------------------------------------------------
    async def create_ad(self, name: str, adset_id: str, creative_id: str,
                         status: str = "PAUSED") -> dict:
        payload = {
            "name": name,
            "adset_id": adset_id,
            "creative": _to_json({"creative_id": creative_id}),
            "status": status,
        }
        return await self._post(f"{self.ad_account_id}/ads", payload)

    # -----------------------------------------------------------------
    # Launch / status
    # -----------------------------------------------------------------
    async def activate(self, object_id: str) -> dict:
        """
        Sets any campaign/ad set/ad object to ACTIVE. This is the actual
        "go live" switch - call it last, after the full chain above has
        been created successfully and the user has confirmed the budget.
        Meta still has to review the ad before it serves; this call just
        flips it from PAUSED into the review queue.
        """
        return await self._post(object_id, {"status": "ACTIVE"})

    async def get_status(self, object_id: str) -> dict:
        return await self._get(object_id, {"fields": "status,effective_status"})

    async def get_insights(self, object_id: str) -> dict:
        """Impressions/clicks/spend so far - poll this after activating."""
        return await self._get(f"{object_id}/insights",
                                {"fields": "impressions,clicks,spend,cpc"})


def _to_json(d: dict) -> str:
    import json
    return json.dumps(d)


def get_config_from_env() -> dict:
    required = ["META_APP_ID", "META_APP_SECRET"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise MetaAuthError(f"Missing Meta app env vars: {', '.join(missing)}")
    return {
        "app_id": os.environ["META_APP_ID"],
        "app_secret": os.environ["META_APP_SECRET"],
    }