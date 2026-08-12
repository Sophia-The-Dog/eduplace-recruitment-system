/**
 * Eduplace Social Publisher - Cloudflare Worker
 *
 * Publishes posts to Meta (Facebook Pages + Instagram) DIRECTLY via the Graph
 * API, replacing the dependency on Buffer. Port of the reference implementation
 * to the Cloudflare Workers runtime (fetch + Web Crypto, no dependencies).
 *
 * Routes:
 *   GET  /api/social/publish   -> config/status (no secrets echoed)
 *   POST /api/social/publish   -> publish (supports "dry_run": true)
 *   *                          -> static assets (the /social-test.html console)
 *
 * Configuration (Worker vars/secrets - set per brand, brand key upper-cased):
 *   META_GRAPH_VERSION   (var, default "v25.0")
 *   META_APP_SECRET      (secret, optional - enables appsecret_proof)
 *   WEBHOOK_SECRET       (secret, optional - requires X-Webhook-Signature HMAC)
 *   META_<BRAND>_PAGE_ID
 *   META_<BRAND>_PAGE_TOKEN
 *   META_<BRAND>_IG_USER_ID
 *   META_<BRAND>_IG_TOKEN   (optional; falls back to PAGE_TOKEN)
 */

const DEFAULT_VERSION = 'v25.0';
const FB_SCHEDULE_MIN_SECONDS = 10 * 60;
const FB_SCHEDULE_MAX_SECONDS = 75 * 24 * 60 * 60;

// IG video/reel containers process asynchronously. Workers have a bounded
// request lifetime, so keep polling modest; long videos belong in a queue.
const IG_STATUS_MAX_POLLS = 6;
const IG_STATUS_POLL_MS = 5000;
const IG_DAILY_POST_LIMIT = 25; // Meta hard cap per IG account / rolling 24h.

class MetaError extends Error {}

const te = (s) => new TextEncoder().encode(s);
const graphRoot = (env) => `https://graph.facebook.com/${env.META_GRAPH_VERSION || DEFAULT_VERSION}`;

function clean(obj) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) if (v !== undefined && v !== null) out[k] = v;
  return out;
}

async function hmacHex(secret, message) {
  const key = await crypto.subtle.importKey('raw', te(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, te(message));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function constantTimeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function brandKey(brand) {
  return brand.toUpperCase().replace(/[-\s]/g, '_');
}

function brandConfig(env, brand) {
  const k = brandKey(brand);
  return {
    page_id: env[`META_${k}_PAGE_ID`] || '',
    page_token: env[`META_${k}_PAGE_TOKEN`] || '',
    ig_user_id: env[`META_${k}_IG_USER_ID`] || '',
    ig_token: env[`META_${k}_IG_TOKEN`] || env[`META_${k}_PAGE_TOKEN`] || '',
  };
}

function configuredBrands(env) {
  const set = new Set();
  for (const name of Object.keys(env)) {
    if (typeof env[name] !== 'string') continue; // skip bindings (ASSETS, etc.)
    if (name.startsWith('META_') && name.endsWith('_PAGE_ID')) set.add(name.slice(5, -8).toLowerCase());
    else if (name.startsWith('META_') && name.endsWith('_IG_USER_ID')) set.add(name.slice(5, -11).toLowerCase());
  }
  return [...set].sort();
}

async function graph(env, method, path, token, params) {
  const payload = { ...clean(params), access_token: token };
  if (env.META_APP_SECRET) payload.appsecret_proof = await hmacHex(env.META_APP_SECRET, token);

  const form = new URLSearchParams(payload).toString();
  const base = `${graphRoot(env)}/${path.replace(/^\//, '')}`;
  let resp;
  try {
    if (method === 'GET') {
      resp = await fetch(`${base}?${form}`, { method: 'GET' });
    } else {
      resp = await fetch(base, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: form,
      });
    }
  } catch (e) {
    throw new MetaError(`Network error reaching Graph API: ${e.message}`);
  }
  const text = await resp.text();
  let json;
  try { json = text ? JSON.parse(text) : {}; } catch { json = { raw: text }; }
  if (!resp.ok) {
    const msg = json && json.error && json.error.message ? json.error.message : text;
    throw new MetaError(`Graph API error (${resp.status}): ${msg}`);
  }
  return json;
}

function resolveSchedule(scheduleTime) {
  if (!scheduleTime) return null;
  const ms = Date.parse(scheduleTime);
  if (Number.isNaN(ms)) throw new MetaError(`Invalid schedule_time "${scheduleTime}"`);
  const ts = Math.floor(ms / 1000);
  const delta = ts - Math.floor(Date.now() / 1000);
  if (delta < FB_SCHEDULE_MIN_SECONDS) throw new MetaError('schedule_time must be at least 10 minutes in the future');
  if (delta > FB_SCHEDULE_MAX_SECONDS) throw new MetaError('schedule_time must be within 75 days');
  return ts;
}

async function postFacebook(env, brand, cfg, { message, link, image_url, schedule_time, dry_run }) {
  if (!cfg.page_id || !cfg.page_token) {
    throw new MetaError(`Facebook not configured for brand "${brand}" (need META_${brandKey(brand)}_PAGE_ID and _PAGE_TOKEN)`);
  }
  const params = {};
  const scheduled = resolveSchedule(schedule_time);
  if (scheduled) {
    params.published = 'false';
    params.scheduled_publish_time = scheduled;
  }
  const edge = image_url ? 'photos' : 'feed';
  if (image_url) {
    params.url = image_url;      // photo caption goes in `caption`
    params.caption = message;
  } else {
    params.message = message;
    if (link) params.link = link;
  }

  if (dry_run) {
    return {
      status: 'dry_run',
      config_ok: true,
      would_call: `POST /${cfg.page_id}/${edge}`,
      params_preview: Object.keys(params).sort(),
      scheduled_publish_time: scheduled,
    };
  }

  const result = await graph(env, 'POST', `${cfg.page_id}/${edge}`, cfg.page_token, params);
  const postId = edge === 'photos' ? (result.post_id || result.id) : result.id;
  return { status: scheduled ? 'scheduled' : 'published', id: postId, scheduled_publish_time: scheduled };
}

async function awaitContainer(env, creationId, token) {
  for (let i = 0; i < IG_STATUS_MAX_POLLS; i++) {
    const status = await graph(env, 'GET', creationId, token, { fields: 'status_code' });
    if (status.status_code === 'FINISHED') return;
    if (status.status_code === 'ERROR') throw new MetaError('Instagram media processing failed');
    await new Promise((r) => setTimeout(r, IG_STATUS_POLL_MS));
  }
  throw new MetaError('Instagram media still processing after timeout (use a queue for long videos)');
}

async function postInstagram(env, brand, cfg, { message, image_url, video_url, media_type, dry_run }) {
  if (!cfg.ig_user_id || !cfg.ig_token) {
    throw new MetaError(`Instagram not configured for brand "${brand}" (need META_${brandKey(brand)}_IG_USER_ID and a token)`);
  }
  const type = (media_type || 'IMAGE').toUpperCase();
  const container = { caption: message };
  if (type === 'REELS') {
    if (!video_url) throw new MetaError('Instagram REELS requires "video_url"');
    container.media_type = 'REELS';
    container.video_url = video_url;
  } else {
    if (!image_url) throw new MetaError('Instagram IMAGE requires "image_url"');
    container.image_url = image_url;
  }

  if (dry_run) {
    const steps = [`POST /${cfg.ig_user_id}/media`, `POST /${cfg.ig_user_id}/media_publish`];
    if (type === 'REELS') steps.splice(1, 0, 'GET /{creation_id}?fields=status_code');
    return { status: 'dry_run', config_ok: true, media_type: type, would_call: steps, params_preview: Object.keys(container).sort() };
  }

  const created = await graph(env, 'POST', `${cfg.ig_user_id}/media`, cfg.ig_token, container);
  const creationId = created.id;
  if (!creationId) throw new MetaError(`Instagram container not created: ${JSON.stringify(created)}`);

  if (type === 'REELS') await awaitContainer(env, creationId, cfg.ig_token);

  const published = await graph(env, 'POST', `${cfg.ig_user_id}/media_publish`, cfg.ig_token, { creation_id: creationId });
  return { status: 'published', id: published.id, creation_id: creationId };
}

async function publish(env, body) {
  const brand = (body.brand || '').trim().toLowerCase();
  if (!brand) throw new MetaError('Missing "brand" in request');
  const cfg = brandConfig(env, brand);

  const message = body.message || '';
  if (!message && !body.image_url && !body.video_url) {
    throw new MetaError('Provide at least "message", "image_url", or "video_url"');
  }

  const platforms = (body.platforms && body.platforms.length ? body.platforms : ['facebook', 'instagram'])
    .map((p) => String(p).trim().toLowerCase());
  const dry_run = Boolean(body.dry_run);

  const results = {};
  for (const platform of platforms) {
    try {
      if (platform === 'facebook') {
        results.facebook = await postFacebook(env, brand, cfg, {
          message, link: body.link, image_url: body.image_url, schedule_time: body.schedule_time, dry_run,
        });
      } else if (platform === 'instagram') {
        results.instagram = await postInstagram(env, brand, cfg, {
          message, image_url: body.image_url, video_url: body.video_url, media_type: body.media_type || 'IMAGE', dry_run,
        });
      } else {
        results[platform] = { status: 'error', error: `Unknown platform "${platform}"` };
      }
    } catch (e) {
      results[platform] = { status: 'error', error: e instanceof MetaError ? e.message : String(e.message || e) };
    }
  }
  return { brand, dry_run, results };
}

// ---- HTTP layer -----------------------------------------------------------

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-Webhook-Signature',
};

function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS },
  });
}

function configResponse(env) {
  return {
    service: 'Eduplace Social Publisher (Meta direct) - Cloudflare Worker',
    graph_version: env.META_GRAPH_VERSION || DEFAULT_VERSION,
    configured_brands: configuredBrands(env),
    signature_required: Boolean(env.WEBHOOK_SECRET),
    platforms: ['facebook', 'instagram'],
    ig_daily_post_limit: IG_DAILY_POST_LIMIT,
  };
}

async function handlePost(request, env) {
  const raw = await request.text();

  if (env.WEBHOOK_SECRET) {
    const signature = request.headers.get('X-Webhook-Signature') || '';
    const expected = await hmacHex(env.WEBHOOK_SECRET, raw);
    if (!signature || !constantTimeEqual(signature, expected)) {
      return jsonResponse({ error: 'Invalid signature' }, 401);
    }
  }

  let body;
  try { body = JSON.parse(raw); } catch { return jsonResponse({ error: 'Invalid JSON' }, 400); }

  try {
    const result = await publish(env, body);
    const statuses = Object.values(result.results).map((r) => r.status);
    const code = statuses.some((s) => s !== 'error') ? 200 : 502;
    return jsonResponse(result, code);
  } catch (e) {
    if (e instanceof MetaError) return jsonResponse({ error: e.message }, 400);
    return jsonResponse({ error: `Processing error: ${e.message || e}` }, 500);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });

    if (url.pathname === '/api/social/publish') {
      if (request.method === 'GET') return jsonResponse(configResponse(env));
      if (request.method === 'POST') return handlePost(request, env);
      return jsonResponse({ error: 'Method not allowed' }, 405);
    }

    // Everything else -> static assets (the test console), if the binding exists.
    if (env.ASSETS) return env.ASSETS.fetch(request);
    return new Response('Not found', { status: 404 });
  },
};
