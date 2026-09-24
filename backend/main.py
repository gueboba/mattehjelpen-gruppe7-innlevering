"""MatteHjelpen – FastAPI-backend.

`main.py` er koblingslaget: `llm_client` lager forslaget, `validator` prøver å
motbevise det, og `main.py` bestemmer hva brukeren faktisk får se. Selve
matematikken hører hjemme i `tools.py`.

- `POST /solve` tar `{"oppgave": "..."}` og svarer med JSON som alltid
  inneholder feltene `svar`, `steg`, `formler_brukt`, `validert`,
  `tokens_brukt` og `estimert_kostnad` – også når noe går galt.
- `GET /status` forteller frontend hvilken modell som er i bruk, om verktøy og
  stegforklaring er slått på, og om API-nøkkel er satt (aldri selve nøkkelen).
- `GET /` serverer `frontend/index.html`.

VÅRE VALG (jf. [FYLL INN SELV] i PROMPTS/05_main.md):

1. *Feil skjules ikke.* Brukeren får en forklaring på norsk i `feil`, og den
   tekniske meldingen i `teknisk_detalj` (aldri API-nøkler). Et «vellykket»
   svar returneres aldri når kallet feilet – da ville appen løyet.
2. *Statuskoder:* 400 ved tom eller altfor lang oppgave, 500 når appen selv er
   feil satt opp (manglende API_KEY) eller ved uventede feil, 502 når
   leverandøren svarer med feil, 503 ved oppbrukt kvote/rate limit og 504 ved
   tidsavbrudd. Frontend viser innholdet uansett statuskode.
3. *Validering skjer alltid*, også når modellen ikke oppga et maskinlesbart
   svar. Da blir resultatet «ikke mulig å validere» med en ærlig begrunnelse,
   ikke grønt lys.
"""

from __future__ import annotations

import math
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from backend import llm_client, validator

app = FastAPI(title="MatteHjelpen")

# CORS åpent for lokal utvikling: frontend kan kjøre fra en annen port/origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
MAKS_OPPGAVELENGDE = 2000


class Oppgave(BaseModel):
    oppgave: str


@app.get("/")
async def index():
    return FileResponse(FRONTEND)


@app.get("/status")
async def status():
    """Oppsettet appen kjører med akkurat nå (ingen hemmeligheter)."""
    try:
        return llm_client.status()
    except Exception as e:  # status skal aldri velte appen
        return {"feil": f"Kunne ikke lese oppsettet: {type(e).__name__}: {e}"}


@app.post("/solve")
def solve(oppgave: Oppgave):
    data, statuskode = behandle_oppgave(oppgave.oppgave)
    return JSONResponse(status_code=statuskode, content=_rensk(data))


@app.exception_handler(RequestValidationError)
async def ugyldig_forespoersel(_: Request, feil: RequestValidationError):
    """Også Pydantics egne 422-svar skal ha de seks feltene.

    Uten dette fikk frontend et svar med bare «detail», leste `data.steg` som
    undefined og kastet en TypeError i stedet for å vise feilmeldingen.
    """
    data, statuskode = _feilsvar(
        "Forespørselen mangler feltet «oppgave», eller det er ikke tekst.",
        422,
        str(feil.errors())[:300],
    )
    return JSONResponse(status_code=statuskode, content=data)


def _grunnsvar(**ekstra) -> dict:
    """Alle seks feltene frontend forventer, alltid til stede – og med rett type.

    At feltet *finnes* er ikke nok: får frontend en streng der den venter en
    liste, kaller den .map() på den og krasjer. Derfor tvinger vi typen her,
    så kontrakten holder selv om modell-laget skulle levere noe rart.
    """
    svar = {
        "svar": "",
        "steg": [],
        "formler_brukt": [],
        "validert": False,
        "tokens_brukt": 0,
        "estimert_kostnad": 0.0,
    }
    svar.update(ekstra)
    svar["svar"] = "" if svar["svar"] is None else str(svar["svar"])
    svar["steg"] = _liste(svar["steg"])
    svar["formler_brukt"] = _liste(svar["formler_brukt"])
    svar["validert"] = bool(svar["validert"])
    svar["tokens_brukt"] = _tall(svar["tokens_brukt"], int)
    svar["estimert_kostnad"] = _tall(svar["estimert_kostnad"], float)
    return svar


def _liste(verdi) -> list:
    if isinstance(verdi, list):
        return verdi
    return [] if verdi is None else [verdi]


def _tall(verdi, type_):
    """Tallet som riktig type – men None beholdes.

    None betyr «vi vet ikke»: leverandøren oppga ikke tokentall. Frontend
    viser da «ukjent». Gjorde vi None om til 0 her, ville appen påstå at den
    ikke brukte noen tokens, og det er verre enn å si at vi ikke vet.
    """
    if verdi is None:
        return None
    try:
        tall = type_(verdi)
    except (TypeError, ValueError):
        return type_(0)
    return tall if math.isfinite(tall) else type_(0)


def _rensk(data):
    """Bytter ut NaN og uendelig med None før JSON-serialisering.

    `json.dumps` skriver dem som `NaN`/`Infinity`, som ikke er gyldig JSON;
    Starlette nekter og svarer 500 i stedet for å vise resultatet. Det skjedde
    da en leverandør ikke oppga token-tall og kostnaden ble NaN.
    """
    if isinstance(data, dict):
        return {nokkel: _rensk(verdi) for nokkel, verdi in data.items()}
    if isinstance(data, list):
        return [_rensk(verdi) for verdi in data]
    if isinstance(data, float) and not math.isfinite(data):
        return None
    return data


def _feilsvar(melding: str, statuskode: int, teknisk: str | None = None) -> tuple[dict, int]:
    return (
        _grunnsvar(
            svar=melding,
            feil=melding,
            teknisk_detalj=teknisk,
            valideringsstatus="ikke_mulig",
            valideringsdetaljer="Ingenting ble beregnet, så ingenting er kontrollert.",
            advarsler=[],
        ),
        statuskode,
    )


def behandle_oppgave(tekst: str, **overstyring) -> tuple[dict, int]:
    """Løser én oppgave og validerer svaret. Brukes av /solve og av eksperimentskriptet."""
    tekst = (tekst or "").strip()
    if not tekst:
        return _feilsvar("Oppgaveteksten er tom. Skriv inn en matteoppgave.", 400)
    if len(tekst) > MAKS_OPPGAVELENGDE:
        return _feilsvar(
            f"Oppgaveteksten er for lang ({len(tekst)} tegn, maks {MAKS_OPPGAVELENGDE}).", 400
        )

    try:
        resultat = llm_client.solve_task(tekst, **overstyring)
    except llm_client.KonfigurasjonsFeil as e:
        return _feilsvar(f"Appen er ikke ferdig satt opp: {e}", 500)
    except llm_client.LLMFeil as e:
        return _feilsvar(str(e), e.http_status, teknisk=e.teknisk)
    except ValueError as e:
        return _feilsvar(f"Ugyldig oppgave: {e}", 400)
    except Exception as e:
        return _feilsvar(
            "Noe gikk galt inne i appen, og vi viser derfor ikke noe svar.",
            500,
            teknisk=f"{type(e).__name__}: {e}",
        )

    validering = _valider(resultat)
    advarsler = list(resultat.get("advarsler") or [])
    advarsler.extend(validering.get("advarsler") or [])
    samsvarsadvarsel = _sjekk_samsvar(resultat)
    if samsvarsadvarsel:
        advarsler.append(samsvarsadvarsel)

    svar = _grunnsvar(
        svar=resultat.get("svar", ""),
        steg=resultat.get("steg", []),
        formler_brukt=resultat.get("formler_brukt", []),
        validert=bool(validering.get("validert")),
        tokens_brukt=resultat.get("tokens_brukt", 0),
        estimert_kostnad=resultat.get("estimert_kostnad", 0.0),
    )
    svar.update(
        {
            "valideringsstatus": validering.get("status", "ikke_mulig"),
            "valideringsdetaljer": validering.get("detaljer", ""),
            "valideringsmetode": validering.get("metode"),
            # Forbehold fra selve kontrollen, holdt atskilt fra de andre
            # advarslene: «validert» skal ikke se like grønt ut når svaret
            # f.eks. bare oppgir én av to røtter.
            "valideringsforbehold": list(validering.get("advarsler") or []),
            "tolkning": resultat.get("tolkning", ""),
            "oppgavetype": resultat.get("oppgavetype", ""),
            "problem_sympy": resultat.get("problem_sympy", ""),
            "svar_sympy": resultat.get("svar_sympy", ""),
            "betingelser": resultat.get("betingelser", []),
            "usikkerhet": resultat.get("usikkerhet", ""),
            "steg_detaljer": resultat.get("steg_detaljer", []),
            "verktoy_brukt": resultat.get("verktoy_brukt", []),
            "advarsler": advarsler,
            "tokens": resultat.get("tokens"),
            "estimert_kostnad_nok": resultat.get("estimert_kostnad_nok"),
            "kostnad_kjent": resultat.get("kostnad_kjent", False),
            "modell": resultat.get("modell", ""),
            "use_tools": resultat.get("use_tools"),
            "forklar_steg": resultat.get("forklar_steg"),
            "runder": resultat.get("runder"),
            "varighet_s": resultat.get("varighet_s"),
        }
    )
    return _rensk(svar), 200


def _valider(resultat: dict) -> dict:
    """Kaller validatoren, og lar aldri en feil der velte hele svaret.

    Klarte vi ikke å kontrollere svaret ut fra det modellen oppga, prøver vi en
    gang til med problemet rekonstruert fra det verktøykallet som faktisk ble
    kjørt. Verktøyloggen er tross alt det eneste vi vet er sant.
    """
    validering = _kall_validator(
        resultat.get("problem_sympy") or "",
        resultat.get("svar_sympy") or "",
        resultat.get("oppgavetype"),
        resultat.get("variabel"),
        resultat.get("betingelser"),
    )
    if validering.get("status") != "ikke_mulig":
        return validering
    fra_verktoy = _problem_fra_verktoy(resultat.get("verktoy_brukt") or [])
    if not fra_verktoy or not (resultat.get("svar_sympy") or "").strip():
        return validering
    problem, oppgavetype, variabel, betingelser = fra_verktoy
    andre = _kall_validator(problem, resultat["svar_sympy"], oppgavetype, variabel, betingelser)
    if andre.get("status") == "ikke_mulig":
        return validering
    andre["detaljer"] = (
        f"Modellen oppga ikke problemet på en form vi kunne kontrollere, så vi brukte "
        f"verktøykallet den faktisk gjorde ({problem}). " + andre.get("detaljer", "")
    )
    return andre


def _kall_validator(problem, losning, oppgavetype, variabel, betingelser) -> dict:
    try:
        return validator.validate(
            problem,
            losning,
            oppgavetype=oppgavetype,
            variabel=variabel,
            betingelser=betingelser,
        )
    except Exception as e:
        return {
            "validert": False,
            "status": "ikke_mulig",
            "detaljer": f"Valideringen feilet: {type(e).__name__}: {e}. Svaret er ikke kontrollert.",
        }


def _problem_fra_verktoy(verktoylogg: list) -> tuple[str, str, str | None, list | None] | None:
    """Bygger (problem, oppgavetype, variabel, betingelser) ut fra siste vellykkede verktøykall."""
    matriseoppgaver = {
        "determinant": "determinant",
        "invers": "invers",
        "egenverdier": "egenverdier",
        "solve_ax_b": "ax_b",
    }
    for kall in reversed(verktoylogg):
        if not kall.get("ok"):
            continue
        navn = kall.get("navn")
        argumenter = kall.get("argumenter") or {}
        if not isinstance(argumenter, dict):
            continue
        variabel = argumenter.get("variabel")
        if navn == "derive":
            return argumenter.get("uttrykk", ""), "derivasjon", variabel, None
        if navn == "integrate":
            return argumenter.get("uttrykk", ""), "integral", variabel, None
        if navn == "definite_integral":
            return (
                f"Integral({argumenter.get('uttrykk', '')}, ({variabel or 'x'}, "
                f"{argumenter.get('nedre', '')}, {argumenter.get('ovre', '')}))",
                "bestemt_integral",
                variabel,
                None,
            )
        if navn == "limit":
            return (
                f"Limit({argumenter.get('uttrykk', '')}, {variabel or 'x'}, "
                f"{argumenter.get('punkt', '')})",
                "grenseverdi",
                variabel,
                None,
            )
        if navn == "solve_equation":
            return argumenter.get("ligning", ""), "ligning", variabel, None
        if navn == "solve_ode":
            return argumenter.get("ligning", ""), "ode", None, None
        if navn == "solve_ode_ivp":
            return (
                argumenter.get("ligning", ""),
                "ode",
                None,
                argumenter.get("betingelser"),
            )
        if navn == "matrix_op":
            oppgavetype = matriseoppgaver.get(str(argumenter.get("operasjon", "")).lower())
            if oppgavetype:
                return str(argumenter.get("matrise", "")), oppgavetype, None, None
        if navn in ("complex_op", "calculate"):
            uttrykk = argumenter.get("uttrykk") or argumenter.get("tall") or ""
            if navn == "complex_op" and str(argumenter.get("operasjon", "")).lower() in ("potens", "rotter"):
                continue  # «1+I, 7» er ikke et uttrykk vi kan sammenligne direkte
            return str(uttrykk), "beregning", None, None
    return None


def _sjekk_samsvar(resultat: dict) -> str | None:
    """Advarer hvis modellens endelige svar ikke stemmer med det verktøyene regnet ut."""
    try:
        samsvar = validator.samsvarer_med_verktoy(
            resultat.get("svar_sympy") or "", resultat.get("verktoy_brukt") or []
        )
    except Exception:
        return None
    if samsvar is False:
        return (
            "Svaret modellen oppgir, er ikke det samme som noe av det verktøyene faktisk "
            "regnet ut. Se verktøyloggen og kontroller selv."
        )
    return None
