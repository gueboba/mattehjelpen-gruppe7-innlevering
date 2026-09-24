"""Trygg tolking av matteuttrykk (tekst -> SymPy) for MatteHjelpen.

HVORFOR EN EGEN MODUL: Både tools.py og validator.py må gjøre tekst om til
SymPy-objekter. sympify/parse_expr bruker Pythons eval() under panseret, og
teksten kommer fra en språkmodell som styres av det brukeren skriver. En
ondsinnet oppgavetekst kan derfor få modellen til å sende inn Python-kode.
Vi stoler ikke på input, og gjør dette:

- Maks lengde, og forbudte tegn/mønstre (``__``, anførselstegn, backslash,
  semikolon, kolon, klammeparenteser ...).
- Pythons innebygde funksjoner (open, eval, ...) finnes ikke i navnerommet
  uttrykket evalueres i. Ukjente navn blir bare SymPy-symboler.
- Attributtilgang («.») er bare lov for noen få SymPy-metoder (diff, subs,
  doit ...).
- Heltall med over 1000 sifre og enorme tallpotenser avvises før de regnes ut.

TOLKNINGSVALG (se også PROMPTS/01_tools.md):

- ``^`` betyr potens (``x^2`` = ``x**2``).
- ``e``/``E`` er Eulers tall, ``i``/``I`` er den imaginære enheten og ``ln``
  er den naturlige logaritmen.
- Implisitt multiplikasjon («2x») er IKKE tillatt – skriv ``2*x``. Grunnen er
  at SymPys implisitte multiplikasjon gjør ``y(x)`` om til ``y*x``, som
  ødelegger differensialligninger (vi testet det).
- ``sin^-1(x)`` og lignende AVVISES som tvetydig: skriv ``asin(x)`` for
  arcsin eller ``1/sin(x)``. Verktøyet skal aldri gjette på en tolkning.
- ``sin^2(x)`` avvises også; skriv ``sin(x)**2``.
"""

from __future__ import annotations

import ast
import io
import keyword
import math
import re
import tokenize

import sympy as sp
from sympy.core.function import AppliedUndef
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication,
    parse_expr,
    standard_transformations,
)


class TolkningsFeil(ValueError):
    """Teksten kunne ikke tolkes trygt. Meldingen er skrevet for modellen/brukeren."""


MAKS_LENGDE = 1500
MAKS_SIFRE = 1000
MAKS_EKSPONENT = 10_000
MAKS_FAKULTET = 5_000
MAKS_MATRISE = 10

_TRANSFORMASJONER = standard_transformations + (convert_xor,)
# Brukes BARE av validatoren, aldri av verktøyene: modeller skriver ofte
# «x*e^(2x)» med implisitt multiplikasjon. Da er det bedre å kontrollere
# svaret enn å la det stå ukontrollert. Verktøyene må være strenge, for der
# ville SymPys implisitte multiplikasjon gjort y(x) om til y*x.
_LEMPELIGE_TRANSFORMASJONER = _TRANSFORMASJONER + (implicit_multiplication,)

# Navn som kan brukes i uttrykk. Alt annet blir symboler (x, t, C1, ...) eller
# ukjente funksjoner (y(x)). Merk: «integrate», «limit» og «diff» gir her de
# UBEREGNEDE formene Integral/Limit/Derivative. Da kan validatoren kontrollere
# svaret med en uavhengig numerisk metode i stedet for å la SymPy regne svaret
# ut på nytt under tolkingen.
TILLATTE_NAVN: dict[str, object] = {
    # Trengs av SymPys egne parser-transformasjoner:
    "Symbol": sp.Symbol,
    "Function": sp.Function,
    "Integer": sp.Integer,
    "Float": sp.Float,
    "Rational": sp.Rational,
    # Konstanter
    "pi": sp.pi,
    "π": sp.pi,
    "E": sp.E,
    "e": sp.E,
    "I": sp.I,
    "i": sp.I,
    "oo": sp.oo,
    "inf": sp.oo,
    "zoo": sp.zoo,
    "nan": sp.nan,
    "true": sp.true,
    "false": sp.false,
    # Elementære funksjoner
    "sqrt": sp.sqrt,
    "cbrt": sp.cbrt,
    "root": sp.root,
    "exp": sp.exp,
    "log": sp.log,
    "ln": sp.log,
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "cot": sp.cot,
    "sec": sp.sec,
    "csc": sp.csc,
    "asin": sp.asin,
    "acos": sp.acos,
    "atan": sp.atan,
    "acot": sp.acot,
    "asec": sp.asec,
    "acsc": sp.acsc,
    "arcsin": sp.asin,
    "arccos": sp.acos,
    "arctan": sp.atan,
    "arccot": sp.acot,
    "atan2": sp.atan2,
    "sinh": sp.sinh,
    "cosh": sp.cosh,
    "tanh": sp.tanh,
    "coth": sp.coth,
    "asinh": sp.asinh,
    "acosh": sp.acosh,
    "atanh": sp.atanh,
    "Abs": sp.Abs,
    "abs": sp.Abs,
    "sign": sp.sign,
    "floor": sp.floor,
    "ceiling": sp.ceiling,
    "Min": sp.Min,
    "Max": sp.Max,
    "min": sp.Min,
    "max": sp.Max,
    "Mod": sp.Mod,
    "factorial": sp.factorial,
    "binomial": sp.binomial,
    # Komplekse tall
    "re": sp.re,
    "im": sp.im,
    "arg": sp.arg,
    "conjugate": sp.conjugate,
    "exp_polar": sp.exp_polar,
    "polar_lift": sp.polar_lift,
    # Spesialfunksjoner som dukker opp i SymPy-svar (f.eks. integraler)
    "erf": sp.erf,
    "erfc": sp.erfc,
    "erfi": sp.erfi,
    "Si": sp.Si,
    "Ci": sp.Ci,
    "Ei": sp.Ei,
    "li": sp.li,
    "Shi": sp.Shi,
    "Chi": sp.Chi,
    "gamma": sp.gamma,
    "uppergamma": sp.uppergamma,
    "lowergamma": sp.lowergamma,
    "polylog": sp.polylog,
    "LambertW": sp.LambertW,
    "fresnels": sp.fresnels,
    "fresnelc": sp.fresnelc,
    "Heaviside": sp.Heaviside,
    "DiracDelta": sp.DiracDelta,
    "CRootOf": sp.CRootOf,
    "RootOf": sp.CRootOf,
    # Relasjoner og logikk (Piecewise-svar fra SymPy)
    "Eq": sp.Eq,
    "Ne": sp.Ne,
    "Lt": sp.Lt,
    "Le": sp.Le,
    "Gt": sp.Gt,
    "Ge": sp.Ge,
    "And": sp.And,
    "Or": sp.Or,
    "Piecewise": sp.Piecewise,
    # Analyse (uberegnede former)
    "Derivative": sp.Derivative,
    "diff": sp.Derivative,
    "Integral": sp.Integral,
    "integrate": sp.Integral,
    "Limit": sp.Limit,
    "limit": sp.Limit,
    "Sum": sp.Sum,
    "Subs": sp.Subs,
    # Lineær algebra
    "Matrix": sp.Matrix,
    "ImmutableMatrix": sp.ImmutableMatrix,
    "MutableDenseMatrix": sp.Matrix,
    "ImmutableDenseMatrix": sp.ImmutableMatrix,
}

# Metoder som er lov å kalle med punktum, f.eks. y(x).diff(x).
TILLATTE_METODER = {"diff", "subs", "doit", "evalf", "n", "T", "simplify", "expand", "factor"}

# Funksjonsnavn som ikke er ukjente funksjoner i en ODE (brukes for å finne y).
_KJENTE_FUNKSJONER = {navn for navn, verdi in TILLATTE_NAVN.items() if callable(verdi)}

_ERSTATNINGER = {
    "−": "-",  # matematisk minus
    "–": "-",  # tankestrek
    "·": "*",
    "⋅": "*",
    "×": "*",
    "÷": "/",
    "∞": "oo",
    "²": "**2",
    "³": "**3",
    "√(": "sqrt(",
}

# Tegn uten matematisk betydning som følger med når noen limer inn en oppgave
# fra PDF eller nettside. Uten dette ble «x + 1» med hardt mellomrom tolket som
# Symbol('x\xa0') + Symbol('\xa01') – to tullesymboler – og appen meldte at et
# helt riktig svar var feil.
_USYNLIGE = {ord(tegn): None for tegn in "​‌‍⁠﻿­"}
_USYNLIGE.update({ord(tegn): " " for tegn in "      　"})

# Rottegn uten parentes: √4, √x. Med parentes tas det av _ERSTATNINGER.
_ROTTEGN = re.compile(r"√\s*([A-Za-z0-9_.]+)")

# Norsk desimalkomma. Lookaround-ene gjør at «0.5,1.5» (to tall i en liste)
# ikke treffer, mens «0,5» og «2,5*x» gjør det.
_DESIMALKOMMA = re.compile(r"(?<![\d.])\d+\s*,\s*\d+(?![\d.])")

# Symbolnavn skal være vanlige navn. Alt annet er et tegn vi ikke forstod, og
# som SymPy stilltiende gjorde om til et symbol (√4 ble Symbol('√4')).
_GYLDIG_SYMBOLNAVN = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")

_FORBUDT = re.compile(r"__|[\\`$@#!?{}:;&|~%]")
_TVETYDIG_INVERS = re.compile(
    r"\b(a?sin|a?cos|a?tan|a?cot|a?sec|a?csc|sinh|cosh|tanh|ln|log)\s*(\^|\*\*)\s*\(?\s*-\s*1\s*\)?"
)
_TRIGPOTENS = re.compile(
    r"\b(sin|cos|tan|cot|sec|csc|sinh|cosh|tanh|ln|log)\s*(\^|\*\*)\s*\(?\s*\d+\s*\)?\s*\("
)
_DIR_ARGUMENT = re.compile(r"""dir\s*=\s*(['"])(\+-|\+|-)\1""")


def normaliser(tekst: str) -> str:
    """Bytter vanlige Unicode-tegn (−, ·, ², ∞ ...) til SymPy-syntaks.

    Usynlige tegn fjernes først. De har ingen matematisk betydning, men SymPy
    tar dem med i symbolnavn, og da blir «x» og «x␣» to forskjellige symboler.
    """
    tekst = tekst.translate(_USYNLIGE)
    for gammel, ny in _ERSTATNINGER.items():
        tekst = tekst.replace(gammel, ny)
    tekst = _ROTTEGN.sub(r"sqrt(\1)", tekst)
    return tekst.strip()


def _utenfor_parentes(regex: re.Pattern, tekst: str) -> re.Match | None:
    """Finner første treff som ikke står inne i en parentes eller klammer.

    Brukes til desimalkomma: «0,5» er et tall skrevet på norsk, mens kommaene i
    «[[1,2],[3,4]]» og «Integral(x, (x, 0, 1))» skiller elementer.
    """
    dybde = 0
    apne = {"(": 1, "[": 1, "{": 1}
    lukke = {")": 1, "]": 1, "}": 1}
    for plass, tegn in enumerate(tekst):
        if tegn in apne:
            dybde += 1
        elif tegn in lukke:
            dybde = max(0, dybde - 1)
        elif dybde == 0:
            treff = regex.match(tekst, plass)
            if treff:
                return treff
    return None


def _sjekk_tekst(tekst: str) -> None:
    if len(tekst) > MAKS_LENGDE:
        raise TolkningsFeil(f"Uttrykket er for langt (maks {MAKS_LENGDE} tegn).")
    if not tekst:
        raise TolkningsFeil("Uttrykket er tomt.")
    if _TVETYDIG_INVERS.search(tekst):
        raise TolkningsFeil(
            "Tvetydig notasjon: «sin^-1(x)» kan bety arcsin(x) ELLER 1/sin(x). "
            "Skriv asin(x) for arcsin eller 1/sin(x) for den inverse verdien, "
            "og fortell brukeren hvilken tolkning du valgte."
        )
    if _TRIGPOTENS.search(tekst):
        raise TolkningsFeil(
            "Skriv potenser av funksjoner eksplisitt: sin(x)**2, ikke sin^2(x)."
        )
    komma = _utenfor_parentes(_DESIMALKOMMA, tekst)
    if komma:
        riktig = komma.group(0).replace(",", ".").replace(" ", "")
        raise TolkningsFeil(
            f"«{komma.group(0)}» ser ut som et desimaltall skrevet på norsk. "
            f"Bruk punktum som desimalskilletegn: {riktig}. "
            "Komma betyr «og» og ville delt uttrykket i to."
        )
    uten_dir = _DIR_ARGUMENT.sub("", tekst)
    if "'" in uten_dir or '"' in uten_dir:
        raise TolkningsFeil(
            "Anførselstegn er ikke tillatt. Deriverte skrives y(x).diff(x) "
            "eller Derivative(y(x), x), ikke y'."
        )
    treff = _FORBUDT.search(tekst)
    if treff:
        raise TolkningsFeil(f"Tegnet/mønsteret «{treff.group(0)}» er ikke tillatt i et matteuttrykk.")


def _sjekk_tokens(tekst: str) -> None:
    """Går gjennom Python-tokenene og avviser alt som ikke er ren matematikk."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(tekst).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as e:
        raise TolkningsFeil(f"Ugyldig syntaks: {e}") from None
    forrige = None
    for tok in tokens:
        if tok.type == tokenize.NAME:
            navn = tok.string
            if forrige is not None and forrige.type == tokenize.OP and forrige.string == ".":
                if navn not in TILLATTE_METODER:
                    raise TolkningsFeil(
                        f"Metoden «.{navn}» er ikke tillatt. Tillatt: "
                        + ", ".join(sorted(TILLATTE_METODER))
                    )
            elif keyword.iskeyword(navn) and navn not in ("True", "False"):
                raise TolkningsFeil(f"Python-nøkkelordet «{navn}» er ikke tillatt i et matteuttrykk.")
            if navn.startswith("_"):
                raise TolkningsFeil("Navn som starter med understrek er ikke tillatt.")
        elif tok.type == tokenize.NUMBER:
            if len(tok.string) > MAKS_SIFRE:
                raise TolkningsFeil(f"Tall med over {MAKS_SIFRE} sifre er ikke tillatt.")
        elif tok.type == tokenize.STRING:
            if tok.string.strip("\"'") not in ("+", "-", "+-"):
                raise TolkningsFeil("Tekststrenger er ikke tillatt i et matteuttrykk.")
        elif tok.type == tokenize.ERRORTOKEN and tok.string.strip():
            raise TolkningsFeil(f"Ugyldig tegn: «{tok.string}».")
        forrige = tok


def _log10_storrelse(node: ast.AST) -> float | None:
    """Anslår log10(|verdi|) for uttrykk som bare består av tall. None ellers.

    Vi regner med flyttall og logaritmer, ikke eksakte heltall, nettopp fordi
    poenget er å AVVISE tall som er for store til å regnes ut (9**9**9 har
    rundt 370 millioner sifre – å regne det ut er angrepet vi stopper).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        verdi = abs(node.value)
        return -math.inf if verdi == 0 else math.log10(verdi)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _log10_storrelse(node.operand)
    if isinstance(node, ast.BinOp):
        venstre = _log10_storrelse(node.left)
        hoyre = _log10_storrelse(node.right)
        if venstre is None or hoyre is None:
            return None
        if isinstance(node.op, ast.Pow):
            if hoyre > 12:
                return math.inf
            return (10.0**hoyre) * venstre
        if isinstance(node.op, ast.Mult):
            return venstre + hoyre
        if isinstance(node.op, ast.Div):
            return venstre - hoyre
        if isinstance(node.op, (ast.Add, ast.Sub)):
            return max(venstre, hoyre) + 0.31
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "factorial":
        if len(node.args) != 1:
            return None
        argument = _log10_storrelse(node.args[0])
        if argument is None:
            return None
        if argument > 12:
            return math.inf
        n = 10.0**argument
        if n > MAKS_FAKULTET:
            raise TolkningsFeil(f"Fakultet av tall over {MAKS_FAKULTET} er ikke tillatt.")
        return n * argument - n * 0.4343 if n > 1 else 0.0
    return None


def _sjekk_storrelse(tekst: str) -> None:
    """Avviser tallpotenser og fakulteter som ville tatt evigheter å regne ut.

    Sjekken gjøres på Pythons AST FØR SymPy får se teksten, slik at vi aldri
    starter selve beregningen vi vil unngå.
    """
    try:
        tre = ast.parse(tekst.replace("^", "**"), mode="eval")
    except SyntaxError:
        return  # fanges av parse_expr med en bedre feilmelding
    for node in ast.walk(tre):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) or isinstance(node, ast.Call):
            storrelse = _log10_storrelse(node)
            if storrelse is not None and abs(storrelse) > MAKS_SIFRE:
                raise TolkningsFeil(
                    f"Tallet blir for stort til å regnes ut (over {MAKS_SIFRE} sifre). "
                    "Skriv om oppgaven, eller be om et symbolsk svar."
                )


def tolk_uttrykk(tekst: str, *, tillat_ukjente_funksjoner: bool = False, lempelig: bool = False):
    """Tolker tekst til et SymPy-objekt (uttrykk, Eq, liste eller Matrix).

    Kaster TolkningsFeil med en forklarende norsk melding hvis teksten er
    ugyldig, utrygg eller tvetydig.

    `lempelig=True` gir ett ekstra forsøk med implisitt multiplikasjon
    («2x» = 2*x) hvis det strenge forsøket feilet. Det brukes bare av
    validatoren: der er alternativet at et riktig svar blir stående
    ukontrollert fordi modellen skrev litt slurvete.
    """
    if not isinstance(tekst, str):
        raise TolkningsFeil(f"Forventet tekst, fikk {type(tekst).__name__}.")
    tekst = normaliser(tekst)
    _sjekk_tekst(tekst)
    _sjekk_tokens(tekst)
    _sjekk_storrelse(tekst)
    try:
        resultat = parse_expr(
            tekst,
            local_dict={},
            global_dict=dict(TILLATTE_NAVN, __builtins__={}),
            transformations=_TRANSFORMASJONER,
        )
    except TolkningsFeil:
        raise
    except SyntaxError:
        if lempelig:
            try:
                resultat = parse_expr(
                    tekst,
                    local_dict={},
                    global_dict=dict(TILLATTE_NAVN, __builtins__={}),
                    transformations=_LEMPELIGE_TRANSFORMASJONER,
                )
            except Exception:
                resultat = None
            if resultat is not None:
                _sjekk_type(resultat, tekst)
                if not tillat_ukjente_funksjoner and _ukjente_funksjoner(resultat):
                    raise TolkningsFeil(f"«{tekst}» inneholder ukjente funksjoner.") from None
                return resultat
        raise TolkningsFeil(
            f"Ugyldig syntaks i «{tekst}». Bruk SymPy-syntaks: * for gange "
            "(2*x, ikke 2x), ** eller ^ for potens, f.eks. x**2*sin(3*x)."
        ) from None
    except Exception as e:  # SymPy kan kaste mange typer feil
        # Inne i en parentes kan vi ikke vite om kommaet skiller argumenter
        # eller er et norsk desimaltegn – men feilet tolkningen og teksten har
        # et komma mellom sifre, er desimalkomma den klart mest sannsynlige
        # forklaringen, og den eneste brukeren kan gjøre noe med.
        hint = ""
        if _DESIMALKOMMA.search(tekst):
            hint = (
                " Tips: bruk punktum som desimalskilletegn (0.5), ikke komma – "
                "komma skiller argumenter."
            )
        raise TolkningsFeil(
            f"Kunne ikke tolke «{tekst}»: {type(e).__name__}: {e}{hint}"
        ) from None

    if isinstance(resultat, tuple):
        resultat = list(resultat)
    _sjekk_type(resultat, tekst)
    _sjekk_symbolnavn(resultat, tekst)
    if not tillat_ukjente_funksjoner:
        ukjente = _ukjente_funksjoner(resultat)
        if ukjente:
            navn = ", ".join(sorted({str(f.func) for f in ukjente}))
            raise TolkningsFeil(
                f"Ukjent funksjon: {navn}. Bruk kjente funksjoner (sin, exp, log, sqrt, "
                "asin, ...) eller riktig verktøy (f.eks. solve_equation i stedet for solve)."
            )
    return resultat


def _sjekk_symbolnavn(resultat, tekst: str) -> None:
    """Avviser symboler med tegn vi ikke forstod.

    SymPy lager et symbol av nesten hva som helst. «√4» ble Symbol('√4') og så
    riktig ut i utskriften, men er et navn – ikke en kvadratrot. Da er det
    bedre å si fra enn å regne videre på noe annet enn brukeren mente.
    """
    elementer = resultat if isinstance(resultat, list) else [resultat]
    rare = set()
    for element in elementer:
        for symbol in getattr(element, "free_symbols", set()):
            if not _GYLDIG_SYMBOLNAVN.match(str(symbol)):
                rare.add(str(symbol))
    if rare:
        raise TolkningsFeil(
            f"«{tekst}» inneholder tegn vi ikke kjenner igjen som matematikk: "
            + ", ".join(f"«{navn}»" for navn in sorted(rare))
            + ". Skriv uttrykket med vanlige bokstaver, tall og operatorer."
        )


def _sjekk_type(resultat, tekst: str) -> None:
    if isinstance(resultat, list):
        for element in resultat:
            _sjekk_type(element, tekst)
        return
    if isinstance(resultat, (sp.Basic, sp.MatrixBase, bool, int, float, complex)):
        return
    raise TolkningsFeil(f"«{tekst}» ble ikke et matematisk uttrykk (fikk {type(resultat).__name__}).")


def _ukjente_funksjoner(objekt) -> set:
    if isinstance(objekt, list):
        funnet: set = set()
        for element in objekt:
            funnet |= _ukjente_funksjoner(element)
        return funnet
    if isinstance(objekt, (sp.Basic, sp.MatrixBase)):
        return set(objekt.atoms(AppliedUndef))
    return set()


def tolk_symbol(navn: str) -> sp.Symbol:
    """Tolker et variabelnavn, f.eks. «x» eller «t»."""
    if not isinstance(navn, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,15}", navn.strip()):
        raise TolkningsFeil(f"«{navn}» er ikke et gyldig variabelnavn (bruk f.eks. x eller t).")
    navn = navn.strip()
    if navn in TILLATTE_NAVN or keyword.iskeyword(navn):
        raise TolkningsFeil(f"«{navn}» er et reservert navn og kan ikke brukes som variabel.")
    return sp.Symbol(navn)


def tolk_symboler(tekst) -> list[sp.Symbol]:
    """Tolker én eller flere variabler: «x», «x, y, z» eller ["x", "y"]."""
    if isinstance(tekst, (list, tuple)):
        deler = [str(d) for d in tekst]
    else:
        deler = [d for d in re.split(r"[,\s]+", str(tekst).strip("[]() ")) if d]
    if not deler:
        raise TolkningsFeil("Ingen variabel oppgitt.")
    return [tolk_symbol(d) for d in deler]


def del_pa_toppniva(tekst: str, skilletegn: str = ",;") -> list[str]:
    """Deler tekst på skilletegn som ikke står inni parenteser/klammer."""
    deler, dybde, start = [], 0, 0
    for i, tegn in enumerate(tekst):
        if tegn in "([{":
            dybde += 1
        elif tegn in ")]}":
            dybde -= 1
        elif tegn in skilletegn and dybde == 0:
            deler.append(tekst[start:i])
            start = i + 1
    deler.append(tekst[start:])
    return [d.strip() for d in deler if d.strip()]


def _del_likhetstegn(tekst: str) -> tuple[str, str] | None:
    """Finner ett «=» (ikke ==, <=, >=, !=) på toppnivå. Returnerer (venstre, høyre)."""
    tekst = tekst.replace("==", "=")
    posisjoner = []
    dybde = 0
    for i, tegn in enumerate(tekst):
        if tegn in "([":
            dybde += 1
        elif tegn in ")]":
            dybde -= 1
        elif tegn == "=" and dybde == 0:
            forrige = tekst[i - 1] if i > 0 else ""
            if forrige not in "<>!":
                posisjoner.append(i)
    if not posisjoner:
        return None
    if len(posisjoner) > 1:
        raise TolkningsFeil("En ligning kan bare ha ett likhetstegn.")
    i = posisjoner[0]
    return tekst[:i], tekst[i + 1 :]


def tolk_likning(tekst: str, *, tillat_ukjente_funksjoner: bool = False) -> sp.Eq:
    """«x**2 - 4 = 0», «Eq(x**2, 4)» eller «x**2 - 4» (betyr = 0) -> Eq."""
    tekst = normaliser(str(tekst))
    deler = _del_likhetstegn(tekst)
    if deler is not None:
        venstre = tolk_uttrykk(deler[0], tillat_ukjente_funksjoner=tillat_ukjente_funksjoner)
        hoyre = tolk_uttrykk(deler[1], tillat_ukjente_funksjoner=tillat_ukjente_funksjoner)
        _krev_skalar(venstre, tekst)
        _krev_skalar(hoyre, tekst)
        return sp.Eq(venstre, hoyre, evaluate=False)
    objekt = tolk_uttrykk(tekst, tillat_ukjente_funksjoner=tillat_ukjente_funksjoner)
    if isinstance(objekt, sp.Equality):
        return objekt
    if objekt is sp.true or objekt is sp.false:
        raise TolkningsFeil(f"«{tekst}» er alltid {'sann' if objekt is sp.true else 'usann'} – ingenting å løse.")
    _krev_skalar(objekt, tekst)
    return sp.Eq(objekt, 0, evaluate=False)


def tolk_likninger(tekst) -> list[sp.Eq]:
    """En ligning eller et system: «x + y = 3; x - y = 1», liste av strenger osv."""
    if isinstance(tekst, (list, tuple)):
        deler = [str(d) for d in tekst]
    else:
        tekst = normaliser(str(tekst))
        if tekst.startswith("[") and tekst.endswith("]"):
            tekst = tekst[1:-1]
        deler = del_pa_toppniva(tekst.replace("\n", ";"), ",;")
    if not deler:
        raise TolkningsFeil("Ingen ligning oppgitt.")
    return [tolk_likning(d) for d in deler]


def _krev_skalar(objekt, tekst: str) -> None:
    if not isinstance(objekt, sp.Expr):
        raise TolkningsFeil(f"«{tekst}» er ikke et enkelt matematisk uttrykk.")


def krev_uttrykk(objekt, tekst: str) -> sp.Expr:
    """Sørger for at objektet er et vanlig uttrykk (ikke ligning, liste eller matrise)."""
    if isinstance(objekt, sp.Equality):
        raise TolkningsFeil(f"«{tekst}» er en ligning; her trengs et uttrykk uten likhetstegn.")
    _krev_skalar(objekt, tekst)
    return objekt


# --- Differensialligninger ---------------------------------------------------

# Merk: vi bruker (?<![A-Za-z_]) i stedet for \b foran navnet, slik at «3y» og
# «2y''» også blir funnet (mellom «3» og «y» er det ingen \b-grense).
_FORAN = r"(?<![A-Za-z_])"
_APPLISERT = re.compile(_FORAN + r"([A-Za-z]\w*)\s*\(\s*([A-Za-z]\w*)\s*\)")
_PRIMTEGN = re.compile(_FORAN + r"([A-Za-z]\w*)('+)")


def _finn_ode_funksjon(tekst: str) -> tuple[str, str]:
    """Finner navnet på den ukjente funksjonen (y) og variabelen (x) i en ODE-tekst."""
    kandidater: dict[str, str] = {}
    for navn, var in _APPLISERT.findall(tekst):
        if navn not in _KJENTE_FUNKSJONER and var not in TILLATTE_NAVN:
            kandidater.setdefault(navn, var)
    med_primtegn = {navn for navn, _ in _PRIMTEGN.findall(tekst)}
    for navn in med_primtegn:
        kandidater.setdefault(navn, "")
    if not kandidater:
        raise TolkningsFeil(
            "Fant ingen ukjent funksjon. Skriv den ukjente som y(x), f.eks. "
            "Eq(y(x).diff(x, 2) + 4*y(x), 0)."
        )
    if len(kandidater) > 1:
        raise TolkningsFeil(
            "Fant flere ukjente funksjoner (" + ", ".join(sorted(kandidater)) + "). "
            "Verktøyet løser én differensialligning med én ukjent funksjon."
        )
    navn, var = next(iter(kandidater.items()))
    if not var:
        uten_navn = re.sub(rf"\b{re.escape(navn)}\b", " ", tekst)
        var = "t" if re.search(r"\bt\b", uten_navn) and not re.search(r"\bx\b", uten_navn) else "x"
    return navn, var


def _omskriv_primtegn(tekst: str, navn: str, var: str) -> str:
    """y'' -> Derivative(y(x), x, 2), y' -> Derivative(y(x), x), bar y -> y(x), 2y -> 2*y."""

    def erstatt(treff: re.Match) -> str:
        orden = len(treff.group(1))
        if orden == 1:
            return f"Derivative({navn}({var}), {var})"
        return f"Derivative({navn}({var}), {var}, {orden})"

    tekst = re.sub(
        _FORAN + rf"{re.escape(navn)}('+)(\s*\(\s*{re.escape(var)}\s*\))?",
        erstatt,
        tekst,
    )
    tekst = re.sub(_FORAN + rf"{re.escape(navn)}\b(?!\s*\()", f"{navn}({var})", tekst)
    # «3y» og «2sin(x)» skrives om til «3*y(x)» og «2*sin(x)». Vi setter bare
    # inn gangetegn foran navn vi kjenner igjen, aldri generelt (1e-5 må bestå).
    kjente = "|".join(sorted(map(re.escape, _KJENTE_FUNKSJONER | {navn, "Derivative"}), key=len, reverse=True))
    tekst = re.sub(rf"(\d|\))\s*(?=(?:{kjente})\s*\()", r"\1*", tekst)
    return tekst


def tolk_ode(tekst: str) -> tuple[sp.Eq, sp.Expr, sp.Symbol]:
    """Tolker en ODE. Returnerer (ligning, y(x), x).

    Godtar SymPy-syntaks (Eq(y(x).diff(x, 2) + 4*y(x), 0), uttrykk = 0) og
    vanlig primtegn-notasjon (y'' + 4*y = 0).
    """
    tekst = normaliser(str(tekst))
    navn, var = _finn_ode_funksjon(tekst)
    tekst = _omskriv_primtegn(tekst, navn, var)
    likning = tolk_likning(tekst, tillat_ukjente_funksjoner=True)
    x = sp.Symbol(var)
    y = sp.Function(navn)(x)
    if not likning.has(sp.Derivative):
        raise TolkningsFeil(
            f"Ligningen inneholder ingen derivert av {y}. Er dette virkelig en differensialligning?"
        )
    andre = {f for f in likning.atoms(AppliedUndef) if f != y}
    if andre:
        raise TolkningsFeil(
            "Ligningen inneholder andre ukjente funksjoner: " + ", ".join(sorted(map(str, andre)))
        )
    return likning, y, x


_BETINGELSE = re.compile(r"^\s*([A-Za-z]\w*)\s*('*)\s*\(\s*([^()]+?)\s*\)\s*=\s*(.+?)\s*$")


def tolk_betingelser(betingelser, y: sp.Expr, x: sp.Symbol) -> list[tuple[int, sp.Expr, sp.Expr]]:
    """«y(0)=1, y'(0)=0» -> [(0, 0, 1), (1, 0, 0)] som (orden, punkt, verdi)."""
    if betingelser is None:
        return []
    if isinstance(betingelser, str):
        deler = del_pa_toppniva(normaliser(betingelser).replace(" og ", ";").replace("\n", ";"), ",;")
    else:
        deler = [normaliser(str(b)) for b in betingelser]
    navn = str(y.func)
    resultat = []
    for del_ in deler:
        treff = _BETINGELSE.match(del_)
        if not treff:
            raise TolkningsFeil(
                f"Forstod ikke betingelsen «{del_}». Skriv f.eks. y(0) = 1 eller y'(0) = 0."
            )
        funksjon, primtegn, punkt, verdi = treff.groups()
        if funksjon != navn:
            raise TolkningsFeil(f"Betingelsen «{del_}» gjelder {funksjon}, men den ukjente er {navn}.")
        punkt_verdi = krev_uttrykk(tolk_uttrykk(punkt), punkt)
        verdi_uttrykk = krev_uttrykk(tolk_uttrykk(verdi), verdi)
        resultat.append((len(primtegn), punkt_verdi, verdi_uttrykk))
    return resultat


# --- Matriser og tall ----------------------------------------------------------


def tolk_matrise(verdi) -> sp.Matrix:
    """Tolker en matrise fra liste ([[1, 2], [3, 4]]) eller tekst («[[1, 1/2], [1/2, 1/3]]»)."""
    if isinstance(verdi, sp.MatrixBase):
        matrise = sp.Matrix(verdi)
    else:
        rader = _tolk_liste(verdi)
        if not rader or not all(isinstance(r, list) for r in rader):
            raise TolkningsFeil("En matrise må være en liste av rader, f.eks. [[1, 2], [3, 4]].")
        lengder = {len(r) for r in rader}
        if len(lengder) != 1 or 0 in lengder:
            raise TolkningsFeil("Alle rader i matrisen må ha like mange (og minst ett) elementer.")
        matrise = sp.Matrix([[tolk_element(e) for e in rad] for rad in rader])
    if matrise.rows > MAKS_MATRISE or matrise.cols > MAKS_MATRISE:
        raise TolkningsFeil(f"Matriser større enn {MAKS_MATRISE}x{MAKS_MATRISE} er ikke tillatt.")
    return matrise


def tolk_vektor(verdi) -> sp.Matrix:
    """Tolker en vektor ([1, 2, 3] eller [[1], [2], [3]]) til en kolonnematrise."""
    elementer = _tolk_liste(verdi)
    if elementer and all(isinstance(e, list) and len(e) == 1 for e in elementer):
        elementer = [e[0] for e in elementer]
    if not elementer or any(isinstance(e, list) for e in elementer):
        raise TolkningsFeil("En vektor må være en flat liste, f.eks. [1, 2, 3].")
    return sp.Matrix([tolk_element(e) for e in elementer])


def tolk_element(verdi) -> sp.Expr:
    """Ett tall/uttrykk i en matrise. Brøker bør gis som tekst («1/3») for å være eksakte."""
    if isinstance(verdi, bool):
        raise TolkningsFeil("Sannhetsverdier kan ikke være matriseelementer.")
    if isinstance(verdi, int):
        return sp.Integer(verdi)
    if isinstance(verdi, float):
        # 0.3333333333333333 fra JSON: nsimplify finner 1/3 hvis tallet er en enkel brøk.
        return sp.nsimplify(verdi, rational=False, tolerance=1e-12)
    if isinstance(verdi, sp.Basic):
        return verdi
    return krev_uttrykk(tolk_uttrykk(str(verdi)), str(verdi))


def _tolk_liste(verdi):
    if isinstance(verdi, str):
        objekt = tolk_uttrykk(verdi)
        if isinstance(objekt, sp.MatrixBase):
            return objekt.tolist()
        if not isinstance(objekt, list):
            raise TolkningsFeil(f"«{verdi}» er ikke en liste.")
        return objekt
    if isinstance(verdi, tuple):
        verdi = list(verdi)
    if not isinstance(verdi, list):
        raise TolkningsFeil(f"Forventet en liste, fikk {type(verdi).__name__}.")
    return [(_tolk_liste(v) if isinstance(v, (list, tuple)) else v) for v in verdi]
