"""Eksperimentkjører for Del B: de samme 10 oppgavene, to modeller, med og uten verktøy.

    python scripts/eksperiment.py                    # kjører alt som mangler
    python scripts/eksperiment.py --oppgaver 1 2 3   # bare noen oppgaver
    python scripts/eksperiment.py --aha              # aha-bryterne (2, 4 og 5)
    python scripts/eksperiment.py --rapport          # tabell og kostnadstall til EKSPERIMENT.md

Resultatene skrives fortløpende til `eksperiment/resultater.jsonl`, én linje
per kjøring, slik at en avbrutt kjøring kan fortsettes senere (kjøringer som
allerede finnes, hoppes over hvis ikke `--pa-nytt` er satt).

VIKTIG SKILLE mellom de to kolonnene vi logger:

- «validert» er appens egen kontroll: stemmer svaret med oppgaven SLIK
  MODELLEN TOLKET DEN?
- «riktig» sammenligner med fasiten vi selv har regnet ut på forhånd (se
  OPPGAVER under). Et svar kan være validert og likevel feil – det er nettopp
  det aha-bryter 4 (tvetydig notasjon) skal vise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sympy as sp  # noqa: E402

from backend import main  # noqa: E402
from backend.parsing import (  # noqa: E402
    TolkningsFeil,
    del_pa_toppniva,
    tolk_likning,
    tolk_uttrykk,
)

RESULTATFIL = Path(__file__).resolve().parent.parent / "eksperiment" / "resultater.jsonl"
ENV_FIL = Path(__file__).resolve().parent.parent / ".env"

# Hovedtabellen kjøres med to modeller av ulik størrelse hos samme leverandør,
# slik at latens og pris kan sammenlignes rett fram.
MODELLER = ["nvidia/nemotron-3-super-120b-a12b:free", "qwen/qwen3.8-27b:free"]
MAKS_FORSOK = 3  # nye forsøk når leverandøren svarer med rate limit

# Hvilken leverandør hver modell hører hjemme hos. Nøklene ligger i .env (aldri
# i git). Slik kan vi kjøre de samme oppgavene hos ulike leverandører uten å
# redigere .env mellom hver kjøring – nyttig for aha-bryter 3 (modellbytte).
LEVERANDORER = [
    (lambda m: m.endswith(":free"), "OpenRouter", "OPENROUTER_API_KEY", "OPENROUTER_API_BASE_URL"),
    (
        lambda m: m.startswith(("openai/gpt-oss", "llama-", "qwen/")),
        "Groq",
        "GROQ_API_KEY",
        "GROQ_API_BASE_URL",
    ),
]


def _velg_leverandor(modell: str) -> str:
    """Setter API_KEY/API_BASE_URL for modellen (miljøvariabler slår .env i llm_client)."""
    from dotenv import dotenv_values

    verdier = dotenv_values(ENV_FIL)
    for passer, navn, nokkel, base in LEVERANDORER:
        if passer(modell) and verdier.get(nokkel) and verdier.get(base):
            os.environ["API_KEY"] = verdier[nokkel]
            os.environ["API_BASE_URL"] = verdier[base]
            return navn
    os.environ.pop("API_KEY", None)
    os.environ.pop("API_BASE_URL", None)
    return "standard (.env)"


x, y, z, t = sp.symbols("x y z t")


# --- Sammenligning med fasit --------------------------------------------------


def _tolk(tekst: str):
    """Tolker svaret. «y(x) = 2*exp(x)» gir høyresiden, slik modeller ofte svarer."""
    tekst = str(tekst).strip()
    if re.search(r"(?<![<>!=])=(?!=)", tekst):
        likning = tolk_likning(tekst, tillat_ukjente_funksjoner=True)
        return likning.rhs if likning.lhs.atoms(sp.Symbol, sp.Function) else likning.lhs
    return tolk_uttrykk(tekst, tillat_ukjente_funksjoner=True)


def _tall_lik(a, b, toleranse=1e-9) -> bool:
    """Er svaret a lik fasiten b?

    To eksakte tall sammenlignes eksakt. En relativ toleranse hører ikke
    hjemme der: på fasiten 87654321·12345679 godtok 1e-9 et avvik på over en
    million, og det er nettopp de store tallene modellene bommer på.
    """
    if isinstance(a, sp.Rational) and isinstance(b, sp.Rational):
        return a == b
    forskjell = sp.simplify(a - b)
    if forskjell == 0:
        return True
    try:
        return abs(complex(sp.N(forskjell, 30))) <= toleranse * max(1.0, abs(complex(sp.N(b, 30))))
    except TypeError:
        return False


def _tall_lik_tekst(svar: str, fasit) -> bool:
    """Som _tall_lik, men godtar et korrekt avrundet desimalsvar (f.eks. 0,1534).

    Slakken er en halv enhet i siste oppgitte siffer, og den er ABSOLUTT.
    Ganget vi den med fasitens størrelse – som vi gjorde – ble «1082152000000000.0»
    godtatt som svar på 1082152110028959, altså 110 millioner feil.
    """
    verdi = _tolk(svar)
    desimaler = [len(d) for d in re.findall(r"\d\.(\d+)", str(svar))]
    if desimaler:
        try:
            avvik = abs(complex(sp.N(verdi - fasit, 30)))
        except TypeError:
            return False
        return avvik <= 0.5 * 10 ** (-max(desimaler)) * (1 + 1e-9)
    return _tall_lik(verdi, fasit)


def _uttrykk_lik(svar: str, fasit, variabel=x) -> bool:
    uttrykk = _tolk(svar)
    if isinstance(uttrykk, sp.Equality):
        uttrykk = uttrykk.rhs
    if sp.simplify(uttrykk - fasit) == 0:
        return True
    for punkt in (0.37, 1.23, 2.11):
        try:
            a = complex(sp.N(uttrykk.subs(variabel, punkt), 30))
            b = complex(sp.N(fasit.subs(variabel, punkt), 30))
        except TypeError:
            return False
        if abs(a - b) > 1e-9 * max(1.0, abs(b)):
            return False
    return True


def _antiderivert_lik(svar: str, fasit) -> bool:
    """Antideriverte kan skille seg med en konstant, så vi sammenligner de deriverte."""
    uttrykk = _tolk(svar.replace("+ C", "").replace("+C", ""))
    if isinstance(uttrykk, sp.Equality):
        uttrykk = uttrykk.rhs
    return sp.simplify(sp.diff(uttrykk - fasit, x)) == 0


def _mengde_lik(svar: str, fasit: list) -> bool:
    tekst = str(svar).strip().strip("[]")
    deler = del_pa_toppniva(tekst, ",;")
    try:
        verdier = [_tolk(d.split("=")[-1]) for d in deler]
    except TolkningsFeil:
        return False
    if len(verdier) != len(fasit):
        return False
    igjen = list(fasit)
    for verdi in verdier:
        treff = next((f for f in igjen if _tall_lik(verdi, f)), None)
        if treff is None:
            return False
        igjen.remove(treff)
    return True


def _losning_lik(svar: str, fasit: dict, rekkefolge: tuple[str, ...] = ("x", "y", "z")) -> bool:
    """Godtar både «x = 3, y = -24, z = 30», «{x: 3, ...}» og vektoren «[3, -24, 30]».

    Den siste formen kommer når modellen løser systemet som A·x = b og svarer
    med løsningsvektoren. Da tolker vi verdiene i rekkefølgen variablene står i
    oppgaven.
    """
    tekst = str(svar).strip().strip("[]{}")
    deler = del_pa_toppniva(tekst, ",;")
    funnet = {}
    for del_ in deler:
        if ":" in del_ or "=" in del_:
            navn, verdi = del_.replace("=", ":").split(":", 1)
            try:
                funnet[navn.strip()] = _tolk(verdi)
            except TolkningsFeil:
                return False
    if not funnet and len(deler) == len(fasit):
        try:
            funnet = {navn: _tolk(del_) for navn, del_ in zip(rekkefolge, deler)}
        except TolkningsFeil:
            return False
    if set(funnet) != set(fasit):
        return False
    return all(_tall_lik(funnet[navn], verdi) for navn, verdi in fasit.items())


def _generell_ode_losning(svar: str, ode: str, orden: int) -> bool:
    """Riktig hvis løsningen oppfyller ligningen OG har nok vilkårlige konstanter."""
    from backend.parsing import tolk_ode

    likning, funksjon, variabel = tolk_ode(ode)
    uttrykk = _tolk(svar)
    if isinstance(uttrykk, sp.Equality):
        uttrykk = uttrykk.rhs
    konstanter = uttrykk.free_symbols - {variabel}
    if len(konstanter) != orden:
        return False
    rest = sp.simplify((likning.lhs - likning.rhs).subs(funksjon, uttrykk).doit())
    return sp.simplify(rest) == 0


# --- De ti oppgavene ----------------------------------------------------------

OPPGAVER = [
    {
        "nr": 1,
        "tekst": "Deriver f(x) = x^2 * sin(3x).",
        "omrade": "derivasjon (produkt- og kjerneregel)",
        "fasit": "2*x*sin(3*x) + 3*x**2*cos(3*x)",
        "sjekk": lambda s: _uttrykk_lik(s, 2 * x * sp.sin(3 * x) + 3 * x**2 * sp.cos(3 * x)),
    },
    {
        "nr": 2,
        "tekst": "Finn det ubestemte integralet av x*e^(2x).",
        "omrade": "integrasjon (delvis integrasjon)",
        "fasit": "(2*x - 1)*exp(2*x)/4 + C",
        "sjekk": lambda s: _antiderivert_lik(s, (2 * x - 1) * sp.exp(2 * x) / 4),
    },
    {
        "nr": 3,
        "tekst": "Beregn det bestemte integralet av x^3/(x^2 + 1) fra 0 til 1.",
        "omrade": "bestemt integral (brøkuttrykk)",
        "fasit": "1/2 - log(2)/2",
        "sjekk": lambda s: _tall_lik_tekst(s, sp.Rational(1, 2) - sp.log(2) / 2),
    },
    {
        "nr": 4,
        "tekst": "Løs ligningen x^2 - 2x - 8 = 0.",
        "omrade": "andregradsligning (kan regnes for hånd)",
        "fasit": "[-2, 4]",
        "sjekk": lambda s: _mengde_lik(s, [sp.Integer(-2), sp.Integer(4)]),
    },
    {
        "nr": 5,
        "tekst": (
            "Løs likningssystemet: x + y/2 + z/3 = 1, x/2 + y/3 + z/4 = 1, x/3 + y/4 + z/5 = 1."
        ),
        "omrade": "lineært system med brøker (Hilbert-matrise)",
        "fasit": "x = 3, y = -24, z = 30",
        "sjekk": lambda s: _losning_lik(s, {"x": sp.Integer(3), "y": sp.Integer(-24), "z": sp.Integer(30)}),
    },
    {
        "nr": 6,
        "tekst": "Finn den generelle løsningen av differensialligningen y'' + 2y = 0.",
        "omrade": "2. ordens ODE (komplekse røtter)",
        "fasit": "y = C1*cos(sqrt(2)*x) + C2*sin(sqrt(2)*x)",
        "sjekk": lambda s: _generell_ode_losning(s, "Eq(y(x).diff(x, 2) + 2*y(x), 0)", 2),
    },
    {
        "nr": 7,
        "tekst": "Løs initialverdiproblemet y'' - 3y' + 2y = 0 med y(0) = 1 og y'(0) = 0.",
        "omrade": "ODE med startbetingelser",
        "fasit": "y = 2*exp(x) - exp(2*x)",
        "sjekk": lambda s: _uttrykk_lik(s, 2 * sp.exp(x) - sp.exp(2 * x)),
    },
    {
        "nr": 8,
        "tekst": "Finn egenverdiene til matrisen [[2, 1, 0], [1, 3, 1], [0, 1, 4]].",
        "omrade": "lineær algebra (egenverdier)",
        "fasit": "3, 3 - sqrt(3), 3 + sqrt(3)",
        "sjekk": lambda s: _mengde_lik(s, [sp.Integer(3), 3 - sp.sqrt(3), 3 + sp.sqrt(3)]),
    },
    {
        "nr": 9,
        "tekst": "Skriv (1 + i*sqrt(3))^7 på formen a + bi.",
        "omrade": "komplekse tall (De Moivre)",
        "fasit": "64 + 64*sqrt(3)*I",
        "sjekk": lambda s: _tall_lik_tekst(s, 64 + 64 * sp.sqrt(3) * sp.I),
    },
    {
        "nr": 10,
        "tekst": "Bevis Pythagoras' læresetning.",
        "omrade": "bevis (kan ikke verktøy-verifiseres)",
        "fasit": "(vurderes manuelt: er beviset gyldig, og sier appen fra at det ikke er verifisert?)",
        "sjekk": None,
    },
]

# Tilleggssett: oppgaver som er tunge å regne i hodet. De ti hovedoppgavene er
# hentet fra pensum og viste seg å være for enkle til å skille modellene med og
# uten verktøy – disse fire er valgt for å finne grensen.
EKSTRA_OPPGAVER = [
    {
        "nr": 201,
        "tekst": (
            "Finn determinanten til 4x4-Hilbertmatrisen "
            "[[1, 1/2, 1/3, 1/4], [1/2, 1/3, 1/4, 1/5], [1/3, 1/4, 1/5, 1/6], [1/4, 1/5, 1/6, 1/7]]. "
            "Svar med eksakt brøk."
        ),
        "omrade": "lineær algebra med brøker (tung hoderegning)",
        "fasit": "1/6048000",
        "sjekk": lambda s: _tall_lik(_tolk(s), sp.Rational(1, 6048000)),
    },
    {
        "nr": 202,
        "tekst": "Regn ut det eksakte produktet 87654321 * 12345679.",
        "omrade": "aritmetikk med store tall",
        "fasit": "1082152110028959",
        "sjekk": lambda s: _tall_lik(_tolk(s), sp.Integer(1082152110028959)),
    },
    {
        "nr": 203,
        "tekst": "Beregn det bestemte integralet av x^4*(1-x)^4/(1+x^2) fra 0 til 1. Svar eksakt.",
        "omrade": "bestemt integral med delbrøk (klassisk 22/7 - pi)",
        "fasit": "22/7 - pi",
        "sjekk": lambda s: _tall_lik_tekst(s, sp.Rational(22, 7) - sp.pi),
    },
    {
        "nr": 204,
        "tekst": (
            "Løs initialverdiproblemet y'' + 3y' + 2y = e^(-x) med y(0) = 0 og y'(0) = 1."
        ),
        "omrade": "inhomogen ODE med startbetingelser",
        "fasit": "y = x*exp(-x)",
        "sjekk": lambda s: _uttrykk_lik(s, x * sp.exp(-x)),
    },
]

AHA_OPPGAVER = [
    {
        "nr": 101,
        "navn": "aha4-tvetydig",
        "tekst": "Deriver sin^-1(x).",
        "omrade": "aha-bryter 4: tvetydig notasjon",
        "fasit": "arcsin: 1/sqrt(1 - x^2) · 1/sin: -cos(x)/sin(x)^2",
        "sjekk": None,
    },
    {
        "nr": 102,
        "navn": "aha5-valideringsfeil",
        "tekst": "Vis at den deriverte av x^3 er 2x^2.",
        "omrade": "aha-bryter 5: fremprovosert valideringsfeil",
        "fasit": "Påstanden er feil: den deriverte er 3x^2.",
        "sjekk": None,
    },
]


# --- Kjøring ------------------------------------------------------------------


def sett_resultatfil(sti) -> None:
    """Lar to modeller kjøres samtidig uten å skrive i samme fil (egne TPM-kvoter)."""
    global RESULTATFIL
    RESULTATFIL = Path(sti)


def _les_tidligere() -> list[dict]:
    if not RESULTATFIL.exists():
        return []
    linjer = []
    for linje in RESULTATFIL.read_text(encoding="utf-8").splitlines():
        if linje.strip():
            linjer.append(json.loads(linje))
    return linjer


def _skriv(resultat: dict) -> None:
    RESULTATFIL.parent.mkdir(parents=True, exist_ok=True)
    with RESULTATFIL.open("a", encoding="utf-8") as fil:
        fil.write(json.dumps(resultat, ensure_ascii=False) + "\n")


def _vurder(oppgave: dict, data: dict) -> str:
    if oppgave.get("sjekk") is None:
        return "manuell"
    svar = (data.get("svar_sympy") or "").strip()
    if not svar:
        return "nei (ingen maskinlesbart svar)"
    try:
        return "ja" if oppgave["sjekk"](svar) else "nei"
    except Exception as e:
        return f"? ({type(e).__name__})"


def kjor_en(oppgave: dict, modell: str, bruk_verktoy: bool, forklar_steg: bool = True) -> dict:
    leverandor = _velg_leverandor(modell)
    start = time.time()
    data, statuskode = main.behandle_oppgave(
        oppgave["tekst"], modell=modell, use_tools=bruk_verktoy, forklar_steg=forklar_steg
    )
    return {
        "tidspunkt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "oppgave_nr": oppgave["nr"],
        "oppgave": oppgave["tekst"],
        "omrade": oppgave["omrade"],
        "fasit": oppgave["fasit"],
        "modell": modell,
        "leverandor": leverandor,
        "verktoy": bruk_verktoy,
        "forklar_steg": forklar_steg,
        "statuskode": statuskode,
        "riktig": _vurder(oppgave, data),
        "validert": bool(data.get("validert")),
        "valideringsstatus": data.get("valideringsstatus"),
        "valideringsdetaljer": (data.get("valideringsdetaljer") or "")[:400],
        "tokens": data.get("tokens_brukt"),
        "tokens_detaljer": data.get("tokens"),
        "kostnad_usd": data.get("estimert_kostnad"),
        "varighet_s": round(time.time() - start, 1),
        "antall_verktoykall": len(data.get("verktoy_brukt") or []),
        "verktoykall": [
            {"navn": k["navn"], "argumenter": k["argumenter"], "ok": k["ok"]}
            for k in (data.get("verktoy_brukt") or [])
        ],
        "tolkning": (data.get("tolkning") or "")[:400],
        "problem_sympy": data.get("problem_sympy") or "",
        "oppgavetype": data.get("oppgavetype") or "",
        "variabel": data.get("variabel") or "",
        "betingelser": data.get("betingelser") or [],
        "svar": (data.get("svar") or "")[:800],
        "svar_sympy": data.get("svar_sympy") or "",
        "antall_steg": len(data.get("steg") or []),
        "formler": [f["id"] for f in (data.get("formler_brukt") or [])],
        "advarsler": data.get("advarsler") or [],
        "feil": data.get("feil"),
        "teknisk_detalj": (data.get("teknisk_detalj") or "")[:600],
    }


def kjor(
    oppgaver,
    modeller,
    verktoyvalg,
    pause: float,
    pa_nytt: bool,
    forklar_steg: bool,
    merkelapp: str | None,
    tpm: float = 8000.0,
):
    tidligere = {} if pa_nytt else {
        (r["oppgave_nr"], r["modell"], r["verktoy"], r.get("forklar_steg", True)): r
        for r in _les_tidligere()
    }
    totalt = len(oppgaver) * len(modeller) * len(verktoyvalg)
    teller = 0
    for modell in modeller:
        for bruk_verktoy in verktoyvalg:
            for oppgave in oppgaver:
                teller += 1
                nokkel = (oppgave["nr"], modell, bruk_verktoy, forklar_steg)
                if nokkel in tidligere:
                    print(f"[{teller}/{totalt}] hopper over (finnes allerede): {nokkel}")
                    continue
                merke = f"oppgave {oppgave['nr']} · {modell} · verktøy={'på' if bruk_verktoy else 'av'}"
                print(f"[{teller}/{totalt}] {merke} ...", flush=True)
                resultat = kjor_en(oppgave, modell, bruk_verktoy, forklar_steg)
                for forsok in range(1, MAKS_FORSOK + 1):
                    if resultat["statuskode"] != 503:
                        break
                    vent = 60 * forsok
                    print(
                        f"    kvote/rate limit – venter {vent} s (forsøk {forsok}/{MAKS_FORSOK})",
                        flush=True,
                    )
                    time.sleep(vent)
                    resultat = kjor_en(oppgave, modell, bruk_verktoy, forklar_steg)
                if merkelapp:
                    resultat["merkelapp"] = merkelapp
                _skriv(resultat)
                print(
                    f"    riktig={resultat['riktig']} validert={resultat['validert']} "
                    f"tokens={resultat['tokens']} verktøykall={resultat['antall_verktoykall']} "
                    f"tid={resultat['varighet_s']}s"
                    + (f" FEIL: {resultat['feil']}" if resultat["feil"] else ""),
                    flush=True,
                )
                # Gratisnivåene har en tokens-per-minutt-grense. Vi venter så lenge
                # det tar å «tjene inn» tokenene denne kjøringen brukte, slik at vi
                # ikke bare braser inn i rate limit på neste oppgave.
                hvile = max(pause, (resultat["tokens"] or 0) / max(1.0, tpm / 60.0))
                if hvile > pause:
                    print(f"    hviler {hvile:.0f} s for å holde oss under {tpm} tokens/min", flush=True)
                time.sleep(hvile)


# --- Rapport ------------------------------------------------------------------


def _celle(resultat: dict | None) -> str:
    if resultat is None:
        return "–"
    riktig = {"ja": "ja", "manuell": "manuell"}.get(resultat["riktig"], resultat["riktig"].split(" ")[0])
    validert = "ja" if resultat["validert"] else ("nei" if resultat["valideringsstatus"] == "feilet" else "n/a")
    tokens = resultat["tokens"] if resultat["tokens"] is not None else "ukjent"
    return f"{riktig}/{validert}/{tokens}"


def _les_alle() -> list[dict]:
    """Slår sammen alle resultatfiler (vi kjører modeller parallelt i hver sin fil)."""
    rader = []
    for fil in sorted(RESULTATFIL.parent.glob("resultater*.jsonl")):
        for linje in fil.read_text(encoding="utf-8").splitlines():
            if linje.strip():
                rader.append(json.loads(linje))
    return rader


def rapport() -> None:
    # Hovedtabellen skal bare ha standardkjøringene: aha-kjøringer (egen
    # merkelapp eller uten stegforklaring) hører hjemme i sine egne avsnitt.
    resultater = [
        r
        for r in _les_alle()
        if r["oppgave_nr"] <= 100 and r.get("forklar_steg", True) and not r.get("merkelapp")
    ]
    if not resultater:
        print("Ingen resultater ennå. Kjør scripts/eksperiment.py først.")
        return
    modeller = sorted({r["modell"] for r in resultater}, key=lambda m: (m.endswith(":free"), m))
    oppslag = {(r["oppgave_nr"], r["modell"], r["verktoy"]): r for r in resultater}
    kolonner = [(m, v) for m in modeller for v in (True, False)]

    print("\n| # | Oppgave | " + " | ".join(
        f"{m.split('/')[-1]} {'+ tools' if v else 'uten tools'}" for m, v in kolonner
    ) + " | Kommentar |")
    print("|---|---------|" + "|".join(["------------------"] * len(kolonner)) + "|-----------|")
    for oppgave in OPPGAVER:
        celler = [_celle(oppslag.get((oppgave["nr"], m, v))) for m, v in kolonner]
        print(f"| {oppgave['nr']} | {oppgave['tekst']} | " + " | ".join(celler) + " |  |")

    print("\n### Tokenforbruk og kostnad\n")
    print("| Modell | Verktøy | Kjøringer | Tokens totalt | Snitt | Kostnad (USD) | Snitt tid |")
    print("|---|---|---|---|---|---|---|")
    for modell, bruk_verktoy in kolonner:
        utvalg = [r for r in resultater if r["modell"] == modell and r["verktoy"] == bruk_verktoy]
        if not utvalg:
            continue
        tokens = sum(r["tokens"] or 0 for r in utvalg)
        kostnad = sum(r["kostnad_usd"] or 0 for r in utvalg)
        tid = sum(r["varighet_s"] for r in utvalg) / len(utvalg)
        print(
            f"| {modell} | {'på' if bruk_verktoy else 'av'} | {len(utvalg)} | {tokens} | "
            f"{tokens // max(1, len(utvalg))} | {kostnad:.4f} | {tid:.1f} s |"
        )

    med_verktoy = [r for r in resultater if r["verktoy"] and r["tokens"]]
    if med_verktoy:
        snitt_tokens = sum(r["tokens"] for r in med_verktoy) / len(med_verktoy)
        snitt_kostnad = sum(r["kostnad_usd"] or 0 for r in med_verktoy) / len(med_verktoy)
        print(
            f"\nSnitt per oppgave med verktøy: {snitt_tokens:.0f} tokens, "
            f"{snitt_kostnad:.5f} USD.\n"
            f"1000 studenter x 50 oppgaver = 50 000 oppgaver: "
            f"{snitt_tokens * 50000 / 1e6:.1f} millioner tokens, "
            f"{snitt_kostnad * 50000:.0f} USD."
        )

    print("\n### Riktig/feil per kolonne\n")
    for modell, bruk_verktoy in kolonner:
        utvalg = [r for r in resultater if r["modell"] == modell and r["verktoy"] == bruk_verktoy]
        if not utvalg:
            continue
        riktige = sum(1 for r in utvalg if r["riktig"] == "ja")
        validerte = sum(1 for r in utvalg if r["validert"])
        manuelle = sum(1 for r in utvalg if r["riktig"] == "manuell")
        print(
            f"- {modell}, verktøy {'på' if bruk_verktoy else 'av'}: {riktige} riktige av "
            f"{len(utvalg) - manuelle} maskinsjekkbare ({manuelle} vurderes manuelt), "
            f"{validerte} validert av appen."
        )


def vurder_pa_nytt() -> None:
    """Regner ut «riktig» på nytt for alle lagrede kjøringer, uten nye API-kall.

    Brukes når vi oppdager at fasitsjekken vår var for streng (f.eks. at
    modellen svarte med løsningsvektoren [3, -24, 30] i stedet for
    x = 3, y = -24, z = 30). Selve modellsvaret ligger lagret, så vurderingen
    kan gjøres om uten å bruke kvote.
    """
    rader = _les_tidligere()
    if not rader:
        print("Ingen resultater å vurdere.")
        return
    etter_nr = {o["nr"]: o for o in OPPGAVER + AHA_OPPGAVER + EKSTRA_OPPGAVER}
    endret = 0
    for rad in rader:
        oppgave = etter_nr.get(rad["oppgave_nr"])
        if oppgave is None:
            continue
        ny = _vurder(oppgave, {"svar_sympy": rad.get("svar_sympy", "")})
        if ny != rad.get("riktig"):
            print(f"  oppgave {rad['oppgave_nr']} ({rad['modell']}, verktøy={rad['verktoy']}): "
                  f"{rad.get('riktig')} -> {ny}")
            rad["riktig"] = ny
            endret += 1
    RESULTATFIL.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rader), encoding="utf-8"
    )
    print(f"Oppdaterte {endret} av {len(rader)} vurderinger.")


def valider_pa_nytt() -> None:
    """Kjører valideringen på nytt for alle lagrede kjøringer, uten nye API-kall.

    Brukes når vi har forbedret validatoren underveis, slik at alle radene er
    vurdert av samme versjon av appen.
    """
    from backend.main import _valider

    for fil in sorted(RESULTATFIL.parent.glob("resultater*.jsonl")):
        rader = [json.loads(l) for l in fil.read_text(encoding="utf-8").splitlines() if l.strip()]
        endret = 0
        for rad in rader:
            if rad.get("statuskode") != 200:
                continue
            if rad.get("valideringsstatus") != "ikke_mulig":
                # Rader som allerede ble kontrollert, står som de ble målt.
                # Vi ser bare om den forbedrede validatoren klarer dem vi ga opp på.
                continue
            resultat = {
                "problem_sympy": rad.get("problem_sympy", ""),
                "svar_sympy": rad.get("svar_sympy", ""),
                "oppgavetype": rad.get("oppgavetype", ""),
                "variabel": rad.get("variabel", ""),
                "betingelser": rad.get("betingelser", []),
                "verktoy_brukt": [
                    {"navn": k["navn"], "argumenter": k["argumenter"], "ok": k["ok"]}
                    for k in (rad.get("verktoykall") or [])
                ],
            }
            ny = _valider(resultat)
            if bool(ny.get("validert")) != bool(rad.get("validert")) or ny.get(
                "status"
            ) != rad.get("valideringsstatus"):
                endret += 1
                print(
                    f"  {fil.name} oppgave {rad['oppgave_nr']} ({rad['modell']}, "
                    f"verktøy={rad['verktoy']}): {rad.get('valideringsstatus')} -> {ny.get('status')}"
                )
            rad["validert"] = bool(ny.get("validert"))
            rad["valideringsstatus"] = ny.get("status")
            rad["valideringsdetaljer"] = (ny.get("detaljer") or "")[:400]
        fil.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rader), encoding="utf-8"
        )
        print(f"{fil.name}: {len(rader)} rader, {endret} endret")


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Eksperimentkjører for Del B")
    parser.add_argument("--modeller", nargs="+", default=MODELLER)
    parser.add_argument("--oppgaver", nargs="+", type=int, help="Oppgavenumre (standard: alle 10)")
    parser.add_argument("--kun-tools", action="store_true", help="Bare kjøringer med verktøy")
    parser.add_argument("--uten-tools", action="store_true", help="Bare kjøringer uten verktøy")
    parser.add_argument("--uten-steg", action="store_true", help="Aha-bryter 2: uten stegforklaring")
    parser.add_argument("--aha", action="store_true", help="Kjør aha-oppgavene (tvetydig + valideringsfeil)")
    parser.add_argument("--ekstra", action="store_true", help="Kjør tilleggssettet med vanskeligere oppgaver")
    parser.add_argument("--pause", type=float, default=8.0, help="Minste antall sekunder mellom kjøringer")
    parser.add_argument(
        "--tpm",
        type=float,
        default=8000.0,
        help="Leverandørens tokens-per-minutt-grense (styrer hvor lenge vi hviler)",
    )
    parser.add_argument("--pa-nytt", action="store_true", help="Kjør på nytt selv om resultatet finnes")
    parser.add_argument("--rapport", action="store_true", help="Skriv tabell og kostnadstall")
    parser.add_argument(
        "--vurder-pa-nytt",
        action="store_true",
        help="Regn ut «riktig» på nytt for lagrede kjøringer (ingen nye API-kall)",
    )
    parser.add_argument("--merkelapp", help="Merkelapp som lagres på kjøringene")
    parser.add_argument("--ut", help="Egen resultatfil (for å kjøre flere modeller samtidig)")
    parser.add_argument(
        "--valider-pa-nytt",
        action="store_true",
        help="Kjør valideringen på nytt for lagrede kjøringer (ingen nye API-kall)",
    )
    argumenter = parser.parse_args()
    if argumenter.ut:
        sett_resultatfil(argumenter.ut)

    if argumenter.valider_pa_nytt:
        valider_pa_nytt()
        return
    if argumenter.vurder_pa_nytt:
        vurder_pa_nytt()
        return
    if argumenter.rapport:
        rapport()
        return

    oppgaver = OPPGAVER
    if argumenter.aha:
        oppgaver = AHA_OPPGAVER
    elif argumenter.ekstra:
        oppgaver = EKSTRA_OPPGAVER
    if argumenter.oppgaver:
        oppgaver = [o for o in oppgaver if o["nr"] in argumenter.oppgaver]
    verktoyvalg = [True, False]
    if argumenter.kun_tools:
        verktoyvalg = [True]
    elif argumenter.uten_tools:
        verktoyvalg = [False]

    kjor(
        oppgaver,
        argumenter.modeller,
        verktoyvalg,
        argumenter.pause,
        argumenter.pa_nytt,
        forklar_steg=not argumenter.uten_steg,
        merkelapp=argumenter.merkelapp,
        tpm=argumenter.tpm,
    )
    print("\nFerdig. Kjør «python scripts/eksperiment.py --rapport» for tabellen.")


if __name__ == "__main__":
    main_cli()
