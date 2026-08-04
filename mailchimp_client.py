"""
Thin wrapper around the Mailchimp API for one job: push a captured lead
into an Audience (list).

Mailchimp auth is a single static API key - no OAuth flow. The key looks
like: abc123def456abc123def456abc123-us21
The part after the last dash ("us21") is the "server prefix" and is
required in the base URL for every request.
"""

import hashlib
import httpx


class MailchimpClient:
    def __init__(self, api_key: str, audience_id: str):
        if "-" not in api_key:
            raise ValueError(
                "Mailchimp API key looks malformed - expected a server "
                "prefix after a dash, e.g. ...-us21"
            )
        self.api_key = api_key
        self.audience_id = audience_id
        self.server_prefix = api_key.split("-")[-1]
        self.base_url = f"https://{self.server_prefix}.api.mailchimp.com/3.0"

    def _member_id(self, email: str) -> str:
        # Mailchimp identifies list members by the MD5 hash of the
        # lowercased email address - this makes "add or update" idempotent.
        return hashlib.md5(email.lower().encode("utf-8")).hexdigest()

    async def add_lead(self, email: str, first_name: str = "", last_name: str = "",
                        source_post: str = "") -> dict:
        """
        Adds (or updates) a contact in the configured Audience.
        Uses PUT on the member resource, which Mailchimp treats as
        "create if missing, update if present" - so re-submits don't error.
        """
        url = f"{self.base_url}/lists/{self.audience_id}/members/{self._member_id(email)}"

        payload = {
            "email_address": email,
            "status_if_new": "subscribed",
            "merge_fields": {
                "FNAME": first_name,
                "LNAME": last_name,
                "SOURCE": source_post,  # requires a custom merge field named SOURCE in the Audience
            },
        }

        async with httpx.AsyncClient() as client:
            resp = await client.put(
                url,
                json=payload,
                auth=("anystring", self.api_key),  # Mailchimp accepts any username with the key as password
                timeout=10.0,
            )

        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Mailchimp error {resp.status_code}: {resp.text}")

        return resp.json()
