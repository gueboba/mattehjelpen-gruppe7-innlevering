"""En falsk språkmodell til testene.

Vi vil teste tool-calling-løkken, JSON-tolkingen og advarslene uten å bruke
API-kvote (og uten at testene blir avhengige av nettet eller av hva en modell
finner på å svare akkurat i dag).
"""

from __future__ import annotations

import types

KONFIG = {
    "api_nokkel": "test",
    "modell": "openai/gpt-oss-120b",
    "api_base": "https://eksempel.test/v1",
    "temperatur": 0.0,
    "maks_svartokens": 0,
    "tidsgrense": 30.0,
    "forsok": 0,
    "pris_inn": None,
    "pris_ut": None,
    "usd_til_nok": 10.0,
}


class FalskFunksjon:
    def __init__(self, navn: str, argumenter: str):
        self.name = navn
        self.arguments = argumenter


class FalskVerktoykall:
    def __init__(self, navn: str, argumenter: str, id: str = "call_1"):
        self.id = id
        self.type = "function"
        self.function = FalskFunksjon(navn, argumenter)


class FalskMelding:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FalskBruk:
    def __init__(self, inn: int, ut: int):
        self.prompt_tokens = inn
        self.completion_tokens = ut
        self.total_tokens = inn + ut


class FalskRespons:
    def __init__(self, melding: FalskMelding, inn: int = 1200, ut: int = 300):
        self.choices = [types.SimpleNamespace(message=melding)]
        self.usage = FalskBruk(inn, ut)


class FalskKlient:
    """Svarer med de forhåndsdefinerte responsene, én per kall."""

    def __init__(self, responser):
        self.responser = list(responser)
        self.kall = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **argumenter):
        self.kall.append(argumenter)
        if not self.responser:
            raise AssertionError("Den falske modellen fikk flere kall enn den har svar på.")
        return self.responser.pop(0)


def monter(llm_client, responser) -> FalskKlient:
    """Bytter ut klienten og konfigurasjonen i llm_client med falske."""
    klient = FalskKlient(responser)
    llm_client._klient = lambda konfig: klient
    llm_client.les_konfig = lambda: dict(KONFIG)
    return klient
