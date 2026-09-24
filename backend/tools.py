"""Deterministiske matteverktøy (SymPy) for MatteHjelpen.

PRINSIPP: Modellen resonnerer – verktøyet regner. En språkmodell skal ALDRI
gjøre symbolsk/numerisk regning selv.

Hver funksjon returnerer ``{"resultat": str, "latex": str, ...}``.
``TOOL_DEFINITIONS`` er JSON-schema (OpenAI function calling) som
``llm_client.py`` sender til modellen.

VÅRE VALG (jf. [FYLL INN SELV] i PROMPTS/01_tools.md):

1. *Feil kastes som unntak* (``VerktoyFeil``/``TolkningsFeil``) inne i
   funksjonene, og oversettes til ``{"feil": "..."}`` av ``kjor_verktoy``.
   Da ser enhetstestene den ekte feilen, samtidig som modellen får en
   forklarende melding tilbake i samtalen og kan rette opp inputen selv.
2. *Feilsituasjoner vi håndterer eksplisitt:* ugyldig syntaks, ukjent
   funksjon, tvetydig notasjon (``sin^-1``), ugyldig variabelnavn, singulær
   matrise, matriser med ulik radlengde eller feil dimensjon, deling på null
   (``zoo``), integraler/ODE-er SymPy ikke får løst, tomme løsningsmengder og
   beregninger som tar for lang tid.
3. *Tidsgrense:* hvert verktøykall kjøres i en egen prosess med tidsgrense
   (``VERKTOY_TIDSGRENSE``, standard 25 s). Uten dette kan ett vanskelig
   integral henge hele appen. Prosessen drepes når tiden er ute.
4. *Parsing:* streng SymPy-syntaks, se ``backend/parsing.py``. Verktøyet
   gjetter aldri på tvetydig notasjon – det sier fra.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import sympy as sp

from backend.parsing import (
    TolkningsFeil,
    del_pa_toppniva,
    krev_uttrykk,
    tolk_betingelser,
    tolk_likninger,
    tolk_matrise,
    tolk_ode,
    tolk_symbol,
    tolk_symboler,
    tolk_uttrykk,
    tolk_vektor,
)


class VerktoyFeil(ValueError):
    """Verktøyet kunne ikke fullføre. Meldingen er skrevet for modellen/brukeren."""


TIDSGRENSE_SEKUNDER = int(os.getenv("VERKTOY_TIDSGRENSE", "25"))
MINNEGRENSE_BYTE = int(os.getenv("VERKTOY_MINNEGRENSE_MB", "2048")) * 1024 * 1024
_MAKS_OPERASJONER_FOR_FORENKLING = 400
_PROSJEKTROT = Path(__file__).resolve().parent.parent


# --- Hjelpefunksjoner ---------------------------------------------------------


def _tekst(objekt) -> str:
    if isinstance(objekt, sp.MatrixBase):
        return str(objekt.tolist())
    if isinstance(objekt, (list, tuple)):
        return "[" + ", ".join(_tekst(e) for e in objekt) + "]"
    if isinstance(objekt, dict):
        return "{" + ", ".join(f"{_tekst(k)}: {_tekst(v)}" for k, v in objekt.items()) + "}"
    return str(objekt)


def _latex(objekt) -> str:
    if isinstance(objekt, (list, tuple)):
        return ",\\ ".join(sp.latex(e) for e in objekt)
    return sp.latex(objekt)


def _svar(resultat, latex: str | None = None, **ekstra) -> dict:
    svar = {
        "resultat": _tekst(resultat),
        "latex": latex if latex is not None else _latex(resultat),
    }
    for nokkel, verdi in ekstra.items():
        if verdi is not None:
            svar[nokkel] = verdi
    return svar


def _forenkle(uttrykk):
    """Forenkler hvis uttrykket er lite nok, og bare hvis det faktisk blir enklere."""
    try:
        if sp.count_ops(uttrykk) > _MAKS_OPERASJONER_FOR_FORENKLING:
            return uttrykk
        forenklet = sp.simplify(uttrykk)
        return forenklet if sp.count_ops(forenklet) <= sp.count_ops(uttrykk) else uttrykk
    except Exception:
        return uttrykk


def _velg_pen_form(uttrykk):
    """Velger den mest leservennlige av rå, utvidet og forenklet form.

    Den rå formen (slik regelen gir den, f.eks. u'v + uv' fra produktregelen)
    beholdes så lenge den ikke er vesentlig mer komplisert enn alternativene –
    da kjenner studenten igjen regelen som ble brukt.
    """
    kandidater = [uttrykk]
    try:
        if sp.count_ops(uttrykk) <= _MAKS_OPERASJONER_FOR_FORENKLING:
            kandidater.append(sp.expand(uttrykk))
    except Exception:
        pass
    kandidater.append(_forenkle(uttrykk))
    try:
        minste = min(sp.count_ops(k) for k in kandidater)
        for kandidat in kandidater:
            if sp.count_ops(kandidat) <= minste * 1.25:
                return kandidat
    except Exception:
        pass
    return uttrykk


def _sjekk_endelig(uttrykk, hva: str):
    """Fanger opp deling på null (zoo/nan) før vi presenterer noe som et «svar»."""
    if uttrykk.has(sp.zoo) or uttrykk.has(sp.nan):
        raise VerktoyFeil(
            f"{hva} ga et udefinert resultat (deling på null eller lignende): {uttrykk}"
        )
    return uttrykk


def _tall(tekst) -> sp.Expr:
    return krev_uttrykk(tolk_uttrykk(str(tekst)), str(tekst))


def _numerisk(uttrykk, siffer: int = 12) -> str | None:
    """Desimaltilnærming, hvis uttrykket er et tall."""
    try:
        if not isinstance(uttrykk, sp.Expr) or uttrykk.free_symbols:
            return None
        verdi = sp.N(uttrykk, siffer)
        if verdi.has(sp.zoo) or verdi.has(sp.nan) or verdi.has(sp.oo):
            return None
        return str(verdi)
    except Exception:
        return None


def _normaliser_operasjon(operasjon) -> str:
    return str(operasjon).strip().lower().replace(" ", "_").replace("-", "_")


# --- Verktøyene ---------------------------------------------------------------


def derive(uttrykk: str, variabel: str = "x") -> dict:
    """Deriverer et uttrykk med hensyn på én variabel (SymPy diff).

    derive("x**2*sin(3*x)", "x") -> 2*x*sin(3*x) + 3*x**2*cos(3*x)
    """
    f = krev_uttrykk(tolk_uttrykk(uttrykk), uttrykk)
    x = tolk_symbol(variabel)
    merknad = None
    if f.free_symbols and x not in f.free_symbols:
        merknad = f"Uttrykket inneholder ikke {x}, så den deriverte blir 0."
    derivert = _velg_pen_form(sp.diff(f, x))
    return _svar(
        derivert,
        oppgave=f"d/d{x} ({f})",
        oppgave_latex=sp.latex(sp.Derivative(f, x)),
        merknad=merknad,
    )


def integrate(uttrykk: str, variabel: str = "x") -> dict:
    """Finner et ubestemt integral (antiderivert). Husk konstanten + C.

    integrate("x*exp(2*x)", "x") -> (x/2 - 1/4)*exp(2*x) + C
    """
    f = krev_uttrykk(tolk_uttrykk(uttrykk), uttrykk)
    x = tolk_symbol(variabel)
    antiderivert = sp.integrate(f, x)
    merknad = None
    if antiderivert.has(sp.Integral):
        merknad = (
            "SymPy fant ingen antiderivert på lukket form. Si ærlig fra til brukeren "
            "at integralet ikke lot seg løse symbolsk."
        )
    else:
        antiderivert = _forenkle(antiderivert)
    return _svar(
        f"{antiderivert} + C",
        latex=sp.latex(antiderivert) + " + C",
        oppgave=f"Integral({f}, {x})",
        oppgave_latex=sp.latex(sp.Integral(f, x)),
        uten_konstant=str(antiderivert),
        merknad=merknad,
    )


def definite_integral(uttrykk: str, variabel: str, nedre: str, ovre: str) -> dict:
    """Beregner et bestemt integral fra nedre til øvre grense (oo er uendelig).

    definite_integral("x**3/(x**2 + 1)", "x", "0", "1") -> 1/2 - log(2)/2
    """
    f = krev_uttrykk(tolk_uttrykk(uttrykk), uttrykk)
    x = tolk_symbol(variabel)
    a, b = _tall(nedre), _tall(ovre)
    verdi = sp.integrate(f, (x, a, b))
    merknad = None
    if verdi.has(sp.Integral):
        merknad = "SymPy klarte ikke å regne integralet eksakt; oppgir bare en numerisk verdi."
        try:
            verdi = sp.Integral(f, (x, a, b)).evalf(15)
        except Exception as e:
            raise VerktoyFeil(
                f"Klarte verken å regne integralet eksakt eller numerisk: {e}"
            ) from None
    else:
        verdi = _forenkle(verdi)
    return _svar(
        verdi,
        oppgave=f"Integral({f}, ({x}, {a}, {b}))",
        oppgave_latex=sp.latex(sp.Integral(f, (x, a, b))),
        desimal=_numerisk(verdi),
        merknad=merknad,
    )


def solve_equation(ligning: str, variabel: str = "x") -> dict:
    """Løser en ligning eller et ligningssystem eksakt.

    solve_equation("x**2 - 2*x - 8 = 0", "x") -> [-2, 4]
    solve_equation("x + y = 3; x - y = 1", "x, y") -> [{x: 2, y: 1}]
    """
    likninger = tolk_likninger(ligning)
    variabler = tolk_symboler(variabel)
    if len(likninger) == 1 and len(variabler) == 1:
        return _los_en_likning(likninger[0], variabler[0])
    return _los_system(likninger, variabler)


def _los_en_likning(likning: sp.Eq, x: sp.Symbol) -> dict:
    losninger = sp.solve(likning, x, dict=False)
    if not isinstance(losninger, list):
        losninger = [losninger]
    losninger = [_forenkle(losning) for losning in losninger]
    ekstra: dict = {}
    if not losninger:
        ekstra["merknad"] = "SymPy fant ingen løsninger. Si ærlig fra om det til brukeren."
    try:
        mengde = sp.solveset(likning, x, domain=sp.S.Complexes)
        if not isinstance(mengde, sp.FiniteSet) and mengde is not sp.S.EmptySet:
            ekstra["full_losningsmengde"] = str(mengde)
            ekstra["merknad"] = (
                "Ligningen har uendelig mange løsninger (se full_losningsmengde); "
                "listen over er bare noen av dem."
            )
    except Exception:
        pass
    desimaler = [d for d in (_numerisk(losning) for losning in losninger) if d]
    if desimaler:
        ekstra["desimaler"] = desimaler
    return _svar(losninger, oppgave=str(likning), oppgave_latex=sp.latex(likning), **ekstra)


def _los_system(likninger: list[sp.Eq], variabler: list[sp.Symbol]) -> dict:
    losninger = sp.solve(likninger, variabler, dict=True)
    oppgave = "; ".join(str(likning) for likning in likninger)
    if not losninger:
        return _svar(
            [],
            latex=r"\text{ingen løsning}",
            oppgave=oppgave,
            merknad="Systemet har ingen løsning (eller SymPy fant ingen). Si ærlig fra.",
        )
    losninger = [{k: _forenkle(v) for k, v in losning.items()} for losning in losninger]
    latex = r"\quad ".join(
        ",\\ ".join(f"{sp.latex(k)} = {sp.latex(v)}" for k, v in losning.items())
        for losning in losninger
    )
    frie = sorted({str(s) for losning in losninger for v in losning.values() for s in v.free_symbols})
    merknad = (
        f"Systemet er underbestemt: løsningen avhenger av {', '.join(frie)}." if frie else None
    )
    return _svar(losninger, latex=latex, oppgave=oppgave, merknad=merknad)


def solve_ode(ligning: str) -> dict:
    """Løser en differensialligning (generell løsning, med konstanter C1, C2 ...).

    solve_ode("Eq(y(x).diff(x, 2) + 2*y(x), 0)")
    -> Eq(y(x), C1*sin(sqrt(2)*x) + C2*cos(sqrt(2)*x))
    """
    return _los_ode(ligning, None)


def solve_ode_ivp(ligning: str, betingelser: str) -> dict:
    """Løser et initialverdiproblem: differensialligning + startbetingelser.

    solve_ode_ivp("y'' - 3*y' + 2*y = 0", "y(0)=1, y'(0)=0")
    -> Eq(y(x), 2*exp(x) - exp(2*x))
    """
    if not betingelser or not str(betingelser).strip():
        raise VerktoyFeil(
            "Ingen startbetingelser oppgitt. Bruk solve_ode for generell løsning, "
            "eller skriv betingelser som «y(0)=1, y'(0)=0»."
        )
    return _los_ode(ligning, betingelser)


def _los_ode(ligning: str, betingelser) -> dict:
    likning, y, x = tolk_ode(ligning)
    orden = sp.ode_order(likning, y.func)
    ekstra: dict = {"orden": orden}
    try:
        klasser = sp.classify_ode(likning, y)
        if klasser:
            ekstra["metode"] = klasser[0]
    except Exception:
        pass
    karakteristisk = _karakteristisk_ligning(likning, y, x, orden)
    if karakteristisk:
        ekstra.update(karakteristisk)

    ics = None
    if betingelser:
        ics = {}
        for orden_b, punkt, verdi in tolk_betingelser(betingelser, y, x):
            if orden_b > orden - 1:
                raise VerktoyFeil(
                    f"Betingelsen bruker den {orden_b}. deriverte, men ligningen har orden {orden}."
                )
            if orden_b == 0:
                ics[y.func(punkt)] = verdi
            else:
                ics[sp.Subs(sp.Derivative(y, (x, orden_b)), x, punkt)] = verdi
    try:
        losning = sp.dsolve(likning, y, ics=ics) if ics else sp.dsolve(likning, y)
    except NotImplementedError:
        raise VerktoyFeil(
            "SymPy klarte ikke å løse denne differensialligningen. Si ærlig fra at "
            "løsningen ikke kunne beregnes med verktøy."
        ) from None
    except ValueError as e:
        raise VerktoyFeil(f"Kunne ikke løse differensialligningen: {e}") from None
    if isinstance(losning, list):
        resultat = [
            sp.Eq(del_.lhs, _forenkle(del_.rhs)) if isinstance(del_, sp.Eq) else del_
            for del_ in losning
        ]
    elif isinstance(losning, sp.Eq):
        resultat = sp.Eq(losning.lhs, _forenkle(losning.rhs))
    else:
        resultat = losning
    return _svar(resultat, oppgave=str(likning), oppgave_latex=sp.latex(likning), **ekstra)


def _karakteristisk_ligning(likning: sp.Eq, y: sp.Expr, x: sp.Symbol, orden: int) -> dict | None:
    """Karakteristisk ligning og røtter for lineære ODE-er med konstante koeffisienter.

    Dette er informasjonen modellen trenger for å FORKLARE metoden (formel O1),
    ikke bare presentere et svar.
    """
    if orden < 1 or orden > 4:
        return None
    try:
        uttrykk = sp.expand(likning.lhs - likning.rhs)
        r = sp.Symbol("r")
        polynom = sp.Integer(0)
        rest = uttrykk
        for k in range(orden, -1, -1):
            ledd = sp.Derivative(y, (x, k)) if k else y
            koeffisient = rest.coeff(ledd)
            if koeffisient.free_symbols or koeffisient.has(y):
                return None
            polynom += koeffisient * r**k
            rest = sp.expand(rest - koeffisient * ledd)
        if rest != 0 or polynom == 0:
            return None  # ikke homogen med konstante koeffisienter
        rotter = sp.roots(sp.Poly(polynom, r))
    except Exception:
        return None
    if not rotter:
        return None
    return {
        "karakteristisk_ligning": f"{polynom} = 0",
        "karakteristisk_latex": sp.latex(sp.Eq(polynom, 0)),
        "rotter": [
            f"r = {rot}" + (f" (multiplisitet {mult})" if mult > 1 else "")
            for rot, mult in rotter.items()
        ],
    }


_MATRISE_OPERASJONER = {
    "determinant": "determinant",
    "det": "determinant",
    "invers": "invers",
    "inverse": "invers",
    "inv": "invers",
    "egenverdier": "egenverdier",
    "eigenvalues": "egenverdier",
    "eigenvals": "egenverdier",
    "egenvektorer": "egenvektorer",
    "eigenvectors": "egenvektorer",
    "eigenvects": "egenvektorer",
    "solve_ax_b": "solve_ax_b",
    "los_ax_b": "solve_ax_b",
    "løs_ax_b": "solve_ax_b",
    "ax_b": "solve_ax_b",
    "rang": "rang",
    "rank": "rang",
    "rref": "rref",
    "transponer": "transponer",
    "transpose": "transponer",
}


def matrix_op(operasjon: str, matrise: list) -> dict:
    """Matriseoperasjoner: determinant, invers, egenverdier, egenvektorer,
    solve_ax_b (løser A*x = b), rang, rref og transponer.

    matrix_op("determinant", "[[1, 2], [3, 4]]") -> -2
    matrix_op("solve_ax_b", "[[[2, 1], [1, 3]], [5, 10]]") -> [1, 3]
    """
    navn = _MATRISE_OPERASJONER.get(_normaliser_operasjon(operasjon))
    if navn is None:
        raise VerktoyFeil(
            f"Ukjent matriseoperasjon «{operasjon}». Gyldige: "
            + ", ".join(sorted(set(_MATRISE_OPERASJONER.values())))
        )
    if navn == "solve_ax_b":
        return _los_ax_b(matrise)

    A = tolk_matrise(matrise)
    if navn in {"determinant", "invers", "egenverdier", "egenvektorer"} and A.rows != A.cols:
        raise VerktoyFeil(
            f"Operasjonen «{navn}» krever en kvadratisk matrise, men matrisen er {A.rows}x{A.cols}."
        )
    matrise_latex = sp.latex(A)
    if navn == "determinant":
        verdi = _forenkle(A.det())
        return _svar(
            verdi,
            oppgave=f"det({A.tolist()})",
            oppgave_latex=f"\\det {matrise_latex}",
            desimal=_numerisk(verdi),
        )
    if navn == "invers":
        if A.det() == 0:
            raise VerktoyFeil("Matrisen er singulær (determinanten er 0) og har ingen invers.")
        return _svar(
            A.inv().applyfunc(_forenkle),
            oppgave=f"invers({A.tolist()})",
            oppgave_latex=f"{matrise_latex}^{{-1}}",
        )
    if navn == "egenverdier":
        elementer = [(_forenkle(verdi), mult) for verdi, mult in A.eigenvals().items()]
        tekst = ", ".join(
            f"{verdi}" + (f" (multiplisitet {mult})" if mult > 1 else "")
            for verdi, mult in elementer
        )
        return _svar(
            tekst,
            latex=",\\ ".join(f"\\lambda = {sp.latex(verdi)}" for verdi, _ in elementer),
            oppgave=f"egenverdier({A.tolist()})",
            verdier=[str(verdi) for verdi, mult in elementer for _ in range(mult)],
            karakteristisk_ligning=f"{sp.factor(A.charpoly(sp.Symbol('lam')).as_expr())} = 0 (lam = lambda)",
            desimaler=[d for d in (_numerisk(verdi) for verdi, _ in elementer) if d],
        )
    if navn == "egenvektorer":
        par = A.eigenvects()
        deler = [
            f"lambda = {verdi} (multiplisitet {mult}): "
            + ", ".join(str(vektor.T.tolist()[0]) for vektor in vektorer)
            for verdi, mult, vektorer in par
        ]
        return _svar(
            "; ".join(deler),
            latex=",\\ ".join(
                f"\\lambda = {sp.latex(verdi)}:\\ "
                + ",\\ ".join(sp.latex(vektor.T) for vektor in vektorer)
                for verdi, _, vektorer in par
            ),
            oppgave=f"egenvektorer({A.tolist()})",
        )
    if navn == "rang":
        return _svar(
            A.rank(),
            oppgave=f"rang({A.tolist()})",
            oppgave_latex=f"\\operatorname{{rang}}{matrise_latex}",
        )
    if navn == "rref":
        redusert, pivoter = A.rref()
        return _svar(redusert, oppgave=f"rref({A.tolist()})", pivotkolonner=list(pivoter))
    return _svar(A.T, oppgave=f"transponer({A.tolist()})", oppgave_latex=f"{matrise_latex}^{{T}}")


def _los_ax_b(matrise) -> dict:
    A, b = _del_ax_b(matrise)
    if A.rows != b.rows:
        raise VerktoyFeil(
            f"A har {A.rows} rader, men b har {b.rows} elementer – de må være like mange."
        )
    try:
        losning, parametre = A.gauss_jordan_solve(b)
    except ValueError as e:
        raise VerktoyFeil(
            f"Systemet A*x = b har ingen løsning ({e}). Si ærlig fra til brukeren."
        ) from None
    losning = losning.applyfunc(_forenkle)
    merknad = None
    if parametre:
        merknad = (
            "Systemet har uendelig mange løsninger; svaret inneholder de frie parameterne "
            + ", ".join(str(parameter) for parameter in parametre)
            + "."
        )
    return _svar(
        losning,
        latex="x = " + sp.latex(losning),
        oppgave=f"A = {A.tolist()}, b = {b.T.tolist()[0]}",
        oppgave_latex=f"{sp.latex(A)}x = {sp.latex(b)}",
        merknad=merknad,
    )


def _del_ax_b(matrise) -> tuple[sp.Matrix, sp.Matrix]:
    """Godtar både [A, b] og totalmatrisen [A|b]."""
    objekt = tolk_uttrykk(matrise) if isinstance(matrise, str) else matrise
    if (
        isinstance(objekt, (list, tuple))
        and len(objekt) == 2
        and isinstance(objekt[0], (list, tuple))
        and objekt[0]
        and isinstance(objekt[0][0], (list, tuple))
    ):
        return tolk_matrise(objekt[0]), tolk_vektor(objekt[1])
    total = tolk_matrise(objekt)
    if total.cols < 2:
        raise VerktoyFeil(
            "Oppgi enten [A, b] (f.eks. [[[2, 1], [1, 3]], [5, 10]]) eller totalmatrisen [A|b]."
        )
    return total[:, :-1], total[:, -1]


_KOMPLEKSE_OPERASJONER = {
    "polar": "polar",
    "polarform": "polar",
    "modulus": "polar",
    "absoluttverdi": "polar",
    "argument": "polar",
    "rektangulaer": "rektangulaer",
    "rektangulær": "rektangulaer",
    "kartesisk": "rektangulaer",
    "standardform": "rektangulaer",
    "a+bi": "rektangulaer",
    "potens": "potens",
    "power": "potens",
    "de_moivre": "potens",
    "rotter": "rotter",
    "røtter": "rotter",
    "roots": "rotter",
    "nte_rotter": "rotter",
    "euler": "euler",
    "konjugat": "konjugat",
    "conjugate": "konjugat",
}


def complex_op(operasjon: str, tall: str) -> dict:
    """Komplekse tall: polar (polarform), rektangulaer (a+bi), potens (De Moivre),
    rotter (n-te røtter), euler og konjugat.

    complex_op("polar", "1+I") -> r = sqrt(2), theta = pi/4
    complex_op("potens", "1+sqrt(3)*I, 7") -> 64 + 64*sqrt(3)*I
    complex_op("rotter", "-8, 3") -> [1 + sqrt(3)*I, -2, 1 - sqrt(3)*I]
    """
    navn = _KOMPLEKSE_OPERASJONER.get(_normaliser_operasjon(operasjon))
    if navn is None:
        raise VerktoyFeil(
            f"Ukjent operasjon «{operasjon}». Gyldige: "
            + ", ".join(sorted(set(_KOMPLEKSE_OPERASJONER.values())))
        )
    z, n = _del_tall_og_n(tall, del_potens=navn in {"potens", "rotter"})
    if navn in {"potens", "rotter"} and n is None:
        raise VerktoyFeil(
            f"Operasjonen «{navn}» trenger et tall til: skriv «{tall}, n», f.eks. «1+I, 8»."
        )
    if navn == "potens":
        return _kompleks_potens(z, n)
    if navn == "rotter":
        return _kompleks_rotter(z, n)
    if navn == "konjugat":
        return _svar(
            _forenkle(sp.conjugate(z)),
            oppgave=f"konjugat({z})",
            oppgave_latex=f"\\overline{{{sp.latex(z)}}}",
        )
    if navn == "rektangulaer":
        return _rektangulaer(z)
    return _polar(z, euler=(navn == "euler"))


def _polar(z: sp.Expr, *, euler: bool = False) -> dict:
    if z == 0:
        raise VerktoyFeil("Tallet 0 har ingen entydig polarform (argumentet er udefinert).")
    r = _forenkle(sp.Abs(z))
    theta = _forenkle(sp.arg(z))
    eksponentialform = r * sp.exp(sp.I * theta)
    trigform = r * (sp.cos(theta) + sp.I * sp.sin(theta))
    return _svar(
        f"r = {r}, theta = {theta} (dvs. {r}*exp(I*{theta}))",
        latex=f"{sp.latex(z)} = {sp.latex(eksponentialform)} = {sp.latex(trigform)}",
        oppgave=f"polarform({z})",
        modulus=str(r),
        argument=str(theta),
        eksponentialform=str(eksponentialform),
        trigonometrisk_form=str(trigform),
        argument_grader=_numerisk(sp.deg(theta)),
        modulus_desimal=_numerisk(r),
        merknad=(
            "Eulers formel: r*exp(I*theta) = r*(cos(theta) + I*sin(theta))."
            if euler
            else "Argumentet er i radianer i hovedgrenen (-pi, pi]."
        ),
    )


def _rektangulaer(z: sp.Expr) -> dict:
    utvidet = sp.expand_complex(sp.simplify(z))
    a, b = _forenkle(sp.re(utvidet)), _forenkle(sp.im(utvidet))
    verdi = a + b * sp.I
    return _svar(
        verdi,
        oppgave=f"rektangulaer({z})",
        realdel=str(a),
        imaginaerdel=str(b),
        desimal=_numerisk(verdi),
    )


def _kompleks_potens(z: sp.Expr, n: sp.Expr) -> dict:
    r = _forenkle(sp.Abs(z))
    theta = _forenkle(sp.arg(z))
    verdi = _forenkle(sp.expand_complex(sp.simplify(z**n)))
    _sjekk_endelig(verdi, f"({z})**{n}")
    return _svar(
        verdi,
        oppgave=f"({z})**{n}",
        oppgave_latex=f"\\left({sp.latex(z)}\\right)^{{{sp.latex(n)}}}",
        de_moivre=f"r**n = {_forenkle(r**n)}, n*theta = {_forenkle(n * theta)}",
        modulus=str(r),
        argument=str(theta),
        desimal=_numerisk(verdi),
    )


MAKS_ROTTER = 60


def _kompleks_rotter(z: sp.Expr, n: sp.Expr) -> dict:
    if not (n.is_Integer and n > 0):
        raise VerktoyFeil(f"Antall røtter må være et positivt heltall, ikke «{n}».")
    if n > MAKS_ROTTER:
        # Hver rot koster en sp.simplify. n = 1000 brukte 31 s og spiste hele
        # tidsgrensen; et svar med tusen røtter er uansett ikke til å lese.
        raise VerktoyFeil(
            f"{n} røtter er for mange til å regnes ut og leses (grensen er {MAKS_ROTTER}). "
            f"Røttene er r^(1/n)·exp(I·(theta + 2πk)/n) for k = 0 … {n - 1}."
        )
    if z == 0:
        raise VerktoyFeil("Alle n-te røtter av 0 er 0.")
    r = _forenkle(sp.Abs(z))
    theta = _forenkle(sp.arg(z))
    antall = int(n)
    rotter = [
        _forenkle(
            sp.expand_complex(
                sp.simplify(r ** sp.Rational(1, antall) * sp.exp(sp.I * (theta + 2 * sp.pi * k) / antall))
            )
        )
        for k in range(antall)
    ]
    return _svar(
        rotter,
        oppgave=f"De {antall} {antall}-te røttene av {z}",
        modulus=str(_forenkle(r ** sp.Rational(1, antall))),
        argumenter=[str(_forenkle((theta + 2 * sp.pi * k) / antall)) for k in range(antall)],
        desimaler=[d for d in (_numerisk(rot) for rot in rotter) if d],
    )


def _del_tall_og_n(tall, *, del_potens: bool = False) -> tuple[sp.Expr, sp.Expr | None]:
    """«1+I, 8» -> (1+I, 8). For potens/rotter tolkes også «(1+I)**8» som (1+I, 8)."""
    tekst = str(tall)
    deler = del_pa_toppniva(tekst.strip().strip("[]"), ",")
    if len(deler) == 2:
        return _tall(deler[0]), _tall(deler[1])
    if len(deler) > 2:
        raise VerktoyFeil(f"Forstod ikke «{tekst}». Skriv ett tall, eventuelt «tall, n».")
    z = _tall(tekst)
    if del_potens and isinstance(z, sp.Pow) and z.exp.is_Integer:
        return z.base, z.exp
    return z, None


def limit(uttrykk: str, variabel: str, punkt: str, retning: str = "+-") -> dict:
    """Beregner en grenseverdi. Punkt kan være et tall, oo eller -oo.
    Retning: «+» (høyre), «-» (venstre) eller «+-» (tosidig).

    limit("sin(x)/x", "x", "0") -> 1
    """
    f = krev_uttrykk(tolk_uttrykk(uttrykk), uttrykk)
    x = tolk_symbol(variabel)
    a = _tall(punkt)
    if retning not in ("+", "-", "+-"):
        raise VerktoyFeil(f"Ugyldig retning «{retning}». Bruk «+», «-» eller «+-».")
    try:
        verdi = sp.limit(f, x, a, dir=retning)
    except ValueError as e:
        raise VerktoyFeil(
            f"Grenseverdien finnes ikke eller er ikke entydig: {e}. Si ærlig fra til brukeren."
        ) from None
    if verdi.has(sp.Limit):
        raise VerktoyFeil("SymPy klarte ikke å regne ut denne grenseverdien.")
    merknad = None
    if verdi.has(sp.zoo):
        merknad = (
            "zoo betyr «kompleks uendelig»: den tosidige grensen finnes ikke fordi "
            "venstre- og høyregrensen går hver sin vei. Sjekk ensidige grenser med "
            "retning «-» og «+», og si ærlig fra at grensen ikke eksisterer."
        )
    elif verdi.has(sp.oo):
        merknad = "Grensen er uendelig; funksjonen vokser over alle grenser her."
    return _svar(
        _forenkle(verdi),
        oppgave=f"Limit({f}, {x}, {a}, dir={retning})",
        oppgave_latex=sp.latex(sp.Limit(f, x, a, dir=retning)),
        desimal=_numerisk(verdi),
        merknad=merknad,
    )


def calculate(uttrykk: str) -> dict:
    """Regner ut og forenkler et uttrykk eksakt (aritmetikk, brøk, rot, forenkling).

    Bruk dette i stedet for å regne i hodet – også for enkel aritmetikk.
    calculate("417*383") -> 159711
    """
    objekt = tolk_uttrykk(uttrykk)
    if isinstance(objekt, list):
        raise VerktoyFeil("calculate tar ett uttrykk, ikke en liste.")
    if isinstance(objekt, sp.MatrixBase):
        verdi = objekt.applyfunc(_forenkle)
    else:
        verdi = objekt.doit() if hasattr(objekt, "doit") else objekt
        if isinstance(verdi, sp.Expr):
            _sjekk_endelig(verdi, f"«{uttrykk}»")
            verdi = _forenkle(verdi)
    return _svar(
        verdi,
        oppgave=str(objekt),
        oppgave_latex=sp.latex(objekt),
        desimal=_numerisk(verdi),
    )


# --- Tool-definisjoner (OpenAI function calling) ------------------------------

_SYNTAKS = (
    "SymPy-syntaks: * for gange (2*x, ikke 2x), ** for potens, exp/log/sqrt/asin osv., "
    "eksakte brøker (1/3), I for imaginær enhet, pi, oo for uendelig."
)


def _verktoy(navn: str, beskrivelse: str, egenskaper: dict, pakrevd: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": navn,
            "description": beskrivelse,
            "parameters": {"type": "object", "properties": egenskaper, "required": pakrevd},
        },
    }


TOOL_DEFINITIONS = [
    _verktoy(
        "derive",
        "Deriverer et uttrykk med SymPy. " + _SYNTAKS,
        {
            "uttrykk": {
                "type": "string",
                "description": "Uttrykket som skal deriveres, f.eks. x**2*sin(3*x)",
            },
            "variabel": {
                "type": "string",
                "description": "Variabelen det deriveres med hensyn på (standard x)",
            },
        },
        ["uttrykk"],
    ),
    _verktoy(
        "integrate",
        "Finner et ubestemt integral (antiderivert) med SymPy. " + _SYNTAKS,
        {
            "uttrykk": {"type": "string", "description": "Integranden, f.eks. x*exp(2*x)"},
            "variabel": {"type": "string", "description": "Integrasjonsvariabel (standard x)"},
        },
        ["uttrykk"],
    ),
    _verktoy(
        "definite_integral",
        "Beregner et bestemt integral mellom to grenser med SymPy.",
        {
            "uttrykk": {"type": "string", "description": "Integranden, f.eks. x**3/(x**2 + 1)"},
            "variabel": {"type": "string", "description": "Integrasjonsvariabel, f.eks. x"},
            "nedre": {"type": "string", "description": "Nedre grense, f.eks. 0, -oo eller pi/2"},
            "ovre": {"type": "string", "description": "Øvre grense, f.eks. 1 eller oo"},
        },
        ["uttrykk", "variabel", "nedre", "ovre"],
    ),
    _verktoy(
        "solve_equation",
        "Løser en ligning eller et ligningssystem eksakt med SymPy. Flere ligninger "
        "skilles med semikolon, flere variabler med komma.",
        {
            "ligning": {
                "type": "string",
                "description": "Ligning(er), f.eks. x**2 - 2*x - 8 = 0 eller x + y = 3; x - y = 1",
            },
            "variabel": {"type": "string", "description": "Variabel(er), f.eks. x eller x, y"},
        },
        ["ligning"],
    ),
    _verktoy(
        "solve_ode",
        "Finner den generelle løsningen av en differensialligning (konstanter C1, C2 ...).",
        {
            "ligning": {
                "type": "string",
                "description": "ODE, f.eks. Eq(y(x).diff(x, 2) + 2*y(x), 0) eller y'' + 2*y = 0",
            }
        },
        ["ligning"],
    ),
    _verktoy(
        "solve_ode_ivp",
        "Løser et initialverdiproblem: differensialligning med startbetingelser.",
        {
            "ligning": {"type": "string", "description": "ODE, f.eks. y'' - 3*y' + 2*y = 0"},
            "betingelser": {
                "type": "string",
                "description": "Startbetingelser, f.eks. y(0)=1, y'(0)=0",
            },
        },
        ["ligning", "betingelser"],
    ),
    _verktoy(
        "matrix_op",
        "Matriseoperasjoner med SymPy: determinant, invers, egenverdier, egenvektorer, "
        "solve_ax_b (løser A*x = b), rang, rref, transponer.",
        {
            "operasjon": {
                "type": "string",
                "enum": [
                    "determinant",
                    "invers",
                    "egenverdier",
                    "egenvektorer",
                    "solve_ax_b",
                    "rang",
                    "rref",
                    "transponer",
                ],
            },
            "matrise": {
                "type": "string",
                "description": "Matrisen som tekst, f.eks. [[1, 1/2], [1/2, 1/3]]. For solve_ax_b: "
                "[A, b], f.eks. [[[2, 1], [1, 3]], [5, 10]]. Bruk eksakte brøker, ikke desimaltall.",
            },
        },
        ["operasjon", "matrise"],
    ),
    _verktoy(
        "complex_op",
        "Komplekse tall med SymPy: polar (polarform), rektangulaer (a+bi), potens (De Moivre), "
        "rotter (n-te røtter), euler, konjugat.",
        {
            "operasjon": {
                "type": "string",
                "enum": ["polar", "rektangulaer", "potens", "rotter", "euler", "konjugat"],
            },
            "tall": {
                "type": "string",
                "description": "Det komplekse tallet, f.eks. 1+I. For potens og rotter: «tall, n», "
                "f.eks. «1+sqrt(3)*I, 7» eller «-8, 3».",
            },
        },
        ["operasjon", "tall"],
    ),
    _verktoy(
        "limit",
        "Beregner en grenseverdi med SymPy.",
        {
            "uttrykk": {"type": "string", "description": "Uttrykket, f.eks. sin(x)/x"},
            "variabel": {"type": "string", "description": "Variabelen, f.eks. x"},
            "punkt": {"type": "string", "description": "Punktet, f.eks. 0, oo eller -oo"},
            "retning": {
                "type": "string",
                "enum": ["+", "-", "+-"],
                "description": "Standard +- (tosidig)",
            },
        },
        ["uttrykk", "variabel", "punkt"],
    ),
    _verktoy(
        "calculate",
        "Regner ut og forenkler et uttrykk eksakt med SymPy. Bruk dette til all aritmetikk "
        "og forenkling – ikke regn i hodet.",
        {
            "uttrykk": {
                "type": "string",
                "description": "Uttrykket, f.eks. 417*383, sqrt(8)/2 eller (1+I)**2",
            }
        },
        ["uttrykk"],
    ),
]

VERKTOY = {
    "derive": derive,
    "integrate": integrate,
    "definite_integral": definite_integral,
    "solve_equation": solve_equation,
    "solve_ode": solve_ode,
    "solve_ode_ivp": solve_ode_ivp,
    "matrix_op": matrix_op,
    "complex_op": complex_op,
    "limit": limit,
    "calculate": calculate,
}


def kjor_verktoy(navn: str, argumenter: dict) -> dict:
    """Kjører ett verktøy og oversetter alle feil til {"feil": "..."}.

    Modellen får feilmeldingen tilbake i samtalen og kan rette opp inputen selv.
    """
    funksjon = VERKTOY.get(navn)
    if funksjon is None:
        return {"feil": f"Ukjent verktøy «{navn}». Tilgjengelige: {', '.join(VERKTOY)}."}
    if not isinstance(argumenter, dict):
        return {"feil": "Argumentene må være et JSON-objekt."}
    gyldige = funksjon.__code__.co_varnames[: funksjon.__code__.co_argcount]
    ukjente = set(argumenter) - set(gyldige)
    if ukjente:
        return {
            "feil": f"Ukjente argumenter til {navn}: {', '.join(sorted(ukjente))}. "
            f"Gyldige: {', '.join(gyldige)}."
        }
    try:
        return funksjon(**argumenter)
    except (VerktoyFeil, TolkningsFeil) as e:
        return {"feil": str(e)}
    except TypeError as e:
        return {"feil": f"Feil bruk av {navn}: {e}"}
    except RecursionError:
        return {"feil": f"{navn} ga et uttrykk som ble for komplisert å regne ut."}
    except Exception as e:  # SymPy kaster mange ulike feiltyper
        return {"feil": f"SymPy klarte ikke å fullføre: {type(e).__name__}: {e}"}


def kjor_verktoy_med_tidsgrense(navn: str, argumenter: dict, sekunder: int | None = None) -> dict:
    """Som kjor_verktoy, men i en egen prosess som drepes ved tidsavbrudd.

    Noen SymPy-kall (vanskelige integraler, dsolve) kan bruke svært lang tid
    eller mye minne. Uten dette ville hele appen henge på én oppgave. Vi
    starter derfor «python -m backend.tools» som leser argumentene på stdin
    og svarer med JSON på stdout, og dreper prosessen når tiden er ute.
    """
    sekunder = TIDSGRENSE_SEKUNDER if sekunder is None else sekunder
    melding = json.dumps({"navn": navn, "argumenter": argumenter}, ensure_ascii=False, default=str)
    try:
        ferdig = subprocess.run(
            [sys.executable, "-m", "backend.tools"],
            input=melding,
            capture_output=True,
            text=True,
            timeout=sekunder,
            cwd=str(_PROSJEKTROT),
            env={**os.environ, "PYTHONPATH": str(_PROSJEKTROT)},
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "feil": f"Beregningen brukte mer enn {sekunder} sekunder og ble avbrutt. "
            "Prøv en enklere formulering, eller si ærlig fra at den ikke lot seg beregne."
        }
    except Exception as e:
        # Klarte ikke å starte prosessen: kjør direkte, men si fra i svaret.
        resultat = kjor_verktoy(navn, argumenter)
        resultat.setdefault("merknad", f"Kjørte uten tidsgrense ({type(e).__name__}: {e}).")
        return resultat
    if ferdig.returncode != 0:
        slutt = (ferdig.stderr or "").strip()[-300:]
        if "MemoryError" in slutt or ferdig.returncode == -9:
            return {"feil": "Beregningen brukte for mye minne og ble stoppet."}
        return {"feil": f"Verktøyprosessen stoppet uventet (kode {ferdig.returncode}): {slutt}"}
    try:
        return json.loads(ferdig.stdout)
    except json.JSONDecodeError:
        return {"feil": f"Uforståelig svar fra verktøyprosessen: {ferdig.stdout[:200]}"}


def _hovedprogram() -> None:
    """Kjøres som «python -m backend.tools» av kjor_verktoy_med_tidsgrense.

    Leser {"navn": ..., "argumenter": {...}} fra stdin og skriver resultatet
    som JSON til stdout.
    """
    try:  # minnetak, så en gal beregning ikke spiser opp maskinen (Unix)
        import resource

        tak = MINNEGRENSE_BYTE
        resource.setrlimit(resource.RLIMIT_AS, (tak, tak))
    except Exception:
        pass
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as e:
        svar = {"feil": f"Ugyldig JSON til verktøyprosessen: {e}"}
    else:
        svar = kjor_verktoy(str(data.get("navn", "")), data.get("argumenter") or {})
    sys.stdout.write(json.dumps(svar, ensure_ascii=False, default=str))


if __name__ == "__main__":
    _hovedprogram()
