"""Numerisk validering av løsninger.

HVORFOR: Etterprøvbarhet er ikke valgfritt for ingeniører. Hvis appen sier at
y(x) løser differensialligningen, skal vi SJEKKE det – ved å sette løsningen
inn i originalproblemet og evaluere numerisk i flere punkter.

Validatoren prøver å MOTBEVISE svaret, og bruker så langt det går en annen
metode enn den som produserte svaret:

- derivasjon: numerisk derivasjon (mpmath) av oppgaven, sammenlignet med svaret
- ubestemt integral: numerisk derivasjon av svaret, sammenlignet med integranden
- bestemt integral: numerisk integrasjon (mpmath.quad)
- ligning/system: løsningen settes inn i ligningen
- ODE: løsningen (og eventuelle startbetingelser) settes inn i ligningen
- matriser: A·A⁻¹ = I, A·x = b, det(A − λI) = 0 med numerisk (mpmath) regning
- komplekse tall/forenkling: begge uttrykk regnes ut numerisk og sammenlignes

VÅRE VALG (jf. [FYLL INN SELV] i PROMPTS/04_validator.md):

1. *Toleranse:* relativ 1e-8 (``VALIDERING_TOLERANSE``). Vi regner med 30
   gjeldende siffer, så ekte avrundingsfeil ligger rundt 1e-25 – langt under
   grensen. Samtidig er 1e-8 strengt nok til å avsløre reelle feil: en
   koeffisient som 0,333 i stedet for 1/3 gir et avvik på ca. 1e-3. Oppgir
   modellen svaret med desimaler (f.eks. 0,1534), slakkes toleransen til
   halve siste siffer, ellers ville et korrekt avrundet svar blitt underkjent.
   Er BEGGE sidene eksakte tall, finnes det ingen avrundingsfeil å ta høyde
   for, og da gjelder ingen slakk: to brøker sammenlignes eksakt, og andre
   eksakte uttrykk får ``TOLERANSE_EKSAKT``. Uten dette tillot den relative
   grensen et avvik på over ti millioner på et 16-sifret heltall.
2. *Punkter:* 3 «tilfeldige» punkter, men trukket deterministisk ut fra
   oppgaveteksten, slik at samme oppgave gir samme kontroll hver gang
   (reproduserbarhet i eksperimentet). Punkter der uttrykket ikke lar seg
   evaluere (deling på null o.l.) trekkes på nytt.
3. *Dette klarer vi IKKE å validere*, og da sier appen ærlig fra i stedet for
   å vise grønt lys: bevis og begrepsforklaringer, svar uten maskinlesbar
   form, integraler uten lukket form, egenvektorer, uendelige løsningsmengder
   (vi sjekker bare de oppgitte løsningene), og oppgaver der modellen ikke
   oppga hvilken type oppgave det var.
4. *Viktig begrensning:* validering skjer mot problemet SLIK MODELLEN TOLKET
   DET. Har modellen misforstått oppgaven (klassisk: sin^-1), kan svaret være
   «validert» og likevel feil svar på studentens spørsmål. Derfor viser
   frontend alltid tolkningen ved siden av valideringen.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import mpmath as mp
import sympy as sp

from backend.parsing import (
    TolkningsFeil,
    del_pa_toppniva,
    tolk_betingelser,
    tolk_likning,
    tolk_likninger,
    tolk_matrise,
    tolk_ode,
    tolk_symbol,
    tolk_uttrykk,
    tolk_vektor,
)

TOLERANSE = float(os.getenv("VALIDERING_TOLERANSE", "1e-8"))
# Når begge sider er eksakte tall, finnes det ingen trunkeringsfeil å ta høyde
# for, og 1e-8 er 17 størrelsesordener for slakt: på et 16-sifret heltall
# tillater den relative grensen et avvik på over 10 millioner. Se _eksakt_tall.
TOLERANSE_EKSAKT = 1e-20
ANTALL_PUNKTER = int(os.getenv("VALIDERING_PUNKTER", "3"))
PRESISJON = 30
TIDSGRENSE_SEKUNDER = int(os.getenv("VALIDERING_TIDSGRENSE", "30"))
_PROSJEKTROT = Path(__file__).resolve().parent.parent

VALIDERT = "validert"
FEILET = "feilet"
IKKE_MULIG = "ikke_mulig"

_TYPER = {
    "derivasjon": "derivasjon",
    "derivert": "derivasjon",
    "derivative": "derivasjon",
    "integral": "integral",
    "integrasjon": "integral",
    "ubestemt_integral": "integral",
    "antiderivert": "integral",
    "bestemt_integral": "bestemt_integral",
    "definite_integral": "bestemt_integral",
    "grenseverdi": "grenseverdi",
    "grense": "grenseverdi",
    "limit": "grenseverdi",
    "ligning": "ligning",
    "likning": "ligning",
    "equation": "ligning",
    "ligningssystem": "ligning",
    "likningssystem": "ligning",
    "ode": "ode",
    "differensialligning": "ode",
    "ivp": "ode",
    "initialverdiproblem": "ode",
    "determinant": "determinant",
    "invers": "invers",
    "inverse": "invers",
    "egenverdier": "egenverdier",
    "eigenvalues": "egenverdier",
    "ax_b": "ax_b",
    "solve_ax_b": "ax_b",
    "lineart_system": "ax_b",
    "kompleks": "beregning",
    "beregning": "beregning",
    "forenkling": "beregning",
    "aritmetikk": "beregning",
    "bevis": "ikke_mulig",
    "begrep": "ikke_mulig",
    "begrepsforklaring": "ikke_mulig",
    "teori": "ikke_mulig",
    "annet": "ukjent",
}


class ValideringsFeil(ValueError):
    """Validering kunne ikke gjennomføres (ikke det samme som at svaret er feil)."""


# --- Numeriske hjelpefunksjoner ----------------------------------------------


def _til_mp(verdi):
    """SymPy-tall -> mpmath-tall."""
    tall = complex(verdi)
    if abs(tall.imag) < 1e-25:
        return mp.mpf(tall.real)
    return mp.mpc(tall)


def _evaluer(uttrykk: sp.Expr, verdier: dict):
    """Regner ut uttrykket numerisk med høy presisjon. Kaster ValideringsFeil hvis umulig."""
    try:
        substituert = uttrykk.subs(verdier)
        if substituert.free_symbols:
            raise ValideringsFeil(
                "Uttrykket inneholder ukjente symboler: "
                + ", ".join(sorted(map(str, substituert.free_symbols)))
            )
        tall = sp.N(substituert, PRESISJON)
        if tall.has(sp.zoo) or tall.has(sp.nan) or tall.has(sp.oo):
            raise ValideringsFeil("Uttrykket er ikke endelig i dette punktet.")
        return _til_mp(tall)
    except ValideringsFeil:
        raise
    except Exception as e:
        raise ValideringsFeil(f"Kunne ikke regne ut uttrykket numerisk: {type(e).__name__}") from None


def _callable(uttrykk: sp.Expr, x: sp.Symbol, faste: dict):
    """Lager en mpmath-funksjon av uttrykket, med parametere satt til faste verdier."""
    fast = uttrykk.subs(faste)

    try:
        funksjon = sp.lambdify(x, fast, modules=["mpmath"])
        funksjon(mp.mpf("1.0"))  # prøvekjøring
        return funksjon
    except Exception:
        def reserve(t):
            return _evaluer(fast, {x: sp.Float(mp.nstr(mp.mpf(t), 25), PRESISJON)})

        return reserve


def _punkter(fro: str, antall: int = ANTALL_PUNKTER, lav: float = 0.35, hoy: float = 2.65) -> list[float]:
    """«Tilfeldige» punkter, men reproduserbare for samme oppgave."""
    generator = random.Random(hashlib.sha256(fro.encode("utf-8")).hexdigest())
    punkter, forsok = [], 0
    while len(punkter) < antall and forsok < 60:
        forsok += 1
        kandidat = round(generator.uniform(lav, hoy), 6)
        if all(abs(kandidat - p) > 0.05 for p in punkter):
            punkter.append(kandidat)
    return punkter


def _tilfeldige_verdier(symboler, fro: str) -> dict:
    """Gir frie parametere (C1, C2, a, ...) konkrete verdier, ulike for hvert symbol."""
    generator = random.Random(hashlib.sha256(("par" + fro).encode("utf-8")).hexdigest())
    return {
        symbol: sp.Float(round(generator.uniform(0.4, 1.9), 6), PRESISJON)
        for symbol in sorted(symboler, key=str)
    }


def _toleranser(losning_tekst: str) -> tuple[float, float]:
    """(relativ, absolutt) toleranse. Desimalsvar får slakkere absolutt toleranse.

    Slakken er en halv enhet i siste oppgitte siffer – nøyaktig det et korrekt
    avrundet svar kan avvike. Den lille påplussingen tar høyde for at vi
    sammenligner flyttall. Var den større (vi hadde 0,75), ville et svar med
    FEIL siste siffer sluppet gjennom: 2,0000 ble godkjent som avrunding av
    2,00007, som skal bli 2,0001.
    """
    desimaler = [len(d) for d in re.findall(r"\d\.(\d+)", losning_tekst)]
    if desimaler and max(desimaler) <= 12:
        return TOLERANSE, 0.5 * 10 ** (-max(desimaler)) * (1 + 1e-9)
    return TOLERANSE, 0.0


def _eksakt_tall(uttrykk) -> bool:
    """Er dette et eksakt tall – heltall, brøk, sqrt(2), pi, I – uten desimaler?

    Oppgir modellen et desimaltall, er svaret avrundet, og da MÅ det finnes
    slakk. Er begge sider eksakte, skal det ikke finnes slakk i det hele tatt.
    """
    try:
        return bool(uttrykk.is_number) and not uttrykk.atoms(sp.Float)
    except AttributeError:
        return False


def _eksakt_toleranse(tol_rel: float, *uttrykk) -> float:
    """Strammer den relative toleransen når alle sidene er eksakte tall."""
    if all(_eksakt_tall(u) for u in uttrykk):
        return min(tol_rel, TOLERANSE_EKSAKT)
    return tol_rel


def _godkjent(avvik, skala, tol_rel: float, tol_abs: float) -> bool:
    grense = max(tol_abs, tol_rel * max(1.0, float(abs(skala))))
    return float(abs(avvik)) <= grense


def _format(verdi) -> str:
    try:
        return mp.nstr(verdi, 6)
    except Exception:
        return str(verdi)


# --- Selve valideringen -------------------------------------------------------


def _normaliser_type(oppgavetype) -> str:
    if not oppgavetype:
        return "ukjent"
    return _TYPER.get(str(oppgavetype).strip().lower().replace(" ", "_").replace("-", "_"), "ukjent")


def _resultat(status: str, detaljer: str, **ekstra) -> dict:
    svar = {"validert": status == VALIDERT, "status": status, "detaljer": detaljer}
    svar.update({k: v for k, v in ekstra.items() if v is not None})
    return svar


def _auto_type(problem: str) -> str:
    """Gjetter oppgavetype når modellen ikke oppga den (brukes bl.a. av selvtesten)."""
    try:
        tolk_ode(problem)
        return "ode"
    except (TolkningsFeil, ValueError):
        pass
    try:
        objekt = tolk_uttrykk(problem, tillat_ukjente_funksjoner=True)
    except TolkningsFeil:
        return "ukjent"
    if isinstance(objekt, sp.Equality) or "=" in problem:
        return "ligning"
    if isinstance(objekt, sp.Integral):
        return "bestemt_integral" if objekt.limits and len(objekt.limits[0]) == 3 else "integral"
    if isinstance(objekt, sp.Limit):
        return "grenseverdi"
    return "ukjent"


def _valider(problem: str, losning: str, oppgavetype=None, variabel=None, betingelser=None) -> dict:
    problem = (problem or "").strip()
    losning = (losning or "").strip()
    type_ = _normaliser_type(oppgavetype)
    if type_ == IKKE_MULIG:
        return _resultat(
            IKKE_MULIG,
            "Dette er en bevis- eller begrepsoppgave. Den kan ikke verifiseres numerisk av "
            "SymPy, og svaret er derfor IKKE verktøyverifisert – det er språkmodellens eget "
            "resonnement.",
            metode="ingen (bevis/begrep)",
        )
    if not problem or not losning:
        return _resultat(
            IKKE_MULIG,
            "Modellen oppga ikke problem og svar i maskinlesbar form, så svaret kunne ikke "
            "kontrolleres. Stol ikke på det uten å regne etter selv.",
        )
    if type_ == "ukjent":
        type_ = _auto_type(problem)
        if type_ == "ukjent":
            return _resultat(
                IKKE_MULIG,
                "Vi vet ikke hvilken type oppgave dette er, og kan derfor ikke velge en "
                "kontrollmetode. Svaret er ikke verifisert.",
            )
    tol_rel, tol_abs = _toleranser(losning)
    fro = problem + "|" + losning
    try:
        if type_ == "derivasjon":
            return _valider_derivasjon(problem, losning, variabel, tol_rel, tol_abs, fro)
        if type_ == "integral":
            return _valider_integral(problem, losning, variabel, tol_rel, tol_abs, fro)
        if type_ == "bestemt_integral":
            return _valider_bestemt_integral(problem, losning, variabel, tol_rel, tol_abs)
        if type_ == "grenseverdi":
            return _valider_grenseverdi(problem, losning, variabel, tol_rel, tol_abs)
        if type_ == "ligning":
            return _valider_ligning(problem, losning, variabel, tol_rel, tol_abs, fro)
        if type_ == "ode":
            return _valider_ode(problem, losning, betingelser, tol_rel, tol_abs, fro)
        if type_ in {"determinant", "invers", "egenverdier", "ax_b"}:
            return _valider_matrise(type_, problem, losning, tol_rel, tol_abs)
        if type_ == "beregning":
            return _valider_likhet(problem, losning, variabel, tol_rel, tol_abs, fro)
    except (TolkningsFeil, ValideringsFeil) as e:
        return _resultat(IKKE_MULIG, f"Kunne ikke kontrollere svaret: {e}")
    except Exception as e:
        return _resultat(IKKE_MULIG, f"Valideringen feilet uventet: {type(e).__name__}: {e}")
    return _resultat(IKKE_MULIG, f"Ingen kontrollmetode for oppgavetypen «{oppgavetype}».")


def _finn_variabel(variabel, *uttrykk) -> sp.Symbol:
    if variabel:
        return tolk_symbol(str(variabel))
    symboler: set = set()
    for u in uttrykk:
        symboler |= getattr(u, "free_symbols", set())
    if not symboler:
        raise ValideringsFeil("Fant ingen variabel å kontrollere i.")
    for navn in ("x", "t", "y", "z", "u", "s"):
        for symbol in symboler:
            if str(symbol) == navn:
                return symbol
    return sorted(symboler, key=str)[0]


def _uttrykk(tekst: str) -> sp.Expr:
    # lempelig=True: modellen skriver ofte «x*e^(2x)». Vi vil heller kontrollere
    # svaret enn å la det stå ukontrollert fordi notasjonen var slurvete.
    objekt = tolk_uttrykk(tekst, lempelig=True)
    if isinstance(objekt, sp.Equality):
        objekt = objekt.rhs if objekt.lhs.is_Symbol or objekt.lhs.is_Function else objekt.lhs - objekt.rhs
    if not isinstance(objekt, sp.Expr):
        raise ValideringsFeil(f"«{tekst}» er ikke et vanlig uttrykk.")
    return objekt


def _valider_derivasjon(problem, losning, variabel, tol_rel, tol_abs, fro) -> dict:
    f = _uttrykk(problem)
    g = _uttrykk(losning)
    x = _finn_variabel(variabel, f, g)
    parametre = _tilfeldige_verdier((f.free_symbols | g.free_symbols) - {x}, fro)
    f_num = _callable(f, x, parametre)
    g_num = _callable(g, x, parametre)
    mp.mp.dps = PRESISJON
    punkter, avvik_liste, detaljer = [], [], []
    for punkt in _punkter(fro, antall=12):
        if len(punkter) >= ANTALL_PUNKTER:
            break
        try:
            fasit = mp.diff(f_num, mp.mpf(punkt))
            oppgitt = g_num(mp.mpf(punkt))
        except Exception:
            continue
        avvik = abs(oppgitt - fasit)
        punkter.append(punkt)
        avvik_liste.append(avvik)
        detaljer.append(f"x = {punkt}: numerisk derivert {_format(fasit)}, svaret gir {_format(oppgitt)}")
        if not _godkjent(avvik, fasit, tol_rel, tol_abs):
            return _resultat(
                FEILET,
                f"Svaret stemmer ikke med den numeriske deriverte av {f} i x = {punkt}: "
                f"den deriverte er {_format(fasit)}, men svaret gir {_format(oppgitt)} "
                f"(avvik {_format(avvik)}).",
                punkter=punkter,
                metode="numerisk derivasjon (mpmath.diff, 30 siffer)",
                maks_avvik=_format(max(avvik_liste)),
            )
    if not punkter:
        raise ValideringsFeil("Fant ingen punkter der både oppgave og svar kunne evalueres.")
    return _resultat(
        VALIDERT,
        f"Derivert numerisk i {len(punkter)} punkter og sammenlignet med svaret: "
        + "; ".join(detaljer)
        + f". Største avvik {_format(max(avvik_liste))} (toleranse {tol_rel:g}).",
        punkter=punkter,
        metode="numerisk derivasjon (mpmath.diff, 30 siffer)",
        maks_avvik=_format(max(avvik_liste)),
    )


def _valider_integral(problem, losning, variabel, tol_rel, tol_abs, fro) -> dict:
    f = _uttrykk(problem)
    if isinstance(f, sp.Integral):
        f = f.function
    F = _uttrykk(re.sub(r"\+\s*C\b", "", losning).strip() or losning)
    if F.has(sp.Integral):
        return _resultat(
            IKKE_MULIG,
            "Svaret inneholder et uløst integral, så det finnes ingen antiderivert å "
            "kontrollere. Si ærlig fra at oppgaven ikke ble løst.",
        )
    x = _finn_variabel(variabel, f, F)
    parametre = _tilfeldige_verdier((f.free_symbols | F.free_symbols) - {x}, fro)
    f_num = _callable(f, x, parametre)
    F_num = _callable(F, x, parametre)
    mp.mp.dps = PRESISJON
    punkter, avvik_liste, detaljer = [], [], []
    for punkt in _punkter(fro, antall=12):
        if len(punkter) >= ANTALL_PUNKTER:
            break
        try:
            derivert = mp.diff(F_num, mp.mpf(punkt))
            integrand = f_num(mp.mpf(punkt))
        except Exception:
            continue
        avvik = abs(derivert - integrand)
        punkter.append(punkt)
        avvik_liste.append(avvik)
        detaljer.append(
            f"x = {punkt}: d/dx(svaret) = {_format(derivert)}, integranden = {_format(integrand)}"
        )
        if not _godkjent(avvik, integrand, tol_rel, tol_abs):
            return _resultat(
                FEILET,
                f"Den deriverte av svaret er ikke lik integranden i x = {punkt}: "
                f"{_format(derivert)} mot {_format(integrand)} (avvik {_format(avvik)}). "
                "Antiderivasjonen ser altså ikke riktig ut.",
                punkter=punkter,
                metode="numerisk derivasjon av svaret (mpmath.diff)",
                maks_avvik=_format(max(avvik_liste)),
            )
    if not punkter:
        raise ValideringsFeil("Fant ingen punkter der både integrand og svar kunne evalueres.")
    return _resultat(
        VALIDERT,
        f"Deriverte svaret numerisk i {len(punkter)} punkter og sammenlignet med integranden: "
        + "; ".join(detaljer)
        + f". Største avvik {_format(max(avvik_liste))} (toleranse {tol_rel:g}). "
        "Merk at integrasjonskonstanten C ikke kan kontrolleres på denne måten.",
        punkter=punkter,
        metode="numerisk derivasjon av svaret (mpmath.diff)",
        maks_avvik=_format(max(avvik_liste)),
    )


def _valider_bestemt_integral(problem, losning, variabel, tol_rel, tol_abs) -> dict:
    objekt = tolk_uttrykk(problem)
    if not isinstance(objekt, sp.Integral) or not objekt.limits or len(objekt.limits[0]) != 3:
        raise ValideringsFeil(
            "For et bestemt integral må problemet være på formen Integral(f, (x, a, b))."
        )
    integrand = objekt.function
    x, a, b = objekt.limits[0]
    svar = _uttrykk(losning)
    if svar.free_symbols:
        raise ValideringsFeil("Svaret på et bestemt integral må være et tall.")
    mp.mp.dps = PRESISJON
    f_num = _callable(integrand, x, {})
    grenser = [
        mp.inf if grense == sp.oo else (-mp.inf if grense == -sp.oo else _evaluer(grense, {}))
        for grense in (a, b)
    ]
    try:
        fasit = mp.quad(f_num, grenser)
    except Exception as e:
        raise ValideringsFeil(f"Numerisk integrasjon lyktes ikke: {type(e).__name__}") from None
    oppgitt = _evaluer(svar, {})
    avvik = abs(oppgitt - fasit)
    if not _godkjent(avvik, fasit, max(tol_rel, 1e-10), tol_abs):
        return _resultat(
            FEILET,
            f"Numerisk integrasjon gir {_format(fasit)}, men svaret er {_format(oppgitt)} "
            f"(avvik {_format(avvik)}).",
            metode="numerisk integrasjon (mpmath.quad)",
            maks_avvik=_format(avvik),
        )
    return _resultat(
        VALIDERT,
        f"Regnet integralet numerisk med mpmath.quad: {_format(fasit)}. Svaret gir "
        f"{_format(oppgitt)}, avvik {_format(avvik)} (toleranse {tol_rel:g}).",
        metode="numerisk integrasjon (mpmath.quad)",
        maks_avvik=_format(avvik),
    )


def _valider_grenseverdi(problem, losning, variabel, tol_rel, tol_abs) -> dict:
    objekt = tolk_uttrykk(problem)
    if not isinstance(objekt, sp.Limit):
        raise ValideringsFeil("For en grenseverdi må problemet være på formen Limit(f, x, a).")
    f, x, a = objekt.args[0], objekt.args[1], objekt.args[2]
    retning = str(objekt.args[3]) if len(objekt.args) > 3 else "+"
    svar = _uttrykk(losning)
    mp.mp.dps = PRESISJON
    f_num = _callable(f, x, {})
    if svar.has(sp.oo) or svar.has(sp.zoo):
        return _resultat(
            IKKE_MULIG,
            "Svaret er uendelig. Vi kontrollerer ikke uendelige grenseverdier numerisk, "
            "så dette svaret er ikke verifisert.",
            metode="ingen",
        )
    mal = _evaluer(svar, {})
    punkter, verdier = [], []
    for eksponent in (3, 5, 7):
        try:
            if a == sp.oo:
                punkt = mp.mpf(10) ** eksponent
            elif a == -sp.oo:
                punkt = -(mp.mpf(10) ** eksponent)
            else:
                steg = mp.mpf(10) ** (-eksponent)
                punkt = _evaluer(a, {}) + (steg if retning != "-" else -steg)
            verdi = f_num(punkt)
        except Exception:
            continue
        punkter.append(float(punkt) if abs(punkt) < 1e15 else float("inf"))
        verdier.append(abs(verdi - mal))
    if not verdier:
        raise ValideringsFeil("Klarte ikke å evaluere funksjonen nær grensepunktet.")
    if not _godkjent(verdier[-1], mal, max(tol_rel, 1e-5), max(tol_abs, 1e-5)):
        return _resultat(
            FEILET,
            f"Funksjonsverdiene nærmer seg ikke {_format(mal)} når {x} går mot {a}: "
            f"avviket er fortsatt {_format(verdier[-1])} tett på grensen.",
            metode="numerisk grensetilnærming",
            maks_avvik=_format(verdier[-1]),
        )
    return _resultat(
        VALIDERT,
        f"Evaluerte funksjonen stadig nærmere {x} = {a} (avvik "
        + ", ".join(_format(v) for v in verdier)
        + f"). Verdiene nærmer seg {_format(mal)}. Dette er en numerisk indikasjon, "
        "ikke et bevis for grenseverdien.",
        metode="numerisk grensetilnærming",
        maks_avvik=_format(verdier[-1]),
    )


def _tolk_losningsmengde(tekst: str, variabler: list[sp.Symbol]) -> list[dict]:
    """«[-2, 4]», «x = 3, y = -24», «[{x: 3, y: -24, z: 30}]» -> liste av løsninger."""
    tekst = tekst.strip()
    if tekst.startswith("[") and tekst.endswith("]"):
        tekst = tekst[1:-1].strip()
    if "{" in tekst:
        losninger = []
        for blokk in re.findall(r"\{([^{}]*)\}", tekst):
            losning = {}
            for del_ in del_pa_toppniva(blokk, ","):
                treff = re.match(r"^\s*([A-Za-z]\w*)\s*[:=]\s*(.+)$", del_)
                if not treff:
                    raise ValideringsFeil(f"Forstod ikke løsningen «{del_}».")
                losning[tolk_symbol(treff.group(1))] = _uttrykk(treff.group(2))
            if losning:
                losninger.append(losning)
        if losninger:
            return losninger
    deler = del_pa_toppniva(tekst, ",;")
    if not deler:
        raise ValideringsFeil("Ingen løsninger å kontrollere.")
    tilordninger = [re.match(r"^\s*([A-Za-z]\w*)\s*=\s*(.+)$", del_) for del_ in deler]
    if all(tilordninger) and len({t.group(1) for t in tilordninger}) == len(deler) > 1:
        return [{tolk_symbol(t.group(1)): _uttrykk(t.group(2)) for t in tilordninger}]
    losninger = []
    for del_, treff in zip(deler, tilordninger):
        if treff:
            losninger.append({tolk_symbol(treff.group(1)): _uttrykk(treff.group(2))})
        else:
            losninger.append({variabler[0]: _uttrykk(del_)})
    return losninger


def _valider_ligning(problem, losning, variabel, tol_rel, tol_abs, fro) -> dict:
    likninger = tolk_likninger(problem)
    variabler = []
    if variabel:
        variabler = [tolk_symbol(v) for v in re.split(r"[,\s]+", str(variabel).strip()) if v]
    if not variabler:
        symboler: set = set()
        for likning in likninger:
            symboler |= likning.free_symbols
        variabler = sorted(symboler, key=str) or [sp.Symbol("x")]
    losninger = _tolk_losningsmengde(losning, variabler)
    if not losninger:
        return _resultat(IKKE_MULIG, "Fant ingen løsninger i svaret å kontrollere.")
    detaljer, verste = [], mp.mpf(0)
    for nummer, kandidat in enumerate(losninger, start=1):
        for likning in likninger:
            rest = likning.lhs - likning.rhs
            frie = rest.free_symbols - set(kandidat)
            ekstra = _tilfeldige_verdier(frie, fro + str(nummer))
            try:
                verdi = _evaluer(rest.subs(kandidat), ekstra)
                skala = max(
                    abs(_evaluer(likning.lhs.subs(kandidat), ekstra)),
                    abs(_evaluer(likning.rhs.subs(kandidat), ekstra)),
                    mp.mpf(1),
                )
            except ValideringsFeil as e:
                return _resultat(
                    IKKE_MULIG,
                    f"Kunne ikke sette løsning {nummer} inn i ligningen: {e}",
                )
            verste = max(verste, abs(verdi))
            tekst = ", ".join(f"{k} = {v}" for k, v in kandidat.items())
            detaljer.append(f"{tekst}: venstre − høyre = {_format(verdi)}")
            if not _godkjent(verdi, skala, tol_rel, tol_abs):
                return _resultat(
                    FEILET,
                    f"Løsningen {tekst} oppfyller ikke ligningen {likning}: "
                    f"venstre side minus høyre side blir {_format(verdi)}, ikke 0.",
                    metode="innsetting i ligningen",
                    maks_avvik=_format(verste),
                )
    advarsel = _mangler_losninger(likninger, variabler, losninger)
    return _resultat(
        VALIDERT,
        "Satte hver oppgitte løsning inn i ligningen(e): "
        + "; ".join(detaljer)
        + f". Største avvik {_format(verste)} (toleranse {tol_rel:g}). "
        "Merk: dette viser at løsningene stemmer, ikke at alle løsninger er funnet.",
        metode="innsetting i ligningen",
        maks_avvik=_format(verste),
        advarsler=[advarsel] if advarsel else None,
    )


def _mangler_losninger(likninger, variabler, losninger) -> str | None:
    """Advarer hvis et polynom har flere røtter enn modellen oppga."""
    if len(likninger) != 1 or len(variabler) != 1:
        return None
    x = variabler[0]
    rest = likninger[0].lhs - likninger[0].rhs
    try:
        if rest.free_symbols != {x}:
            return None
        polynom = sp.Poly(rest, x)
        if polynom.degree() < 1:
            return None
        rotter = polynom.nroots(n=20, maxsteps=100)
    except Exception:
        return None
    oppgitte = []
    for kandidat in losninger:
        try:
            oppgitte.append(_evaluer(kandidat[x], {}))
        except Exception:
            return None
    mangler = [
        rot
        for rot in rotter
        if all(abs(_til_mp(rot) - o) > 1e-6 * max(1, abs(_til_mp(rot))) for o in oppgitte)
    ]
    if not mangler:
        return None
    return (
        f"Polynomet har grad {polynom.degree()} og røttene "
        + ", ".join(str(sp.nsimplify(r, rational=False, tolerance=1e-10)) for r in rotter)
        + ". Svaret mangler "
        + ", ".join(_format(_til_mp(r)) for r in mangler)
        + " (greit hvis oppgaven bare spør etter reelle løsninger)."
    )


def _valider_ode(problem, losning, betingelser, tol_rel, tol_abs, fro) -> dict:
    likning, y, x = tolk_ode(problem)
    uttrykk = _ode_losning(losning, y, x)
    rest = (likning.lhs - likning.rhs).subs(y, uttrykk).doit()
    # Konstantene hentes fra BÅDE resten og løsningen: en riktig løsning gir
    # rest = 0, og da forsvinner C1/C2 derfra – men vi trenger dem for å kunne
    # regne ut løsningen selv.
    konstanter = sorted((rest.free_symbols | uttrykk.free_symbols) - {x}, key=str)
    detaljer, verste = [], mp.mpf(0)
    punkter = _punkter(fro, antall=12)
    brukte = []
    for punkt in punkter:
        if len(brukte) >= ANTALL_PUNKTER:
            break
        verdier = _tilfeldige_verdier(konstanter, fro + str(punkt))
        verdier[x] = sp.Float(punkt, PRESISJON)
        try:
            avvik = _evaluer(rest, verdier)
            skala = max(abs(_evaluer(uttrykk, verdier)), mp.mpf(1))
        except ValideringsFeil:
            continue
        brukte.append(punkt)
        verste = max(verste, abs(avvik))
        konstant_tekst = ", ".join(f"{k} = {sp.N(v, 3)}" for k, v in verdier.items() if k != x)
        detaljer.append(
            f"{x} = {punkt}" + (f" ({konstant_tekst})" if konstant_tekst else "") + f": rest = {_format(avvik)}"
        )
        if not _godkjent(avvik, skala, tol_rel, tol_abs):
            return _resultat(
                FEILET,
                f"Løsningen oppfyller ikke differensialligningen: settes {y} = {uttrykk} inn i "
                f"{likning}, blir venstre side minus høyre side {_format(avvik)} (ikke 0) i {x} = {punkt}.",
                metode="innsetting i differensialligningen",
                punkter=brukte,
                maks_avvik=_format(verste),
            )
    if not brukte:
        raise ValideringsFeil("Fant ingen punkter der løsningen kunne evalueres.")

    advarsler = []
    orden = sp.ode_order(likning, y.func)
    if not betingelser and len(konstanter) < orden:
        advarsler.append(
            f"Ligningen har orden {orden}, men løsningen har bare {len(konstanter)} vilkårlige "
            "konstanter. Da er dette en spesiell løsning, ikke den generelle."
        )
    if betingelser:
        resultat_betingelser = _sjekk_betingelser(betingelser, uttrykk, y, x, tol_rel, tol_abs)
        if resultat_betingelser["status"] != VALIDERT:
            resultat_betingelser["detaljer"] = (
                "Løsningen oppfyller selve differensialligningen, men "
                + resultat_betingelser["detaljer"]
            )
            return resultat_betingelser
        detaljer.append(resultat_betingelser["detaljer"])
    elif konstanter:
        detaljer.append(f"konstantene {', '.join(map(str, konstanter))} fikk tilfeldige verdier")
    return _resultat(
        VALIDERT,
        f"Satte løsningen inn i {likning} og regnet ut resten i {len(brukte)} punkter: "
        + "; ".join(detaljer)
        + f". Største avvik {_format(verste)} (toleranse {tol_rel:g}).",
        metode="innsetting i differensialligningen",
        punkter=brukte,
        maks_avvik=_format(verste),
        advarsler=advarsler or None,
    )


def _ode_losning(losning: str, y: sp.Expr, x: sp.Symbol) -> sp.Expr:
    """«y(x) = C1*exp(x)», «Eq(y(x), ...)» eller bare høyresiden -> uttrykket for y(x)."""
    if re.search(r"(?<![<>!=])=(?!=)", losning):
        objekt = tolk_likning(losning, tillat_ukjente_funksjoner=True)
    else:
        objekt = tolk_uttrykk(losning, tillat_ukjente_funksjoner=True)
    if isinstance(objekt, list):
        if len(objekt) != 1:
            raise ValideringsFeil(
                "Svaret inneholder flere løsninger; oppgi én løsning om gangen for validering."
            )
        objekt = objekt[0]
    if isinstance(objekt, sp.Equality):
        if objekt.lhs == y:
            objekt = objekt.rhs
        elif objekt.rhs == y:
            objekt = objekt.lhs
        else:
            raise ValideringsFeil(f"Forventet en løsning på formen {y} = ..., fikk «{losning}».")
    if objekt.has(y):
        raise ValideringsFeil(
            f"Løsningen er ikke løst med hensyn på {y} (den inneholder fortsatt {y})."
        )
    if objekt.atoms(sp.core.function.AppliedUndef):
        raise ValideringsFeil("Løsningen inneholder ukjente funksjoner og kan ikke evalueres.")
    return objekt


def _sjekk_betingelser(betingelser, uttrykk, y, x, tol_rel, tol_abs) -> dict:
    try:
        krav = tolk_betingelser(betingelser, y, x)
    except TolkningsFeil as e:
        return _resultat(IKKE_MULIG, f"Forstod ikke startbetingelsene: {e}")
    if uttrykk.free_symbols - {x}:
        return _resultat(
            FEILET,
            "løsningen inneholder fortsatt vilkårlige konstanter ("
            + ", ".join(sorted(map(str, uttrykk.free_symbols - {x})))
            + "), så startbetingelsene er ikke brukt.",
        )
    detaljer = []
    for orden, punkt, verdi in krav:
        derivert = sp.diff(uttrykk, x, orden)
        try:
            beregnet = _evaluer(derivert.subs(x, punkt), {})
            onsket = _evaluer(verdi, {})
        except ValideringsFeil as e:
            return _resultat(IKKE_MULIG, f"kunne ikke kontrollere startbetingelsen: {e}")
        avvik = abs(beregnet - onsket)
        primtegn = "'" * orden
        detaljer.append(f"{y.func}{primtegn}({punkt}) = {_format(beregnet)} (krav {_format(onsket)})")
        if not _godkjent(avvik, onsket, tol_rel, tol_abs):
            return _resultat(
                FEILET,
                f"startbetingelsen {y.func}{primtegn}({punkt}) = {verdi} er ikke oppfylt: "
                f"løsningen gir {_format(beregnet)}.",
            )
    return _resultat(VALIDERT, "startbetingelsene stemmer (" + "; ".join(detaljer) + ")")


def _valider_matrise(type_, problem, losning, tol_rel, tol_abs) -> dict:
    mp.mp.dps = PRESISJON
    if type_ == "ax_b":
        from backend.tools import _del_ax_b  # samme tolkning av [A, b] som verktøyet

        A, b = _del_ax_b(problem)
        x = tolk_vektor(losning)
        if x.rows != A.cols:
            return _resultat(
                FEILET,
                f"Løsningsvektoren har {x.rows} elementer, men A har {A.cols} kolonner.",
            )
        rest = A * x - b
        avvik = max(abs(_evaluer(element, {})) for element in rest)
        skala = max([abs(_evaluer(element, {})) for element in b] + [mp.mpf(1)])
        if not _godkjent(avvik, skala, tol_rel, tol_abs):
            return _resultat(
                FEILET,
                f"A·x er ikke lik b: største avvik i A·x − b er {_format(avvik)}.",
                metode="innsetting i A·x = b",
                maks_avvik=_format(avvik),
            )
        return _resultat(
            VALIDERT,
            f"Regnet ut A·x − b numerisk; største avvik {_format(avvik)} (toleranse {tol_rel:g}).",
            metode="innsetting i A·x = b",
            maks_avvik=_format(avvik),
        )

    A = tolk_matrise(problem)
    if A.rows != A.cols:
        return _resultat(IKKE_MULIG, "Matrisen er ikke kvadratisk, så denne kontrollen gir ikke mening.")
    n = A.rows
    A_num = mp.matrix([[_evaluer(A[i, j], {}) for j in range(n)] for i in range(n)])
    if type_ == "determinant":
        svar = _uttrykk(losning)
        if n <= 8 and isinstance(svar, sp.Rational) and all(verdi.is_Rational for verdi in A):
            # Eksakt matrise og eksakt svar: da er determinanten et eksakt tall,
            # og en relativ toleranse ville bare skjult feil i store tall.
            eksakt = sp.Matrix(A).det()
            if eksakt != svar:
                return _resultat(
                    FEILET,
                    f"Eksakt determinant er {eksakt}, men svaret er {svar} "
                    f"(differanse {svar - eksakt}).",
                    metode="eksakt determinant (SymPy)",
                    maks_avvik=str(abs(svar - eksakt)),
                )
            return _resultat(
                VALIDERT,
                f"Regnet determinanten eksakt med SymPy: {eksakt}, "
                f"som er nøyaktig det svaret oppgir.",
                metode="eksakt determinant (SymPy)",
                maks_avvik="0",
            )
        fasit = mp.det(A_num)
        oppgitt = _evaluer(svar, {})
        avvik = abs(oppgitt - fasit)
        if not _godkjent(avvik, fasit, _eksakt_toleranse(tol_rel, svar), tol_abs):
            return _resultat(
                FEILET,
                f"Numerisk determinant (LU-faktorisering i mpmath) er {_format(fasit)}, "
                f"men svaret er {_format(oppgitt)}.",
                metode="numerisk determinant (mpmath.det)",
                maks_avvik=_format(avvik),
            )
        return _resultat(
            VALIDERT,
            f"Regnet determinanten numerisk med en annen metode (mpmath LU): {_format(fasit)}; "
            f"svaret gir {_format(oppgitt)}, avvik {_format(avvik)}.",
            metode="numerisk determinant (mpmath.det)",
            maks_avvik=_format(avvik),
        )
    if type_ == "invers":
        B = tolk_matrise(losning)
        if B.rows != n or B.cols != n:
            return _resultat(FEILET, f"Den inverse må være {n}x{n}, men svaret er {B.rows}x{B.cols}.")
        B_num = mp.matrix([[_evaluer(B[i, j], {}) for j in range(n)] for i in range(n)])
        produkt = A_num * B_num
        avvik = max(
            abs(produkt[i, j] - (1 if i == j else 0)) for i in range(n) for j in range(n)
        )
        if not _godkjent(avvik, 1, tol_rel, tol_abs):
            return _resultat(
                FEILET,
                f"A·A⁻¹ er ikke identitetsmatrisen: største avvik {_format(avvik)}.",
                metode="A·A⁻¹ = I",
                maks_avvik=_format(avvik),
            )
        return _resultat(
            VALIDERT,
            f"Ganget A med svaret: resultatet er identitetsmatrisen med største avvik {_format(avvik)}.",
            metode="A·A⁻¹ = I",
            maks_avvik=_format(avvik),
        )
    # egenverdier
    verdier = [_uttrykk(del_) for del_ in del_pa_toppniva(losning.strip().strip("[]"), ",;")]
    if not verdier:
        return _resultat(IKKE_MULIG, "Fant ingen egenverdier i svaret.")
    detaljer, verste = [], mp.mpf(0)
    for lam in verdier:
        lam_num = _evaluer(lam, {})
        M = mp.matrix(
            [[A_num[i, j] - (lam_num if i == j else 0) for j in range(n)] for i in range(n)]
        )
        determinant = abs(mp.det(M))
        skala = max(abs(A_num[i, j]) for i in range(n) for j in range(n)) ** n
        verste = max(verste, determinant)
        detaljer.append(f"λ = {lam}: |det(A − λI)| = {_format(determinant)}")
        if not _godkjent(determinant, skala, max(tol_rel, 1e-9), tol_abs):
            return _resultat(
                FEILET,
                f"λ = {lam} er ikke en egenverdi: det(A − λI) = {_format(determinant)}, ikke 0.",
                metode="det(A − λI) = 0 numerisk",
                maks_avvik=_format(verste),
            )
    advarsler = []
    if len(verdier) < n:
        advarsler.append(
            f"Matrisen er {n}x{n} og har {n} egenverdier med multiplisitet, men svaret oppgir "
            f"{len(verdier)}. Kontrollen sier bare at de oppgitte er riktige."
        )
    return _resultat(
        VALIDERT,
        "Kontrollerte hver egenverdi med det(A − λI) = 0 numerisk: " + "; ".join(detaljer) + ".",
        metode="det(A − λI) = 0 numerisk",
        maks_avvik=_format(verste),
        advarsler=advarsler or None,
    )


def _valider_likhet(problem, losning, variabel, tol_rel, tol_abs, fro) -> dict:
    venstre = _uttrykk(problem)
    hoyre = _uttrykk(losning)
    frie = (venstre.free_symbols | hoyre.free_symbols)
    if not frie:
        if isinstance(venstre, sp.Rational) and isinstance(hoyre, sp.Rational):
            # To brøker/heltall er enten like eller ulike. Her hører toleranse
            # ikke hjemme: den ville godtatt 1082152110028958 som svar på
            # 87654321·12345679 = 1082152110028959.
            if venstre != hoyre:
                return _resultat(
                    FEILET,
                    f"Eksakt verdi er {venstre}, men svaret er {hoyre} "
                    f"(differanse {hoyre - venstre}).",
                    metode="eksakt sammenligning",
                    maks_avvik=str(abs(hoyre - venstre)),
                )
            return _resultat(
                VALIDERT,
                f"Regnet ut uttrykket eksakt: {venstre}, som er nøyaktig det svaret oppgir.",
                metode="eksakt sammenligning",
                maks_avvik="0",
            )
        tol_rel = _eksakt_toleranse(tol_rel, venstre, hoyre)
        a, b = _evaluer(venstre, {}), _evaluer(hoyre, {})
        avvik = abs(a - b)
        if not _godkjent(avvik, max(abs(a), abs(b)), tol_rel, tol_abs):
            return _resultat(
                FEILET,
                f"Uttrykket har verdien {_format(a)}, men svaret er {_format(b)} "
                f"(avvik {_format(avvik)}).",
                metode="numerisk sammenligning",
                maks_avvik=_format(avvik),
            )
        return _resultat(
            VALIDERT,
            f"Regnet ut begge uttrykkene numerisk med 30 siffer: {_format(a)} mot {_format(b)} "
            f"(avvik {_format(avvik)}).",
            metode="numerisk sammenligning",
            maks_avvik=_format(avvik),
        )
    x = _finn_variabel(variabel, venstre, hoyre)
    detaljer, verste, brukte = [], mp.mpf(0), []
    for punkt in _punkter(fro, antall=12):
        if len(brukte) >= ANTALL_PUNKTER:
            break
        verdier = _tilfeldige_verdier(frie - {x}, fro + str(punkt))
        verdier[x] = sp.Float(punkt, PRESISJON)
        try:
            a, b = _evaluer(venstre, verdier), _evaluer(hoyre, verdier)
        except ValideringsFeil:
            continue
        brukte.append(punkt)
        avvik = abs(a - b)
        verste = max(verste, avvik)
        detaljer.append(f"{x} = {punkt}: {_format(a)} mot {_format(b)}")
        if not _godkjent(avvik, max(abs(a), abs(b)), tol_rel, tol_abs):
            return _resultat(
                FEILET,
                f"Uttrykkene er ikke like i {x} = {punkt}: {_format(a)} mot {_format(b)}.",
                metode="numerisk sammenligning i tilfeldige punkter",
                punkter=brukte,
                maks_avvik=_format(verste),
            )
    if not brukte:
        raise ValideringsFeil("Fant ingen punkter der begge uttrykkene kunne evalueres.")
    return _resultat(
        VALIDERT,
        f"Sammenlignet uttrykkene numerisk i {len(brukte)} punkter: "
        + "; ".join(detaljer)
        + f". Største avvik {_format(verste)} (toleranse {tol_rel:g}).",
        metode="numerisk sammenligning i tilfeldige punkter",
        punkter=brukte,
        maks_avvik=_format(verste),
    )


# --- Sammenligning med de faktiske verktøyresultatene -------------------------


def samsvarer_med_verktoy(svar_sympy: str, verktoylogg: list[dict]) -> bool | None:
    """Stemmer modellens endelige svar med noe et verktøy faktisk regnet ut?

    Returnerer True/False, eller None hvis vi ikke kan avgjøre det.

    Kjøres i egen prosess med tidsgrense, av samme grunn som validate():
    sp.simplify har ingen øvre kjøretid. Med 16 nivåers nesting (sin(sin(…)))
    kom den aldri tilbake, og siden dette kallet lå rett i web-tråden, låste
    det hele forespørselen. Klarer vi ikke å avgjøre det i tide, svarer vi
    None – «vet ikke» – og appen sier ingenting om samsvar.
    """
    if not svar_sympy or not verktoylogg:
        return None
    argumenter = {
        "oppgave": "samsvar",
        "svar_sympy": svar_sympy,
        "verktoylogg": verktoylogg,
    }
    try:
        ferdig = subprocess.run(
            [sys.executable, "-m", "backend.validator"],
            input=json.dumps(argumenter, ensure_ascii=False, default=str),
            capture_output=True,
            text=True,
            timeout=TIDSGRENSE_SEKUNDER,
            cwd=str(_PROSJEKTROT),
            env={**os.environ, "PYTHONPATH": str(_PROSJEKTROT)},
            check=False,
        )
    except Exception:
        return None
    if ferdig.returncode != 0:
        return None
    try:
        svar = json.loads(ferdig.stdout)
    except json.JSONDecodeError:
        return None
    return svar if isinstance(svar, bool) else None


def _samsvarer_med_verktoy(svar_sympy: str, verktoylogg: list[dict]) -> bool | None:
    """Selve sammenligningen. Kjøres i underprosessen, se samsvarer_med_verktoy."""
    if not svar_sympy or not verktoylogg:
        return None
    try:
        svar = tolk_uttrykk(svar_sympy, tillat_ukjente_funksjoner=True)
    except TolkningsFeil:
        return None
    if isinstance(svar, sp.Equality):
        svar = svar.rhs
    kandidater = []
    for kall in verktoylogg:
        resultat = (kall.get("resultat") or {}).get("resultat")
        if not isinstance(resultat, str):
            continue
        for tekst in (resultat, re.sub(r"\+\s*C\b", "", resultat).strip()):
            try:
                objekt = tolk_uttrykk(tekst, tillat_ukjente_funksjoner=True)
            except TolkningsFeil:
                continue
            if isinstance(objekt, sp.Equality):
                objekt = objekt.rhs
            kandidater.append(objekt)
    if not kandidater:
        return None
    for kandidat in kandidater:
        try:
            if _likt_nok(svar, kandidat):
                return True
        except Exception:
            continue
    return False


def _for_tungt_for_simplify(*uttrykk) -> bool:
    """sp.simplify vokser eksponentielt med nestingsdybden.

    Målt på sin(sin(…sin(x)…)) mot cos(…): 10 nivåer tok 1,6 s, 12 tok 5,4 s,
    14 tok 21 s, og 16 kom aldri tilbake. Over disse grensene hopper vi rett
    til den numeriske sammenligningen nedenfor, som alltid er rask.
    """
    for u in uttrykk:
        try:
            if sp.count_ops(u) > 200 or _dybde(u) > 10:
                return True
        except Exception:
            return True
    return False


def _dybde(uttrykk, nivaa: int = 0) -> int:
    if nivaa > 12 or not getattr(uttrykk, "args", None):
        return nivaa
    return max(_dybde(arg, nivaa + 1) for arg in uttrykk.args)


def _likt_nok(a, b) -> bool:
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        return str(a) == str(b)
    if a == b:
        return True
    try:
        if _for_tungt_for_simplify(a, b):
            raise ValideringsFeil("for tungt for simplify")
        differanse = sp.simplify(a - b)
        if differanse == 0:
            return True
    except Exception:
        pass
    frie = sorted((getattr(a, "free_symbols", set()) | getattr(b, "free_symbols", set())), key=str)
    if len(frie) > 3:
        return False
    for indeks in range(3):
        verdier = {
            symbol: sp.Float(0.7 + 0.31 * (indeks + 1) + 0.13 * plass, PRESISJON)
            for plass, symbol in enumerate(frie)
        }
        try:
            va, vb = _evaluer(a, verdier), _evaluer(b, verdier)
        except ValideringsFeil:
            return False
        if abs(va - vb) > 1e-8 * max(1, abs(va), abs(vb)):
            return False
    return True


# --- Offentlig API (med tidsgrense) -------------------------------------------


def validate(problem: str, losning: str, oppgavetype=None, variabel=None, betingelser=None) -> dict:
    """Kontrollerer et svar numerisk. Returnerer {"validert": bool, "detaljer": str, ...}.

    Kjøres i en egen prosess med tidsgrense, slik at en tung SymPy-beregning
    ikke kan henge appen. Feiler prosessen, valideres det direkte i stedet.
    """
    argumenter = {
        "problem": problem,
        "losning": losning,
        "oppgavetype": oppgavetype,
        "variabel": variabel,
        "betingelser": betingelser,
    }
    try:
        ferdig = subprocess.run(
            [sys.executable, "-m", "backend.validator"],
            input=json.dumps(argumenter, ensure_ascii=False, default=str),
            capture_output=True,
            text=True,
            timeout=TIDSGRENSE_SEKUNDER,
            cwd=str(_PROSJEKTROT),
            env={**os.environ, "PYTHONPATH": str(_PROSJEKTROT)},
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _resultat(
            IKKE_MULIG,
            f"Valideringen brukte mer enn {TIDSGRENSE_SEKUNDER} sekunder og ble avbrutt. "
            "Svaret er derfor ikke kontrollert.",
        )
    except Exception:
        return _valider(**argumenter)
    if ferdig.returncode != 0:
        return _resultat(
            IKKE_MULIG,
            "Valideringsprosessen stoppet uventet: " + (ferdig.stderr or "").strip()[-200:],
        )
    try:
        return json.loads(ferdig.stdout)
    except json.JSONDecodeError:
        return _resultat(IKKE_MULIG, "Uforståelig svar fra valideringsprosessen.")


def _hovedprogram() -> None:
    """Kjøres som «python -m backend.validator» av validate()."""
    try:
        import resource

        tak = 2048 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (tak, tak))
    except Exception:
        pass
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as e:
        svar = _resultat(IKKE_MULIG, f"Ugyldig JSON til valideringsprosessen: {e}")
    else:
        if data.get("oppgave") == "samsvar":
            sys.stdout.write(
                json.dumps(
                    _samsvarer_med_verktoy(
                        data.get("svar_sympy") or "", data.get("verktoylogg") or []
                    )
                )
            )
            return
        svar = _valider(
            data.get("problem") or "",
            data.get("losning") or "",
            data.get("oppgavetype"),
            data.get("variabel"),
            data.get("betingelser"),
        )
    sys.stdout.write(json.dumps(svar, ensure_ascii=False, default=str))


if __name__ == "__main__":
    _hovedprogram()
