"""
Client for Microsoft Advertising (formerly Bing Ads) - the display network
that actually places ads on other websites (MSN, Outlook, and partner sites).

This is a genuinely different kind of integration than Mailchimp:
Mailchimp = one static API key.
Microsoft Advertising = THREE things together:

  1. Developer Token   - a partner-level key for your application itself
                         (get this once, from the Microsoft Advertising
                         Developer Portal)
  2. OAuth app credentials (Client ID + Client Secret) - from registering
                         an app in Microsoft Entra ID (portal.azure.com)
  3. A per-user refresh token - obtained by sending the business owner
                         through a Microsoft consent screen once; after
                         that you exchange the refresh token for short-
                         lived access tokens automatically

This file handles #2 and #3 (the OAuth exchange). The Developer Token (#1)
is just a config value once you have it - no code needed to "get" it,
you request it in the portal.

IMPORTANT: Microsoft Advertising is migrating from a legacy SOAP API to a
newer REST API in 2026 (SOAP is being retired). This client targets the
REST direction. Confirm exact endpoint paths against current Microsoft
Advertising API docs before going to production - this is a fast-moving
part of their platform right now.
"""

import httpx

TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"


class BingAdsAuthError(Exception):
    pass


class MicrosoftAdvertisingClient:
    def __init__(self, client_id: str, client_secret: str, developer_token: str,
                 refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.developer_token = developer_token
        self.refresh_token = refresh_token
        self._access_token = None

    async def _refresh_access_token(self) -> str:
        """
        Exchanges the long-lived refresh token for a short-lived access
        token. The refresh token itself is obtained once via a user
        consent redirect flow (not shown here - it's a browser-based
        step the business owner does once when they connect their
        Microsoft Advertising account, similar to a HubSpot OAuth
        connect flow).
        """
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://ads.microsoft.com/msads.manage offline_access",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(TOKEN_URL, data=payload, timeout=10.0)

        if resp.status_code != 200:
            raise BingAdsAuthError(f"Token refresh failed: {resp.status_code} {resp.text}")

        data = resp.json()
        self._access_token = data["access_token"]
        return self._access_token

    async def _headers(self) -> dict:
        if not self._access_token:
            await self._refresh_access_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "DeveloperToken": self.developer_token,
            "Content-Type": "application/json",
        }

    async def create_display_campaign(self, account_id: str, campaign_name: str,
                                       daily_budget: float, destination_url: str,
                                       headline: str, description: str,
                                       image_url: str) -> dict:
        """
        Creates a basic display/audience campaign that places an ad across
        Microsoft's partner network (MSN, Outlook, and affiliated sites).

        This is a SCAFFOLD, not a verified production call - Microsoft
        Advertising campaign creation typically requires multiple steps
        (create campaign -> create ad group -> create the ad itself ->
        set targeting), each a separate call. Treat this method as the
        shape of the integration, and fill in the exact request bodies
        from Microsoft's current REST API reference before running it
        against a real account.
        """
        headers = await self._headers()

        # Placeholder endpoint shape - confirm the exact path/version
        # against current Microsoft Advertising REST API docs.
        url = f"https://campaign.api.ads.microsoft.com/v1/accounts/{account_id}/campaigns"

        payload = {
            "name": campaign_name,
            "campaignType": "Audience",  # display-style placement, not search
            "budget": {"amount": daily_budget, "type": "DailyBudgetStandard"},
            "ad": {
                "headline": headline,
                "description": description,
                "imageUrl": image_url,
                "finalUrl": destination_url,
            },
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=15.0)

        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Campaign creation failed: {resp.status_code} {resp.text}")

        return resp.json()
