"""Health check endpoint for the Eduplace API."""
from http.server import BaseHTTPRequestHandler
import json

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        response = {
            "status": "healthy",
            "service": "Eduplace Recruitment Operating System",
            "version": "1.0.0",
            "endpoints": {
                "health": "/api/health",
                "resume_parse": "/api/resume/parse",
                "resume_generate": "/api/resume/generate",
                "webhook": "/api/webhook"
            },
            "airtable_base": "appC97LZ25VRInfRq",
            "tables": 16
        }
        self.wfile.write(json.dumps(response, indent=2).encode())
