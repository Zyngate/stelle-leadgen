"""
In-memory onboarding session store - holds one user's progress through
the flow: connect Facebook -> analyze website -> questionnaire ->
ai ad content generation -> budget -> launch.

Same pattern as ad_poster.py's campaign store: a plain dict, fine for a
demo, resets on restart. Swap for a real database keyed by your actual
user id (not a bare session id) before this goes near production -
the Facebook access_token in particular needs to live somewhere
encrypted and durable, not in server memory.
"""

import uuid
from datetime import datetime, timezone

_SESSIONS: dict[str, dict] = {}


class SessionNotFound(Exception):
    pass


def create_session() -> dict:
    session_id = str(uuid.uuid4())[:8]
    _SESSIONS[session_id] = {
        "id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stage": "created",
        "facebook": None,          # {access_token, ad_account_id, page_id}
        "website_analysis": None,  # output of website_analyzer.analyze_website
        "questionnaire": None,     # {goal, countries, age_min, age_max, offer}
        "ad_content": None,        # {headline, description, image_url}
        "budget": None,            # {daily_budget, start_date, end_date}
        "meta_ids": None,          # {campaign_id, adset_id, creative_id, ad_id}
    }
    return _SESSIONS[session_id]


def get_session(session_id: str) -> dict:
    if session_id not in _SESSIONS:
        raise SessionNotFound(session_id)
    return _SESSIONS[session_id]


def update_session(session_id: str, stage: str | None = None, **fields) -> dict:
    session = get_session(session_id)
    session.update(fields)
    if stage:
        session["stage"] = stage
    return session