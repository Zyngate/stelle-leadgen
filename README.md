# Stelle Lead Generation Prototype

## What's in here
- `main.py` - the FastAPI app tying everything together
- `mailchimp_client.py` - real Mailchimp integration (working)
- `email_sender.py` - sends the acknowledgment email after a lead is captured (working)
- `ad_poster.py` - our own mock "ad network" for demo purposes (fake, but proves the loop)
- `bing_ads_client.py` - real Microsoft Advertising scaffold (needs real credentials + verification against current docs before use)
- `static/index.html` - the test lead-capture form
- `requirements.txt` - Python dependencies

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your real values:
   ```
   cp .env.example .env
   ```
   Then open `.env` in any text editor and paste in your Mailchimp key, SMTP credentials, etc. `main.py` loads this file automatically on startup - no need to export variables by hand.

   **Never commit the real `.env` file anywhere** (a `.gitignore` is included that already excludes it).

3. Run the server:
   ```
   uvicorn main:app --reload
   ```

## Demo the full loop

1. Create a mock ad campaign:
   ```
   curl -X POST http://127.0.0.1:8000/campaigns/mock \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Salon Promo",
       "headline": "20% Off First Visit",
       "description": "Book this week and save.",
       "image_url": "https://picsum.photos/300/180",
       "destination_url": "http://127.0.0.1:8000/",
       "daily_budget": 15
     }'
   ```
   This returns a `publisher_demo_url`.

2. Open that URL in a browser - it's a mock "news site" showing your ad.

3. Click the ad - it logs the click and redirects you to the real lead-capture form.

4. Fill out the form and submit - this:
   - Adds the contact to your Mailchimp Audience
   - Sends an acknowledgment email immediately

5. Check campaign stats anytime:
   ```
   curl http://127.0.0.1:8000/campaigns/mock
   ```

## What's real vs. what's a placeholder

| Piece | Status |
|---|---|
| Mailchimp lead capture | Real, working |
| Acknowledgment email | Real, working |
| Mock ad poster / click tracking | Demo only - not a real ad network |
| Microsoft Advertising (Bing Ads) | Scaffolded, needs real OAuth credentials + a developer token before it can run |
