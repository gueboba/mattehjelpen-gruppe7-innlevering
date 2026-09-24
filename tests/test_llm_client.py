"""Tester for llm_client: tool-calling-løkken, JSON-tolking og advarsler.

Testene bruker en falsk modell (tests/falsk_llm.py), så de koster ingenting og
er ikke avhengige av nett.
"""

import json
import re
import unittest
from pathlib import Path

from backend import llm_client
from backend.llm_client import les_json, systemprompt
from tests.falsk_llm import FalskMelding, FalskRespons, FalskVerktoykall, monter

SVAR_JSON = json.dumps(
    {
        "tolkning": "Deriver x^2*sin(3x) med hensyn på x.",
        "oppgavetype": "derivasjon",
        "problem_sympy": "x**2*sin(3*x)",
        "variabel": "x",
        "betingelser": [],
        "steg": [
            {"tekst": "Produktregelen med u = x^2 og v = sin(3x).", "formel_ider": ["D1"]},
            {"tekst": "Kjerneregelen gir 3cos(3x).", "formel_ider": ["D2", "X9"]},
        ],
        "svar": "f'(x) = 2x sin(3x) + 3x^2 cos(3x)",
        "svar_sympy": "2*x*sin(3*x) + 3*x**2*cos(3*x)",
        "verifisert_med_verktoy": True,
        "usikkerhet": "",
    },
    ensure_ascii=False,
)


class TestSystemprompt(unittest.TestCase):
    def test_kjernen_er_med(self):
        prompt = systemprompt(bruk_verktoy=True, forklar_steg=True)
        self.assertIn("du skal ALDRI late", prompt)
        self.assertIn("Forklar hvert steg pedagogisk på norsk", prompt)
        self.assertIn("D1 | Produktregelen", prompt)

    def test_aha_bryter_2_fjerner_stegkravet(self):
        prompt = systemprompt(bruk_verktoy=True, forklar_steg=False)
        self.assertNotIn("Forklar hvert steg pedagogisk", prompt)
        self.assertNotIn("[STEG", prompt)
        self.assertIn("[SVAR]", systemprompt(bruk_verktoy=True, forklar_steg=True))
        self.assertIn("[STEG D1]", systemprompt(bruk_verktoy=True, forklar_steg=True))

    def test_uten_verktoy_sies_det_fra(self):
        self.assertIn("INGEN verktøy", systemprompt(bruk_verktoy=False, forklar_steg=True))


class TestJsonTolking(unittest.TestCase):
    def test_vanlig_json(self):
        self.assertEqual(les_json('{"svar": "42"}')["svar"], "42")

    def test_json_i_kodeblokk(self):
        self.assertEqual(les_json('Her:\n```json\n{"svar": "42"}\n```\nferdig')["svar"], "42")

    def test_latex_med_enkel_backslash_repareres(self):
        # \frac ville blitt tolket som formfeed + "rac" av json.loads.
        data = les_json('{"svar": "$\\frac{d}{dx}\\sin(3x)$ og $\\theta$"}')
        self.assertIn("\\frac", data["svar"])
        self.assertIn("\\theta", data["svar"])
        self.assertNotIn("\x0c", data["svar"])

    def test_ekte_linjeskift_beholdes(self):
        self.assertEqual(les_json('{"svar": "a\\nDermed b"}')["svar"], "a\nDermed b")

    def test_tekst_uten_json(self):
        self.assertIsNone(les_json("Svaret er 42."))


class TestVerktoylokke(unittest.TestCase):
    def test_verktoykall_kjores_og_logges(self):
        monter(
            llm_client,
            [
                FalskRespons(
                    FalskMelding(None, [FalskVerktoykall("derive", '{"uttrykk": "x**2*sin(3*x)"}')])
                ),
                FalskRespons(FalskMelding(SVAR_JSON)),
            ],
        )
        svar = llm_client.solve_task("Deriver x^2 sin(3x)")
        self.assertEqual(len(svar["verktoy_brukt"]), 1)
        self.assertTrue(svar["verktoy_brukt"][0]["ok"])
        self.assertEqual(svar["oppgavetype"], "derivasjon")
        self.assertEqual(svar["tokens_brukt"], 3000)

    def test_ukjent_formel_id_avvises(self):
        monter(llm_client, [FalskRespons(FalskMelding(SVAR_JSON))])
        svar = llm_client.solve_task("Deriver x^2 sin(3x)")
        self.assertEqual([f["id"] for f in svar["formler_brukt"]], ["D1", "D2"])
        self.assertTrue(any("X9" in a for a in svar["advarsler"]))

    def test_pastand_om_verktoybruk_uten_verktoykall_gir_advarsel(self):
        monter(llm_client, [FalskRespons(FalskMelding(SVAR_JSON))])
        svar = llm_client.solve_task("Deriver x^2 sin(3x)", use_tools=False)
        self.assertTrue(any("påstår" in a for a in svar["advarsler"]))

    def test_verktoyfeil_sendes_tilbake_til_modellen(self):
        klient = monter(
            llm_client,
            [
                FalskRespons(FalskMelding(None, [FalskVerktoykall("derive", '{"uttrykk": "sin^-1(x)"}')])),
                FalskRespons(FalskMelding(SVAR_JSON)),
            ],
        )
        svar = llm_client.solve_task("Deriver sin^-1(x)")
        self.assertFalse(svar["verktoy_brukt"][0]["ok"])
        verktoymelding = [m for m in klient.kall[-1]["messages"] if m.get("role") == "tool"][0]
        self.assertIn("arcsin", verktoymelding["content"])
        self.assertTrue(any("feilet" in a for a in svar["advarsler"]))

    def test_feil_format_gir_en_reparasjonsrunde(self):
        klient = monter(
            llm_client,
            [FalskRespons(FalskMelding("Svaret er 2x sin(3x).")), FalskRespons(FalskMelding(SVAR_JSON))],
        )
        svar = llm_client.solve_task("Deriver x^2 sin(3x)")
        self.assertEqual(len(klient.kall), 2)
        self.assertTrue(any("feil format" in a for a in svar["advarsler"]))

    def test_gir_opp_ærlig_nar_modellen_aldri_svarer_riktig(self):
        monter(llm_client, [FalskRespons(FalskMelding("bare tekst")) for _ in range(3)])
        svar = llm_client.solve_task("Deriver x^2", maks_runder=3)
        self.assertTrue(any("fulgte ikke svarformatet" in a for a in svar["advarsler"]))
        self.assertEqual(svar["svar_sympy"], "")

    def test_siste_runde_kjores_uten_verktoy(self):
        klient = monter(
            llm_client,
            [
                FalskRespons(FalskMelding(None, [FalskVerktoykall("derive", '{"uttrykk": "x**2"}')])),
                FalskRespons(FalskMelding(SVAR_JSON)),
            ],
        )
        llm_client.solve_task("Deriver x^2", maks_runder=2)
        self.assertIn("tools", klient.kall[0])
        self.assertNotIn("tools", klient.kall[1])


class TestKostnad(unittest.TestCase):
    def test_kostnad_regnes_ut_fra_listepris(self):
        monter(llm_client, [FalskRespons(FalskMelding(SVAR_JSON), inn=1_000_000, ut=1_000_000)])
        svar = llm_client.solve_task("Deriver x^2", use_tools=False)
        # gpt-oss-120b: 0,15 USD inn + 0,60 USD ut per million tokens.
        self.assertAlmostEqual(svar["estimert_kostnad"], 0.75, places=6)
        self.assertAlmostEqual(svar["estimert_kostnad_nok"], 7.5, places=4)

    def test_ukjent_tokenforbruk_vises_som_ukjent(self):
        respons = FalskRespons(FalskMelding(SVAR_JSON))
        respons.usage = None
        monter(llm_client, [respons])
        svar = llm_client.solve_task("Deriver x^2", use_tools=False)
        self.assertIsNone(svar["tokens_brukt"])
        self.assertFalse(svar["kostnad_kjent"])


class TestVentetid(unittest.TestCase):
    """Ventetiden avgjorde om vi kom gjennom gratisnivået eller brant opp forsøkene."""

    class _Svar:
        def __init__(self, hoder):
            self.headers = hoder

    class _Feil(Exception):
        def __init__(self, melding, hoder=None):
            super().__init__(melding)
            if hoder is not None:
                self.response = TestVentetid._Svar(hoder)

    def _som_i_appen(self, feil):
        """Nøyaktig slik _kall_modellen henter ventetiden."""
        pakket = llm_client.LLMFeil("x", teknisk=str(feil), opphav=feil)
        return llm_client._ventetid(pakket.opphav or pakket.teknisk or "")

    def test_tid_i_meldingsteksten(self):
        feil = self._Feil("Rate limit reached. Please try again in 32.5s")
        self.assertAlmostEqual(self._som_i_appen(feil), 32.5)

    def test_retry_after_headeren_brukes(self):
        # Før: headeren nådde aldri fram, fordi bare str(e) ble sendt videre.
        self.assertAlmostEqual(self._som_i_appen(self._Feil("Rate limit.", {"retry-after": "45"})), 45.0)
        self.assertAlmostEqual(
            self._som_i_appen(self._Feil("Rate limit.", {"retry-after-ms": "60000"})), 60.0
        )

    def test_den_lengste_vinner(self):
        feil = self._Feil("try again in 2s", {"retry-after": "90"})
        self.assertAlmostEqual(self._som_i_appen(feil), 90.0)
        feil = self._Feil("try again in 32.5s", {"retry-after": "2"})
        self.assertAlmostEqual(self._som_i_appen(feil), 32.5)

    def test_uten_opplysninger_venter_vi_litt(self):
        self.assertEqual(self._som_i_appen(self._Feil("Noe gikk galt")), 20.0)


class TestLatexIJson(unittest.TestCase):
    """LaTeX og JSON passer dårlig sammen; her sjekker vi at ingenting ødelegges."""

    def test_begin_overlever(self):
        # «\begin» ble til «\x08egin» fordi \b er en JSON-escape.
        # Merk: «\\» i JSON-kilden er én backslash i det ferdige svaret.
        self.assertEqual(
            les_json(r'{"svar": "\begin{pmatrix} 1 \\ 2 \end{pmatrix}"}')["svar"],
            r"\begin{pmatrix} 1 \ 2 \end{pmatrix}",
        )

    def test_ukjent_latexkommando_overlever(self):
        self.assertEqual(
            les_json(r'{"svar": "\ukjentkommando{x}"}')["svar"], r"\ukjentkommando{x}"
        )

    def test_ekte_linjeskift_beholdes(self):
        self.assertEqual(les_json(r'{"svar": "linje1\nlinje2"}')["svar"], "linje1\nlinje2")

    def test_frac_overlever(self):
        self.assertEqual(les_json(r'{"svar": "\frac{1}{2}"}')["svar"], r"\frac{1}{2}")


class TestInnstillingerFraEnv(unittest.TestCase):
    """En dokumentert innstilling som ikke virker, er verre enn ingen innstilling."""

    def _rot(self):
        import backend

        return Path(backend.__file__).resolve().parent.parent

    def test_alle_os_getenv_innstillinger_loftes_fra_env(self):
        """tools.py og validator.py leser disse på modulnivå, før .env er lest.

        dotenv_values() fyller ikke os.environ, så uten backend/__init__.py
        var VALIDERING_TOLERANSE og fire andre dokumentert i .env.example,
        men uten virkning.
        """
        import backend

        lest = set()
        for fil in ("backend/tools.py", "backend/validator.py"):
            tekst = (self._rot() / fil).read_text(encoding="utf-8")
            lest.update(re.findall(r'os\.getenv\(\s*"([A-Z_0-9]+)"', tekst))
        self.assertTrue(lest, "fant ingen os.getenv-innstillinger å sjekke")
        self.assertEqual(lest - set(backend._INNSTILLINGER), set())

    def test_env_example_dokumenterer_bare_innstillinger_som_virker(self):
        eksempel = (self._rot() / ".env.example").read_text(encoding="utf-8")
        nevnt = {
            linje.split("=", 1)[0].strip()
            for linje in eksempel.splitlines()
            if "=" in linje and not linje.strip().startswith("#")
        }
        import backend

        virker = set(llm_client._KONFIGNOKLER) | set(backend._INNSTILLINGER)
        self.assertEqual(nevnt - virker, set(), "dokumentert i .env.example, men leses aldri")

    def test_api_nokkelen_loftes_aldri_inn_i_miljoet(self):
        """Underprosessene arver os.environ; nøkkelen har ingenting der å gjøre."""
        import backend

        self.assertNotIn("API_KEY", backend._INNSTILLINGER)


class TestFormelIder(unittest.TestCase):
    """Formelreferansene er et av appens kvalitetsstempler – de må ikke gå tapt."""

    def _ider(self, verdi):
        steg = llm_client._normaliser_steg([{"tekst": "Deriver", "formel_ider": verdi}])
        formler, _, advarsler = llm_client._formler_brukt(steg)
        return [f["id"] for f in formler], advarsler

    def test_liste(self):
        self.assertEqual(self._ider(["D1", "D2"]), (["D1", "D2"], []))

    def test_en_streng_med_flere_ider(self):
        # «D1, D2» ble slått opp som én ID, og begge referansene gikk tapt.
        for verdi in ("D1, D2", "D1 D2", "D1;D2", ["D1, D2"]):
            with self.subTest(verdi=verdi):
                self.assertEqual(self._ider(verdi), (["D1", "D2"], []))

    def test_smabokstaver(self):
        self.assertEqual(self._ider("d1"), (["D1"], []))

    def test_oppdiktet_id_gir_fortsatt_advarsel(self):
        formler, advarsler = self._ider("TULL9")
        self.assertEqual(formler, [])
        self.assertTrue(advarsler)


class TestTokenregnskap(unittest.TestCase):
    class _Bruk:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _Svar:
        def __init__(self, bruk):
            self.usage = bruk

    def _tell(self, bruk):
        tokens = {"inn": 0, "ut": 0, "totalt": 0, "kall": 0, "kall_uten_tall": 0}
        llm_client._tell_tokens(tokens, self._Svar(bruk))
        return tokens

    def test_vanlige_tall(self):
        tokens = self._tell(self._Bruk(prompt_tokens=100, completion_tokens=20, total_tokens=120))
        self.assertEqual((tokens["inn"], tokens["ut"], tokens["totalt"]), (100, 20, 120))

    def test_tall_som_tekst_velter_ikke_regnskapet(self):
        tokens = self._tell(self._Bruk(prompt_tokens="100", completion_tokens="20"))
        self.assertEqual((tokens["inn"], tokens["ut"], tokens["totalt"]), (100, 20, 120))

    def test_negative_tall_blir_null(self):
        tokens = self._tell(self._Bruk(prompt_tokens=-5, completion_tokens=-1, total_tokens=-6))
        self.assertEqual((tokens["inn"], tokens["ut"], tokens["totalt"]), (0, 0, 0))

    def test_uten_usage_telles_kallet_som_ukjent(self):
        tokens = self._tell(None)
        self.assertEqual(tokens["kall_uten_tall"], 1)


if __name__ == "__main__":
    unittest.main()
