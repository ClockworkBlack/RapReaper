#!/usr/bin/env python3
"""Turn the two guides (docs/guide_windows.md, docs/guide_mac.md) into PDFs with Chromium (through Playwright).

BUILD TOOL (not shipped in a usable form). Fills in, before rendering:
    __AUDIO__ and __E2E__   the test counts, from build/test_counts.json ({"audio": N, "e2e": N})
    __GUIDE_TABLE__         the measured separation numbers, from build/eval.json (see build_release.py)

    python3 build/build_guides.py             # both PDFs into docs/
    python3 build/build_guides.py --html      # also keep the HTML next to them (to look at)
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DOCS = os.path.join(ROOT, "docs")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
body { font-family: "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 10.6pt; line-height: 1.45; color: #1b1b1b; }
h1 { font-size: 22pt; margin: 0 0 6pt; letter-spacing: -0.2pt; }
h2 { font-size: 15pt; margin: 22pt 0 6pt; padding-top: 6pt; border-top: 1px solid #c9c9c9; page-break-after: avoid; }
h3 { font-size: 12pt; margin: 14pt 0 4pt; page-break-after: avoid; }
p { margin: 5pt 0; }
li { margin: 2pt 0; }
code { font-family: Consolas, Menlo, "Courier New", monospace; font-size: 9.4pt; background: #f1f1f1; padding: 0 2px; border-radius: 2px; }
pre { background: #f1f1f1; padding: 6pt 8pt; font-size: 9pt; white-space: pre-wrap; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 8pt; font-size: 9.4pt; page-break-inside: auto; }
th, td { border: 1px solid #bbb; padding: 3pt 5pt; vertical-align: top; text-align: left; }
th { background: #e9e9e9; }
tr { page-break-inside: avoid; }
blockquote { margin: 6pt 0 6pt 12pt; padding-left: 8pt; border-left: 3px solid #bbb; color: #333; }
a { color: #0b4f8a; text-decoration: none; }
"""


def counts():
    p = os.path.join(ROOT, "build", "test_counts.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def fill(text):
    c = counts()
    text = text.replace("__AUDIO__", str(c.get("audio", "all"))).replace("__E2E__", str(c.get("e2e", "all")))
    try:
        from build_release import guide_table, load_eval
        text = text.replace("__GUIDE_TABLE__", guide_table(load_eval()))
    except Exception as e:  # the table is filled when the numbers exist; the build goes on without them
        text = text.replace("__GUIDE_TABLE__", "| (measurements not available in this build: %s) | | | | |" % e)
    return text


def to_html(md_text, title):
    import markdown
    import re
    # sub-points under numbered steps are written with three spaces in the sources; Python-Markdown wants four
    md_text = re.sub(r"^   - ", "    - ", md_text, flags=re.M)
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "sane_lists"])
    return "<!doctype html><html><head><meta charset='utf-8'><title>%s</title><style>%s</style></head><body>%s</body></html>" % (title, CSS, body)


def to_pdf(html_path, pdf_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None,
                                    args=["--no-sandbox"])
        page = browser.new_page()
        page.goto("file://" + html_path)
        page.wait_for_load_state("load")
        page.pdf(path=pdf_path, format="A4", print_background=True, margin={"top": "18mm", "bottom": "20mm", "left": "16mm", "right": "16mm"},
                 display_header_footer=True, header_template="<span></span>",
                 footer_template="<div style='font-size:8pt;color:#666;width:100%;text-align:center;'>RapReaper Guide &nbsp; page <span class='pageNumber'></span> of <span class='totalPages'></span></div>")
        browser.close()


def main(argv):
    keep_html = "--html" in argv
    made = []
    for src, title, out in (("guide_windows.md", "RapReaper Guide (Windows)", "RapReaper Guide (Windows).pdf"),
                            ("guide_mac.md", "RapReaper Guide (Mac)", "RapReaper Guide (Mac).pdf")):
        md_path = os.path.join(DOCS, src)
        if not os.path.isfile(md_path):
            print("missing", md_path)
            return 1
        text = fill(open(md_path, encoding="utf-8").read())
        html = to_html(text, title)
        html_path = os.path.join(DOCS, os.path.splitext(src)[0] + ".html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        pdf_path = os.path.join(DOCS, out)
        to_pdf(html_path, pdf_path)
        if not keep_html:
            os.remove(html_path)
        made.append("%s (%.0f KB)" % (out, os.path.getsize(pdf_path) / 1024.0))
    print("wrote " + ", ".join(made))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
