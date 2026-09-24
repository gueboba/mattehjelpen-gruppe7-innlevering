"""MatteHjelpen backend-pakke.

Her løftes noen få innstillinger fra `.env` inn i miljøet, og det må skje FØR
noen undermodul importeres.

Grunnen: `tools.py` og `validator.py` leser tidsgrenser, minnetak og
valideringstoleranse med `os.getenv(...)` på modulnivå. `llm_client` bruker
`dotenv_values()`, som bare gir en dict – den rører ikke `os.environ`. Uten
dette var de fem innstillingene nedenfor dokumentert i `.env.example`, men
uten virkning: satte man dem i `.env`, skjedde det ingenting.

To valg her er med vilje:

1. *Bare disse fem.* API_KEY løftes IKKE inn i miljøet. Underprosessene våre
   arver `os.environ`, og nøkkelen har ingenting der å gjøre – `llm_client`
   leser den rett fra filen når den trenger den.
2. *Ekte miljøvariabler vinner*, slik at `VALIDERING_TOLERANSE=1e-3 python -m
   backend.validator` fortsatt overstyrer `.env`.
"""

import os
from pathlib import Path

from dotenv import dotenv_values

_INNSTILLINGER = (
    "VERKTOY_TIDSGRENSE",
    "VERKTOY_MINNEGRENSE_MB",
    "VALIDERING_TOLERANSE",
    "VALIDERING_PUNKTER",
    "VALIDERING_TIDSGRENSE",
)

for _navn, _verdi in dotenv_values(Path(__file__).resolve().parent.parent / ".env").items():
    if _navn in _INNSTILLINGER and _verdi and _navn not in os.environ:
        os.environ[_navn] = _verdi.strip()
