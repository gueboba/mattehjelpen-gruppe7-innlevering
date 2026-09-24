# MatteHjelpen 🧮🤖

Vår besvarelse av Innlevering 1 i KI-modulen i ING100 (HVL): en webapp som
løser førsteårsoppgaver i matematikk ved å la en språkmodell *resonnere* mens
SymPy *regner*, og som deretter prøver å *motbevise* sitt eget svar.

> **Modellen skal resonnere. Verktøyet skal regne. Vi står ansvarlige for
> svaret.**

Utgangspunktet er malen [sdy087/Innlevering1](https://github.com/sdy087/Innlevering1).
Oppgaveteksten ligger i [`OPPGAVE.md`](OPPGAVE.md), kravene i
[`SYSTEMBESKRIVELSE.md`](SYSTEMBESKRIVELSE.md) og vurderingsrubrikken i
[`EVALUERING.md`](EVALUERING.md).

| Del | Hvor |
|---|---|
| A: appen | `backend/`, `frontend/`, `tests/` |
| B: eksperimentet | [`EKSPERIMENT.md`](EKSPERIMENT.md), `scripts/eksperiment.py`, `eksperiment/resultater.jsonl` |
| C: refleksjonsnotatet | [`refleksjonsnotat/`](refleksjonsnotat/) |
| KI-bruk (dokumentasjon) | [`KI-BRUK.md`](KI-BRUK.md) |

## Kom i gang

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # fyll inn API-nøkkel, modellnavn og base-URL
uvicorn backend.main:app --reload
```

Åpne <http://localhost:8000>. Appen fungerer med et hvilket som helst
OpenAI-kompatibelt API; vi har brukt gratisnivåene hos **Groq** og
**OpenRouter**. Se `.env.example` for gratis-alternativer og grensene deres.

Sjekk at alt virker:

```bash
python scripts/selftest.py --strict          # 11 sjekker: format og kontrakt
python -m unittest discover -s tests -t .    # 177 tester: innhold og ærlighet
```

## Slik henger det sammen

```
Student  →  POST /solve  →  llm_client.solve_task()
                               ├─ systemprompt + formelsamling + verktøydefinisjoner
                               ├─ modellen ber om verktøykall  →  tools.py (SymPy, egen prosess)
                               └─ endelig svar som JSON
                            main.py  →  validator.validate()  →  numerisk kontroll
                            frontend/index.html  →  svar, steg, formler, verktøylogg, validering
```

| Fil | Ansvar |
|---|---|
| `backend/parsing.py` | Trygg tolking av matteuttrykk (vår egen modul, se under) |
| `backend/tools.py` | 10 SymPy-verktøy + `TOOL_DEFINITIONS`, kjørt med tids- og minnegrense |
| `backend/formelsamling.py` | 27 formler med navn, LaTeX, bruksområde og bok-/avsnittsreferanse |
| `backend/llm_client.py` | Systemprompt, tool-calling-løkke, formelkontroll, tokens og kostnad |
| `backend/validator.py` | Numerisk kontroll som prøver å motbevise svaret |
| `backend/main.py` | FastAPI: `POST /solve`, `GET /status`, ærlig feilhåndtering |
| `frontend/index.html` | Grensesnitt: tolkning, steg, formler, verktøylogg, validering, kostnad |
| `scripts/eksperiment.py` | Kjører Del B og lager tabellen til `EKSPERIMENT.md` |
| `tests/` | 177 enhetstester (parsing, verktøy, validator, klient, API, fasitsjekk) |

## Det vi har lagt mest vekt på

**1. Verktøyet regner – og vi kan bevise det.** Verktøyloggen i grensesnittet
bygges av de faktiske `tool_calls`, ikke av det modellen skriver. Påstår
modellen at den har brukt SymPy uten å ha gjort det, sier appen fra.

**2. Validering som prøver å motbevise svaret.** Validatoren bruker en annen
metode enn den som lagde svaret: numerisk derivasjon mot derivasjon og
integrasjon, numerisk integrasjon (`mpmath.quad`) mot bestemte integraler,
innsetting i ligningen for ligninger og ODE-er, og `A·A⁻¹ = I`, `A·x = b` og
`det(A − λI) = 0` for matriser.

**3. Ærlighet framfor grønt lys.** Valideringen har tre utfall: kontrollert,
kontroll slo feil, og *ikke mulig å verifisere*. Bevisoppgaver havner i den
siste – appen later aldri som SymPy har sjekket noe SymPy ikke kan sjekke.
Tokenforbruk og kostnad vises som «ukjent» når vi ikke vet, ikke som 0.

**4. Vi viser tolkningen.** Validering skjer mot oppgaven *slik modellen
tolket den*. Derfor står tolkningen ved siden av svaret, og tvetydig notasjon
som `sin^-1(x)` avvises av verktøyene med en beskjed om å velge `asin(x)`
eller `1/sin(x)` – appen gjetter ikke på studentens vegne.

**5. Vi stoler ikke på teksten som kommer inn.** `sympify` kjører Python-kode
under panseret. `backend/parsing.py` fjerner Pythons innebygde funksjoner fra
navnerommet, avviser `__`-navn, strenger og ukjente metodekall, og stopper
tallpotenser og fakulteter som ville tatt evigheter (`9**9**9` henger SymPy).
Hvert verktøykall kjøres i en egen prosess med 25 sekunders tidsgrense og
2 GB minnetak.

## Eksperimentbryterne (Del B)

| Bryter | Hvor | Hva den viser |
|---|---|---|
| 1 | `USE_TOOLS` i `backend/llm_client.py` | Feilraten uten SymPy |
| 2 | `FORKLAR_STEG` i `backend/llm_client.py` | Appen blir en svart boks |
| 3 | `MODEL_NAME` i `.env` (eller `--modeller`) | Kvalitet, tid og pris |
| 4 | Oppgaven «Deriver sin^-1(x)» | Riktig regning på feil problem |
| 5 | Oppgaven «Vis at den deriverte av x^3 er 2x^2» | Hvordan appen håndterer at kontrollen slår feil |

```bash
python scripts/eksperiment.py              # 10 oppgaver x 2 modeller x med/uten verktøy
python scripts/eksperiment.py --aha        # bryter 4 og 5
python scripts/eksperiment.py --uten-steg  # bryter 2
python scripts/eksperiment.py --rapport    # tabell og kostnadstall
```

Resultatene ligger som én JSON-linje per kjøring i
`eksperiment/resultater.jsonl`, slik at tallene i `EKSPERIMENT.md` kan
etterprøves.

## Gratisprinsipp og hemmeligheter

Hele oppgaven er gjennomført på gratisnivåer, uten kredittkort. Kostnadstallene
i appen er *listepris for tilsvarende betalt modell*, slik at vi kan svare på
«hva ville dette kostet i skala?» – vi har ikke betalt noe.

API-nøkler ligger kun i `.env`, som står i `.gitignore`. De skal aldri limes
inn i en ekstern chat eller sjekkes inn.
