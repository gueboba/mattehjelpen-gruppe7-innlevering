# Prompt: `backend/tools.py` – deterministiske SymPy-verktøy

**Fil:** `backend/tools.py`
**Prinsipp:** Modellen skal ALDRI late som den har beregnet noe et verktøy
kunne gjort. All BEREGNING (derivasjon, ligninger, matriser, komplekse tall)
skjer her, med SymPy – bevis og begrepsforklaringer er en annen kategori
(se `PROMPTS/03_llm_client.md`).

## Krav (fast – ikke forhandlingsbart)

Implementer disse funksjonene med SymPy, med **akkurat** disse navnene og
parameterne (llm_client og resten av appen forventer disse signaturene):

- `derive(uttrykk: str, variabel: str = "x") -> dict`
- `integrate(uttrykk: str, variabel: str = "x") -> dict`
- `solve_equation(ligning: str, variabel: str = "x") -> dict`
- `solve_ode(ligning: str) -> dict`
- `matrix_op(operasjon: str, matrise: list) -> dict`
- `complex_op(operasjon: str, tall: str) -> dict`

Hver funksjon returnerer `{"resultat": str, "latex": str}` ved suksess.

Definer også `TOOL_DEFINITIONS`: en liste med JSON-schema (OpenAI
function-calling-format) som beskriver disse 6 verktøyene, til bruk i
`llm_client.py`.

## Hva disse verktøyene ikke dekker

De 6 funksjonene dekker konkrete beregninger. De dekker IKKE bevisoppgaver
(f.eks. «bevis Pythagoras' læresetning») eller begrepsforklaringer – det er
like fullt legitime matteoppgaver, bare ikke noe SymPy kan «regne ut». Dere
står fritt til å utvide `tools.py` med flere funksjoner senere (f.eks.
grenseverdier, serieutvikling) – hold da samme mønster:
`{"resultat": str, "latex": str}`, og oppdater `TOOL_DEFINITIONS` tilsvarende.

## [FYLL INN SELV] – ta stilling til dette FØR dere sender prompten

- Hvilke feilsituasjoner skal funksjonene håndtere eksplisitt? (F.eks. ugyldig
  syntaks, deling på null, matrise med feil dimensjoner.) Skriv egen liste:

  **Vårt svar:** ugyldig syntaks (inkludert implisitt multiplikasjon som «2x»),
  ukjent funksjonsnavn, tvetydig notasjon (`sin^-1`, `sin^2(x)`), ugyldig eller
  reservert variabelnavn, deling på null (`zoo`/`nan`), singulær matrise ved
  invers, matriser med ulik radlengde, ikke-kvadratisk matrise ved
  determinant/egenverdier, feil dimensjon mellom A og b i A·x = b, integraler
  og differensialligninger SymPy ikke får løst, tomme løsningsmengder, ugyldig
  retning i grenseverdi, startbetingelser av høyere orden enn ligningen,
  beregninger som bruker for lang tid eller for mye minne, og forsøk på å
  smugle Python-kode inn i et «matteuttrykk» (`__import__`, `open`,
  attributtilgang, tall med millioner av sifre).

- Skal feil kastes som exceptions, eller returneres som del av dict
  (f.eks. `{"feil": "..."}`)? Bestem selv og vær konsekvent – dette påvirker
  hvordan `main.py` må håndtere det.

  **Vårt svar:** funksjonene **kaster** `VerktoyFeil`/`TolkningsFeil` med en
  forklarende norsk melding. Bare innpakningen `kjor_verktoy(...)` oversetter
  til `{"feil": "..."}`, som er det modellen får tilbake i samtalen. Da ser
  enhetstestene den ekte feilen, modellen får en melding den kan rette opp
  etter, og `main.py` slipper å kjenne feiltypene til ti ulike verktøy.

- Hvor «smart» skal parsing av matteuttrykk være? (F.eks.: skal `sin^-1(x)`
  tolkes som invers funksjon eller som potens? Dette er et av
  «aha-punktene» i `OPPGAVE.md` – bestem en tolkning og vær eksplisitt om
  den i koden/docstringen.)

  **Vårt svar:** streng SymPy-syntaks, med tre bevisste unntak: `^` godtas som
  potens, vanlige Unicode-tegn (−, ·, ², π, ∞) normaliseres, og primtegn i
  differensialligninger (`y'' + 2y = 0`) skrives om til `Derivative(...)`.
  `sin^-1(x)` **avvises** med en melding som ber modellen velge `asin(x)`
  eller `1/sin(x)` og fortelle brukeren hvilken tolkning den valgte –
  verktøyet skal aldri gjette på vegne av studenten. Implisitt multiplikasjon
  (`2x`) avvises også: SymPys egen variant gjør `y(x)` om til `y*x` og
  ødelegger differensialligninger (det testet vi). Se `backend/parsing.py`.

## Ferdig prompt å lime inn (etter at dere har fylt inn over)

```
Implementer backend/tools.py i et FastAPI/SymPy-prosjekt. Funksjonene som
skal implementeres er: derive, integrate, solve_equation, solve_ode,
matrix_op, complex_op (se signaturer og docstrings i filen). Bruk sympy.
Returner alltid {"resultat": str, "latex": str} ved suksess.

Feilhåndtering: [LIM INN SVARET DERES FRA "FYLL INN SELV" OVER]

Legg også til TOOL_DEFINITIONS: en liste med JSON-schema for OpenAI
function-calling som beskriver disse 6 funksjonene (navn, beskrivelse,
parametere med typer).

Skriv en kort forklarende docstring per funksjon, på norsk.
```

## Kvalitetssjekk før du limer inn koden

- [ ] Alle 6 funksjonsnavn og parametere er UENDRET fra skjelettet.
- [ ] Ingen `NotImplementedError` igjen.
- [ ] `TOOL_DEFINITIONS` finnes og er en liste.
- [ ] Dere forstår hvordan feil håndteres, og det stemmer med det dere
      bestemte i «FYLL INN SELV» over.
- [ ] Kjør `python scripts/selftest.py` – tools-sjekkene bør nå vise ✅.
