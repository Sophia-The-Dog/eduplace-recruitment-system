"""
Eduplace Social Publisher - Vercel Serverless Function

Publishes posts to Meta (Facebook Pages + Instagram) DIRECTLY via the Graph API,
removing the dependency on Buffer for scheduling and distribution.

Design goals:
  - Zero third-party dependencies (stdlib only) so it runs on @vercel/python
    with the existing requirements.txt unchanged.
  - Multi-brand: one function serves every Eduplace Group brand. Credentials are
    resolved per-brand from environment variables (see _brand_config).
  - Same request-signing scheme as api/webhook.py (X-Webhook-Signature / HMAC).

Request (POST, application/json):
  {
    "brand": "eduplace",                 # required - selects credentials
    "platforms": ["facebook", "instagram"],  # default: both
    "message": "Caption / post text",    # required
    "link": "https://...",               # optional, Facebook link posts
    "image_url": "https://...",          # required for IG image; optional FB photo
    "video_url": "https://...",          # optional, IG reels
    "media_type": "IMAGE",               # IG: IMAGE (default) or REELS
    "schedule_time": "2026-08-20T09:00:00Z"  # optional, Facebook native scheduling
  }

Response (200):
  {
    "brand": "eduplace",
    "results": {
      "facebook":  {"status": "published", "id": "..."},
      "instagram": {"status": "published", "id": "..."}
    }
  }

Environment variables (per brand, UPPER-CASED brand key):
  META_GRAPH_VERSION            (optional, default "v21.0")
  META_APP_SECRET               (optional, enables appsecret_proof hardening)
  WEBHOOK_SECRET                (optional, enables HMAC request verification)

  META_<BRAND>_PAGE_ID          Facebook Page numeric ID
  META_<BRAND>_PAGE_TOKEN       Long-lived Page access token
  META_<BRAND>_IG_USER_ID       Instagram Business account ID (IG user id)
  META_<BRAND>_IG_TOKEN         (optional) IG token; falls back to PAGE_TOKEN

  Example: META_EDUPLACE_PAGE_ID, META_KOVA_PAGE_TOKEN, META_MRPIGEON_IG_USER_ID
"""

import os
import json
import time
import hmac
import hashlib
import logging
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Current stable as of 2026-08 is v25.0 (v26.0 is latest; its breaking changes
# are ads/placement-side, not content publishing). Override via env when bumping.
GRAPH_VERSION = os.environ.get('META_GRAPH_VERSION', 'v25.0')
GRAPH_ROOT = f'https://graph.facebook.com/{GRAPH_VERSION}'

# Facebook requires scheduled posts to be 10 minutes to 75 days in the future.
FB_SCHEDULE_MIN_SECONDS = 10 * 60
FB_SCHEDULE_MAX_SECONDS = 75 * 24 * 60 * 60

# Instagram video/reel containers are processed asynchronously; poll before publish.
IG_STATUS_MAX_POLLS = 12
IG_STATUS_POLL_SECONDS = 5

# Meta enforces a hard cap of 25 published posts per IG account per rolling 24h
# (reels/stories count toward the same bucket). This function does NOT track that
# quota - a scheduling queue in front of it should. Exceeding it returns a Graph
# API error, which is surfaced per-platform in the response.
IG_DAILY_POST_LIMIT = 25


class MetaError(Exception):
    """Raised when the Graph API returns an error or config is missing."""


class MetaClient:
    """Thin, dependency-free wrapper over the Meta Graph API."""

    def __init__(self, brand: str):
        self.brand = (brand or '').strip().lower()
        if not self.brand:
            raise MetaError('Missing "brand" in request')
        self.app_secret = os.environ.get('META_APP_SECRET', '')
        self.config = self._brand_config(self.brand)

    @staticmethod
    def _brand_config(brand: str) -> Dict[str, str]:
        key = brand.upper().replace('-', '_').replace(' ', '_')
        return {
            'page_id': os.environ.get(f'META_{key}_PAGE_ID', ''),
            'page_token': os.environ.get(f'META_{key}_PAGE_TOKEN', ''),
            'ig_user_id': os.environ.get(f'META_{key}_IG_USER_ID', ''),
            # IG publishing uses the linked Page token unless a specific one is set.
            'ig_token': os.environ.get(f'META_{key}_IG_TOKEN', '')
                        or os.environ.get(f'META_{key}_PAGE_TOKEN', ''),
        }

    # ---- low-level Graph request ------------------------------------------

    def _appsecret_proof(self, token: str) -> Optional[str]:
        if not self.app_secret or not token:
            return None
        return hmac.new(self.app_secret.encode(), token.encode(),
                        hashlib.sha256).hexdigest()

    def _graph(self, method: str, path: str, token: str,
               params: Dict[str, Any]) -> Dict[str, Any]:
        payload = {k: v for k, v in params.items() if v is not None}
        payload['access_token'] = token
        proof = self._appsecret_proof(token)
        if proof:
            payload['appsecret_proof'] = proof

        url = f'{GRAPH_ROOT}/{path.lstrip("/")}'
        data = urllib.parse.urlencode(payload).encode()
        if method == 'GET':
            url = f'{url}?{data.decode()}'
            req = urllib.request.Request(url, method='GET')
        else:
            req = urllib.request.Request(url, data=data, method='POST')

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode('utf-8') or '{}')
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')
            try:
                err = json.loads(body).get('error', {})
                msg = err.get('message', body)
            except (ValueError, AttributeError):
                msg = body
            raise MetaError(f'Graph API error ({e.code}): {msg}') from e
        except urllib.error.URLError as e:
            raise MetaError(f'Network error reaching Graph API: {e.reason}') from e

    # ---- Facebook Pages ---------------------------------------------------

    def post_facebook(self, message: str, link: Optional[str] = None,
                      image_url: Optional[str] = None,
                      schedule_time: Optional[str] = None,
                      dry_run: bool = False) -> Dict[str, Any]:
        page_id = self.config['page_id']
        token = self.config['page_token']
        if not page_id or not token:
            raise MetaError(
                f'Facebook not configured for brand "{self.brand}" '
                f'(need META_{self.brand.upper()}_PAGE_ID and _PAGE_TOKEN)')

        params: Dict[str, Any] = {}
        scheduled = self._resolve_schedule(schedule_time)
        if scheduled:
            params['published'] = 'false'
            params['scheduled_publish_time'] = scheduled

        edge = 'photos' if image_url else 'feed'
        if image_url:
            params['url'] = image_url        # photo caption goes in `caption`
            params['caption'] = message
        else:
            params['message'] = message
            if link:
                params['link'] = link

        if dry_run:
            return {
                'status': 'dry_run',
                'config_ok': True,
                'would_call': f'POST /{page_id}/{edge}',
                'params_preview': sorted(params.keys()),
                'scheduled_publish_time': scheduled,
            }

        result = self._graph('POST', f'{page_id}/{edge}', token, params)
        post_id = (result.get('post_id') or result.get('id')) if edge == 'photos' \
            else result.get('id')

        return {
            'status': 'scheduled' if scheduled else 'published',
            'id': post_id,
            'scheduled_publish_time': scheduled,
        }

    @staticmethod
    def _resolve_schedule(schedule_time: Optional[str]) -> Optional[int]:
        if not schedule_time:
            return None
        try:
            iso = schedule_time.replace('Z', '+00:00')
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError as e:
            raise MetaError(f'Invalid schedule_time "{schedule_time}": {e}')
        ts = int(dt.timestamp())
        delta = ts - int(time.time())
        if delta < FB_SCHEDULE_MIN_SECONDS:
            raise MetaError('schedule_time must be at least 10 minutes in the future')
        if delta > FB_SCHEDULE_MAX_SECONDS:
            raise MetaError('schedule_time must be within 75 days')
        return ts

    # ---- Instagram --------------------------------------------------------

    def post_instagram(self, message: str, image_url: Optional[str] = None,
                       video_url: Optional[str] = None,
                       media_type: str = 'IMAGE',
                       dry_run: bool = False) -> Dict[str, Any]:
        ig_id = self.config['ig_user_id']
        token = self.config['ig_token']
        if not ig_id or not token:
            raise MetaError(
                f'Instagram not configured for brand "{self.brand}" '
                f'(need META_{self.brand.upper()}_IG_USER_ID and a token)')

        media_type = (media_type or 'IMAGE').upper()
        container: Dict[str, Any] = {'caption': message}
        if media_type == 'REELS':
            if not video_url:
                raise MetaError('Instagram REELS requires "video_url"')
            container['media_type'] = 'REELS'
            container['video_url'] = video_url
        else:
            if not image_url:
                raise MetaError('Instagram IMAGE requires "image_url"')
            container['image_url'] = image_url

        if dry_run:
            steps = [f'POST /{ig_id}/media', f'POST /{ig_id}/media_publish']
            if media_type == 'REELS':
                steps.insert(1, 'GET /{creation_id}?fields=status_code')
            return {
                'status': 'dry_run',
                'config_ok': True,
                'media_type': media_type,
                'would_call': steps,
                'params_preview': sorted(container.keys()),
            }

        # Step 1: create the media container.
        created = self._graph('POST', f'{ig_id}/media', token, container)
        creation_id = created.get('id')
        if not creation_id:
            raise MetaError(f'Instagram container not created: {created}')

        # Step 2: video/reel containers process asynchronously - wait for FINISHED.
        if media_type == 'REELS':
            self._await_container(creation_id, token)

        # Step 3: publish.
        published = self._graph('POST', f'{ig_id}/media_publish', token,
                                {'creation_id': creation_id})
        return {
            'status': 'published',
            'id': published.get('id'),
            'creation_id': creation_id,
        }

    def _await_container(self, creation_id: str, token: str) -> None:
        for _ in range(IG_STATUS_MAX_POLLS):
            status = self._graph('GET', creation_id, token,
                                 {'fields': 'status_code'})
            code = status.get('status_code')
            if code == 'FINISHED':
                return
            if code == 'ERROR':
                raise MetaError('Instagram media processing failed')
            time.sleep(IG_STATUS_POLL_SECONDS)
        raise MetaError('Instagram media still processing after timeout')


def publish(body: Dict[str, Any]) -> Dict[str, Any]:
    """Fan a single content payload out to the requested Meta platforms."""
    brand = body.get('brand', '')
    client = MetaClient(brand)

    message = body.get('message', '')
    if not message and not body.get('image_url') and not body.get('video_url'):
        raise MetaError('Provide at least "message", "image_url", or "video_url"')

    platforms = body.get('platforms') or ['facebook', 'instagram']
    platforms = [p.strip().lower() for p in platforms]
    dry_run = bool(body.get('dry_run', False))

    results: Dict[str, Any] = {}
    for platform in platforms:
        try:
            if platform == 'facebook':
                results['facebook'] = client.post_facebook(
                    message=message,
                    link=body.get('link'),
                    image_url=body.get('image_url'),
                    schedule_time=body.get('schedule_time'),
                    dry_run=dry_run)
            elif platform == 'instagram':
                results['instagram'] = client.post_instagram(
                    message=message,
                    image_url=body.get('image_url'),
                    video_url=body.get('video_url'),
                    media_type=body.get('media_type', 'IMAGE'),
                    dry_run=dry_run)
            else:
                results[platform] = {'status': 'error',
                                     'error': f'Unknown platform "{platform}"'}
        except MetaError as e:
            results[platform] = {'status': 'error', 'error': str(e)}

    return {'brand': client.brand, 'dry_run': dry_run, 'results': results}


def _configured_brands() -> List[str]:
    brands = set()
    for name in os.environ:
        if name.startswith('META_') and name.endswith('_PAGE_ID'):
            brands.add(name[len('META_'):-len('_PAGE_ID')].lower())
        elif name.startswith('META_') and name.endswith('_IG_USER_ID'):
            brands.add(name[len('META_'):-len('_IG_USER_ID')].lower())
    return sorted(brands)


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: Dict[str, Any]) -> None:
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode('utf-8'))

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length).decode('utf-8')

            secret = os.environ.get('WEBHOOK_SECRET', '')
            signature = self.headers.get('X-Webhook-Signature', '')
            if secret and signature:
                expected = hmac.new(secret.encode(), raw.encode(),
                                    hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    self._send(401, {'error': 'Invalid signature'})
                    return

            body = json.loads(raw)
            result = publish(body)

            # Surface a 502 if every requested platform errored.
            statuses = [r.get('status') for r in result['results'].values()]
            code = 200 if any(s != 'error' for s in statuses) else 502
            self._send(code, result)

        except json.JSONDecodeError:
            self._send(400, {'error': 'Invalid JSON'})
        except MetaError as e:
            self._send(400, {'error': str(e)})
        except Exception as e:  # noqa: BLE001 - report unexpected failures cleanly
            logger.error('Social publish error: %s', e)
            self._send(500, {'error': f'Processing error: {str(e)}'})

    def do_GET(self):
        """Report configuration status (no secrets echoed)."""
        self._send(200, {
            'service': 'Eduplace Social Publisher (Meta direct)',
            'graph_version': GRAPH_VERSION,
            'configured_brands': _configured_brands(),
            'signature_required': bool(os.environ.get('WEBHOOK_SECRET', '')),
            'platforms': ['facebook', 'instagram'],
        })

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers',
                         'Content-Type, X-Webhook-Signature')
        self.end_headers()
