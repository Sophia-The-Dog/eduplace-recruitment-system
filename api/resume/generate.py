"""
Eduplace Resume Generator - Vercel Serverless Function
Generates formatted PDF resumes with Eduplace branding
"""
import json
import io
from http.server import BaseHTTPRequestHandler
from typing import Dict, Any, List

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
except ImportError:
    pass

NAVY = HexColor('#1B2A4A')
BLUE = HexColor('#2E86AB')
GOLD = HexColor('#D4A84B')
DARK_GRAY = HexColor('#333333')

class EduplacePDFGenerator:
    def __init__(self, format_type='global'):
        self.format_type = format_type
        self.page_width, self.page_height = letter
        self.margin = 0.5 * inch

    def generate(self, candidate_data: Dict[str, Any]) -> bytes:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter,
            rightMargin=self.margin, leftMargin=self.margin,
            topMargin=self.margin, bottomMargin=self.margin)
        story = []
        styles = self._get_styles()

        personal = candidate_data.get('personal_info', {})
        name = personal.get('name', 'Candidate Name')
        story.append(Paragraph(f'<font color="#1B2A4A" size="18"><b>{name}</b></font>', styles['Normal']))

        contact_parts = []
        if personal.get('email'): contact_parts.append(personal['email'])
        if personal.get('phone'): contact_parts.append(personal['phone'])
        if contact_parts:
            story.append(Paragraph(f'<font color="#666666" size="9">{" | ".join(contact_parts)}</font>', styles['Normal']))
        story.append(Spacer(1, 0.1 * inch))

        if candidate_data.get('professional_summary'):
            story.append(Paragraph('<b><font color="#2E86AB">Professional Summary</font></b>', styles['Heading1']))
            story.append(Paragraph(candidate_data['professional_summary'], styles['Normal']))
            story.append(Spacer(1, 0.1 * inch))

        if candidate_data.get('experience'):
            story.append(Paragraph('<b><font color="#2E86AB">Professional Experience</font></b>', styles['Heading1']))
            for exp in candidate_data['experience']:
                title = exp.get('title', '')
                company = exp.get('company', '')
                dates = exp.get('dates', '')
                story.append(Paragraph(f'<b>{title}</b> | {company}', styles['Normal']))
                if dates: story.append(Paragraph(f'<i>{dates}</i>', styles['Normal']))
                for r in exp.get('responsibilities', [])[:5]:
                    story.append(Paragraph(f'\u2022 {r}', styles['Normal']))
                story.append(Spacer(1, 0.08 * inch))
            story.append(Spacer(1, 0.1 * inch))

        if candidate_data.get('education'):
            story.append(Paragraph('<b><font color="#2E86AB">Education</font></b>', styles['Heading1']))
            for edu in candidate_data['education']:
                story.append(Paragraph(edu.get('entry', ''), styles['Normal']))
            story.append(Spacer(1, 0.1 * inch))

        if candidate_data.get('skills'):
            story.append(Paragraph('<b><font color="#2E86AB">Skills</font></b>', styles['Heading1']))
            story.append(Paragraph(', '.join(candidate_data['skills'][:15]), styles['Normal']))

        story.append(Spacer(1, 0.15 * inch))
        story.append(Paragraph('<font color="#999999" size="8">Prepared by Eduplace | Confidential</font>', styles['Normal']))

        doc.build(story)
        return buffer.getvalue()

    def _get_styles(self):
        styles = getSampleStyleSheet()
        return styles

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)
            candidate_data = data.get('candidate_data', {})
            format_type = data.get('format', 'global')

            if not candidate_data:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'candidate_data is required'}).encode('utf-8'))
                return

            generator = EduplacePDFGenerator(format_type)
            pdf_bytes = generator.generate(candidate_data)

            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            self.send_header('Content-Disposition', 'attachment; filename="resume.pdf"')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(pdf_bytes)))
            self.end_headers()
            self.wfile.write(pdf_bytes)

        except json.JSONDecodeError:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Invalid JSON'}).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': f'Generation error: {str(e)}'}).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
