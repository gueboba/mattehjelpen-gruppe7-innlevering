"""LLM-klient for MatteHjelpen: systemprompt, tool-calling-løkke og kostnad.

`solve_task(...)` er orkestratoren. Den styrer turene mellom modellen og
verktøyene, men regner ikke selv: modellen velger metode og forklarer, mens
`backend/tools.py` (SymPy) gjør beregningen og `backend/validator.py`
kontrollerer svaret etterpå (kalt fra `main.py`).

ANTI-HALLUSINASJON – vi stoler ikke på det modellen sier om seg selv:

- Verktøyloggen bygges av de faktiske `tool_calls` vi kjørte, ikke av teksten.
- Formel-ID-er kontrolleres mot FORMELSAMLING; ukjente ID-er avvises, og navn
  og referanse hentes fra samlingen i stedet for fra modellens tekst.
- Påstår modellen at svaret er verktøyverifisert uten at noe verktøy ble
  kalt, legger vi det inn som en advarsel i svaret.
- Nevner teksten SymPy eller «verktøy» når ingen verktøy ble kalt, advarer vi
  også om det. (Det skjer i praksis når USE_TOOLS er False.)

VÅRE VALG (jf. [FYLL INN SELV] i PROMPTS/03_llm_client.md):

1. *Tillegg til systemprompten:* modellen må si hvordan den har TOLKET
   oppgaven før den løser den, forklare HVORFOR hvert steg gjøres (ikke bare
   hva), unngå unødvendig fagsjargong, og bruke verktøy også til aritmetikk.
2. *Maks 8 runder* med verktøykall. Vanlige oppgaver trenger 1–3; resten er
   slingringsmonn for at modellen skal kunne rette opp en syntaksfeil etter
   en feilmelding. Grensen hindrer evighetsløkker og at tokenforbruket
   løper løpsk. I siste runde tas verktøyene bort, slik at modellen må
   konkludere i stedet for å bli avbrutt uten svar.
3. *Ukjent tokenforbruk vises som «ukjent»*, ikke som 0. Det samme gjelder
   kostnad når vi ikke kjenner prisen for modellen.

EKSPERIMENT-BRYTERE (Del B): se USE_TOOLS og FORKLAR_STEG rett under.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import openai
from dotenv import dotenv_values
from openai import OpenAI

from backend import tools
from backend.formelsamling import FORMELSAMLING, kompakt_liste, sla_opp

USE_TOOLS = True  # <-- Aha-bryter nr. 1
FORKLAR_STEG = false  # <-- Aha-bryter nr. 2: False fjerner kravet om stegvis forklaring

MAKS_RUNDER = 8
TEMPERATUR = 0.0
MAKS_VENTEFORSOK = 7  # nye forsøk når leverandøren sier «vent litt»
MAKS_VENTETID = 150.0  # sekunder vi maksimalt venter per forsøk
_ENV_FIL = Path(__file__).resolve().parent.parent / ".env"
_KONFIGNOKLER = (
    "API_KEY",
    "MODEL_NAME",
    "API_BASE_URL",
    "TEMPERATUR",
    "MAKS_SVARTOKENS",
    "API_TIDSGRENSE",
    "API_FORSOK",
    "PRIS_INN_USD_PER_MTOK",
    "PRIS_UT_USD_PER_MTOK",
    "USD_TIL_NOK",
)

# Listepriser i USD per million tokens (inn, ut).
#
# Modeller med «:free» koster faktisk 0 kroner – det er nettopp derfor vi kan
# gjøre oppgaven gratis. Men for å svare på «hva ville dette kostet i skala?»
# trenger vi en pris, og da bruker vi listeprisen for den BETALTE varianten av
# samme modell. Estimert kostnad i appen er altså «hva dette ville kostet hvis
# vi betalte listepris», ikke hva vi faktisk betalte (0).
#
# Kilder, kontrollert 20.09.2026: OpenRouters /api/v1/models for
# OpenRouter-modellene, og Groqs prisliste for Groq-modellene.
PRISER_USD_PER_MTOK = {
    # OpenRouter (gratisvarianten prises som den betalte)
    "nvidia/nemotron-3-ultra-550b-a55b:free": (0.60, 2.40),
    "nvidia/nemotron-3-ultra-550b-a55b": (0.60, 2.40),
    "google/gemma-4-26b-a4b-it:free": (0.09, 0.30),
    "google/gemma-4-26b-a4b-it": (0.09, 0.30),
    "nvidia/nemotron-3-super-120b-a12b:free": (0.08, 0.45),
    "deepseek/deepseek-v4-flash-0731:free": (0.04, 0.08),
    "qwen/qwen3.8-27b:free": (0.20, 2.55),
    "qwen/qwen3.8-27b": (0.20, 2.55),
    # Groq
    "openai/gpt-oss-20b": (0.075, 0.30),
    "openai/gpt-oss-120b": (0.15, 0.60),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "llama-3.3-70b-versatile": (0.59, 0.79),
}

SYSTEMPROMPT_KJERNE_START = (
    "Du er en matematikklærer for ingeniørstudenter. Bruk verktøyene (SymPy) "
    "til all beregning når oppgaven lar seg beregne slik – du skal ALDRI late "
    "som du har brukt et verktøy du ikke faktisk kalte. Kan oppgaven ikke "
    "beregnes (f.eks. et bevis eller en begrepsforklaring), resonnerer du i "
    "tekst og sier eksplisitt at svaret IKKE er verifisert av et verktøy. "
)
# Denne setningen er aha-bryter nr. 2: den fjernes når FORKLAR_STEG er False.
SYSTEMPROMPT_KJERNE_STEG = (
    "Forklar hvert steg pedagogisk på norsk, og oppgi nøyaktig hvilke "
    "formler/verktøy du faktisk brukte. Knytt hver formel-ID til steget der "
    "den brukes, og ta med navn og referanse fra formelsamlingen. "
)
SYSTEMPROMPT_KJERNE_SLUTT = "Hvis du er usikker, si det eksplisitt."

# [FYLL INN SELV] i PROMPTS/03_llm_client.md – våre tillegg:
SYSTEMPROMPT_TILLEGG = (
    "\n\nVåre tilleggskrav:\n"
    "- Si først hvordan du TOLKER oppgaven. Er notasjonen tvetydig (klassisk: "
    "sin^-1(x), som kan bety arcsin(x) eller 1/sin(x)), nevn begge tolkningene, "
    "velg én og si tydelig hvilken du valgte.\n"
    "- Forklar HVORFOR hvert steg gjøres, ikke bare hva som gjøres. En "
    "medstudent skal kunne følge forklaringen.\n"
    "- Unngå unødvendig fagsjargong, og forklar fagord første gang du bruker dem.\n"
    "- Ikke regn i hodet: bruk verktøyene også til aritmetikk og forenkling "
    "(calculate), slik at all regning er etterprøvbar.\n"
    "- Får du «feil» tilbake fra et verktøy, rett opp inputen og prøv igjen, "
    "eller si ærlig fra at beregningen ikke lyktes.\n"
    "- Skriv matematikk som LaTeX mellom dollartegn, f.eks. $x^2 + 1$."
)


class KonfigurasjonsFeil(RuntimeError):
    """Noe mangler i .env (typisk API-nøkkelen)."""


class LLMFeil(RuntimeError):
    """Kallet til språkmodellen feilet. Meldingen er skrevet for sluttbrukeren."""

    def __init__(
        self,
        melding: str,
        *,
        http_status: int = 502,
        teknisk: str | None = None,
        opphav: Exception | None = None,
    ):
        super().__init__(melding)
        self.http_status = http_status
        self.teknisk = teknisk
        # Selve unntaket fra leverandøren. Vi trenger det for å komme til
        # retry-after-headeren: str(e) inneholder bare meldingsteksten, og da
        # ble headeren aldri lest.
        self.opphav = opphav


def les_konfig() -> dict:
    """Leser .env på nytt ved hvert kall, slik at modellbytte virker uten omstart."""
    verdier = {n: (v or "").strip() for n, v in dotenv_values(_ENV_FIL).items() if v}
    for nokkel in _KONFIGNOKLER:
        fra_miljo = os.environ.get(nokkel)
        if fra_miljo:
            verdier[nokkel] = fra_miljo.strip()
    return {
        "api_nokkel": verdier.get("API_KEY", ""),
        "modell": verdier.get("MODEL_NAME", ""),
        "api_base": verdier.get("API_BASE_URL", "").rstrip("/"),
        "temperatur": _flyttall(verdier.get("TEMPERATUR"), TEMPERATUR),
        "maks_svartokens": _heltall(verdier.get("MAKS_SVARTOKENS"), 0),
        "tidsgrense": _flyttall(verdier.get("API_TIDSGRENSE"), 120.0),
        "forsok": _heltall(verdier.get("API_FORSOK"), 1),
        "pris_inn": _flyttall(verdier.get("PRIS_INN_USD_PER_MTOK"), None),
        "pris_ut": _flyttall(verdier.get("PRIS_UT_USD_PER_MTOK"), None),
        "usd_til_nok": _flyttall(verdier.get("USD_TIL_NOK"), None),
    }


def _flyttall(verdi, standard):
    try:
        return float(str(verdi).replace(",", "."))
    except (TypeError, ValueError):
        return standard


def _heltall(verdi, standard):
    try:
        return int(float(verdi))
    except (TypeError, ValueError):
        return standard


def status() -> dict:
    """Info til frontend om hvordan appen er satt opp akkurat nå (uten hemmeligheter)."""
    konfig = les_konfig()
    priser = _priser(konfig, konfig["modell"])
    return {
        "modell": konfig["modell"] or "(ikke satt)",
        "api_base": konfig["api_base"] or "(ikke satt)",
        "api_nokkel_satt": bool(konfig["api_nokkel"]),
        "use_tools": USE_TOOLS,
        "forklar_steg": FORKLAR_STEG,
        "maks_runder": MAKS_RUNDER,
        "temperatur": konfig["temperatur"],
        "antall_verktoy": len(tools.TOOL_DEFINITIONS),
        "antall_formler": len(FORMELSAMLING),
        "pris_inn_usd_per_mtok": priser[0],
        "pris_ut_usd_per_mtok": priser[1],
        "usd_til_nok": konfig["usd_til_nok"],
    }


def _priser(konfig: dict, modell: str) -> tuple[float | None, float | None]:
    if konfig["pris_inn"] is not None and konfig["pris_ut"] is not None:
        return konfig["pris_inn"], konfig["pris_ut"]
    return PRISER_USD_PER_MTOK.get(modell, (None, None))


def systemprompt(*, bruk_verktoy: bool, forklar_steg: bool) -> str:
    """Setter sammen systemprompten. Kjernen beholdes ordrett fra oppgaven."""
    kjerne = SYSTEMPROMPT_KJERNE_START
    if forklar_steg:
        kjerne += SYSTEMPROMPT_KJERNE_STEG
    else:
        kjerne += "Svar kort på norsk. "
    kjerne += SYSTEMPROMPT_KJERNE_SLUTT
    deler = [kjerne]
    if forklar_steg:
        deler.append(SYSTEMPROMPT_TILLEGG)
    if not bruk_verktoy:
        deler.append(
            "\n\nMERK: I denne kjøringen har du INGEN verktøy tilgjengelig. Da må du "
            "si tydelig fra at svaret ikke er verifisert av et verktøy."
        )
    deler.append("\n\nFORMELSAMLING (bruk ID-ene i feltet formel_ider):\n" + kompakt_liste())
    deler.append(_svarformat(forklar_steg, bruk_verktoy))
    return "".join(deler)


OPPGAVETYPER = [
    "derivasjon",
    "integral",
    "bestemt_integral",
    "grenseverdi",
    "ligning",
    "ode",
    "determinant",
    "invers",
    "egenverdier",
    "ax_b",
    "kompleks",
    "beregning",
    "bevis",
    "begrep",
    "annet",
]

# Sluttsvaret kan også leveres som et verktøykall. Vi ber ikke om det (se
# _svarformat), men beholder verktøyet fordi gpt-oss-modellene av seg selv
# prøver å levere strukturerte svar som verktøykall. Er verktøyet deklarert,
# blir et slikt svar tatt imot i stedet for at leverandøren avviser hele
# forespørselen med «attempted to call tool 'json'».
SVAR_VERKTOY = {
    "type": "function",
    "function": {
        "name": "endelig_svar",
        "description": (
            "Kall dette når du er ferdig og har det endelige svaret. Ikke skriv "
            "sluttsvaret som vanlig tekst."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tolkning": {
                    "type": "string",
                    "description": "Hvordan du tolket oppgaven, med antakelser",
                },
                "oppgavetype": {"type": "string", "enum": OPPGAVETYPER},
                "problem_sympy": {
                    "type": "string",
                    "description": "Problemet slik du tolket det, i SymPy-syntaks",
                },
                "variabel": {"type": "string", "description": "Hovedvariabelen, f.eks. x"},
                "betingelser": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Startbetingelser for ODE, f.eks. y(0) = 1",
                },
                "steg": {
                    "type": "array",
                    "description": "Stegvis forklaring, ett objekt per steg",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tekst": {"type": "string"},
                            "formel_ider": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["tekst"],
                    },
                },
                "svar": {"type": "string", "description": "Kort svar til studenten, LaTeX i $...$"},
                "svar_sympy": {"type": "string", "description": "Svaret i SymPy-syntaks"},
                "verifisert_med_verktoy": {"type": "boolean"},
                "usikkerhet": {"type": "string"},
            },
            "required": ["tolkning", "oppgavetype", "svar"],
        },
    },
}

# gpt-oss-modellene forsøker hardnakket å levere strukturerte svar gjennom et
# verktøy de kaller «json», uansett hva systemprompten ber om. Er det ikke
# deklarert, avviser Groq hele forespørselen. Vi deklarerer det derfor som en
# reserveløsning med samme felter, og godtar feltnavn i både store og små
# bokstaver.
JSON_VERKTOY = {
    "type": "function",
    "function": {
        "name": "json",
        "description": (
            "Reserveløsning for sluttsvaret som strukturerte data. Bruk helst "
            "merkelappformatet i vanlig tekst i stedet."
        ),
        "parameters": SVAR_VERKTOY["function"]["parameters"],
    },
}

SVARVERKTOY_NAVN = {SVAR_VERKTOY["function"]["name"], JSON_VERKTOY["function"]["name"]}

# Feltnavn vi godtar fra modellen, uansett om den bruker merkelappnavn eller
# små bokstaver.
_FELTNAVN = {
    "tolkning": "tolkning",
    "type": "oppgavetype",
    "oppgavetype": "oppgavetype",
    "problem": "problem_sympy",
    "problem_sympy": "problem_sympy",
    "variabel": "variabel",
    "betingelser": "betingelser",
    "steg": "steg",
    "svar": "svar",
    "svar_sympy": "svar_sympy",
    "verifisert": "verifisert_med_verktoy",
    "verifisert_med_verktoy": "verifisert_med_verktoy",
    "usikkerhet": "usikkerhet",
}


def _normaliser_nokler(data: dict) -> dict:
    """«TOLKNING»/«Type»/«problem» -> våre feltnavn."""
    resultat: dict = {}
    for nokkel, verdi in data.items():
        navn = _FELTNAVN.get(str(nokkel).strip().lower())
        if navn and (navn not in resultat or verdi not in (None, "", [])):
            resultat[navn] = verdi
    if isinstance(resultat.get("verifisert_med_verktoy"), str):
        resultat["verifisert_med_verktoy"] = (
            resultat["verifisert_med_verktoy"].lower().startswith(("ja", "true", "yes"))
        )
    if isinstance(resultat.get("betingelser"), str):
        resultat["betingelser"] = [
            d.strip() for d in re.split(r"[;\n]", resultat["betingelser"]) if d.strip()
        ]
    return resultat


def _svarformat(forklar_steg: bool, bruk_verktoy: bool = True) -> str:
    steg_linje = (
        "[STEG D1] Forklaring av steget, med LaTeX i $...$. Formel-ID-ene skrives i "
        "merkelappen, som her. Bruk én [STEG]-merkelapp per steg.\n"
        if forklar_steg
        else ""
    )
    return (
        "\n\nSVARFORMAT: Når du er ferdig med eventuelle verktøykall, svarer du med "
        "merkelappene under – og ingenting annet. IKKE bruk JSON: LaTeX og JSON krever "
        "dobbel backslash og blir fort ødelagt. Her kan du skrive LaTeX rett fram.\n"
        "[TOLKNING] Hvordan du tolket oppgaven, med antakelser.\n"
        "[TYPE] en av: " + ", ".join(OPPGAVETYPER) + "\n"
        "[PROBLEM] problemet slik du tolket det, i SymPy-syntaks\n"
        "[VARIABEL] x\n"
        "[BETINGELSER] y(0) = 1; y'(0) = 0 (bare for initialverdiproblem, ellers tom)\n"
        + steg_linje
        + "[SVAR] kort svar til studenten, med LaTeX i $...$\n"
        "[SVAR_SYMPY] svaret i SymPy-syntaks\n"
        "[VERIFISERT] ja hvis du faktisk brukte verktøy, ellers nei\n"
        "[USIKKERHET] hva du er usikker på, eller la den stå tom\n"
        "Slik skal [PROBLEM] og [SVAR_SYMPY] se ut per oppgavetype:\n"
        "- derivasjon: problem = uttrykket (x**2*sin(3*x)), svar = den deriverte\n"
        "- integral: problem = integranden, svar = antiderivert (+ C er valgfritt)\n"
        "- bestemt_integral: problem = Integral(f, (x, a, b)), svar = tallet\n"
        "- grenseverdi: problem = Limit(f, x, a), svar = grenseverdien\n"
        "- ligning: problem = x**2 - 2*x - 8 = 0 (system: ligninger skilt med ;), "
        'svar = [-2, 4] eller [{x: 3, y: -24}]\n'
        "- ode: problem = Eq(y(x).diff(x, 2) + 2*y(x), 0), svar = y(x) = C1*cos(sqrt(2)*x) "
        "+ C2*sin(sqrt(2)*x). Startbetingelser settes i [BETINGELSER], ellers tom\n"
        "- determinant/invers/egenverdier: problem = matrisen [[1, 2], [3, 4]], "
        "svar = tallet/matrisen/listen av egenverdier\n"
        "- ax_b: problem = [A, b], svar = løsningsvektoren\n"
        "- kompleks/beregning: problem = uttrykket, svar = verdien\n"
        "- bevis/begrep/annet: la [PROBLEM] og [SVAR_SYMPY] stå tomme\n"
        "Bruk bare formel-ID-er som finnes i formelsamlingen over."
    )


def _klient(konfig: dict) -> OpenAI:
    if not konfig["api_nokkel"]:
        raise KonfigurasjonsFeil(
            "API_KEY mangler. Kopier .env.example til .env og lim inn en gratis API-nøkkel "
            "(se STUDENT_START.md steg 4)."
        )
    if not konfig["modell"]:
        raise KonfigurasjonsFeil("MODEL_NAME mangler i .env.")
    if not konfig["api_base"]:
        raise KonfigurasjonsFeil("API_BASE_URL mangler i .env.")
    return OpenAI(
        api_key=konfig["api_nokkel"],
        base_url=konfig["api_base"],
        timeout=konfig["tidsgrense"],
        max_retries=max(0, konfig["forsok"]),
    )


_VENTETID = re.compile(r"try again in ([\d.]+)\s*s", re.IGNORECASE)
_FOR_STOR = re.compile(r"Limit (\d+), Used (\d+), Requested (\d+)", re.IGNORECASE)


def _for_stor_for_kvoten(feil) -> tuple[int, int] | None:
    """Er selve forespørselen større enn hele minuttkvoten? Da hjelper ingen venting."""
    treff = _FOR_STOR.search(str(feil))
    if not treff:
        return None
    grense, _, forespurt = (int(t) for t in treff.groups())
    return (forespurt, grense) if forespurt > grense else None


def _ventetid(feil) -> float:
    """Hvor lenge leverandøren ber oss vente ved rate limit (sekunder).

    Vi tar den LENGSTE av retry-after-headeren og tiden som står i selve
    feilmeldingen. Groq sender nemlig en kort header samtidig som meldingen
    sier «try again in 32.5s», og venter vi for kort, brenner vi bare opp
    forsøkene våre.
    """
    kandidater = [0.0]
    hoder = getattr(getattr(feil, "response", None), "headers", None) or {}
    for navn, faktor in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        verdi = hoder.get(navn) if hasattr(hoder, "get") else None
        try:
            if verdi is not None:
                kandidater.append(float(verdi) * faktor)
        except (TypeError, ValueError):
            pass
    treff = _VENTETID.search(str(feil))
    if treff:
        kandidater.append(float(treff.group(1)))
    return max(kandidater) or 20.0


def _kall_modellen(klient: OpenAI, konfig: dict, modell: str, meldinger: list, verktoy, logg=None):
    """Ett kall til modellen. Venter og prøver igjen når leverandøren sier «rate limit».

    Gratisnivåene har en grense for tokens per minutt, og to kall i samme
    oppgave kan fort overstige den. Leverandøren sier hvor lenge vi må vente,
    så da venter vi – og noterer det i svaret, slik at brukeren skjønner
    hvorfor det tok tid.
    """
    argumenter = {
        "model": modell,
        "messages": meldinger,
        "temperature": konfig["temperatur"],
    }
    if verktoy:
        argumenter["tools"] = verktoy
        argumenter["tool_choice"] = "auto"
    if konfig["maks_svartokens"]:
        argumenter["max_tokens"] = konfig["maks_svartokens"]
    for forsok in range(1, MAKS_VENTEFORSOK + 1):
        try:
            return _ett_kall(klient, konfig, modell, argumenter)
        except LLMFeil as e:
            if e.http_status != 503 or forsok == MAKS_VENTEFORSOK:
                raise
            for_stor = _for_stor_for_kvoten(e.teknisk or "")
            if for_stor:
                forespurt, grense = for_stor
                raise LLMFeil(
                    f"Denne oppgaven ble for stor for gratisnivået: forespørselen trenger "
                    f"{forespurt} tokens, men grensen er {grense} tokens per minutt. Å vente "
                    "hjelper ikke – prøv en kortere oppgave, eller en konto med høyere grense.",
                    http_status=503,
                    teknisk=e.teknisk,
                ) from None
            vent = min(_ventetid(e.opphav or e.teknisk or ""), MAKS_VENTETID)
            if logg is not None:
                logg.append(
                    f"Leverandøren satte oss i kø (hastighetsgrense); ventet {vent:.0f} sekunder."
                )
            time.sleep(vent + 1)
    raise LLMFeil("Kom aldri gjennom hastighetsgrensen hos leverandøren.", http_status=503)


def _ett_kall(klient: OpenAI, konfig: dict, modell: str, argumenter: dict):
    try:
        return klient.chat.completions.create(**argumenter)
    except openai.AuthenticationError as e:
        raise LLMFeil(
            "API-nøkkelen ble avvist av leverandøren. Sjekk API_KEY i .env.",
            http_status=502,
            teknisk=str(e),
        ) from None
    except openai.RateLimitError as e:
        raise LLMFeil(
            "Kvoten eller hastighetsgrensen hos leverandøren er brukt opp. Vent litt, "
            "eller bytt modell i .env.",
            http_status=503,
            teknisk=str(e),
            opphav=e,
        ) from None
    except openai.NotFoundError as e:
        raise LLMFeil(
            f"Modellen «{modell}» finnes ikke hos denne leverandøren. Sjekk MODEL_NAME "
            "og API_BASE_URL i .env.",
            http_status=502,
            teknisk=str(e),
        ) from None
    except openai.BadRequestError as e:
        feil = LLMFeil(
            "Leverandøren avviste forespørselen. Dette skjer blant annet når modellen "
            "ikke klarer å lage et gyldig verktøykall.",
            http_status=502,
            teknisk=str(e),
        )
        # Egen markering: modellen prøvde et verktøykall leverandøren ikke godtok
        # (typisk LaTeX med enkel backslash i JSON-argumentene). Da kan vi be om
        # svar i ren tekst i stedet for å gi opp.
        feil.verktoyfeil = "tool_use_failed" in str(e)
        raise feil from None
    except openai.APITimeoutError as e:
        raise LLMFeil(
            f"Modellen svarte ikke innen {konfig['tidsgrense']:.0f} sekunder.",
            http_status=504,
            teknisk=str(e),
        ) from None
    except openai.APIConnectionError as e:
        raise LLMFeil(
            "Fikk ikke kontakt med API-et. Sjekk nettforbindelsen og API_BASE_URL.",
            http_status=502,
            teknisk=str(e),
        ) from None
    except openai.APIStatusError as e:
        raise LLMFeil(
            f"Leverandøren svarte med feilkode {e.status_code}.",
            http_status=502,
            teknisk=str(e),
        ) from None


# --- Tolking av modellens JSON-svar ------------------------------------------

_JSON_ESCAPE_BOKSTAVER = set("ntrbfu")
_LATEX_KOMMANDOER = {
    "neq", "ne", "nabla", "nu", "notin", "ni", "nmid", "nonumber",
    "theta", "times", "text", "textbf", "textit", "textrm", "tan", "tanh", "tfrac",
    "to", "top", "triangle", "tilde", "therefore", "tau",
    "right", "rightarrow", "rho", "rangle", "rfloor", "rceil", "rvert", "rbrace", "rm",
    "beta", "binom", "big", "bigg", "bmatrix", "boxed", "bar", "because", "bullet", "bot",
    "begin", "bigcap", "bigcup", "bmod", "boldsymbol",
    "frac", "forall", "floor", "fbox", "fi",
    "underline", "uparrow", "upsilon", "unicode", "underbrace", "underset",
}
_KONTROLLTEGN = re.compile(r"[\x08\x0c]")


def _reparer_backslash(tekst: str) -> str:
    """Dobler backslasher som tydeligvis er LaTeX (\\frac), men lar ekte JSON-escapes stå.

    Svake modeller skriver ofte "\\frac" med én backslash i JSON. Da tolker
    json.loads «\\f» som formfeed, og formelen blir ødelagt i stillhet.
    """

    def erstatt(treff: re.Match) -> str:
        kommando = treff.group(1)
        if kommando[0] in _JSON_ESCAPE_BOKSTAVER and kommando not in _LATEX_KOMMANDOER:
            return treff.group(0)
        return "\\\\" + kommando

    return re.sub(r"(?<!\\)\\([A-Za-z]+)", erstatt, tekst)


def _reparer_backslash_hardt(tekst: str) -> str:
    """Dobler ALLE enkle backslasher foran bokstaver, uten unntaksliste.

    Siste utvei, brukt bare når den forsiktige reparasjonen fortsatt gir
    \\x08 eller \\x0c i svaret. Da var backslashen LaTeX uansett hva
    kommandoen het – ordlisten vår kan umulig kjenne alle. \\begin{matrix}
    ble ellers til \\x08egin{matrix} i stillhet.
    """
    return re.sub(r"(?<!\\)\\([A-Za-z]+)", r"\\\\\1", tekst)


def _inneholder_kontrolltegn(data) -> bool:
    if isinstance(data, str):
        return bool(_KONTROLLTEGN.search(data))
    if isinstance(data, dict):
        return any(_inneholder_kontrolltegn(v) for v in data.values())
    if isinstance(data, list):
        return any(_inneholder_kontrolltegn(v) for v in data)
    return False


def les_json(innhold: str) -> dict | None:
    """Henter JSON-objektet ut av modellens svar, også når det er pakket i tekst."""
    if not innhold:
        return None
    tekst = innhold.strip()
    if "```" in tekst:
        blokker = re.findall(r"```(?:json)?\s*(.*?)```", tekst, re.S)
        if blokker:
            tekst = max(blokker, key=len).strip()
    start, slutt = tekst.find("{"), tekst.rfind("}")
    if start == -1 or slutt <= start:
        return None
    kandidat = tekst[start : slutt + 1]
    rå = None
    try:
        rå = json.loads(kandidat, strict=False)
    except json.JSONDecodeError:
        rå = None
    if isinstance(rå, dict) and not _inneholder_kontrolltegn(rå):
        return rå
    reserve = None
    for reparasjon in (_reparer_backslash, _reparer_backslash_hardt):
        try:
            reparert = json.loads(reparasjon(kandidat), strict=False)
        except json.JSONDecodeError:
            continue
        if not isinstance(reparert, dict):
            continue
        if not _inneholder_kontrolltegn(reparert):
            return reparert
        reserve = reserve or reparert
    return reserve if reserve is not None else (rå if isinstance(rå, dict) else None)


_MERKELAPP = re.compile(
    r"\[([A-Za-zÆØÅæøå_]+)([^\]\n]*)\][ \t]*(.*?)(?=\n[ \t*#>-]{0,4}\[[A-Za-zÆØÅæøå_]+[^\]\n]*\]|\Z)",
    re.S,
)


def les_svar(tekst: str) -> dict | None:
    """Tolker svaret fra modellen: først merkelappformatet, ellers JSON.

    Merkelappformatet ([SVAR] ... [STEG D1] ...) ble valgt fordi LaTeX og JSON
    passer dårlig sammen: modellen må doble hver backslash, og når den glipper
    på én av dem, avviser leverandøren hele svaret («Failed to parse tool call
    arguments as JSON»). I ren tekst finnes ikke det problemet.
    """
    if not tekst or not tekst.strip():
        return None
    funn = _MERKELAPP.findall(tekst)
    data: dict = {"steg": []}
    for navn, tillegg, verdi in funn:
        navn = navn.upper()
        # Noen modeller pakker merkelappene i markdown: **[SVAR]** 42
        verdi = verdi.strip().strip("*`").strip()
        if navn == "STEG":
            ider = [d.strip().upper() for d in re.split(r"[,\s]+", tillegg) if d.strip()]
            if verdi:
                data["steg"].append({"tekst": verdi, "formel_ider": ider})
        elif navn == "TOLKNING":
            data["tolkning"] = verdi
        elif navn in ("TYPE", "OPPGAVETYPE"):
            data["oppgavetype"] = verdi
        elif navn in ("PROBLEM", "PROBLEM_SYMPY"):
            data["problem_sympy"] = verdi
        elif navn == "VARIABEL":
            data["variabel"] = verdi
        elif navn == "BETINGELSER":
            data["betingelser"] = [d.strip() for d in re.split(r"[;\n]", verdi) if d.strip()]
        elif navn == "SVAR":
            data["svar"] = verdi
        elif navn == "SVAR_SYMPY":
            data["svar_sympy"] = verdi
        elif navn == "VERIFISERT":
            data["verifisert_med_verktoy"] = verdi.lower().startswith(("ja", "true", "yes"))
        elif navn == "USIKKERHET":
            data["usikkerhet"] = verdi
    if data.get("svar") or data.get("svar_sympy"):
        return data
    return les_json(tekst)


def _tekstliste(verdi) -> list[str]:
    if isinstance(verdi, str):
        return [verdi] if verdi.strip() else []
    if isinstance(verdi, list):
        return [str(v).strip() for v in verdi if str(v).strip()]
    return []


def _formel_ider(verdi) -> list[str]:
    """Formel-ID-er, også når modellen skriver dem som én streng: «D1, D2».

    Uten oppdelingen slo vi opp hele strengen som én ID, fant den ikke, og
    advarte om at «D1, D2» ikke finnes i formelsamlingen – to gyldige
    referanser gikk tapt, og advarselen pekte på feil sted.
    """
    ider = []
    for del_ in _tekstliste(verdi):
        ider.extend(bit for bit in re.split(r"[,;\s]+", del_) if bit)
    return ider


def _normaliser_steg(data) -> list[dict]:
    """Godtar både [{"tekst": ..., "formel_ider": [...]}] og en liste med strenger."""
    steg = []
    for nummer, element in enumerate(data if isinstance(data, list) else [], start=1):
        if isinstance(element, dict):
            felt = {str(k).strip().lower(): v for k, v in element.items()}
            tekst = str(felt.get("tekst") or felt.get("text") or "").strip()
            ider = _formel_ider(felt.get("formel_ider") or felt.get("formler") or [])
        else:
            tekst, ider = str(element).strip(), []
        if tekst:
            steg.append({"nr": nummer, "tekst": tekst, "formel_ider": ider})
    for nummer, element in enumerate(steg, start=1):
        element["nr"] = nummer
    return steg


def _formler_brukt(steg: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """Kontrollerer formel-ID-ene mot formelsamlingen. Ukjente ID-er avvises."""
    brukt: dict[str, dict] = {}
    avvist: list[str] = []
    for element in steg:
        gyldige = []
        for formel_id in element["formel_ider"]:
            formel = sla_opp(formel_id)
            if formel is None:
                if formel_id not in avvist:
                    avvist.append(formel_id)
                continue
            gyldige.append(formel["id"])
            oppforing = brukt.setdefault(formel["id"], {**formel, "steg": []})
            if element["nr"] not in oppforing["steg"]:
                oppforing["steg"].append(element["nr"])
        element["formel_ider"] = gyldige
    advarsler = []
    if avvist:
        advarsler.append(
            "Modellen oppga formel-ID-er som ikke finnes i formelsamlingen og som derfor "
            "ble avvist: " + ", ".join(avvist) + "."
        )
    return list(brukt.values()), avvist, advarsler


# --- Hovedfunksjonen ----------------------------------------------------------


def solve_task(
    oppgave: str,
    *,
    modell: str | None = None,
    use_tools: bool | None = None,
    forklar_steg: bool | None = None,
    maks_runder: int | None = None,
) -> dict:
    """Løser en matteoppgave via LLM + verktøy. Returnerer en dict til main.py.

    Nøkkelordargumentene brukes av eksperimentskriptet (Del B) for å kjøre de
    samme oppgavene med ulike modeller og med/uten verktøy, uten å endre koden.
    """
    if not isinstance(oppgave, str) or not oppgave.strip():
        raise ValueError("Oppgaveteksten er tom.")
    konfig = les_konfig()
    modell = modell or konfig["modell"]
    bruk_verktoy = USE_TOOLS if use_tools is None else use_tools
    med_steg = FORKLAR_STEG if forklar_steg is None else forklar_steg
    runder_igjen = maks_runder or MAKS_RUNDER
    klient = _klient(konfig)

    meldinger = [
        {"role": "system", "content": systemprompt(bruk_verktoy=bruk_verktoy, forklar_steg=med_steg)},
        {"role": "user", "content": oppgave.strip()},
    ]
    verktoylogg: list[dict] = []
    advarsler: list[str] = []
    tokens = {"inn": 0, "ut": 0, "totalt": 0, "kall": 0, "kall_uten_tall": 0}
    start = time.monotonic()
    data: dict | None = None
    rå_svar = ""
    reparasjoner = 0
    uten_verktoy_resten = False

    for runde in range(1, runder_igjen + 1):
        siste_runde = runde == runder_igjen
        verktoy = (
            tools.TOOL_DEFINITIONS + [SVAR_VERKTOY, JSON_VERKTOY]
            if (bruk_verktoy and not siste_runde and not uten_verktoy_resten)
            else None
        )
        try:
            svar = _kall_modellen(klient, konfig, modell, meldinger, verktoy, advarsler)
        except LLMFeil as e:
            if not getattr(e, "verktoyfeil", False) or uten_verktoy_resten or siste_runde:
                raise
            # Leverandøren forkastet verktøykallet før vi fikk se det. Vi ber om
            # svaret i ren tekst i stedet – da finnes ikke JSON-problemet.
            uten_verktoy_resten = True
            advarsler.append(
                "Leverandøren avviste et verktøykall fra modellen (ugyldig JSON, typisk "
                "LaTeX med enkel backslash). Vi ba modellen svare i ren tekst i stedet."
            )
            meldinger.append(
                {
                    "role": "user",
                    "content": "Forrige forsøk ble avvist fordi verktøykallet ikke var "
                    "gyldig JSON. Ikke bruk verktøykall nå. Svar med merkelappene "
                    "[TOLKNING], [TYPE], [PROBLEM], [VARIABEL], [STEG ...], [SVAR], "
                    "[SVAR_SYMPY], [VERIFISERT] i vanlig tekst.",
                }
            )
            continue
        _tell_tokens(tokens, svar)
        valg = svar.choices[0] if svar.choices else None
        if valg is None:
            raise LLMFeil("Modellen returnerte ingen svar.", http_status=502)
        melding = valg.message
        kall = list(getattr(melding, "tool_calls", None) or [])
        endelig = next((k for k in kall if k.function.name in SVARVERKTOY_NAVN), None)
        if endelig is not None:
            if len(kall) > 1:
                advarsler.append(
                    "Modellen ba om beregninger i samme melding som den leverte sluttsvaret; "
                    "beregningene ble ikke kjørt."
                )
            data = les_json(endelig.function.arguments or "")
            if data is not None:
                data = _normaliser_nokler(data)
                rå_svar = endelig.function.arguments or ""
                break
            advarsler.append("Sluttsvaret fra modellen var ikke gyldig JSON.")
            kall = [k for k in kall if k is not endelig]
        if kall:
            meldinger.append(
                {
                    "role": "assistant",
                    "content": melding.content or "",
                    "tool_calls": [
                        {
                            "id": enkelt.id,
                            "type": "function",
                            "function": {
                                "name": enkelt.function.name,
                                "arguments": enkelt.function.arguments,
                            },
                        }
                        for enkelt in kall
                    ],
                }
            )
            for enkelt in kall:
                resultat, argumenter = _kjor_kall(enkelt)
                verktoylogg.append(
                    {
                        "runde": runde,
                        "navn": enkelt.function.name,
                        "argumenter": argumenter,
                        "resultat": resultat,
                        "ok": "feil" not in resultat,
                    }
                )
                meldinger.append(
                    {
                        "role": "tool",
                        "tool_call_id": enkelt.id,
                        "content": json.dumps(resultat, ensure_ascii=False),
                    }
                )
            continue

        rå_svar = (melding.content or "").strip()
        data = les_svar(rå_svar)
        if data is not None:
            break
        if reparasjoner == 0 and not siste_runde:
            reparasjoner += 1
            advarsler.append("Modellen svarte først i feil format og ble bedt om å svare på nytt.")
            meldinger.append({"role": "assistant", "content": rå_svar})
            meldinger.append(
                {
                    "role": "user",
                    "content": "Svaret ditt fulgte ikke svarformatet. Svar på nytt med "
                    "merkelappene [TOLKNING], [TYPE], [PROBLEM], [SVAR], [SVAR_SYMPY] osv., "
                    "og ingenting annet.",
                }
            )
            continue
        break

    varighet = time.monotonic() - start
    if data is None:
        advarsler.append(
            "Modellen fulgte ikke svarformatet, så svaret kunne ikke struktureres. "
            "Teksten vises som den er, og ingenting er kontrollert."
        )
        data = {
            "svar": rå_svar or "Modellen ga ikke noe svar innenfor grensen på "
            f"{runder_igjen} runder.",
            "steg": [],
        }

    steg = _normaliser_steg(data.get("steg"))
    formler, _, formel_advarsler = _formler_brukt(steg)
    advarsler.extend(formel_advarsler)
    vellykkede_kall = [k for k in verktoylogg if k["ok"]]
    pastand = bool(data.get("verifisert_med_verktoy"))
    if pastand and not vellykkede_kall:
        advarsler.append(
            "Modellen påstår at svaret er verifisert med verktøy, men ingen verktøykall "
            "ble faktisk gjort. Påstanden er ikke til å stole på."
        )
    tekstbiter = " ".join([str(data.get("svar", ""))] + [s["tekst"] for s in steg]).lower()
    if not vellykkede_kall and re.search(r"sympy|verktøy|verktoy", tekstbiter):
        advarsler.append(
            "Teksten nevner SymPy eller verktøy, men ingen verktøykall ble gjort i denne "
            "kjøringen. Alt er regnet av språkmodellen selv."
        )
    if bruk_verktoy and not verktoylogg:
        advarsler.append("Modellen hadde verktøy tilgjengelig, men brukte dem ikke.")
    feilede = [k for k in verktoylogg if not k["ok"]]
    if feilede:
        advarsler.append(
            f"{len(feilede)} verktøykall feilet underveis (se verktøyloggen); modellen "
            "fikk feilmeldingen og fortsatte."
        )

    pris_inn, pris_ut = _priser(konfig, modell)
    kostnad_kjent = pris_inn is not None and pris_ut is not None and tokens["kall_uten_tall"] == 0
    kostnad = (
        tokens["inn"] / 1e6 * pris_inn + tokens["ut"] / 1e6 * pris_ut if kostnad_kjent else 0.0
    )
    kurs = konfig["usd_til_nok"]
    if not kostnad_kjent:
        advarsler.append(
            "Kostnaden kunne ikke regnes ut (ukjent pris for modellen eller manglende "
            "tokenrapport), og vises derfor som ukjent."
        )

    return {
        "svar": str(data.get("svar") or "").strip() or "(modellen ga ikke noe svar)",
        "svar_sympy": str(data.get("svar_sympy") or "").strip(),
        "problem_sympy": str(data.get("problem_sympy") or "").strip(),
        "oppgavetype": str(data.get("oppgavetype") or "").strip(),
        "variabel": str(data.get("variabel") or "").strip(),
        "betingelser": _tekstliste(data.get("betingelser")),
        "tolkning": str(data.get("tolkning") or "").strip(),
        "usikkerhet": str(data.get("usikkerhet") or "").strip(),
        "steg": [element["tekst"] for element in steg],
        "steg_detaljer": steg,
        "formler_brukt": formler,
        "verktoy_brukt": verktoylogg,
        "modell_pastod_verktoybruk": pastand,
        "advarsler": advarsler,
        "tokens_brukt": tokens["totalt"] if tokens["kall_uten_tall"] == 0 else None,
        "tokens": tokens,
        "estimert_kostnad": round(kostnad, 8),
        "estimert_kostnad_nok": round(kostnad * kurs, 6) if (kostnad_kjent and kurs) else None,
        "kostnad_kjent": kostnad_kjent,
        "modell": modell,
        "use_tools": bruk_verktoy,
        "forklar_steg": med_steg,
        "runder": min(runde, runder_igjen),
        "varighet_s": round(varighet, 2),
        "rå_svar": rå_svar,
    }


def _kjor_kall(kall) -> tuple[dict, dict]:
    """Kjører ett verktøykall fra modellen, med tidsgrense, og returnerer (resultat, argumenter)."""
    try:
        argumenter = json.loads(kall.function.arguments or "{}")
    except json.JSONDecodeError:
        return (
            {
                "feil": "Argumentene var ikke gyldig JSON. Send dem som et JSON-objekt, "
                "f.eks. {\"uttrykk\": \"x**2\"}."
            },
            {"rå": kall.function.arguments},
        )
    if not isinstance(argumenter, dict):
        return {"feil": "Argumentene må være et JSON-objekt."}, {"rå": kall.function.arguments}
    return tools.kjor_verktoy_med_tidsgrense(kall.function.name, argumenter), argumenter


def _tell_tokens(tokens: dict, svar) -> None:
    tokens["kall"] += 1
    bruk = getattr(svar, "usage", None)
    if bruk is None:
        tokens["kall_uten_tall"] += 1
        return
    inn = _antall(getattr(bruk, "prompt_tokens", None))
    ut = _antall(getattr(bruk, "completion_tokens", None))
    totalt = _antall(getattr(bruk, "total_tokens", None)) or (inn + ut)
    tokens["inn"] += inn
    tokens["ut"] += ut
    tokens["totalt"] += totalt


def _antall(verdi) -> int:
    """Tokentallet fra leverandøren som et ikke-negativt heltall.

    Sendte leverandøren «100» som tekst, krasjet regnskapet med TypeError, og
    et negativt tall ga negativ kostnad i grensesnittet. Verken det ene eller
    det andre skal velte et ellers godt svar.
    """
    try:
        return max(0, int(verdi))
    except (TypeError, ValueError):
        return 0
