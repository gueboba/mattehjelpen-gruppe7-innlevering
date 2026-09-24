# Dokumentasjon av KI-bruk

Oppgaven tillater KI i alle deler av arbeidet, men krever at bruken
dokumenteres og at vi kan forklare og innestå for alt vi leverer
(`OPPGAVE.md`). Dette dokumentet sier hva som faktisk er gjort av hvem.

## 1. KI brukt til å BYGGE appen (utviklingsverktøy)

| Hva | Verktøy/modell | Omfang |
|---|---|---|
| Lese og tolke oppgaven, planlegge arkitektur | Opus 5 | Leste `OPPGAVE.md`, `SYSTEMBESKRIVELSE.md`, `EVALUERING.md` og `PROMPTS/` |
| `backend/parsing.py`, `tools.py`, `formelsamling.py`, `validator.py`, `llm_client.py`, `main.py` | Opus 5 | Skrev koden, kjørte og rettet feil underveis |
| `frontend/index.html` | Opus 5 | Bygget om malens frontend (tolkning, verktøylogg, tre valideringstilstander) |
| `tests/` (177 tester) og `scripts/eksperiment.py` | Opus 5 | Skrev testene og eksperimentkjøreren |
| Utkast til `README.md`, `EKSPERIMENT.md`, `KI-BRUK.md` og refleksjonsnotatet | Opus 5 | Utkast, bearbeidet av gruppen |

Feil KI-en gjorde underveis, og som ble funnet ved testing (eksempler vi tok
vare på fordi de er relevante for Del C):

1. Første forsøk brukte SymPys `implicit_multiplication`, som gjør `y(x)` om
   til `y*x`. Da ble alle differensialligninger stille ødelagt. Oppdaget ved
   en test, ikke ved lesing av koden.
2. Første sikkerhetssjekk mot gigantiske tall lot SymPy regne ut tallet den
   skulle stoppe (`9**9**9**9` hang hele prosessen). Løsningen ble å analysere
   uttrykket på AST-nivå med logaritmer før SymPy ser det.
3. Tidsgrensen for verktøykall ble først laget med `multiprocessing`, som
   feilet når `__main__` ikke kan importeres. Byttet til en egen delprosess.
4. Validatoren hentet først de vilkårlige konstantene (C1, C2) fra residualet.
   For en riktig løsning er residualet eksakt 0, og da forsvinner konstantene –
   valideringen svarte «ikke mulig» for helt riktige svar.
5. Fasitsjekken i eksperimentet var for streng: da modellen svarte med
   løsningsvektoren `[3, -24, 30]` i stedet for `x = 3, y = -24, z = 30`, ble
   et riktig svar først registrert som feil.

Poenget med listen: alle fem feilene ga kode som *så* riktig ut. Fire av dem
ble funnet fordi vi kjørte tester, ikke fordi noen leste seg til dem.

### Systematisk feiljakt etter at appen var «ferdig»

Da appen var ferdig, testet og pushet, gikk vi en runde til – denne gangen med
den hensikten å *ødelegge* den i stedet for å få den til å virke. Metoden var
fire egne prøveskript: ~50 ondsinnede input mot parseren, ~80 kall med feil
argumenttyper mot verktøyene, systematiske forstyrrelser av riktige svar mot
validatoren (140 kontroller), og rare forespørsler og ødelagte modellsvar mot
API-et. Det fant åtte feil til, hvorav to alvorlige:

6. **Validatoren godkjente eksakte svar som var millioner unna.** Toleransen
   var rent relativ (1e-8), og på store tall blir den enorm. Vi målte det
   største avviket som fortsatt ga grønt lys, og fikk:

   | fasit | største godkjente feil |
   |---|---|
   | 87 654 321 × 12 345 679 = 1 082 152 110 028 959 | 10 821 500 |
   | 20! = 2 432 902 008 176 640 000 | 24 329 000 000 |
   | 10²⁰ | 10¹² |

   Det verste er at dette rammet nøyaktig det eksempelet vi bruker i
   refleksjonsnotatet: svarte modellen ett for lite, sa appen «validert».
   Rettingen er å sammenligne eksakte tall *eksakt* – en brøk er enten lik
   eller ulik, og da hører toleranse ikke hjemme.
7. **Én forespørsel kunne låse appen for godt.** Sjekken av om modellens svar
   stemmer med verktøyenes resultat kalte `sp.simplify`, som ikke har noen
   øvre kjøretid, og den lå rett i web-tråden uten tidsgrense. Med
   `sin(sin(…sin(x)…))` målte vi 1,6 s på 10 nivåer, 5,4 s på 12, 21 s på 14 –
   og på 16 nivåer kom den aldri tilbake. Rettingen er en størrelsessperre
   pluss samme delprosess med tidsgrense som resten av valideringen bruker.
8. De seks øvrige: Pydantics egne 422-svar brøt API-kontrakten (frontend
   krasjet i stedet for å vise feilen); `NaN` i kostnadsfeltet ga 500 fordi
   `NaN` ikke er gyldig JSON; `complex_op` med 1000 røtter brukte 31 sekunder;
   et svar som manglet den ene av to røtter fikk grønt banner med advarselen
   gjemt under; desimalslakken var 0,75 i siste siffer og slapp gjennom et
   *feil avrundet* svar; og `_grunnsvar` garanterte at feltene fantes, men
   ikke at de hadde riktig type.

Deretter stilte vi det spørsmålet vi synes er farligst av alle: finnes det
input appen *godtar*, men tolker som noe annet enn det brukeren mente? En
avvist input er ufarlig – brukeren får beskjed. En stille feiltolkning gir
riktig regning på feil problem, og valideringen kontrollerer mot det samme
feile problemet. Det fant tre til:

9.  **Norsk desimalkomma.** `0,5` ble Python-tuppelen `(0, 5)`. Uttrykket
    `x**2 + 0,5*x` ble til to uttrykk, `[x**2, 5*x]`. I en norskspråklig app
    for norske studenter er dette noe av det mest nærliggende en bruker kan
    skrive. Nå sier appen fra med en melding som forteller både hva som er
    galt og hva man skal gjøre – uten å ødelegge kommaet der det faktisk
    skiller elementer (`[[1,2],[3,4]]`, `Integral(x, (x, 0, 1))`).
10. **Usynlige tegn fra kopiert tekst.** Limer man inn en oppgave fra en PDF
    eller en nettside, følger det ofte med harde mellomrom (U+00A0) eller
    nullbredde-mellomrom (U+200B). `x + 1` ble da `Symbol('x\xa0') +
    Symbol('\xa01')` – to tullesymboler. Konsekvensen var at appen meldte at
    et *helt riktig* svar var feil, fordi `x` og `x␣` er to ulike symboler.
11. **Rottegnet.** `√4` ble `Symbol('√4')` – et navn som ser ut som en
    kvadratrot i utskriften, men ikke er det. Nå blir det `2`, og symboler med
    tegn vi ikke kjenner igjen avvises i stedet for å bli gjettet på.

Ingen av de tre ga grønt lys på et galt svar – det verste utfallet var «ikke
mulig å kontrollere» eller et underkjent riktig svar. Men det er flaks, ikke
design: alle tre var stille.

Til slutt snudde vi sjekken mot vårt eget bevismateriale: fasitsjekken i
`scripts/eksperiment.py`, som avgjør tallene i `EKSPERIMENT.md`.

12. **Fasitsjekken hadde samme hull som validatoren.** Den relative
    toleransen på 1e-9 godtok et avvik på 1 082 152 på det store produktet og
    2 432 902 008 på 20!. Et desimalsvar var enda verre, fordi slakken ble
    ganget med fasitens størrelse: `1082152000000000.0` ble godkjent som svar
    på 1 082 152 110 028 959 – 110 millioner feil. Vi kjørte alle de 52
    loggede kjøringene gjennom den rettede sjekken: **ingen vurdering endret
    seg**, så tallene i `EKSPERIMENT.md` står. Men det visste vi ikke før vi
    målte det.

Siste runde gikk mot `llm_client.py`, laget som snakker med modellen:

13. **Retry-after-headeren ble aldri lest.** Funksjonen som regner ut hvor
    lenge vi skal vente ved hastighetsgrense, skulle ta den lengste av
    headeren og tiden i feilmeldingen – men kallstedet sendte inn `str(e)`,
    altså bare meldingsteksten. Headeren var død kode. Sa leverandøren 90
    sekunder i headeren og 2 i meldingen, ventet vi 2, brant et forsøk og ble
    avvist igjen. Det forklarer mye av «rate limit»-styret vi hadde i Del B.
14. **`\begin{matrix}` ble ødelagt i stillhet.** `\b` er en JSON-escape, så
    `\begin` ble til `\x08egin`. Vi hadde en ordliste over LaTeX-kommandoer
    som skulle skjerme mot dette, men `begin` manglet – og en ordliste kan
    uansett aldri bli komplett. Nå finnes det en siste utvei som dobler alle
    backslasher når svaret fortsatt inneholder kontrolltegn.
15. **Tokenregnskapet tålte ikke rare svar.** Sendte leverandøren tokentallet
    som tekst, krasjet hele forespørselen med `TypeError`; sendte den et
    negativt tall, viste appen negativ kostnad.

16. **Formelreferanser gikk tapt.** Skrev modellen `"formel_ider": "D1, D2"`
    som én streng i stedet for en liste, slo vi opp hele strengen som én ID,
    fant den ikke, og advarte om at «D1, D2» ikke finnes i formelsamlingen.
    To gyldige referanser forsvant, og advarselen pekte på feil sted. Dette
    rammet nettopp den funksjonen vi trekker fram som et kvalitetstrekk ved
    appen.

Til slutt kjørte vi appen mot en ekte modell igjen, på oppgaver valgt for å
treffe det vi hadde endret. Det store produktet ble denne gangen kontrollert
med metoden «eksakt sammenligning», og derivasjonsoppgaven fikk fire riktige
formelreferanser. Én kjøring fikk tomt svar fra gratismodellen; appen svarte
502 med alle seks kontraktsfeltene og meldingen «Modellen returnerte ingen
svar», altså slik den skal.

En siste, kortere runde traff to til – den ene selvpåført:

17. **Fem innstillinger i `.env.example` virket ikke.** `VERKTOY_TIDSGRENSE`,
    `VERKTOY_MINNEGRENSE_MB`, `VALIDERING_TOLERANSE`, `VALIDERING_PUNKTER` og
    `VALIDERING_TIDSGRENSE` leses med `os.getenv` på modulnivå, mens `.env`
    ble lest med `dotenv_values()`, som ikke rører `os.environ`. Satte man
    dem i `.env`, skjedde det ingenting. Nå løftes nøyaktig disse fem inn i
    miljøet – og bare dem: API-nøkkelen holdes utenfor, fordi
    underprosessene våre arver miljøet. En test sammenligner nå
    `.env.example` med det koden faktisk leser, så en ny innstilling ikke
    kan dokumenteres uten å kobles opp.
18. **Vi innførte selv en løgn mens vi rettet.** Typetvingingen i `_grunnsvar`
    (funn 8) gjorde `tokens_brukt = None` om til `0`. None betyr «leverandøren
    oppga ikke tallene», og frontend viser da «Tokens: ukjent» – etter
    rettelsen påstod appen i stedet at den hadde brukt null tokens. Fanget
    ved å lete etter regresjoner fra våre egne rettelser.

Alle atten er rettet, og hver av dem har en regresjonstest (testpakken gikk
fra 129 til 177 tester). Det vi tar med oss til Del C: den farligste feilen
var ikke en krasj, men en validator som *sa* at den hadde kontrollert noe, og
som tok feil – og en fasitsjekk med samme hull, som var det eneste vi hadde
til å avsløre validatoren. Ingen av de atten ble funnet ved lesing.

## 2. KI brukt INNE i appen (det appen kaller)

| Rolle | Modell | Leverandør | Hvorfor |
|---|---|---|---|
| Modell A (stor) | `openai/gpt-oss-120b` | Groq (gratisnivå) | Hovedmodell i Del B |
| Modell B (liten) | `openai/gpt-oss-20b` | Groq (gratisnivå) | Samme leverandør, mindre modell – gir en ren sammenligning |
| Ekstra modell | `nvidia/nemotron-3-ultra-550b-a55b:free` | OpenRouter (gratisnivå) | Aha-bryter 3: bytte av både modell og leverandør |

Alle tre er brukt på gratisnivå, uten kredittkort og uten kjøpt kreditt.
Kostnadstallene i `EKSPERIMENT.md` er *listepris for tilsvarende betalt
modell*, regnet ut fra faktisk tokenforbruk – vi har ikke betalt noe.

## 3. Hva KI IKKE har bestemt

- Fasitsvarene i eksperimentet er regnet ut på forhånd og kontrollert mot
  SymPy uavhengig av modellsvarene.
- Bok- og avsnittsreferansene i `formelsamling.py` er kontrollert mot
  forlagets innholdsfortegnelser (Thomas' Calculus 14. utg. og Edwards &
  Penney 4. utg.). Der vi ikke kunne kontrollere sidetall (Tekniske Tabeller),
  står det eksplisitt i referansen.
- Valget av toleranse, tidsgrenser, statuskoder og hva som skal regnes som
  «ikke mulig å validere» er dokumentert i `PROMPTS/`-filene som våre egne
  beslutninger.

## 4. Fylles ut av gruppen før innlevering

Dette må dere skrive selv – det er denne delen sensor bruker for å se at dere
kan innestå for leveransen:

- [ ] Hvem i gruppen har lest gjennom hvilke filer, og hva endret dere?
- [ ] Hvilke deler av koden kan hver enkelt forklare linje for linje, og
      hvilke deler ville dere trengt hjelp til å forsvare?
- [ ] Hvilke prompter brukte dere selv (Copilot, nettleser-KI), og hva fikk
      dere tilbake som dere forkastet?
- [ ] Hvilke av funnene i `EKSPERIMENT.md` har dere selv etterprøvd ved å
      kjøre appen på nytt?
