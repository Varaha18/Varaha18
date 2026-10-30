"""Renders a TailoredResume to ATS-friendly DOCX and PDF (single column, no tables)."""
from __future__ import annotations

import html
import os
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from .resume import TailoredResume


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60]


def _contact_line(p: dict) -> str:
    keys = ("location", "email", "phone", "linkedin", "github", "website")
    return " | ".join(str(p[k]) for k in keys if p.get(k))


def render_docx(r: TailoredResume, personal: dict, path: Path) -> Path:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    for section in doc.sections:
        section.left_margin = section.right_margin = Pt(50)
        section.top_margin = section.bottom_margin = Pt(40)

    def heading(text):
        para = doc.add_paragraph()
        run = para.add_run(text.upper())
        run.bold = True
        run.font.size = Pt(11.5)
        para.paragraph_format.space_before = Pt(10)
        para.paragraph_format.space_after = Pt(2)

    def bullet(text):
        para = doc.add_paragraph(text, style="List Bullet")
        para.paragraph_format.space_after = Pt(1)

    name = doc.add_paragraph()
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = name.add_run(personal.get("name", ""))
    run.bold = True
    run.font.size = Pt(18)
    sub = doc.add_paragraph(r.headline)
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    contact = doc.add_paragraph(_contact_line(personal))
    contact.alignment = WD_ALIGN_PARAGRAPH.CENTER

    heading("Professional Summary")
    doc.add_paragraph(r.summary)

    heading("Skills")
    for g in r.skills:
        para = doc.add_paragraph()
        para.add_run(f"{g.category}: ").bold = True
        para.add_run(", ".join(g.items))
        para.paragraph_format.space_after = Pt(1)

    if r.experience:
        heading("Professional Experience")
        for role in r.experience:
            para = doc.add_paragraph()
            para.add_run(f"{role.title} | {role.company}").bold = True
            para.add_run(f"  —  {role.location}  |  {role.dates}")
            para.paragraph_format.space_before = Pt(6)
            para.paragraph_format.space_after = Pt(0)
            if role.company_context:
                ctx = doc.add_paragraph()
                ctx.add_run(role.company_context).italic = True
                ctx.paragraph_format.space_after = Pt(2)
            for b in role.bullets:
                bullet(b.text)

    if r.projects:
        heading("Projects")
        for pr in r.projects:
            para = doc.add_paragraph()
            para.add_run(pr.name).bold = True
            para.add_run(f"  —  {pr.context}  |  {pr.dates}")
            para.paragraph_format.space_before = Pt(6)
            for b in pr.bullets:
                bullet(b.text)

    if r.education:
        heading("Education")
        for e in r.education:
            para = doc.add_paragraph()
            para.add_run(f"{e.degree} | {e.institution}").bold = True
            para.add_run(f"  —  {e.location}  |  {e.dates}")
            if e.details:
                doc.add_paragraph(e.details)

    if r.certifications:
        heading("Certifications")
        for c in r.certifications:
            bullet(c)
    if r.languages:
        heading("Languages")
        doc.add_paragraph(", ".join(r.languages))

    doc.save(path)
    return path


CSS = """
body{font-family:Calibri,Arial,Helvetica,sans-serif;font-size:10.5pt;color:#111;margin:0;line-height:1.3}
h1{font-size:19pt;margin:0;text-align:center} .sub{text-align:center;font-size:11.5pt;margin:2px 0}
.contact{text-align:center;font-size:9.5pt;color:#333;margin-bottom:6px}
h2{font-size:11pt;text-transform:uppercase;border-bottom:1px solid #444;margin:12px 0 4px;padding-bottom:1px}
.role{margin-top:7px}.role b{font-size:10.5pt}.meta{float:right;color:#333}
.ctx{font-style:italic;color:#333;margin:1px 0 2px}
ul{margin:2px 0 0 16px;padding:0} li{margin:1px 0} p{margin:2px 0}
"""


def resume_html(r: TailoredResume, personal: dict) -> str:
    e = html.escape

    def bullets(items):
        return "<ul>" + "".join(f"<li>{e(b.text)}</li>" for b in items) + "</ul>"

    parts = [f"<h1>{e(personal.get('name', ''))}</h1><div class='sub'>{e(r.headline)}</div>",
             f"<div class='contact'>{e(_contact_line(personal))}</div>",
             f"<h2>Professional Summary</h2><p>{e(r.summary)}</p><h2>Skills</h2>"]
    parts += [f"<p><b>{e(g.category)}:</b> {e(', '.join(g.items))}</p>" for g in r.skills]
    if r.experience:
        parts.append("<h2>Professional Experience</h2>")
        for role in r.experience:
            parts.append(f"<div class='role'><span class='meta'>{e(role.location)} | {e(role.dates)}</span>"
                         f"<b>{e(role.title)} | {e(role.company)}</b>"
                         f"<div class='ctx'>{e(role.company_context)}</div>{bullets(role.bullets)}</div>")
    if r.projects:
        parts.append("<h2>Projects</h2>")
        for p in r.projects:
            parts.append(f"<div class='role'><span class='meta'>{e(p.dates)}</span><b>{e(p.name)}</b>"
                         f"<div class='ctx'>{e(p.context)}</div>{bullets(p.bullets)}</div>")
    if r.education:
        parts.append("<h2>Education</h2>")
        for ed in r.education:
            parts.append(f"<div class='role'><span class='meta'>{e(ed.location)} | {e(ed.dates)}</span>"
                         f"<b>{e(ed.degree)} | {e(ed.institution)}</b><p>{e(ed.details)}</p></div>")
    if r.certifications:
        parts.append("<h2>Certifications</h2><ul>" + "".join(f"<li>{e(c)}</li>" for c in r.certifications) + "</ul>")
    if r.languages:
        parts.append(f"<h2>Languages</h2><p>{e(', '.join(r.languages))}</p>")
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(parts)}</body></html>"


def letter_html(text: str, personal: dict) -> str:
    paras = "".join(f"<p>{html.escape(p)}</p>" for p in text.split("\n") if p.strip())
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS} p{{margin:0 0 10px}}</style></head>"
            f"<body><h1>{html.escape(personal.get('name', ''))}</h1>"
            f"<div class='contact'>{html.escape(_contact_line(personal))}</div><br>{paras}</body></html>")


def html_to_pdf(markup: str, path: Path) -> Path:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("JOBBOT_CHROMIUM") or None)
        page = browser.new_page()
        page.set_content(markup, wait_until="load")
        page.pdf(path=str(path), format="A4",
                 margin={"top": "14mm", "bottom": "14mm", "left": "16mm", "right": "16mm"})
        browser.close()
    return path
