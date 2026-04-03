"""
Eduplace Resume Parser - Vercel Serverless Function
Parses resume text and extracts structured data
"""
import json
import os
import re
from http.server import BaseHTTPRequestHandler
from typing import Dict, List, Any

class ResumeParser:
    def __init__(self):
        self.phone_pattern = r'(\+?1?\s*)?(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})'
        self.email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'

    def parse(self, text: str) -> Dict[str, Any]:
        lines = text.split('\n')
        return {
            'personal_info': self._extract_personal_info(text),
            'professional_summary': self._extract_summary(lines),
            'experience': self._extract_experience(lines),
            'education': self._extract_education(lines),
            'skills': self._extract_skills(lines),
            'certifications': self._extract_certifications(lines),
            'languages': self._extract_languages(lines),
        }

    def _extract_personal_info(self, text):
        info = {}
        email_match = re.search(self.email_pattern, text)
        if email_match: info['email'] = email_match.group(0)
        phone_match = re.search(self.phone_pattern, text)
        if phone_match: info['phone'] = phone_match.group(0).strip()
        for line in text.split('\n'):
            stripped = line.strip()
            if stripped and len(stripped) < 100:
                info['name'] = stripped
                break
        return info

    def _extract_summary(self, lines):
        keywords = ['summary', 'objective', 'professional', 'about']
        for i, line in enumerate(lines):
            if any(k in line.lower() for k in keywords):
                summary_lines = []
                for j in range(i+1, min(i+4, len(lines))):
                    if lines[j].strip() and not any(k in lines[j].lower() for k in ['experience','education','skills']):
                        summary_lines.append(lines[j].strip())
                    else: break
                if summary_lines: return ' '.join(summary_lines)[:500]
        return ''

    def _extract_experience(self, lines):
        experiences = []
        in_exp = False
        current = {}
        for line in lines:
            lower = line.lower()
            if any(k in lower for k in ['experience', 'work', 'employment']):
                in_exp = True; continue
            if in_exp:
                if any(k in lower for k in ['education', 'skills', 'certification', 'languages']):
                    if current: experiences.append(current); current = {}
                    in_exp = False; continue
                stripped = line.strip()
                if not stripped:
                    if current.get('title'): experiences.append(current); current = {}
                    continue
                if '|' in line or ' - ' in line:
                    parts = re.split(r'\s*[\|-]\s*', stripped)
                    if len(parts) >= 2:
                        current['title'] = parts[0].strip()
                        current['company'] = parts[1].strip() if len(parts) > 1 else ''
                        current['dates'] = parts[2].strip() if len(parts) > 2 else ''
                elif current.get('title'):
                    if 'responsibilities' not in current: current['responsibilities'] = []
                    if stripped.startswith('-') or stripped.startswith('\u2022'):
                        current['responsibilities'].append(stripped.lstrip('-\u2022').strip())
        if current.get('title'): experiences.append(current)
        return experiences

    def _extract_education(self, lines):
        education = []
        in_edu = False
        for line in lines:
            lower = line.lower()
            if any(k in lower for k in ['education', 'degree', 'university']):
                in_edu = True; continue
            if in_edu:
                if any(k in lower for k in ['skills', 'certification', 'languages', 'experience']):
                    in_edu = False; continue
                stripped = line.strip()
                if stripped and len(stripped) > 10:
                    entry = {'entry': stripped}
                    for dt in ['B.S.', 'B.A.', 'M.S.', 'M.A.', 'PhD', 'Bachelor', 'Master']:
                        if dt in stripped: entry['degree_type'] = dt; break
                    education.append(entry)
        return education

    def _extract_skills(self, lines):
        skills = []
        in_skills = False
        for line in lines:
            lower = line.lower()
            if any(k in lower for k in ['skills', 'technical', 'competencies']):
                in_skills = True; continue
            if in_skills:
                if any(k in lower for k in ['education', 'certification', 'languages', 'experience']):
                    in_skills = False; continue
                stripped = line.strip()
                if stripped:
                    for s in re.split(r'[,;\u2022\-|]', stripped):
                        c = s.strip()
                        if c and len(c) > 2: skills.append(c)
        return list(set(skills))[:20]

    def _extract_certifications(self, lines):
        certs = []
        in_certs = False
        for line in lines:
            lower = line.lower()
            if any(k in lower for k in ['certification', 'certified']):
                in_certs = True; continue
            if in_certs:
                if any(k in lower for k in ['languages', 'skills', 'education']):
                    in_certs = False; continue
                stripped = line.strip()
                if stripped and len(stripped) > 5:
                    certs.append(stripped.lstrip('-\u2022').strip())
        return certs

    def _extract_languages(self, lines):
        languages = []
        in_langs = False
        for line in lines:
            if 'language' in line.lower(): in_langs = True; continue
            if in_langs:
                stripped = line.strip()
                if stripped:
                    for l in re.split(r'[,;\u2022\-|]', stripped):
                        c = l.strip()
                        if c and len(c) > 2: languages.append(c)
        return languages

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)
            parser = ResumeParser()
            if 'text' in data:
                parsed = parser.parse(data['text'])
                self.wfile.write(json.dumps({'success': True, 'data': parsed}).encode('utf-8'))
            else:
                self.wfile.write(json.dumps({'error': 'Provide "text" field'}).encode('utf-8'))
        except json.JSONDecodeError:
            self.wfile.write(json.dumps({'error': 'Invalid JSON'}).encode('utf-8'))
        except Exception as e:
            self.wfile.write(json.dumps({'error': f'Error: {str(e)}'}).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
