# Eksperimentlogg (Del B)

## Slik er eksperimentet gjort

De samme 10 oppgavene er kjørt med **to modeller av svært ulik størrelse**,
**med og uten verktøy**. I tillegg kommer en tredje modell, et tilleggssett med
fire tyngre oppgaver, og egne kjøringer for aha-bryterne. Til sammen **52
kjøringer**, alle logget i `eksperiment/resultater*.jsonl`.

| | Modell | Leverandør | Aktive/totale parametere | Listepris (inn/ut per Mtok) |
|---|---|---|---|---|
| Modell A (stor) | `nvidia/nemotron-3-ultra-550b-a55b:free` | OpenRouter, gratisnivå | 55B aktive / 550B | $0,60 / $2,40 |
| Modell B (liten) | `openai/gpt-oss-20b` | Groq, gratisnivå | 3,6B aktive / 21B | $0,075 / $0,30 |
| Tredje modell | `openai/gpt-oss-120b` | Groq, gratisnivå | 5,1B aktive / 117B | $0,15 / $0,60 |

Den tredje modellen kom vi bare delvis gjennom: **døgnkvoten tok slutt**. Groqs
gratisnivå gir 200 000 tokens per modell per døgn, og med rundt 12 000 tokens
per oppgave med verktøy holder det til omtrent 16 kjøringer. Det er i seg selv
et resultat, og grunnen til at hovedtabellen bruker modell A og B – som ligger
hos hver sin leverandør, og dermed har hver sin kvote.

Alt er kjørt på gratisnivå. Vi har ikke betalt noe; prisene over er listepris
for de betalte variantene og brukes bare til å regne ut hva forbruket *ville*
kostet (kilder kontrollert 20.09.2026: OpenRouters `/api/v1/models` og Groqs
prisliste). Kurs: 1 USD = 9,41 NOK (midtkurs 19.09.2026, wise.com).

Hver kjøring er gjort med `scripts/eksperiment.py`, som lagrer én JSON-linje per
kjøring med oppgave, modell, bryterstilling, tolkning, svar, verktøykall,
validering, tokenforbruk, kostnad og tid.

### To kolonner som ikke betyr det samme

- **riktig** = svaret sammenlignet med fasit vi selv regnet ut på forhånd (se
  `OPPGAVER` i `scripts/eksperiment.py`).
- **validert** = appens egen kontroll: stemmer svaret med oppgaven *slik
  modellen tolket den*?

Et svar kan være «validert» og likevel feil. Det er hele poenget med
aha-bryter 4.

### De ti oppgavene

| # | Oppgave | Fagområde | Fasit |
|---|---------|-----------|-------|
| 1 | Deriver f(x) = x²·sin(3x) | derivasjon (produkt + kjerne) | 2x·sin(3x) + 3x²·cos(3x) |
| 2 | Finn det ubestemte integralet av x·e^(2x) | delvis integrasjon | (2x−1)e^(2x)/4 + C |
| 3 | Beregn ∫₀¹ x³/(x²+1) dx | bestemt integral, brøkuttrykk | ½ − ½ln2 ≈ 0,153426 |
| 4 | Løs x² − 2x − 8 = 0 | andregradsligning (regnet for hånd) | x = −2 og x = 4 |
| 5 | Løs systemet x + y/2 + z/3 = 1, x/2 + y/3 + z/4 = 1, x/3 + y/4 + z/5 = 1 | lineært system med brøker (Hilbert) | x = 3, y = −24, z = 30 |
| 6 | Generell løsning av y'' + 2y = 0 | 2. ordens ODE, komplekse røtter | C₁cos(√2x) + C₂sin(√2x) |
| 7 | Løs y'' − 3y' + 2y = 0 med y(0)=1, y'(0)=0 | ODE med startbetingelser | 2eˣ − e^(2x) |
| 8 | Egenverdiene til [[2,1,0],[1,3,1],[0,1,4]] | lineær algebra | 3, 3−√3, 3+√3 |
| 9 | Skriv (1 + i√3)⁷ på formen a + bi | komplekse tall, De Moivre | 64 + 64√3·i |
| 10 | Bevis Pythagoras' læresetning | bevis – kan ikke verktøy-verifiseres | vurderes manuelt |

Oppgave 4 kan regnes for hånd, oppgave 5 og 8 har «stygge» tall der små
regnefeil blir synlige, og oppgave 10 er tatt med for å se om appen er ærlig om
at et bevis ikke er verifisert av et verktøy.

## Resultattabell

Format i hver celle: **riktig / validert / tokens**. «n/a» betyr at appen ikke
kunne kontrollere svaret og sa fra om det (ikke at kontrollen slo feil).

| # | Oppgave | Modell A + verktøy | Modell A uten | Modell B + verktøy | Modell B uten | 120b + verktøy | Kommentar |
|---|---|---|---|---|---|---|---|
| 1 | Derivasjon | ja/ja/12 614 | ja/ja/4 641 | ja/ja/11 214 | ja/ja/4 212 | ja/ja/11 169 | Alle bruker produkt- og kjerneregel |
| 2 | Ubestemt integral | ja/ja/12 639 | ja/ja/4 777 | ja/ja/10 649 | ja/**n/a**/5 542 | ja/ja/11 064 | B skrev «x*e^(2x)» – riktig svar, men uleselig for validatoren |
| 3 | Bestemt integral | ja/ja/13 245 | ja/ja/5 136 | ja/ja/12 219 | ja/ja/4 711 | ja/ja/12 710 | |
| 4 | Andregradsligning | ja/ja/12 422 | ja/ja/4 436 | ja/ja/9 946 | ja/ja/4 196 | ja/ja/11 187 | Den vi kunne regne for hånd |
| 5 | Hilbert-system | ja/ja/13 247 | ja/ja/6 546 | ja/ja/18 093 | ja/**n/a**/11 549 | – | B brukte 4 verktøykall her |
| 6 | Generell ODE | ja/ja/12 921 | ja/ja/4 538 | ja/ja/10 195 | ja/ja/4 624 | ja/ja/11 883 | |
| 7 | ODE med startbetingelser | ja/ja/13 076 | ja/ja/5 194 | ja/ja/18 563 | ja/ja/5 355 | ja/ja/11 093 | |
| 8 | Egenverdier | ja/ja/12 726 | ja/ja/6 460 | ja/ja/12 117 | – | – | |
| 9 | Komplekse tall | ja/ja/13 241 | ja/ja/5 426 | ja/ja/29 885 | – | – | B trengte flest tokens av alle |
| 10 | Bevis (Pythagoras) | manuell/**n/a**/6 482 | manuell/**n/a**/4 442 | manuell/**n/a**/6 534 | – | – | Alle sa ærlig fra at beviset ikke er verktøyverifisert |

Tomme celler er kjøringer vi ikke fikk plass til innenfor gratiskvotene samme
døgn (se avsnittet om døgnkvoten over).

### Per modell og bryterstilling

| Modell | Verktøy | Kjøringer | Riktige | Validert | Tokens totalt | Snitt | Kostnad (listepris) | Snitt tid |
|---|---|---|---|---|---|---|---|---|
| Modell A (550B) | på | 10 | 9/9 | 9/10 | 122 613 | 12 261 | $0,096 = 0,90 kr | 23 s |
| Modell A (550B) | av | 10 | 9/9 | 9/10 | 51 596 | 5 159 | $0,058 = 0,55 kr | 23 s |
| Modell B (21B) | på | 10 | 9/9 | 9/10 | 139 415 | 13 941 | $0,013 = 0,13 kr | 52 s |
| Modell B (21B) | av | 7 | 7/7 | 5/7 | 40 189 | 5 741 | $0,005 = 0,05 kr | 6 s |
| 120b | på | 6 | 6/6 | 6/6 | 69 106 | 11 517 | $0,013 = 0,12 kr | 33 s |

## Kostnadsberegning

- Totalt tokenforbruk for de 10 oppgavene med verktøy: **122 613** (modell A) og
  **139 415** (modell B).
- Snitt per oppgave med verktøy: **12 736 tokens**, som med listepris blir
  **0,0047 USD ≈ 0,044 kr**.
- Uten verktøy: rundt **5 200–5 700 tokens** per oppgave, altså under halvparten.
  Verktøybruken koster omtrent **2,4 ganger så mange tokens**, fordi
  verktøydefinisjonene sendes med og hver runde gjentar hele samtalen.

**Ekstrapolert til 1000 studenter × 50 oppgaver = 50 000 oppgaver:**

| Modell | Tokens per oppgave | Tokens totalt | Kostnad |
|---|---|---|---|
| Modell A (550B) | 12 261 | 613 millioner | **$477 ≈ 4 490 kr** |
| 120b | 11 518 | 576 millioner | **$110 ≈ 1 030 kr** |
| Modell B (21B) | 13 942 | 697 millioner | **$66 ≈ 620 kr** |

Den lille modellen bruker altså *flere* tokens per oppgave enn den store (den
prater mer og gjør flere verktøykall), men koster likevel sju ganger mindre,
fordi prisen per token er så mye lavere. Det interessante er at hele emnet
kunne kjørt på under tusen kroner med en mellomstor modell – og at ingen av
oss betalte noe, fordi gratisnivåene holdt. Regningen kommer først hvis appen
skal være tilgjengelig for alle, hele tiden.

## Tilleggssett: fire tyngre oppgaver

De ti pensumoppgavene skilte nesten ikke mellom «med» og «uten» verktøy: begge
modeller klarte det meste på egen hånd. Derfor la vi til fire oppgaver som er
tunge å regne i hodet, og kjørte dem med modell A:

| # | Oppgave | Uten verktøy | Med verktøy |
|---|---|---|---|
| 201 | Determinanten til 4×4-Hilbertmatrisen (fasit 1/6 048 000) | ja/ja/9 873 | – |
| 202 | 87 654 321 × 12 345 679 eksakt | **NEI/NEI**/6 104 | ja/ja/14 662 |
| 203 | ∫₀¹ x⁴(1−x)⁴/(1+x²) dx (fasit 22/7 − π) | ja/ja/6 895 | – |
| 204 | y'' + 3y' + 2y = e^(−x), y(0)=0, y'(0)=1 | ja/ja/5 966 | – |

Oppgave 202 er hele eksperimentet i miniatyr. Uten verktøy svarte modellen

```
1 082 152 022 374 659      (modellens svar)
1 082 152 110 028 959      (riktig)
```

Feilen er på nesten 88 millioner, men svaret ser helt rimelig ut: riktig antall
siffer, riktige første siffer, ingen forbehold i teksten. Valideringen vår slo
rødt fordi den regnet ut produktet selv. Med verktøy kalte den samme modellen
`calculate("87654321 * 12345679")` og fikk eksakt riktig svar, validert.

At den *samme* modellen klarte 4×4-determinanten med brøker og det klassiske
22/7 − π-integralet uten verktøy, men bommet på et vanlig produkt, sier noe om
hvor feilene sitter: ikke i «vanskelig matematikk», men i siffer-for-siffer-
regning der det ikke finnes et mønster å gjenkjenne.

## Dekning: hva vi faktisk rakk å måle

Rutenettet er 3 modeller × 10 oppgaver × verktøy av/på = 60 celler. Vi har 43.
Hullet er i de to Groq-modellene, og det kom i to trinn: først tok **døgnkvoten**
slutt midt i kjøringen (avsnittet øverst), og da vi senere forsøkte å fullføre,
svarte Groq **HTTP 403 «Access denied. Please check your network settings.»** –
samme svar for alle nøklene våre, også en nøkkel som hører til en annen
leverandør. Det siste er altså en nettverksblokkering hos Groq, ikke en
oppbrukt kvote og ikke en avvist nøkkel.

Kravet i oppgaven er minst 40 kjøringer, så det er oppfylt. Men det er verdt å
si tydelig fra om hva tallene *ikke* dekker: sammenligningen «med mot uten
verktøy» hviler i praksis på nemotron, som er den eneste modellen med fullt
sett. For gpt-oss-120b finnes det ingen kjøringer uten verktøy i det hele tatt,
så påstander om hva *den* modellen gjør uten SymPy, har vi ikke dekning for.

| Oppgave | gpt-oss-120b | gpt-oss-20b | nemotron-3-ultra-550b-a55b |
|---|---|---|---|
| 1 | med ✓ / uten – | med ✓ / uten ✓ | med ✓ / uten ✓ |
| 2 | med ✓ / uten – | med ✓ / uten ✓ | med ✓ / uten ✓ |
| 3 | med ✓ / uten – | med ✓ / uten ✓ | med ✓ / uten ✓ |
| 4 | med ✓ / uten – | med ✓ / uten ✓ | med ✓ / uten ✓ |
| 5 | med – / uten – | med ✓ / uten ✓ | med ✓ / uten ✓ |
| 6 | med ✓ / uten – | med ✓ / uten ✓ | med ✓ / uten ✓ |
| 7 | med ✓ / uten – | med ✓ / uten ✓ | med ✓ / uten ✓ |
| 8 | med – / uten – | med ✓ / uten – | med ✓ / uten ✓ |
| 9 | med – / uten – | med ✓ / uten – | med ✓ / uten ✓ |
| 10 | med – / uten – | med ✓ / uten – | med ✓ / uten ✓ |

Manglende celler: alle «uten verktøy» for gpt-oss-120b (10), fire «med
verktøy» for samme modell (oppgave 5, 8, 9, 10), og tre «uten verktøy» for
gpt-oss-20b (oppgave 8, 9, 10).

## Observasjoner fra aha-bryterne

### 1. Verktøy av (USE_TOOLS = False)

På pensumoppgavene: ingen målbar forskjell i riktighet. Nemotron, den eneste
modellen vi fikk kjørt hele settet på, svarte riktig på alle de ni
maskinsjekkbare oppgavene både med og uten SymPy, og gpt-oss-20b svarte riktig
på alle de sju vi rakk uten verktøy (se «Dekning» nedenfor). Ingen av
modellene ble altså målt til å bli dårligere uten verktøy. Det var ikke det vi
forventet, og det er verdt å si tydelig fra om: **2026-modeller klarer mye
førsteårsmatematikk uten hjelp.**

Tre forskjeller fant vi likevel:

1. **Tokenforbruket halveres** uten verktøy (5 200 mot 12 700 i snitt).
2. **Etterprøvbarheten forsvinner.** Uten verktøykall har vi ingenting å
   kontrollere svaret mot annet enn modellens egen tekst – og appen sier fra
   med en advarsel når teksten nevner SymPy uten at noe verktøy ble kalt.
3. **Valideringsdekningen blir dårligere:** med verktøy kunne appen kontrollere
   9 av 10 svar, uten verktøy bare 5 av 7 for den lille modellen. Grunnen er at
   modellen uten verktøy oftere skriver problemet på en form validatoren ikke
   kan tolke (f.eks. «x*e^(2x)»).

Og så, i tilleggssettet: der feilet den uten verktøy (oppgave 202).

### 2. Uten kravet om stegvis forklaring (FORKLAR_STEG = False)

Vi kjørte oppgave 1 og 6 på nytt med `FORKLAR_STEG = False`. Da forsvinner både
setningen «Forklar hvert steg pedagogisk på norsk ...» fra systemprompten og
hele `[STEG]`-feltet fra svarformatet.

Resultatet overrasket oss: den store modellen forklarte i fire steg likevel,
med formel-ID-er, helt uoppfordret (13 925 og 14 202 tokens, begge riktige).
Bryteren gjorde altså ikke appen til en svart boks av seg selv. Det som
forsvant, var *garantien*: vi ba ikke lenger om etterprøvbarhet, vi håpet på
den. En annen modell – eller den samme modellen på en annen dag – kan like
gjerne svare med ett tall.

Det er verdt å merke seg hva som IKKE forsvant: verktøykallene og valideringen
lå fast, fordi de er kode og ikke prompt. Den delen av etterprøvbarheten som er
bygget inn i appen, tåler at prompten endres. Den delen som avhenger av at
modellen er samarbeidsvillig, gjør det ikke.

### 3. Modellbytte

Vi byttet både modell og leverandør (`MODEL_NAME` og `API_BASE_URL` i `.env`):

| | Modell A (550B, OpenRouter) | Modell B (21B, Groq) | 120b (Groq) |
|---|---|---|---|
| Riktige med verktøy | 9/9 | 9/9 | 6/6 |
| Snitt tokens | 12 261 | 13 941 | 11 517 |
| Snitt tid | 23 s | 52 s | 33 s |
| Kostnad per 50 000 oppgaver | 4 490 kr | 620 kr | 1 030 kr |

Kvaliteten skilte lite på disse oppgavene; pris og tid skilte mye. Den store
modellen var raskest per svar til tross for størrelsen (OpenRouter ga oss
raskere svar enn Groqs gratiskø), men er sju ganger dyrere i listepris.

Et praktisk funn på kjøpet: **samme prompt oppførte seg ulikt hos de to
leverandørene.** gpt-oss-modellene på Groq prøvde å levere sluttsvaret som et
verktøykall de kalte `json`, og da avviste Groq hele forespørselen. Vi måtte
endre svarformatet fra JSON til merkelapper i ren tekst for å få appen til å
virke begge steder (se `backend/llm_client.py`). Å «bytte modell i .env» er
altså ikke alltid gratis.

### 4. Tvetydig oppgave: «Deriver sin^-1(x)»

Her gjorde appen nøyaktig det vi håpet, og resultatet er likevel urovekkende.

Verktøyet vårt nekter å tolke `sin^-1`: parseren avviser notasjonen og ber
modellen velge `asin(x)` eller `1/sin(x)` eksplisitt. Modellen skrev i
tolkningsfeltet at notasjonen kan bety begge deler, at standard notasjon som
regel betyr arcsin, og at den valgte arcsin. Deretter kalte den
`derive("asin(x)")`, fikk `1/sqrt(1 - x**2)`, og valideringen bekreftet svaret
i tre punkter.

Hele kjeden er korrekt – og svaret kan likevel være feil svar på studentens
spørsmål. Studenten som mente `1/sin(x)`, får grønt lys på et problem hen ikke
stilte. Det er derfor tolkningen står ved siden av svaret i grensesnittet, og
grunnen til at «validert» aldri kan bety «riktig».

### 5. Fremprovosert valideringsfeil

Første forsøk var å be appen «vis at den deriverte av x^3 er 2x^2». Modellen
nektet å gå med på premisset, svarte at den deriverte er `3x^2`, og
valideringen bekreftet det svaret. Godt oppførsel, men ingen rød validering.

Den ekte valideringsfeilen kom av seg selv i tilleggssettet: oppgave 202 uten
verktøy. Da viste appen rød banner med teksten «Uttrykket har verdien
1.08215e+15, men svaret er 1.08215e+15 (avvik 8.76543e+7)» – og her ser man
både styrken og svakheten i grensesnittet vårt: kontrollen fanget feilen, men
avrundingen i visningen gjør at de to tallene ser like ut. Det er et konkret
punkt vi ville rettet: vis flere siffer når to tall er nesten like.

## Hva vi lærte om vår egen app

1. **Verktøyloggen er det eneste vi vet er sant.** Den bygges fra faktiske
   `tool_calls`. I flere kjøringer nevnte modellen SymPy i teksten uten å ha
   kalt noe verktøy, og da sier appen fra.
2. **Formelkontrollen sjekker eksistens, ikke relevans.** I én kjøring oppga
   modellen kvotientregelen (D3) i en oppgave som bare krevde produkt- og
   kjerneregelen. ID-en var gyldig, referansen ekte – og formelen feil valgt.
3. **Validering er en dekningsgrad, ikke en garanti.** Appen kontrollerte 38 av
   43 hovedkjøringer. De fem resterende var tre bevisoppgaver (kan ikke
   kontrolleres) og to svar modellen skrev på en form validatoren ikke kunne
   tolke. Vi har siden gjort validatoren mer tolerant, men tallene i tabellen
   er slik de faktisk ble målt.
4. **Gratisnivået former appen.** 8 000 tokens per minutt og 200 000 per døgn
   betyr at appen må stå i kø, og at en oppgave med mange verktøykall kan bli
   for stor for kvoten. Vi måtte legge inn ventelogikk og en eksplisitt
   grense for svarlengde for å komme gjennom – det er ikke matematikk, men det
   er en del av å bygge noe som faktisk virker.

## Slik kan tallene etterprøves

Hver kjøring ligger som én JSON-linje i `eksperiment/resultater*.jsonl` med
oppgave, modell, bryterstilling, tolkning, svar, verktøykall, validering,
tokenforbruk, kostnad og tid. Tabellene er laget med:

```bash
python scripts/eksperiment.py --rapport
```

Kjøringene kan gjentas med `python scripts/eksperiment.py` (fortsetter der den
slapp), `--aha` for bryter 4 og 5, `--uten-steg` for bryter 2 og `--ekstra` for
tilleggssettet. Merk at svarene fra en språkmodell ikke er fullstendig
reproduserbare selv med temperatur 0.
