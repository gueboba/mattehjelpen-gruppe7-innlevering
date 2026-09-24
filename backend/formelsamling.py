"""Innebygd formelsamling – à la Jarle Johannessen: «Tekniske Tabeller».

Modellen skal referere til formler herfra i sine forklaringer.

Feltet «bruk» hjelper modellen å velge regel. Modellen oppgir formel-ID i
løsningssteget; appen kontrollerer ID-en og henter navn og referanse herfra.
En gyldig ID viser HVOR regelen står – den beviser ikke at regelen er brukt
riktig. Den kontrollen må mennesket (og validatoren) gjøre.

HVORFOR: Sporbarhet. «Hvilken formel brukte du, og hvor står den?» er et
spørsmål enhver ingeniør må kunne svare på.

REFERANSER (vi har kontrollert kapittel- og avsnittsnummer mot forlagets
innholdsfortegnelser for disse utgavene):

- *Thomas' Calculus: Early Transcendentals*, 14. utgave (Hass, Heil, Weir).
- *Differential Equations and Linear Algebra*, 4. utgave (Edwards, Penney,
  Calvis), forkortet «Edwards & Penney».
- *Tekniske Tabeller* (Jarle Johannessen) – sidetall varierer mellom utgaver,
  så der oppgir vi kapittel og ber dere kontrollere i deres eget eksemplar.
- *Euklids Elementer*, bok I, proposisjon 47 (Pythagoras' læresetning).

Har dere en annen utgave, kan avsnittsnumrene være forskjøvet. Kontroller
referansen før dere bruker den i en innlevering – det er nettopp poenget med
å oppgi den.
"""

FORMELSAMLING = {
    # --- Derivasjon ---------------------------------------------------------
    "D1": {
        "navn": "Produktregelen",
        "formel": r"(uv)' = u'v + uv'",
        "referanse": "Thomas' Calculus (Early Transcendentals, 14. utg.), avsn. 3.3",
        "bruk": "Når et produkt av to funksjoner skal deriveres.",
    },
    "D2": {
        "navn": "Kjerneregelen",
        "formel": r"\frac{dy}{dx} = \frac{dy}{du}\cdot\frac{du}{dx}",
        "referanse": "Thomas' Calculus (Early Transcendentals, 14. utg.), avsn. 3.6",
        "bruk": "Når en sammensatt funksjon som sin(3x) eller e^(2x) skal deriveres.",
    },
    "D3": {
        "navn": "Kvotientregelen",
        "formel": r"\left(\frac{u}{v}\right)' = \frac{u'v - uv'}{v^2}",
        "referanse": "Thomas' Calculus (Early Transcendentals, 14. utg.), avsn. 3.3",
        "bruk": "Når en brøk med funksjoner i teller og nevner skal deriveres.",
    },
    "D4": {
        "navn": "Potensregelen for derivasjon",
        "formel": r"\frac{d}{dx}x^{n} = n x^{n-1}",
        "referanse": "Thomas' Calculus (Early Transcendentals, 14. utg.), avsn. 3.3",
        "bruk": "Når en potens av x skal deriveres, også for negative og brøk-eksponenter.",
    },
    "D5": {
        "navn": "Deriverte av elementære funksjoner",
        "formel": (
            r"\frac{d}{dx}\sin x = \cos x,\quad \frac{d}{dx}\cos x = -\sin x,\quad "
            r"\frac{d}{dx}e^{x} = e^{x},\quad \frac{d}{dx}\ln x = \frac{1}{x}"
        ),
        "referanse": "Thomas' Calculus (Early Transcendentals, 14. utg.), avsn. 3.5 og 3.8",
        "bruk": "Standardderiverte som trengs i nesten alle derivasjonsoppgaver.",
    },
    # --- Integrasjon --------------------------------------------------------
    "I1": {
        "navn": "Delvis integrasjon",
        "formel": r"\int u\,dv = uv - \int v\,du",
        "referanse": "Thomas' Calculus (Early Transcendentals, 14. utg.), avsn. 8.2",
        "bruk": "Når integranden er et produkt som blir enklere når én faktor deriveres, f.eks. x·e^(2x).",
    },
    "I2": {
        "navn": "Integrasjon ved substitusjon",
        "formel": r"\int f(g(x))\,g'(x)\,dx = \int f(u)\,du,\quad u = g(x)",
        "referanse": "Thomas' Calculus (Early Transcendentals, 14. utg.), avsn. 5.5",
        "bruk": "Når integranden inneholder en indre funksjon og dens deriverte, f.eks. x/(x²+1).",
    },
    "I3": {
        "navn": "Potensregelen for integrasjon",
        "formel": r"\int x^{n}\,dx = \frac{x^{n+1}}{n+1} + C,\quad n \neq -1",
        "referanse": "Thomas' Calculus (Early Transcendentals, 14. utg.), avsn. 4.8 og 5.5",
        "bruk": "Når en potens av x skal integreres.",
    },
    "I4": {
        "navn": "Analysens fundamentalteorem (del 2)",
        "formel": r"\int_a^b f(x)\,dx = F(b) - F(a),\quad F' = f",
        "referanse": "Thomas' Calculus (Early Transcendentals, 14. utg.), avsn. 5.4",
        "bruk": "Når et bestemt integral skal regnes ut fra en antiderivert.",
    },
    "I5": {
        "navn": "Delbrøkoppspalting",
        "formel": r"\frac{1}{(x-a)(x-b)} = \frac{1}{a-b}\left(\frac{1}{x-a} - \frac{1}{x-b}\right),\quad a \neq b",
        "referanse": "Thomas' Calculus (Early Transcendentals, 14. utg.), avsn. 8.5",
        "bruk": "Når en rasjonal funksjon skal integreres og nevneren kan faktoriseres.",
    },
    "I6": {
        "navn": "Grunnleggende integraler",
        "formel": (
            r"\int \frac{1}{x}\,dx = \ln|x| + C,\quad \int e^{x}\,dx = e^{x} + C,\quad "
            r"\int \sin x\,dx = -\cos x + C,\quad \int \cos x\,dx = \sin x + C"
        ),
        "referanse": "Thomas' Calculus (Early Transcendentals, 14. utg.), avsn. 8.1 (tabell)",
        "bruk": "Standardintegraler som brukes direkte eller etter en substitusjon.",
    },
    # --- Grenseverdier ------------------------------------------------------
    "G1": {
        "navn": "L'Hôpitals regel",
        "formel": r"\lim_{x\to a}\frac{f(x)}{g(x)} = \lim_{x\to a}\frac{f'(x)}{g'(x)}",
        "referanse": "Thomas' Calculus (Early Transcendentals, 14. utg.), avsn. 4.5",
        "bruk": "Når en grenseverdi gir det ubestemte uttrykket 0/0 eller ∞/∞.",
    },
    # --- Differensialligninger ----------------------------------------------
    "O1": {
        "navn": "Karakteristisk ligning (2. ordens lineær ODE)",
        "formel": r"ar^2 + br + c = 0 \text{ for } ay'' + by' + cy = 0",
        "referanse": "Edwards & Penney (Differential Equations and Linear Algebra, 4. utg.), avsn. 5.3",
        "bruk": "Når en homogen lineær differensialligning med konstante koeffisienter skal løses.",
    },
    "O2": {
        "navn": "Generell løsning ved reelle, ulike røtter",
        "formel": r"y = C_1e^{r_1x} + C_2e^{r_2x}",
        "referanse": "Edwards & Penney (4. utg.), avsn. 5.3",
        "bruk": "Når den karakteristiske ligningen har to ulike reelle røtter r₁ og r₂.",
    },
    "O3": {
        "navn": "Generell løsning ved komplekse røtter",
        "formel": r"r = \alpha \pm \beta i \;\Rightarrow\; y = e^{\alpha x}\left(C_1\cos\beta x + C_2\sin\beta x\right)",
        "referanse": "Edwards & Penney (4. utg.), avsn. 5.3",
        "bruk": "Når den karakteristiske ligningen har komplekse røtter, f.eks. for y'' + 2y = 0.",
    },
    "O4": {
        "navn": "Separable differensialligninger",
        "formel": r"\frac{dy}{dx} = f(x)g(y) \;\Rightarrow\; \int\frac{dy}{g(y)} = \int f(x)\,dx",
        "referanse": "Edwards & Penney (4. utg.), avsn. 1.4",
        "bruk": "Når en 1. ordens ligning kan skrives som et produkt av en x-del og en y-del.",
    },
    "O5": {
        "navn": "Lineær 1. ordens ODE (integrerende faktor)",
        "formel": r"y' + P(x)y = Q(x),\quad \mu(x) = e^{\int P(x)\,dx},\quad (\mu y)' = \mu Q",
        "referanse": "Edwards & Penney (4. utg.), avsn. 1.5",
        "bruk": "Når en 1. ordens ligning er lineær i y, men ikke separabel.",
    },
    # --- Lineær algebra -----------------------------------------------------
    "M1": {
        "navn": "Determinant (2x2)",
        "formel": r"\det\begin{pmatrix}a & b\\ c & d\end{pmatrix} = ad - bc",
        "referanse": "Edwards & Penney (4. utg.), avsn. 3.6",
        "bruk": "Når determinanten til en 2x2-matrise skal beregnes eller inverterbarhet vurderes.",
    },
    "M2": {
        "navn": "Invers av en 2x2-matrise",
        "formel": r"\begin{pmatrix}a & b\\ c & d\end{pmatrix}^{-1} = \frac{1}{ad-bc}\begin{pmatrix}d & -b\\ -c & a\end{pmatrix}",
        "referanse": "Edwards & Penney (4. utg.), avsn. 3.5",
        "bruk": "Når en 2x2-matrise skal inverteres (krever at determinanten ikke er 0).",
    },
    "M3": {
        "navn": "Egenverdiligningen",
        "formel": r"\det(A - \lambda I) = 0",
        "referanse": "Edwards & Penney (4. utg.), avsn. 6.1",
        "bruk": "Når egenverdiene til en kvadratisk matrise skal finnes.",
    },
    "M4": {
        "navn": "Gauss-eliminasjon (løsning av Ax = b)",
        "formel": r"[A\,|\,b] \longrightarrow \text{redusert trappeform} \longrightarrow x",
        "referanse": "Edwards & Penney (4. utg.), avsn. 3.2",
        "bruk": "Når et lineært likningssystem med flere ukjente skal løses systematisk.",
    },
    # --- Komplekse tall -----------------------------------------------------
    "K1": {
        "navn": "Eulers formel",
        "formel": r"e^{i\theta} = \cos\theta + i\sin\theta",
        "referanse": "Thomas' Calculus (14. utg.), appendiks 7; Edwards & Penney (4. utg.), avsn. 5.3",
        "bruk": "Når komplekse tall skal kobles mellom eksponentialform og trigonometrisk form.",
    },
    "K2": {
        "navn": "Polarform for komplekse tall",
        "formel": r"z = r(\cos\theta + i\sin\theta) = re^{i\theta},\quad r = |z|,\; \theta = \arg z",
        "referanse": "Thomas' Calculus (14. utg.), appendiks 7",
        "bruk": "Når et komplekst tall skal skrives på polarform, eller modulus og argument skal finnes.",
    },
    "K3": {
        "navn": "De Moivres formel",
        "formel": r"\left(re^{i\theta}\right)^{n} = r^{n}e^{in\theta} = r^{n}\left(\cos n\theta + i\sin n\theta\right)",
        "referanse": "Thomas' Calculus (14. utg.), appendiks 7",
        "bruk": "Når et komplekst tall skal opphøyes i en potens.",
    },
    "K4": {
        "navn": "n-te røtter av et komplekst tall",
        "formel": r"z^{1/n} = r^{1/n}e^{i\left(\theta + 2\pi k\right)/n},\quad k = 0, 1, \ldots, n-1",
        "referanse": "Thomas' Calculus (14. utg.), appendiks 7",
        "bruk": "Når alle n løsninger av zⁿ = w skal finnes.",
    },
    # --- Algebra og teoremer ------------------------------------------------
    "A1": {
        "navn": "Andregradsformelen",
        "formel": r"ax^2 + bx + c = 0 \;\Rightarrow\; x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}",
        "referanse": "Tekniske Tabeller (Johannessen), algebrakapittelet – kontroller sidetall i deres utgave",
        "bruk": "Når en andregradsligning skal løses, også den karakteristiske ligningen til en ODE.",
    },
    "P1": {
        "navn": "Pythagoras' læresetning",
        "formel": r"a^2 + b^2 = c^2",
        "referanse": "Euklid, Elementer, bok I, proposisjon 47",
        "bruk": (
            "Når sider i en rettvinklet trekant skal knyttes sammen – og som holdepunkt "
            "i bevisoppgaver. Merk: et bevis kan ikke verifiseres av SymPy."
        ),
    },
}


def kompakt_liste() -> str:
    """Formelsamlingen som én tekstblokk til systemprompten (ID, navn, formel, bruk, referanse)."""
    linjer = [
        f"{fid} | {f['navn']} | {f['formel']} | Bruk: {f['bruk']} | Ref: {f['referanse']}"
        for fid, f in FORMELSAMLING.items()
    ]
    return "\n".join(linjer)


def sla_opp(formel_id: str) -> dict | None:
    """Henter én formel, eller None hvis ID-en ikke finnes (da skal den avvises)."""
    if not isinstance(formel_id, str):
        return None
    formel = FORMELSAMLING.get(formel_id.strip().upper())
    if formel is None:
        return None
    return {"id": formel_id.strip().upper(), **formel}
