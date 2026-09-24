"""Tester for API-et (backend/main.py): kontrakt, feilhåndtering og ærlighet."""

import json
import unittest

import openai
from fastapi.testclient import TestClient

from backend import llm_client, main
from tests.falsk_llm import KONFIG, FalskMelding, FalskRespons, FalskVerktoykall, monter

try:  # openai 3.x bruker httpx2, eldre versjoner httpx
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

# Tas vare på før noen test bytter dem ut med falske utgaver.
EKTE_KLIENT = llm_client._klient
EKTE_KONFIG = llm_client.les_konfig

PAKREVDE_FELT = {"svar", "steg", "formler_brukt", "validert", "tokens_brukt", "estimert_kostnad"}

RIKTIG_SVAR = json.dumps(
    {
        "tolkning": "Deriver x^2*sin(3x).",
        "oppgavetype": "derivasjon",
        "problem_sympy": "x**2*sin(3*x)",
        "variabel": "x",
        "steg": [{"tekst": "Produktregelen.", "formel_ider": ["D1"]}],
        "svar": "2x sin(3x) + 3x^2 cos(3x)",
        "svar_sympy": "2*x*sin(3*x) + 3*x**2*cos(3*x)",
        "verifisert_med_verktoy": True,
    },
    ensure_ascii=False,
)

GALT_SVAR = RIKTIG_SVAR.replace("2*x*sin(3*x) + 3*x**2*cos(3*x)", "2*x*cos(3*x)")


class TestKontrakt(unittest.TestCase):
    def setUp(self):
        self.klient = TestClient(main.app)

    def tearDown(self):
        llm_client._klient = EKTE_KLIENT
        llm_client.les_konfig = EKTE_KONFIG

    def test_riktig_svar_blir_validert(self):
        monter(
            llm_client,
            [
                FalskRespons(FalskMelding(None, [FalskVerktoykall("derive", '{"uttrykk": "x**2*sin(3*x)"}')])),
                FalskRespons(FalskMelding(RIKTIG_SVAR)),
            ],
        )
        svar = self.klient.post("/solve", json={"oppgave": "Deriver x^2 sin(3x)"})
        self.assertEqual(svar.status_code, 200)
        data = svar.json()
        self.assertTrue(PAKREVDE_FELT <= data.keys())
        self.assertTrue(data["validert"])
        self.assertEqual(data["valideringsstatus"], "validert")
        self.assertEqual(data["formler_brukt"][0]["id"], "D1")
        self.assertEqual(len(data["verktoy_brukt"]), 1)

    def test_galt_svar_blir_avslort(self):
        monter(llm_client, [FalskRespons(FalskMelding(GALT_SVAR))])
        data = self.klient.post("/solve", json={"oppgave": "Deriver x^2 sin(3x)"}).json()
        self.assertFalse(data["validert"])
        self.assertEqual(data["valideringsstatus"], "feilet")
        self.assertIn("stemmer ikke", data["valideringsdetaljer"])

    def test_tom_oppgave_gir_400_med_alle_felt(self):
        svar = self.klient.post("/solve", json={"oppgave": "   "})
        self.assertEqual(svar.status_code, 400)
        self.assertTrue(PAKREVDE_FELT <= svar.json().keys())

    def test_for_lang_oppgave_gir_400(self):
        svar = self.klient.post("/solve", json={"oppgave": "x" * 3000})
        self.assertEqual(svar.status_code, 400)

    def test_manglende_nokkel_gir_500_og_forklaring(self):
        llm_client._klient = EKTE_KLIENT
        llm_client.les_konfig = lambda: {**KONFIG, "api_nokkel": ""}
        svar = self.klient.post("/solve", json={"oppgave": "Deriver x^2"})
        self.assertEqual(svar.status_code, 500)
        self.assertIn("API_KEY", svar.json()["feil"])

    def test_rate_limit_gir_503(self):
        class Sperret:
            def create(self, **kw):
                raise openai.RateLimitError(
                    "rate limit",
                    response=httpx.Response(429, request=httpx.Request("POST", "https://eksempel.test")),
                    body=None,
                )

        klient = monter(llm_client, [])
        klient.chat.completions = Sperret()
        svar = self.klient.post("/solve", json={"oppgave": "Deriver x^2"})
        self.assertEqual(svar.status_code, 503)
        data = svar.json()
        self.assertIn("Kvoten", data["feil"])
        self.assertTrue(PAKREVDE_FELT <= data.keys())

    def test_status_viser_oppsett_uten_nokkel(self):
        monter(llm_client, [])
        data = self.klient.get("/status").json()
        self.assertIn("modell", data)
        self.assertNotIn("api_nokkel", data)
        self.assertIn("use_tools", data)

    def test_forsiden_serveres(self):
        svar = self.klient.get("/")
        self.assertEqual(svar.status_code, 200)
        self.assertIn("MatteHjelpen", svar.text)

    def test_ugyldig_forespoersel_holder_kontrakten(self):
        """Pydantics eget 422-svar hadde bare «detail», og frontend krasjet på det."""
        for krav in ({}, {"oppgave": 42}, {"oppgave": None}, {"oppgave": ["a"]}):
            with self.subTest(krav=krav):
                svar = self.klient.post("/solve", json=krav)
                self.assertEqual(svar.status_code, 422)
                data = svar.json()
                self.assertTrue(PAKREVDE_FELT <= data.keys())
                self.assertIn("oppgave", data["feil"])


class TestSvaretErAlltidGyldigJSON(unittest.TestCase):
    def test_nan_i_kostnaden_gir_ikke_500(self):
        """NaN skrives som `NaN`, som ikke er gyldig JSON – Starlette svarte 500."""
        klient = TestClient(main.app)
        ekte = llm_client.solve_task
        llm_client.solve_task = lambda oppgave, **kw: {
            "svar": "2*x", "steg": ["Deriver"], "formler_brukt": [], "tokens_brukt": 10,
            "estimert_kostnad": float("nan"), "estimert_kostnad_nok": float("inf"),
        }
        try:
            svar = klient.post("/solve", json={"oppgave": "Deriver x**2"})
        finally:
            llm_client.solve_task = ekte
        self.assertEqual(svar.status_code, 200)
        self.assertEqual(svar.json()["estimert_kostnad"], 0.0)
        self.assertIsNone(svar.json()["estimert_kostnad_nok"])

    def test_grunnsvaret_tvinger_riktige_typer(self):
        """Feil type er like ødeleggende som manglende felt: .map() på en streng."""
        svar = main._grunnsvar(
            svar=None, steg="ikke en liste", formler_brukt={"a": 1},
            validert="ja", tokens_brukt="mange", estimert_kostnad=float("nan"),
        )
        self.assertIsInstance(svar["svar"], str)
        self.assertIsInstance(svar["steg"], list)
        self.assertIsInstance(svar["formler_brukt"], list)
        self.assertIsInstance(svar["validert"], bool)
        self.assertEqual(svar["tokens_brukt"], 0)
        self.assertEqual(svar["estimert_kostnad"], 0.0)

    def test_ukjent_tokenforbruk_forblir_ukjent(self):
        """None betyr «vi vet ikke». Ble det 0, påstod appen at den ikke brukte noe.

        Frontend viser «Tokens: ukjent» for None, og det er den ærlige
        beskjeden når leverandøren ikke oppgir tallene.
        """
        svar = main._grunnsvar(tokens_brukt=None, estimert_kostnad=None)
        self.assertIsNone(svar["tokens_brukt"])
        self.assertIsNone(svar["estimert_kostnad"])
        self.assertEqual(main._grunnsvar()["tokens_brukt"], 0)


class TestValideringFraVerktoylogg(unittest.TestCase):
    """Når modellen ikke oppgir problemet på en form vi kan kontrollere, bruker vi
    verktøykallet den faktisk gjorde."""

    def test_problem_rekonstrueres_fra_verktoykall(self):
        self.assertEqual(
            main._problem_fra_verktoy(
                [
                    {
                        "navn": "definite_integral",
                        "argumenter": {
                            "uttrykk": "x**3/(x**2+1)",
                            "variabel": "x",
                            "nedre": "0",
                            "ovre": "1",
                        },
                        "ok": True,
                    }
                ]
            ),
            ("Integral(x**3/(x**2+1), (x, 0, 1))", "bestemt_integral", "x", None),
        )

    def test_mislykkede_kall_hoppes_over(self):
        self.assertIsNone(
            main._problem_fra_verktoy([{"navn": "derive", "argumenter": {"uttrykk": "x"}, "ok": False}])
        )

    def test_validering_reddes_av_verktoyloggen(self):
        resultat = {
            "problem_sympy": "det bestemte integralet av x^3/(x^2+1) fra 0 til 1",  # ikke SymPy
            "svar_sympy": "1/2 - log(2)/2",
            "oppgavetype": "bestemt_integral",
            "verktoy_brukt": [
                {
                    "navn": "definite_integral",
                    "argumenter": {
                        "uttrykk": "x**3/(x**2+1)",
                        "variabel": "x",
                        "nedre": "0",
                        "ovre": "1",
                    },
                    "ok": True,
                }
            ],
        }
        validering = main._valider(resultat)
        self.assertTrue(validering["validert"])
        self.assertIn("verktøykallet den faktisk gjorde", validering["detaljer"])


if __name__ == "__main__":
    unittest.main()
