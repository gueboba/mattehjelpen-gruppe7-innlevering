# Prompt: `backend/validator.py` – numerisk validering

**Fil:** `backend/validator.py`
**Hvorfor:** Etterprøvbarhet er ikke valgfritt for ingeniører. Hvis appen
sier at en løsning stemmer, skal det være fordi dere faktisk sjekket det.

## Krav (fast)

- `validate(problem: str, losning: str) -> {"validert": bool, "detaljer": str}`
- Sett løsningen inn i det originale problemet og evaluer numerisk i (minst)
  3 punkter med SymPy `subs`/`evalf`.
- Vær ærlig: hvis validering ikke er mulig for denne oppgavetypen, **si det**
  i `detaljer` – ikke returner `validert: True` fordi det ser bra ut.

## [FYLL INN SELV] – ta stilling til dette FØR dere sender prompten

- Hva er «nært nok null»/riktig verdi numerisk (toleranse)? Flyttallregning
  er ikke eksakt. Bestem en toleranse (f.eks. `1e-6`) og begrunn kort hvorfor
  akkurat den:

  **Vårt svar:** relativ toleranse `1e-8`, med tre presiseringer. Vi regner med
  30 gjeldende siffer (mpmath), så ekte avrundingsfeil ligger rundt 1e-25 –
  altså langt under grensen. Samtidig er 1e-8 strengt nok til å avsløre reelle
  feil: skriver modellen 0,333 i stedet for 1/3, blir avviket ca. 1e-3.
  Presisering 1: toleransen er relativ til størrelsen på uttrykket, slik at en
  ODE med store konstanter ikke underkjennes av ren skalering. Presisering 2:
  oppgir modellen svaret med desimaler (0,1534), slakker vi til halve siste
  siffer – ellers ville et korrekt avrundet svar blitt underkjent, og da ville
  appen lyve om at svaret var feil.

  Presisering 3 kom til etter at vi *målte* hvor galt et svar kunne være og
  fortsatt bli godkjent. Der begge sider er eksakte tall, finnes ingen
  trunkeringsfeil å ta høyde for, og da ble den relative slakken absurd: på
  87 654 321 × 12 345 679 godtok den et avvik på over ti millioner. Derfor
  sammenlignes eksakte tall nå eksakt (to brøker er enten like eller ulike),
  og andre eksakte uttrykk får `1e-20`. Slakken for desimalsvar gjelder bare
  når modellen faktisk har oppgitt desimaler, og er nøyaktig en halv enhet i
  siste siffer – vi hadde 0,75, og da slapp et *feil avrundet* svar gjennom.

- Hvilke oppgavetyper klarer dere IKKE å validere med denne metoden (f.eks.
  åpne/ubestemte integraler, symbolske svar uten tallverdi)? List dem opp –
  dette skal appen si ærlig fra om, ikke skjule:

  **Vårt svar:** bevis og begrepsforklaringer (ingen SymPy-kontroll finnes),
  svar uten maskinlesbar form, integraler uten lukket form, uendelige
  grenseverdier, egenvektorer, uendelige løsningsmengder (vi kontrollerer bare
  de løsningene som faktisk er oppgitt, og advarer om røtter som mangler),
  integrasjonskonstanten C, og oppgaver der modellen ikke oppga oppgavetype.
  Alle disse gir status «ikke mulig å validere» med begrunnelse – aldri
  `validert: true`.

  **Viktigst av alt:** validering skjer mot oppgaven *slik modellen tolket
  den*. Har modellen misforstått (f.eks. `sin^-1`), kan svaret være «validert»
  og likevel feil svar på studentens spørsmål. Derfor viser frontend alltid
  tolkningen ved siden av valideringen.

## Ferdig prompt å lime inn (etter at dere har fylt inn over)

```
Implementer backend/validator.py sin funksjon
validate(problem: str, losning: str) -> dict som:
1. Bruker SymPy til å tolke problem og losning.
2. Setter løsningen inn i problemet og evaluerer numerisk i 3 tilfeldige
   punkter (subs + evalf).
3. Bruker toleranse [TOLERANSE FRA OVER] for å avgjøre om det stemmer.
4. Returnerer {"validert": bool, "detaljer": str} der detaljer forklarer
   HVA som ble sjekket og i hvilke punkter.
5. For oppgavetyper som ikke kan valideres slik (f.eks.
   [LISTEN DERES FRA OVER]): returner validert=False med en ÆRLIG forklaring
   i detaljer om AT og HVORFOR validering ikke var mulig – ikke lat som alt er OK.
```

## Kvalitetssjekk før du limer inn koden

- [ ] `validert` er aldri `True` uten at en faktisk numerisk sjekk ble gjort.
- [ ] Når validering ikke er mulig, sier `detaljer` det eksplisitt (ikke bare
      `"Feil"` uten forklaring).
- [ ] Dere har testet med en løsning dere VET er feil, og sett at
      `validert` faktisk blir `False`.
- [ ] Kjør `python scripts/selftest.py` – validator-sjekken bør nå vise ✅.
