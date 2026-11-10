#!/usr/bin/env python3
"""make_store_sources_pdf.py - build "Store-Sources.pdf": the recommended app repos that do NOT come
bundled with the on-car MGU App Store, and the exact text to add each one from the car's screen.

    python3 installer/make_store_sources_pdf.py      # -> docs/Store-Sources.pdf  (needs reportlab)

The list lives in docs/store-sources.json (edit that, then re-run). Developer tool: the PDF it writes is
committed and shipped in the PC zip, so clients need nothing extra to read it.

"sources" entry:  {"repo": "owner/repo", "name": "...", "description": "one sentence",
                  "idrive": "7" | "8" | "7+8", "follow": "Releases" | "Tags" | "Branch: <name>",
                  "also": [{"repo": ..., "follow": ..., "why": ...}]   # optional: sources these apps need}
Built-in Store sources in the list are skipped automatically. "not_sources" lists repos that are in the
list but cannot be added from the car (shown with their link and why): {"repo", "name", "idrive",
"description", "why"}.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, 'docs', 'store-sources.json')
OUT = os.path.join(REPO, 'docs', 'Store-Sources.pdf')

# sources the Store already lists out of the box (tools/mgu-store/sources.json): never repeat these
BUILTIN_SHOWN = ['cauliflowermossgrape/idrive7-apps', 'robertnisipeanu/idrive-apps', 'IterioDev/car-apps']
BUILTIN = {b.lower() for b in BUILTIN_SHOWN + ['robertnisipeanu/mgu-store']}
GROUPS = [('7+8', 'iDrive 7 & iDrive 8'), ('7', 'iDrive 7 only'), ('8', 'iDrive 8 only')]


def load():
    data = json.load(open(SRC, encoding='utf-8'))
    out = []
    for e in data['sources']:
        if e['repo'].lower() in BUILTIN:
            continue
        if e['idrive'] not in dict(GROUPS):
            sys.exit('%s: idrive must be one of %s' % (e['repo'], ', '.join(k for k, _ in GROUPS)))
        out.append(e)
    return data, out


def build():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    data, entries = load()
    ss = getSampleStyleSheet()
    ink, muted, accent = colors.HexColor('#1c2430'), colors.HexColor('#5b6573'), colors.HexColor('#1f5fd6')
    H1 = ParagraphStyle('H1', parent=ss['Title'], fontName='Helvetica-Bold', fontSize=20, textColor=ink,
                        alignment=TA_LEFT, spaceAfter=4)
    SUB = ParagraphStyle('SUB', parent=ss['Normal'], fontSize=9.5, textColor=muted, leading=13)
    H2 = ParagraphStyle('H2', parent=ss['Heading2'], fontName='Helvetica-Bold', fontSize=14, textColor=accent,
                        spaceBefore=14, spaceAfter=6)
    NAME = ParagraphStyle('NAME', parent=ss['Normal'], fontName='Helvetica-Bold', fontSize=11.5, textColor=ink,
                          spaceAfter=1)
    BODY = ParagraphStyle('BODY', parent=ss['Normal'], fontSize=9.5, textColor=ink, leading=13)
    LABEL = ParagraphStyle('LABEL', parent=BODY, fontSize=8, textColor=muted)
    CODE = ParagraphStyle('CODE', parent=BODY, fontName='Courier-Bold', fontSize=11.5, textColor=ink)
    LINK = ParagraphStyle('LINK', parent=BODY, fontSize=9, textColor=accent)
    STEP = ParagraphStyle('STEP', parent=BODY, leftIndent=12, bulletIndent=0)

    def box(text):
        """The exact text to type, in a boxed monospace field."""
        t = Table([[Paragraph(text, CODE)]], colWidths=[6.6 * inch], hAlign='LEFT')
        t.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#9fb3d6')),
                               ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#eef3fb')),
                               ('LEFTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 5),
                               ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
        return t

    story = [Paragraph('Adding apps to the MGU App Store', H1),
             Paragraph('Recommended app repos that do <b>not</b> come bundled with the on-car Store, and the exact '
                       'text to type to add each one. Bimmhacks MGU Tool &middot; BMW iDrive 7 &amp; 8.', SUB),
             Spacer(1, 10),
             Paragraph('How to add a source on the car', H2)]
    for i, s in enumerate([
            'Install the App Store first (Bimmhacks MGU Tool &rarr; <b>App Store</b> tab), and give the car '
            'internet.',
            'On the car open <b>Apps &rarr; Store &rarr; Settings &rarr; Add source</b>.',
            'Type the text shown in the box for the repo <b>exactly</b> (owner/repo, no spaces).',
            'Pick what to follow &mdash; the <b>Follow</b> line under the box (Releases, Tags or a branch).',
            'The repo\'s apps now appear in the Store: install them from there.'], 1):
        story.append(Paragraph(s, STEP, bulletText='%d.' % i))
    story += [Spacer(1, 4),
              Paragraph('<b>iDrive 8:</b> typing on the car only works while the Store\'s renderer draws the '
                        'screen. If the keyboard does not come up, add the source from the Store\'s phone page or '
                        'from the computer (Bimmhacks MGU Tool &rarr; App Store &rarr; <i>Add your repo to the '
                        'Store</i>). Already in the Store, nothing to add: '
                        + ', '.join(BUILTIN_SHOWN) + '.', SUB)]

    for key, title in GROUPS:
        group = [e for e in entries if e['idrive'] == key]
        if not group:
            continue
        story.append(Paragraph(title, H2))
        for e in group:
            url = 'https://github.com/' + e['repo']
            block = [Paragraph(e['name'], NAME),
                     Paragraph(e['description'], BODY), Spacer(1, 4),
                     Paragraph('TYPE IN THE MGU APP STORE (Settings &rarr; Add source)', LABEL), box(e['repo']),
                     Paragraph('Follow: <b>%s</b>' % e['follow'], LABEL), Spacer(1, 2),
                     Paragraph('Repo on your PC: <link href="%s" color="#1f5fd6">%s</link>' % (url, url), LINK)]
            for extra in e.get('also', []):
                xurl = 'https://github.com/' + extra['repo']
                block += [Spacer(1, 5), Paragraph('ALSO ADD (these apps need it) &mdash; ' + extra['why'], LABEL),
                          box(extra['repo']), Paragraph('Follow: <b>%s</b>' % extra['follow'], LABEL),
                          Paragraph('Repo on your PC: <link href="%s" color="#1f5fd6">%s</link>' % (xurl, xurl),
                                    LINK)]
            story.append(KeepTogether(block + [Spacer(1, 12)]))

    others = data.get('not_sources', [])
    if others:
        story.append(Paragraph('In the list, but not added from the car', H2))
        story.append(Paragraph('These are not MGU App Store sources, so there is nothing to type into the Store. '
                               'The link is there to read about each one on your PC.', SUB))
        story.append(Spacer(1, 6))
        for e in others:
            url = 'https://github.com/' + e['repo']
            story.append(KeepTogether([
                Paragraph('%s <font size="8.5" color="#5b6573">&middot; %s</font>' % (e['name'], e['idrive']), NAME),
                Paragraph(e['description'], BODY),
                Paragraph('Why not in the Store: ' + e['why'], LABEL),
                Paragraph('Repo on your PC: <link href="%s" color="#1f5fd6">%s</link>' % (url, url), LINK),
                Spacer(1, 10)]))

    story += [Spacer(1, 6), Paragraph('Each app keeps its own author and licence &mdash; see its repo. '
                                      'List checked: %s.' % data.get('updated', ''), SUB)]
    doc = SimpleDocTemplate(OUT, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch, title='Adding apps to the MGU App Store',
                            author='Bimmhacks')
    doc.build(story)
    print('%s  (%d repos)' % (OUT, len(entries)))


if __name__ == '__main__':
    build()
