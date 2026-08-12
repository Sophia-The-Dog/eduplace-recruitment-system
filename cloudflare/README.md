# Social Publisher — Cloudflare Worker (Meta direct)

Publishes to **Facebook Pages** and **Instagram** directly via the Meta Graph
API, replacing Buffer. This is the Cloudflare Workers port of the integration
(the `api/social/` version was Vercel-specific and can be removed once you're
fully on Cloudflare).

- **Runtime:** Cloudflare Workers (JavaScript, `fetch` + Web Crypto, no deps)
- **API:** `POST /api/social/publish`, `GET /api/social/publish` (status/config)
- **Test console:** `/social-test.html` — open on a phone, no terminal needed
- Endpoints, request/response shape, dry-run, and HMAC signing are identical to
  the reference implementation, so anything feeding it doesn't care which host
  it runs on.

## Deploy from your phone (no CLI)

You don't need `wrangler` or a terminal. Use Cloudflare's Git integration:

1. **Cloudflare dashboard → Workers & Pages → Create → Workers → Import a
   repository.** Connect this GitHub repo.
2. Set the project's **root directory to `cloudflare/`** so it finds
   `wrangler.toml` and `src/index.js`.
3. Deploy. Cloudflare builds and gives you a `*.workers.dev` URL, then
   auto-redeploys on every push to the branch.
4. **Settings → Variables and Secrets** — add the secrets below (dashboard UI,
   all on your phone).
5. Open `https://<your-worker>.workers.dev/social-test.html` and tap
   **Check config → Dry run → Publish**.

### CLI alternative (if you're ever at a desktop)

```
cd cloudflare
npx wrangler deploy
npx wrangler secret put META_EDUPLACE_PAGE_TOKEN   # repeat per secret
```

## Configuration

`META_GRAPH_VERSION` is a plain var (in `wrangler.toml`, default `v25.0`).
Everything else is a **secret** — set them in the dashboard (Variables and
Secrets) so tokens never live in the repo. Brand key = the `brand` value
upper-cased (`eduplace` → `EDUPLACE`, `mr-pigeon` → `MR_PIGEON`).

| Secret | Purpose |
|---|---|
| `META_APP_SECRET` | Optional. Enables `appsecret_proof` on every call. |
| `WEBHOOK_SECRET` | Optional. Requires a matching `X-Webhook-Signature` HMAC on POSTs. |
| `META_<BRAND>_PAGE_ID` | Facebook Page numeric ID. |
| `META_<BRAND>_PAGE_TOKEN` | Long-lived Page access token. |
| `META_<BRAND>_IG_USER_ID` | Instagram Business account ID. |
| `META_<BRAND>_IG_TOKEN` | Optional. IG token; falls back to `_PAGE_TOKEN`. |

`GET /api/social/publish` lists the brands it detected from the secrets you set
(no secret values echoed) — the "Check config" button calls exactly this.

## Request

```jsonc
POST /api/social/publish
{
  "brand": "eduplace",
  "platforms": ["facebook", "instagram"],  // default both
  "message": "Caption / post text",
  "link": "https://eduplace.co.za/jobs",    // FB link posts
  "image_url": "https://.../card.jpg",       // req. for IG image
  "video_url": "https://.../reel.mp4",       // IG reels
  "media_type": "IMAGE",                     // or REELS
  "schedule_time": "2026-08-20T09:00:00Z",   // FB native scheduling
  "dry_run": false                           // true = validate only, no post
}
```

Each platform reports independently; the endpoint returns `502` only if every
requested platform errored.

## Cloudflare-specific notes

- **Reels / long video:** IG video containers process asynchronously. The Worker
  polls briefly (≈30s max) before publishing. Workers have a bounded request
  lifetime, so a long video can exceed it — for reliable reels, drive publishing
  from a **Cloudflare Queue** (or Cron Trigger) instead of one request.
- **Instagram scheduling & the 25-posts/24h cap:** Instagram has no native
  scheduling and Meta caps publishing at 25 posts/account/24h. The
  Buffer-equivalent "schedule + queue" layer is a **Cron Trigger + a store**
  (D1 or KV) of pending posts that POSTs due rows here. Not built yet — this is
  the natural next step on Cloudflare, and you have D1/KV available.
- **Meta setup** (accounts, tokens, App Review permissions) is identical to any
  host — see `../api/social/README.md` for that checklist; it's the real
  prerequisite before a live post will succeed.
