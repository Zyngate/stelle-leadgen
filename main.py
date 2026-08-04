"""
Lead generation prototype - minimal FastAPI service.

Flow this demonstrates:
  mock/real ad --> click --> landing page form --> POST /leads
    --> Mailchimp Audience (nurture)
    --> acknowledgment email sent immediately (receipt, not the nurture sequence)

Run it:
  pip install fastapi uvicorn httpx python-dotenv
  export MAILCHIMP_API_KEY="your-key-here"
  export MAILCHIMP_AUDIENCE_ID="your-audience-id-here"
  export SMTP_HOST="smtp.yourprovider.com"
  export SMTP_PORT="587"
  export SMTP_USERNAME="your-smtp-username"
  export SMTP_PASSWORD="your-smtp-password"
  export FROM_EMAIL="hello@yourbusiness.com"
  export BUSINESS_NAME="Your Business Name"   # optional, defaults to "us"
  uvicorn main:app --reload

Then open http://127.0.0.1:8000/ to see the test form.
"""

import os
from dotenv import load_dotenv
load_dotenv()  # reads .env in the current directory, if present, into os.environ

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr

from mailchimp_client import MailchimpClient
from bing_ads_client import MicrosoftAdvertisingClient
import ad_poster
import email_sender
import Grok_client
import gemini_client
import pollinations_client
import meta_ads_client
import website_analyzer
import onboarding_session
# from activecampaign_client import ActiveCampaignClient   # add later, same pattern
# from hubspot_client import HubSpotClient                 # add later, same pattern

app = FastAPI(title="Stelle Lead Generation Prototype")

app.mount("/static", StaticFiles(directory="static"), name="static")


class LeadIn(BaseModel):
    email: EmailStr
    first_name: str = ""
    last_name: str = ""
    source_post: str = ""  # which ad/post/landing page this lead came from


class BingCampaignIn(BaseModel):
    account_id: str
    campaign_name: str
    daily_budget: float
    destination_url: str  # where the click lands - your lead capture form
    headline: str
    description: str
    image_url: str


class MockCampaignIn(BaseModel):
    name: str
    daily_budget: float
    destination_url: str  # where the click lands - your lead capture form
    headline: str
    description: str
    image_url: str


class AdCopyIn(BaseModel):
    business_description: str
    offer: str = ""


class RegenerateImageIn(BaseModel):
    extra_instructions: str = ""  # e.g. "more colorful", "show the storefront", "less text"


class WebsiteAnalysisIn(BaseModel):
    website_url: str


class FacebookSelectIn(BaseModel):
    ad_account_id: str  # e.g. "act_983072688098855"
    page_id: str


class QuestionnaireIn(BaseModel):
    goal: str  # one of meta_ads_client.OBJECTIVE_MAP: leads, traffic, sales, awareness, engagement
    countries: list[str] = ["US"]
    age_min: int = 18
    age_max: int = 65
    offer: str = ""
    call_to_action: str | None = None  # one of meta_ads_client.CALL_TO_ACTION_OPTIONS; omit to default from goal


class BudgetIn(BaseModel):
    daily_budget: float
    start_date: str | None = None  # ISO 8601, e.g. "2026-08-01T00:00:00-0000"; omit to start on activation
    end_date: str | None = None


def get_mailchimp_client() -> MailchimpClient:
    api_key = os.environ.get("MAILCHIMP_API_KEY")
    audience_id = os.environ.get("MAILCHIMP_AUDIENCE_ID")
    if not api_key or not audience_id:
        raise HTTPException(
            status_code=500,
            detail="MAILCHIMP_API_KEY / MAILCHIMP_AUDIENCE_ID not set in environment",
        )
    return MailchimpClient(api_key=api_key, audience_id=audience_id)


def get_bing_ads_client() -> MicrosoftAdvertisingClient:
    required = ["BING_CLIENT_ID", "BING_CLIENT_SECRET", "BING_DEVELOPER_TOKEN", "BING_REFRESH_TOKEN"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"Missing Microsoft Advertising env vars: {', '.join(missing)}",
        )
    return MicrosoftAdvertisingClient(
        client_id=os.environ["BING_CLIENT_ID"],
        client_secret=os.environ["BING_CLIENT_SECRET"],
        developer_token=os.environ["BING_DEVELOPER_TOKEN"],
        refresh_token=os.environ["BING_REFRESH_TOKEN"],
    )


def get_meta_oauth_config() -> dict:
    required = ["META_APP_ID", "META_APP_SECRET", "META_REDIRECT_URI"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"Missing Meta app env vars: {', '.join(missing)}",
        )
    return {
        "app_id": os.environ["META_APP_ID"],
        "app_secret": os.environ["META_APP_SECRET"],
        "redirect_uri": os.environ["META_REDIRECT_URI"],
    }


@app.get("/")
def serve_form():
    return FileResponse("static/index.html")


@app.get("/create-ad")
def serve_create_ad_page():
    return FileResponse("static/create-ad.html")


@app.get("/dashboard")
def serve_dashboard():
    """The full onboarding flow as one page - connect Meta, analyze the site, set the goal, generate the ad, budget, launch."""
    return FileResponse("static/dashboard.html")


@app.post("/leads")
async def capture_lead(lead: LeadIn):
    """
    Single entry point for a captured lead, regardless of where it came
    from (ad click landing page, organic post form, etc).

    Right now this only forwards to Mailchimp. To support a second
    platform, add its client the same way MailchimpClient is used below,
    and either:
      a) let the caller specify which platform via a `platform` field, or
      b) fan the same lead out to every platform the business has connected
    """
    mailchimp = get_mailchimp_client()

    try:
        result = await mailchimp.add_lead(
            email=lead.email,
            first_name=lead.first_name,
            last_name=lead.last_name,
            source_post=lead.source_post,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Lead is captured at this point regardless of what happens next -
    # the acknowledgment email is a nice-to-have, not something that
    # should undo a successful lead capture if it fails.
    email_sent = False
    email_error = None
    try:
        email_sender.send_acknowledgment_email(
            to_email=lead.email,
            first_name=lead.first_name,
            business_name=os.environ.get("BUSINESS_NAME", "us"),
        )
        email_sent = True
    except email_sender.EmailSendError as e:
        email_error = str(e)

    return {
        "status": "ok",
        "mailchimp_member_id": result.get("id"),
        "mailchimp_status": result.get("status"),
        "acknowledgment_email_sent": email_sent,
        "acknowledgment_email_error": email_error,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/campaigns/bing")
async def create_bing_campaign(campaign: BingCampaignIn):
    """
    Creates a display campaign on Microsoft Advertising - this is the
    piece that actually gets the ad shown on other websites and
    generates the click. destination_url should point at your landing
    page, whose form posts to /leads above - that's how the two halves
    of the funnel connect.

    This is the REAL integration, requiring OAuth + a developer token -
    see /campaigns/mock below for a working demo that doesn't need any
    of that setup.
    """
    bing_ads = get_bing_ads_client()

    try:
        result = await bing_ads.create_display_campaign(
            account_id=campaign.account_id,
            campaign_name=campaign.campaign_name,
            daily_budget=campaign.daily_budget,
            destination_url=campaign.destination_url,
            headline=campaign.headline,
            description=campaign.description,
            image_url=campaign.image_url,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"status": "ok", "campaign": result}


# ---------------------------------------------------------------------
# AI content generation (Grok/xAI) - generates headline + description
# copy so you're not typing it by hand before creating a campaign.
# ---------------------------------------------------------------------

@app.post("/generate-ad-copy")
async def generate_ad_copy(body: AdCopyIn):
    try:
        copy = await Grok_client.generate_ad_copy(
            business_description=body.business_description,
            offer=body.offer,
        )
    except Grok_client.GrokError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"status": "ok", **copy}


@app.post("/generate-ad-creative")
async def generate_ad_creative(body: AdCopyIn):
    """
    Generates headline + description + an actual ad image, all in one
    call - this is what the /create-ad page uses so nothing has to be
    typed or sourced by hand.
    """
    try:
        creative = await Grok_client.generate_ad_creative(
            business_description=body.business_description,
            offer=body.offer,
        )
    except Grok_client.GrokError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"status": "ok", **creative}


# ---------------------------------------------------------------------
# Mock ad poster - our own simple "ad network" for demo purposes.
# No external account, no API key, no OAuth. Good enough to show the
# full loop end to end: create ad -> embed it -> click -> land on the
# form -> capture the lead in Mailchimp.
# ---------------------------------------------------------------------

@app.post("/campaigns/mock")
def create_mock_campaign(campaign: MockCampaignIn, request: Request):
    created = ad_poster.create_campaign(
        name=campaign.name,
        headline=campaign.headline,
        description=campaign.description,
        image_url=campaign.image_url,
        destination_url=campaign.destination_url,
        daily_budget=campaign.daily_budget,
    )
    base_url = str(request.base_url).rstrip("/")
    return {
        "status": "ok",
        "campaign": created,
        "embed_snippet_url": f"{base_url}/ad/{created['id']}/snippet",
        "publisher_demo_url": f"{base_url}/publisher-demo/{created['id']}",
    }


@app.get("/campaigns/mock")
def list_mock_campaigns():
    return {"campaigns": ad_poster.list_campaigns()}


@app.get("/ad/{campaign_id}/snippet", response_class=HTMLResponse)
def get_ad_snippet(campaign_id: str, request: Request):
    """Returns the embeddable HTML ad unit - this is what a 'publisher site' would paste in."""
    base_url = str(request.base_url).rstrip("/")
    try:
        return ad_poster.render_ad_snippet(campaign_id, base_url)
    except ad_poster.CampaignNotFound:
        raise HTTPException(status_code=404, detail="Campaign not found")


@app.get("/ad/{campaign_id}/impression")
def track_impression(campaign_id: str):
    """A 1x1 tracking pixel hit whenever the ad snippet is rendered/viewed."""
    ad_poster.record_impression(campaign_id)
    return Response(status_code=204)


@app.get("/ad/{campaign_id}/click")
def track_click(campaign_id: str):
    """Logs the click, then redirects to the real destination (your lead form)."""
    try:
        destination = ad_poster.record_click(campaign_id)
    except ad_poster.CampaignNotFound:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return RedirectResponse(url=destination)


@app.get("/publisher-demo/{campaign_id}", response_class=HTMLResponse)
def publisher_demo(campaign_id: str, request: Request):
    """
    Simulates 'someone else's website' showing your ad - proves the
    concept without needing a real publisher network.
    """
    base_url = str(request.base_url).rstrip("/")
    try:
        snippet = ad_poster.render_ad_snippet(campaign_id, base_url)
    except ad_poster.CampaignNotFound:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return f"""
    <html>
    <head><title>Example News Site (mock publisher)</title></head>
    <body style="font-family:Arial,sans-serif;max-width:700px;margin:40px auto;">
      <h2>Example News Site</h2>
      <p>This page stands in for any third-party website that would carry your ad
      once a real ad network is wired in. The block below is your ad, embedded
      exactly the way a publisher would embed it.</p>
      <hr>
      {snippet}
      <hr>
      <p style="color:#888;font-size:13px;">Click the ad above to simulate a real visitor click -
      it will log the click and redirect you to the actual landing page.</p>
    </body>
    </html>
    """


# =======================================================================
# Meta ads onboarding flow - the real integration.
#
# Order (matches the design): connect Facebook -> analyze website ->
# questionnaire -> ai ad content generation -> budget -> launch via
# Meta Marketing API. Each step reads/writes one onboarding_session and
# the frontend walks the user through them in this order; nothing here
# assumes the previous steps ran server-side back-to-back in one request.
# =======================================================================

@app.post("/onboarding/start")
def start_onboarding():
    session = onboarding_session.create_session()
    return {"status": "ok", "session": session}


@app.get("/onboarding/{session_id}")
def get_onboarding_status(session_id: str):
    try:
        return {"status": "ok", "session": onboarding_session.get_session(session_id)}
    except onboarding_session.SessionNotFound:
        raise HTTPException(status_code=404, detail="Session not found")


# -----------------------------------------------------------------------
# Step 1: Connect Facebook
# -----------------------------------------------------------------------

@app.get("/onboarding/{session_id}/facebook/connect")
def facebook_connect(session_id: str):
    """
    Returns the Facebook OAuth consent screen URL for this session - the
    frontend redirects the user's browser here. `state=session_id` is how
    we match the callback back to the right onboarding session.
    """
    onboarding_session.get_session(session_id)  # 404s if bad session_id
    config = get_meta_oauth_config()
    scopes = "ads_management,ads_read,business_management,pages_show_list,pages_read_engagement"
    dialog_url = (
        f"https://www.facebook.com/{meta_ads_client.GRAPH_API_VERSION}/dialog/oauth"
        f"?client_id={config['app_id']}"
        f"&redirect_uri={config['redirect_uri']}"
        f"&scope={scopes}"
        f"&response_type=code"
        f"&state={session_id}"
    )
    return {"status": "ok", "oauth_url": dialog_url}


@app.get("/onboarding/facebook/callback")
async def facebook_callback(code: str, state: str):
    """
    Facebook redirects here after consent, with `code` and `state`
    (our session_id) in the query string. Exchanges the code for a
    short-lived token, then a long-lived one, then lists the ad accounts
    and Pages the user can pick from.

    Instead of dumping that list as raw JSON (the old behavior - fine
    for debugging, not something to put in front of an actual user),
    this stores the list on the session and redirects the browser
    straight back into /dashboard, which fetches the list itself via
    GET /onboarding/{session_id}/facebook/accounts and renders it as
    clickable options - no copying IDs out of a JSON blob by hand.
    """
    session_id = state
    onboarding_session.get_session(session_id)  # 404s if bad session_id
    config = get_meta_oauth_config()

    try:
        short_lived = await meta_ads_client.get_short_lived_token(
            app_id=config["app_id"], app_secret=config["app_secret"],
            redirect_uri=config["redirect_uri"], code=code,
        )
        long_lived = await meta_ads_client.exchange_for_long_lived_token(
            app_id=config["app_id"], app_secret=config["app_secret"],
            short_lived_token=short_lived,
        )
        access_token = long_lived["access_token"]
        ad_accounts = await meta_ads_client.list_ad_accounts(access_token)
        pages = await meta_ads_client.list_pages(access_token)
    except meta_ads_client.MetaAdsError as e:
        raise HTTPException(status_code=502, detail=str(e))

    onboarding_session.update_session(
        session_id, stage="facebook_connected",
        facebook={"access_token": access_token, "ad_account_id": None, "page_id": None},
        facebook_accounts={"ad_accounts": ad_accounts, "pages": pages},
    )

    return RedirectResponse(url=f"/dashboard?session_id={session_id}&fb=connected")


@app.get("/onboarding/{session_id}/facebook/accounts")
def facebook_accounts(session_id: str):
    """
    Returns the ad accounts and Pages listed at connect-time, so the
    frontend can render them as pickable options (dropdowns, a list -
    whatever) instead of asking the user to copy IDs out of raw JSON.
    """
    session = onboarding_session.get_session(session_id)
    accounts = session.get("facebook_accounts")
    if not accounts:
        raise HTTPException(status_code=400, detail="Connect Facebook first - no account list on this session yet")
    return {"status": "ok", **accounts}


@app.post("/onboarding/{session_id}/facebook/select")
def facebook_select(session_id: str, choice: FacebookSelectIn):
    """User picks which connected ad account + Page to use, after the callback listed the options."""
    session = onboarding_session.get_session(session_id)
    if not session.get("facebook"):
        raise HTTPException(status_code=400, detail="Connect Facebook first (no access token on this session)")
    session["facebook"]["ad_account_id"] = choice.ad_account_id
    session["facebook"]["page_id"] = choice.page_id
    onboarding_session.update_session(session_id, stage="facebook_account_selected")
    return {"status": "ok", "session": session}


# -----------------------------------------------------------------------
# Step 2: Analyze website
# -----------------------------------------------------------------------

@app.post("/onboarding/{session_id}/analyze-website")
async def analyze_website_step(session_id: str, body: WebsiteAnalysisIn):
    onboarding_session.get_session(session_id)  # 404s if bad session_id
    try:
        analysis = await website_analyzer.analyze_website(body.website_url)
    except website_analyzer.WebsiteAnalysisError as e:
        raise HTTPException(status_code=502, detail=str(e))

    onboarding_session.update_session(session_id, stage="website_analyzed", website_analysis=analysis)
    return {
        "status": "ok",
        "analysis": analysis,
        "note": "Show business_description back to the user for confirmation/edit before continuing.",
    }


# -----------------------------------------------------------------------
# Step 3: Questionnaire
# -----------------------------------------------------------------------

@app.post("/onboarding/{session_id}/questionnaire")
def questionnaire_step(session_id: str, body: QuestionnaireIn):
    onboarding_session.get_session(session_id)  # 404s if bad session_id
    try:
        meta_ads_client.objective_settings(body.goal)  # validates goal is a known enum
    except meta_ads_client.MetaAdsError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if body.call_to_action and body.call_to_action not in meta_ads_client.CALL_TO_ACTION_OPTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown call_to_action '{body.call_to_action}', expected one of {list(meta_ads_client.CALL_TO_ACTION_OPTIONS)}",
        )

    onboarding_session.update_session(session_id, stage="questionnaire_complete", questionnaire=body.model_dump())
    return {"status": "ok", "questionnaire": body.model_dump()}


# -----------------------------------------------------------------------
# Step 4: AI ad content generation
# -----------------------------------------------------------------------

@app.post("/onboarding/{session_id}/generate-content")
async def generate_content_step(session_id: str, request: Request):
    """
    Combines the website analysis and questionnaire answers already on
    this session - no need to re-type the business description or offer.

    The ad image is generated by Pollinations.ai (free, no key needed) -
    it draws a clean background, then the real headline/offer text gets
    composited on top afterward with Pillow rather than asked of the
    image model directly, since every current image model is unreliable
    at actually spelling out real words. If Pollinations fails, or its
    background still comes back with baked-in garbled lettering despite
    the anti-text prompt (a known limitation, not something prompting
    alone fully solves), this falls back to the site's own og:image -
    and the user can always override either one with
    /onboarding/{session_id}/upload-image if neither is good enough.
    """
    session = onboarding_session.get_session(session_id)
    if not session.get("website_analysis"):
        raise HTTPException(status_code=400, detail="Run /analyze-website first")
    if not session.get("questionnaire"):
        raise HTTPException(status_code=400, detail="Run /questionnaire first")

    analysis = session["website_analysis"]
    questionnaire = session["questionnaire"]

    try:
        copy = await Grok_client.generate_ad_copy(
            business_description=analysis["business_description"],
            offer=questionnaire.get("offer", ""),
        )
    except Grok_client.GrokError as e:
        raise HTTPException(status_code=502, detail=str(e))

    base_url = str(request.base_url).rstrip("/")
    image_source = "generated"

    try:
        image_bytes, extension = await pollinations_client.generate_ad_image(
            business_description=analysis["business_description"],
            suggested_industry=analysis.get("suggested_industry", ""),
            goal=questionnaire["goal"],
            headline=copy["headline"],
            offer=questionnaire.get("offer", ""),
        )
        image_url = gemini_client.save_generated_image(image_bytes, base_url, extension=extension)
    except pollinations_client.PollinationsError:
        # Image generation is a nice-to-have, not a hard requirement for
        # this step - fall back to whatever the site itself had rather
        # than blocking the whole onboarding flow on an external service.
        image_url = analysis.get("og_image") or ""
        image_source = "site_logo_fallback"

    content = {
        "headline": copy["headline"],
        "description": copy["description"],
        "image_url": image_url,
        "image_source": image_source,  # "generated", "site_logo_fallback", or (later) "user_uploaded"
    }

    onboarding_session.update_session(session_id, stage="content_generated", ad_content=content)
    return {
        "status": "ok",
        "ad_content": content,
        "note": (
            "Show this preview back to the user - the copy (headline/description) "
            "is the part to get right first. If the image isn't good, they can "
            "call /onboarding/{session_id}/regenerate-image for another attempt, "
            "or /onboarding/{session_id}/upload-image to use their own instead. "
            "If image_source is 'site_logo_fallback', tell them the generated "
            "image failed and this is just the site's own logo/image instead."
        ),
    }


# -----------------------------------------------------------------------
# Step 4b: Regenerate just the image (keeps the existing headline/copy)
# -----------------------------------------------------------------------

@app.post("/onboarding/{session_id}/regenerate-image")
async def regenerate_image_step(session_id: str, body: RegenerateImageIn, request: Request):
    """
    Regenerates only the image via Pollinations, reusing the headline
    already on the session plus any extra steer the user gives (e.g.
    "more colorful", "show the storefront"). Copy text is untouched -
    call /generate-content again instead if the headline/description
    itself needs a redo, or /upload-image if the user would rather just
    supply their own image than keep retrying generation.
    """
    session = onboarding_session.get_session(session_id)
    if not session.get("website_analysis"):
        raise HTTPException(status_code=400, detail="Run /analyze-website first")
    if not session.get("questionnaire"):
        raise HTTPException(status_code=400, detail="Run /questionnaire first")
    if not session.get("ad_content"):
        raise HTTPException(status_code=400, detail="Run /generate-content first")

    analysis = session["website_analysis"]
    questionnaire = session["questionnaire"]
    ad_content = session["ad_content"]

    try:
        image_bytes, extension = await pollinations_client.generate_ad_image(
            business_description=analysis["business_description"],
            suggested_industry=analysis.get("suggested_industry", ""),
            goal=questionnaire["goal"],
            headline=ad_content["headline"],
            offer=questionnaire.get("offer", ""),
            extra_instructions=body.extra_instructions,
        )
    except pollinations_client.PollinationsError as e:
        # Unlike the initial generate step, don't silently fall back to
        # the site logo here - the user explicitly asked for a new
        # image, so a clear failure they can retry (or switch to
        # /upload-image) beats a swap they didn't ask for.
        raise HTTPException(status_code=502, detail=str(e))

    base_url = str(request.base_url).rstrip("/")
    image_url = gemini_client.save_generated_image(image_bytes, base_url, extension=extension)

    updated_content = {**ad_content, "image_url": image_url, "image_source": "generated"}
    onboarding_session.update_session(session_id, ad_content=updated_content)
    return {
        "status": "ok",
        "ad_content": updated_content,
        "note": "Call this again with different extra_instructions, or use /upload-image instead if generation just isn't working out.",
    }


# -----------------------------------------------------------------------
# Step 4c: Let the user upload their own image instead of a generated one
# -----------------------------------------------------------------------

ALLOWED_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8MB - generous for a banner-sized ad image


@app.post("/onboarding/{session_id}/upload-image")
async def upload_image_step(session_id: str, request: Request, file: UploadFile = File(...)):
    """
    Lets the user bypass AI generation entirely and supply their own
    image - the escape hatch for when neither the Pollinations output
    nor the og:image fallback is good enough. Keeps the existing
    headline/description untouched, same as /regenerate-image.
    """
    session = onboarding_session.get_session(session_id)
    if not session.get("ad_content"):
        raise HTTPException(status_code=400, detail="Run /generate-content first")

    if file.content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{file.content_type}' - upload a PNG, JPEG, or WEBP.",
        )

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Image is too large - keep it under 8MB.")

    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[file.content_type]
    base_url = str(request.base_url).rstrip("/")
    image_url = gemini_client.save_generated_image(contents, base_url, extension=extension)

    ad_content = session["ad_content"]
    updated_content = {**ad_content, "image_url": image_url, "image_source": "user_uploaded"}
    onboarding_session.update_session(session_id, ad_content=updated_content)
    return {"status": "ok", "ad_content": updated_content}


# -----------------------------------------------------------------------
# Step 5: Budget
# -----------------------------------------------------------------------

@app.post("/onboarding/{session_id}/budget")
def budget_step(session_id: str, body: BudgetIn):
    onboarding_session.get_session(session_id)  # 404s if bad session_id
    onboarding_session.update_session(session_id, stage="budget_set", budget=body.model_dump())
    return {"status": "ok", "budget": body.model_dump()}


# -----------------------------------------------------------------------
# Step 6: Launch via Meta Marketing API
# -----------------------------------------------------------------------

@app.post("/onboarding/{session_id}/launch")
async def launch_step(session_id: str, request: Request):
    """
    Runs the full Meta object chain (Campaign -> Ad Set -> Creative -> Ad)
    using everything gathered in the earlier steps, then activates the
    campaign. Requires facebook account selection, questionnaire,
    generated content, and a budget to already be on the session.
    """
    session = onboarding_session.get_session(session_id)
    facebook = session.get("facebook") or {}
    if not facebook.get("ad_account_id") or not facebook.get("page_id"):
        raise HTTPException(status_code=400, detail="Select a Facebook ad account and page first")
    if not session.get("questionnaire"):
        raise HTTPException(status_code=400, detail="Run /questionnaire first")
    if not session.get("ad_content"):
        raise HTTPException(status_code=400, detail="Run /generate-content first")
    if not session.get("budget"):
        raise HTTPException(status_code=400, detail="Set a budget first")

    client = meta_ads_client.MetaAdsClient(
        access_token=facebook["access_token"],
        ad_account_id=facebook["ad_account_id"],
        page_id=facebook["page_id"],
    )
    questionnaire = session["questionnaire"]
    ad_content = session["ad_content"]
    budget = session["budget"]
    settings = meta_ads_client.objective_settings(questionnaire["goal"])
    call_to_action = questionnaire.get("call_to_action") or settings["default_cta"]

    # Landing page: your existing lead form, tagged with this session id
    # so a captured lead can be attributed back to this campaign (see
    # `source_post` in LeadIn / capture_lead above).
    base_url = str(request.base_url).rstrip("/")
    destination_url = f"{base_url}/?source_post={session_id}"

    try:
        campaign = await client.create_campaign(
            name=f"Stelle campaign - {session_id}",
            objective=settings["objective"],
        )
        ad_set = await client.create_ad_set(
            campaign_id=campaign["id"],
            name=f"Stelle ad set - {session_id}",
            daily_budget=budget["daily_budget"],
            targeting={
                "geo_locations": {"countries": questionnaire["countries"]},
                "age_min": questionnaire["age_min"],
                "age_max": questionnaire["age_max"],
            },
            optimization_goal=settings["optimization_goal"],
            billing_event=settings["billing_event"],
        )
        creative = await client.create_ad_creative(
            name=f"Stelle creative - {session_id}",
            headline=ad_content["headline"],
            description=ad_content["description"],
            link=destination_url,
            image_url=ad_content["image_url"],
            call_to_action=call_to_action,
        )
        ad = await client.create_ad(
            name=f"Stelle ad - {session_id}",
            adset_id=ad_set["id"],
            creative_id=creative["id"],
        )
        await client.activate(campaign["id"])
    except meta_ads_client.MetaAdsError as e:
        raise HTTPException(status_code=502, detail=str(e))

    meta_ids = {
        "campaign_id": campaign["id"],
        "adset_id": ad_set["id"],
        "creative_id": creative["id"],
        "ad_id": ad["id"],
    }
    onboarding_session.update_session(session_id, stage="launched", meta_ids=meta_ids)

    return {
        "status": "ok",
        "meta_ids": meta_ids,
        "call_to_action": call_to_action,
        "note": "Campaign activated - Meta still has to review the ad before it actually starts serving. Poll /onboarding/{session_id}/status for effective_status.",
    }


@app.get("/onboarding/{session_id}/status")
async def onboarding_launch_status(session_id: str):
    session = onboarding_session.get_session(session_id)
    meta_ids = session.get("meta_ids")
    if not meta_ids:
        raise HTTPException(status_code=400, detail="Nothing launched yet on this session")

    facebook = session["facebook"]
    client = meta_ads_client.MetaAdsClient(
        access_token=facebook["access_token"],
        ad_account_id=facebook["ad_account_id"],
        page_id=facebook["page_id"],
    )
    try:
        campaign_status = await client.get_status(meta_ids["campaign_id"])
        insights = await client.get_insights(meta_ids["ad_id"])
    except meta_ads_client.MetaAdsError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"status": "ok", "campaign_status": campaign_status, "insights": insights}