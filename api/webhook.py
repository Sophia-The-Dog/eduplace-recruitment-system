"""
Eduplace Webhook Handler - Vercel Serverless Function
Handles Airtable/Make webhooks for recruitment events
"""

import json
import os
import logging
from http.server import BaseHTTPRequestHandler
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger(__name__)

class WebhookProcessor:
    EVENTS = {
        'candidate.created': 'A new candidate was added',
        'candidate.updated': 'Candidate profile was updated',
        'resume.uploaded': 'Resume file was uploaded',
        'resume.parsed': 'Resume was parsed successfully',
        'stage.changed': 'Candidate stage was changed',
        'placement.created': 'Placement record was created',
        'interview.scheduled': 'Interview was scheduled',
        'feedback.submitted': 'Interview feedback was submitted',
    }

    def __init__(self):
        self.webhook_secret = os.environ.get('WEBHOOK_SECRET', '')
        self.logs = []

    def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        timestamp = datetime.utcnow().isoformat()
        event = event_data.get('event', '')
        record_id = event_data.get('record_id', '')
        table = event_data.get('table', '')
        data = event_data.get('data', {})

        if event not in self.EVENTS:
            return {'status': 'error', 'message': f'Unknown event type: {event}', 'timestamp': timestamp}

        log_entry = {
            'timestamp': timestamp, 'event': event, 'record_id': record_id,
            'table': table, 'description': self.EVENTS[event],
            'data_keys': list(data.keys()) if data else [],
        }
        self.logs.append(log_entry)

        processed = True
        if event == 'candidate.created':
            logger.info(f'New candidate created: {record_id}')
        elif event == 'resume.uploaded':
            logger.info(f'Resume uploaded for candidate: {record_id}')
        elif event == 'stage.changed':
            logger.info(f'Stage changed for {record_id}: {data.get("old_stage")} -> {data.get("new_stage")}')
        elif event == 'placement.created':
            logger.info(f'Placement created: {record_id}')
        elif event == 'interview.scheduled':
            logger.info(f'Interview scheduled: {record_id}')
        elif event == 'feedback.submitted':
            logger.info(f'Feedback submitted: {record_id}')

        return {
            'status': 'received' if processed else 'pending',
            'event': event, 'record_id': record_id, 'table': table,
            'message': f'Webhook {event} processed successfully',
            'timestamp': timestamp, 'processed': processed,
        }

    def get_logs(self):
        return self.logs

processor = WebhookProcessor()

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)

            signature = self.headers.get('X-Webhook-Signature', '')
            webhook_secret = os.environ.get('WEBHOOK_SECRET', '')
            if webhook_secret and signature:
                import hmac, hashlib
                expected = hmac.new(webhook_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
                if signature != expected:
                    self.send_response(401)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Invalid signature'}).encode('utf-8'))
                    return

            result = processor.process(data)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode('utf-8'))

        except json.JSONDecodeError:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Invalid JSON'}).encode('utf-8'))
        except Exception as e:
            logger.error(f'Webhook error: {str(e)}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': f'Processing error: {str(e)}'}).encode('utf-8'))

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        logs = processor.get_logs()
        self.wfile.write(json.dumps({'webhook_logs': logs, 'total_processed': len(logs)}).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Webhook-Signature')
        self.end_headers()
