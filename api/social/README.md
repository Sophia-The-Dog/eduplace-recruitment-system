# Social Publisher — Meta Direct (Buffer replacement)

Publishes posts to **Facebook Pages** and **Instagram** directly through the
Meta Graph API, so the Eduplace Group brands no longer depend on Buffer for
distribution and scheduling.

- **Endpoint:** `POST /api/social/publish`
- **Status/config:** `GET /api/social/publish`
- **Dependencies:** none (Python stdlib only — nothing added to `requirements.txt`)

## Why this exists

Buffer sat between our content and Meta. This function removes the middleman:
one serverless endpoint, multi-brand, that calls the Graph API itself. Facebook
scheduling is handled natively by Meta; Instagram has no native scheduling
(see below).

## Request

```jsonc
POST /api/social/publish
{
  "brand": "eduplace",                      // required — selects credentials
  "platforms": ["facebook", "instagram"],   // default: both
  "message": "Caption / post text",         // required (unless image/video only)
  "link": "https://eduplace.co.za/jobs",    // optional, Facebook link posts
  "image_url": "https://.../card.jpg",       // required for IG image, optional FB photo
  "video_url": "https://.../reel.mp4",       // optional, IG reels
  "media_type": "IMAGE",                     // IG: IMAGE (default) or REELS
  "schedule_time": "2026-08-20T09:00:00Z"    // optional, Facebook native scheduling
}
```

### Response

```jsonc
{
  "brand": "eduplace",
  "results": {
    "facebook":  { "status": "published", "id": "111_222" },
    "instagram": { "status": "published", "id": "1789..." }
  }
}
```

Each platform reports independently. If Facebook succeeds but Instagram fails,
you get `200` with the Instagram entry marked `"status": "error"`. Only when
**every** requested platform errors does the endpoint return `502`.

## Configuration (environment variables)

Set these in Vercel → Project → Settings → Environment Variables. Brand keys are
the `brand` value upper-cased (`eduplace` → `EDUPLACE`, `mr-pigeon` → `MR_PIGEON`).

| Variable | Purpose |
|---|---|
| `META_GRAPH_VERSION` | Optional. Graph API version, default `v21.0`. |
| `META_APP_SECRET` | Optional. Enables `appsecret_proof` hardening on every call. |
| `WEBHOOK_SECRET` | Optional. If set, requests must send a matching `X-Webhook-Signature` HMAC (same scheme as `/api/webhook`). |
| `META_<BRAND>_PAGE_ID` | Facebook Page numeric ID. |
| `META_<BRAND>_PAGE_TOKEN` | Long-lived Page access token. |
| `META_<BRAND>_IG_USER_ID` | Instagram Business account ID. |
| `META_<BRAND>_IG_TOKEN` | Optional. IG token; falls back to `META_<BRAND>_PAGE_TOKEN`. |

Example for two brands:

```
META_EDUPLACE_PAGE_ID=1234567890
META_EDUPLACE_PAGE_TOKEN=EAAG...
META_EDUPLACE_IG_USER_ID=1789...
META_KOVA_PAGE_ID=9876543210
META_KOVA_PAGE_TOKEN=EAAG...
META_KOVA_IG_USER_ID=1780...
```

`GET /api/social/publish` lists the brands it detected from the environment
(no secrets echoed) — use it to confirm a deploy is wired correctly.

## Getting the tokens (one-time Meta setup)

1. Create a Meta app at developers.facebook.com → add **Facebook Login** and
   **Instagram Graph API** products.
2. Each brand's Instagram must be a **Business/Creator** account linked to its
   Facebook Page.
3. Request permissions: `pages_manage_posts`, `pages_read_engagement`,
   `instagram_basic`, `instagram_content_publish`, `business_management`.
4. Generate a **long-lived Page access token** per brand (Page tokens don't
   expire once long-lived, as long as the user token behind them is valid).
5. App Review is required to publish for Pages you don't own — plan for that
   before cutting a brand over.

## Scheduling — important difference from Buffer

- **Facebook:** native. Pass `schedule_time` (ISO-8601, 10 min – 75 days out)
  and Meta holds and publishes the post. Nothing else needed.
- **Instagram:** the Graph API has **no** scheduled publishing. To keep Buffer's
  "schedule for later" behaviour on IG you need a queue + a cron that calls this
  endpoint at the target time. The repo already runs scheduled Airtable/Make
  automations — a "Scheduled Posts" table + a daily/every-15-min trigger that
  POSTs due rows here is the drop-in replacement. (Not built in this change.)

## Cutover checklist (per brand)

1. Add the brand's env vars, redeploy, confirm it appears in `GET`.
2. Send one test post to a staging Page/IG.
3. Point whatever currently feeds Buffer (Airtable automation / Make scenario)
   at `POST /api/social/publish` instead.
4. Run both in parallel for a short window, then disable the Buffer channel.
