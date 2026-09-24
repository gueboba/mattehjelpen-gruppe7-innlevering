"""Lager PDF av refleksjonsnotatet (Del C).

    python scripts/lag_pdf.py

Leser `refleksjonsnotat/Refleksjonsnotat.md`, lager en enkel HTML-versjon med
A4-oppsett, og skriver ut til PDF med Chrome i hodeløs modus. Vi bruker Chrome
fordi det er det eneste PDF-verktøyet som allerede fantes på maskinen – ingen
nye installasjoner, i tråd med gratisprinsippet.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROT = Path(__file__).resolve().parent.parent
KILDE = ROT / "refleksjonsnotat" / "Refleksjonsnotat.md"
HTML_FIL = ROT / "refleksjonsnotat" / "Refleksjonsnotat.html"
PDF_FIL = ROT / "refleksjonsnotat" / "Refleksjonsnotat.pdf"

NETTLESERE = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
]

STIL = """
@page { size: A4; margin: 20mm 19mm 18mm; }
body { font-family: "Charter", "Georgia", "Times New Roman", serif; font-size: 10.8pt;
       line-height: 1.42; color: #14171a; margin: 0; hyphens: auto; }
h1 { font-size: 16.5pt; margin: 0 0 .2em; }
h2 { font-size: 12.2pt; margin: 1.15em 0 .35em; page-break-after: avoid; }
p { margin: 0 0 .62em; text-align: justify; }
ul, ol { margin: 0 0 .62em 1.05em; padding: 0; }
li { margin-bottom: .28em; }
em { color: #14171a; }
code { font-family: "SF Mono", Menlo, monospace; font-size: .88em;
       background: #f2f3f5; padding: .05em .28em; border-radius: 3px; }
hr { border: none; border-top: .5pt solid #c8ccd2; margin: 1.1em 0 .7em; }
.ingress { color: #4a5158; font-size: 9.8pt; margin-bottom: 1.1em; }
.ingress em { color: #4a5158; }
"""


def marker(tekst: str) -> str:
    """Kursiv, fet og kode – nok markdown til dette notatet."""
    tekst = html.escape(tekst, quote=False)
    tekst = re.sub(r"`([^`]+)`", r"<code>\1</code>", tekst)
    tekst = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", tekst)
    tekst = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", tekst)
    return tekst


def til_html(markdown: str) -> str:
    linjer = markdown.splitlines()
    ut: list[str] = []
    avsnitt: list[str] = []
    i_liste = False
    ingress = False

    def tom_avsnitt() -> None:
        nonlocal avsnitt, ingress
        if avsnitt:
            klasse = ' class="ingress"' if ingress else ""
            ut.append(f"<p{klasse}>" + marker(" ".join(avsnitt)) + "</p>")
            avsnitt = []
            ingress = False

    def lukk_liste() -> None:
        nonlocal i_liste
        if i_liste:
            ut.append("</ul>")
            i_liste = False

    for linje in linjer:
        stripet = linje.strip()
        if not stripet:
            tom_avsnitt()
            lukk_liste()
        elif stripet.startswith("# "):
            tom_avsnitt()
            lukk_liste()
            ut.append("<h1>" + marker(stripet[2:]) + "</h1>")
        elif stripet.startswith("## "):
            tom_avsnitt()
            lukk_liste()
            ut.append("<h2>" + marker(stripet[3:]) + "</h2>")
        elif stripet.startswith("### "):
            tom_avsnitt()
            lukk_liste()
            ut.append("<h3>" + marker(stripet[4:]) + "</h3>")
        elif stripet == "---":
            tom_avsnitt()
            lukk_liste()
            ut.append("<hr>")
        elif re.match(r"^[-*] ", stripet):
            tom_avsnitt()
            if not i_liste:
                ut.append("<ul>")
                i_liste = True
            ut.append("<li>" + marker(stripet[2:]) + "</li>")
        elif re.match(r"^\d+\. ", stripet):
            tom_avsnitt()
            if not i_liste:
                ut.append("<ul>")
                i_liste = True
            ut.append("<li>" + marker(re.sub(r"^\d+\.\s*", "", stripet)) + "</li>")
        else:
            if not avsnitt and (stripet.startswith("**ING100") or stripet.startswith("*Utkast")):
                ingress = True
            avsnitt.append(stripet)
    tom_avsnitt()
    lukk_liste()
    return (
        '<!DOCTYPE html>\n<html lang="nb">\n<head>\n<meta charset="utf-8">\n'
        "<title>Refleksjonsnotat – MatteHjelpen</title>\n"
        f"<style>{STIL}</style>\n</head>\n<body>\n" + "\n".join(ut) + "\n</body>\n</html>\n"
    )


def finn_nettleser() -> str | None:
    for kandidat in NETTLESERE:
        if Path(kandidat).exists():
            return kandidat
        funnet = shutil.which(kandidat)
        if funnet:
            return funnet
    return None


def main() -> int:
    if not KILDE.exists():
        print(f"Fant ikke {KILDE}")
        return 1
    tekst = KILDE.read_text(encoding="utf-8")
    if "{{" in tekst:
        print("ADVARSEL: notatet inneholder plassholdere ({{...}}) som ikke er fylt ut.")
    HTML_FIL.write_text(til_html(tekst), encoding="utf-8")
    print(f"Skrev {HTML_FIL.relative_to(ROT)}")

    nettleser = finn_nettleser()
    if not nettleser:
        print("Fant ingen Chrome/Chromium. Åpne HTML-filen og skriv ut til PDF manuelt.")
        return 1
    resultat = subprocess.run(
        [
            nettleser,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={PDF_FIL}",
            HTML_FIL.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if not PDF_FIL.exists():
        print("PDF ble ikke laget:", resultat.stderr[-400:])
        return 1
    print(f"Skrev {PDF_FIL.relative_to(ROT)} ({PDF_FIL.stat().st_size // 1024} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
