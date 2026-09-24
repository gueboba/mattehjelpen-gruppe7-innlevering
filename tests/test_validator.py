"""Tester for validatoren (backend/validator.py).

Det viktigste her er ikke at riktige svar godkjennes, men at GALE svar
faktisk blir avvist – og at appen sier «ikke mulig å validere» i stedet for
grønt lys når kontrollen ikke kan gjennomføres.
"""

import time
import unittest

from backend.validator import FEILET, IKKE_MULIG, VALIDERT, _valider, samsvarer_med_verktoy


class TestRiktigeSvarGodkjennes(unittest.TestCase):
    def test_derivasjon(self):
        svar = _valider("x**2*sin(3*x)", "2*x*sin(3*x) + 3*x**2*cos(3*x)", "derivasjon", "x")
        self.assertEqual(svar["status"], VALIDERT)

    def test_integral(self):
        svar = _valider("x*exp(2*x)", "(2*x - 1)*exp(2*x)/4 + C", "integral", "x")
        self.assertEqual(svar["status"], VALIDERT)

    def test_bestemt_integral(self):
        svar = _valider("Integral(x**3/(x**2+1), (x, 0, 1))", "1/2 - log(2)/2", "bestemt_integral", "x")
        self.assertEqual(svar["status"], VALIDERT)

    def test_avrundet_desimalsvar_godtas(self):
        svar = _valider("Integral(x**3/(x**2+1), (x, 0, 1))", "0.1534", "bestemt_integral", "x")
        self.assertEqual(svar["status"], VALIDERT)

    def test_ligning(self):
        self.assertEqual(_valider("x**2 - 2*x - 8 = 0", "[-2, 4]", "ligning", "x")["status"], VALIDERT)

    def test_ligningssystem(self):
        svar = _valider(
            "x + y/2 + z/3 = 1; x/2 + y/3 + z/4 = 1; x/3 + y/4 + z/5 = 1",
            "[{x: 3, y: -24, z: 30}]",
            "ligning",
            "x, y, z",
        )
        self.assertEqual(svar["status"], VALIDERT)

    def test_ode(self):
        svar = _valider(
            "Eq(y(x).diff(x, 2) + 2*y(x), 0)", "y(x) = C1*sin(sqrt(2)*x) + C2*cos(sqrt(2)*x)", "ode"
        )
        self.assertEqual(svar["status"], VALIDERT)

    def test_initialverdiproblem(self):
        svar = _valider("y'' - 3*y' + 2*y = 0", "y(x) = 2*exp(x) - exp(2*x)", "ode", None, "y(0)=1, y'(0)=0")
        self.assertEqual(svar["status"], VALIDERT)

    def test_determinant(self):
        self.assertEqual(_valider("[[2, 1, 0], [1, 3, 1], [0, 1, 4]]", "18", "determinant")["status"], VALIDERT)

    def test_egenverdier(self):
        svar = _valider("[[2, 1, 0], [1, 3, 1], [0, 1, 4]]", "[3, 3 - sqrt(3), 3 + sqrt(3)]", "egenverdier")
        self.assertEqual(svar["status"], VALIDERT)

    def test_ax_b(self):
        self.assertEqual(_valider("[[[2, 1], [1, 3]], [5, 10]]", "[1, 3]", "ax_b")["status"], VALIDERT)

    def test_invers(self):
        self.assertEqual(_valider("[[1, 2], [3, 4]]", "[[-2, 1], [3/2, -1/2]]", "invers")["status"], VALIDERT)

    def test_komplekst_tall(self):
        self.assertEqual(_valider("(1+sqrt(3)*I)**7", "64 + 64*sqrt(3)*I", "kompleks")["status"], VALIDERT)

    def test_grenseverdi(self):
        self.assertEqual(_valider("Limit(sin(x)/x, x, 0)", "1", "grenseverdi")["status"], VALIDERT)

    def test_oppgavetype_gjettes_nar_den_mangler(self):
        # Slik selvtesten kaller validatoren, uten oppgavetype.
        self.assertEqual(_valider("Eq(y(x).diff(x), y(x))", "y(x) = C1*exp(x)")["status"], VALIDERT)


class TestGaleSvarAvvises(unittest.TestCase):
    def test_feil_derivert(self):
        self.assertEqual(_valider("x**2*sin(3*x)", "2*x*cos(3*x)", "derivasjon", "x")["status"], FEILET)

    def test_feil_antiderivert(self):
        self.assertEqual(_valider("x*exp(2*x)", "x**2*exp(2*x)/2 + C", "integral", "x")["status"], FEILET)

    def test_feil_verdi_pa_bestemt_integral(self):
        self.assertEqual(
            _valider("Integral(x**3/(x**2+1), (x, 0, 1))", "0.25", "bestemt_integral", "x")["status"], FEILET
        )

    def test_feil_rot(self):
        self.assertEqual(_valider("x**2 - 2*x - 8 = 0", "[-2, 5]", "ligning", "x")["status"], FEILET)

    def test_feil_i_system(self):
        svar = _valider(
            "x + y/2 + z/3 = 1; x/2 + y/3 + z/4 = 1; x/3 + y/4 + z/5 = 1",
            "x = 3, y = -24, z = 29",
            "ligning",
            "x, y, z",
        )
        self.assertEqual(svar["status"], FEILET)

    def test_feil_ode_losning(self):
        svar = _valider("Eq(y(x).diff(x, 2) + 2*y(x), 0)", "y(x) = C1*sin(2*x) + C2*cos(2*x)", "ode")
        self.assertEqual(svar["status"], FEILET)

    def test_ivp_som_ikke_oppfyller_betingelsene(self):
        svar = _valider("y'' - 3*y' + 2*y = 0", "y(x) = exp(x)", "ode", None, "y(0)=1, y'(0)=0")
        self.assertEqual(svar["status"], FEILET)
        self.assertIn("startbetingelsen", svar["detaljer"])

    def test_feil_determinant(self):
        self.assertEqual(_valider("[[2, 1, 0], [1, 3, 1], [0, 1, 4]]", "17", "determinant")["status"], FEILET)

    def test_feil_egenverdi(self):
        self.assertEqual(_valider("[[2, 1, 0], [1, 3, 1], [0, 1, 4]]", "[3, 2, 4]", "egenverdier")["status"], FEILET)

    def test_feil_losningsvektor(self):
        self.assertEqual(_valider("[[[2, 1], [1, 3]], [5, 10]]", "[2, 3]", "ax_b")["status"], FEILET)

    def test_feil_komplekst_svar(self):
        self.assertEqual(_valider("(1+sqrt(3)*I)**7", "64 - 64*sqrt(3)*I", "kompleks")["status"], FEILET)


class TestAerlighet(unittest.TestCase):
    def test_bevis_kan_ikke_valideres(self):
        svar = _valider("", "", "bevis")
        self.assertEqual(svar["status"], IKKE_MULIG)
        self.assertFalse(svar["validert"])
        self.assertIn("IKKE verktøyverifisert", svar["detaljer"])

    def test_manglende_maskinlesbart_svar(self):
        svar = _valider("x**2", "", "derivasjon")
        self.assertEqual(svar["status"], IKKE_MULIG)

    def test_ukjent_oppgavetype_gir_ikke_gront_lys(self):
        svar = _valider("noe rart", "42", None)
        self.assertEqual(svar["status"], IKKE_MULIG)
        self.assertFalse(svar["validert"])

    def test_manglende_rot_gir_advarsel(self):
        svar = _valider("x**2 - 2*x - 8 = 0", "[4]", "ligning", "x")
        self.assertEqual(svar["status"], VALIDERT)
        self.assertTrue(any("mangler" in a for a in svar.get("advarsler", [])))

    def test_spesiell_losning_gir_advarsel_om_manglende_konstant(self):
        svar = _valider("Eq(y(x).diff(x, 2) + 2*y(x), 0)", "y(x) = C1*sin(sqrt(2)*x)", "ode")
        self.assertEqual(svar["status"], VALIDERT)
        self.assertTrue(any("generelle" in a for a in svar.get("advarsler", [])))


class TestSamsvarMedVerktoy(unittest.TestCase):
    def test_svar_som_stemmer_med_verktoyet(self):
        logg = [{"navn": "derive", "resultat": {"resultat": "3*x**2*cos(3*x) + 2*x*sin(3*x)"}}]
        self.assertTrue(samsvarer_med_verktoy("2*x*sin(3*x) + 3*x**2*cos(3*x)", logg))

    def test_svar_som_avviker_fra_verktoyet(self):
        logg = [{"navn": "derive", "resultat": {"resultat": "3*x**2*cos(3*x) + 2*x*sin(3*x)"}}]
        self.assertFalse(samsvarer_med_verktoy("2*x*cos(3*x)", logg))

    def test_uten_verktoykall_kan_vi_ikke_avgjore(self):
        self.assertIsNone(samsvarer_med_verktoy("2*x", []))

    def test_dypt_nestet_uttrykk_henger_ikke(self):
        """sp.simplify kom aldri tilbake på 16 nivåers nesting.

        Sjekken lå den gangen rett i web-tråden uten tidsgrense, så én slik
        forespørsel låste appen for godt. Nå skal den svare raskt.
        """
        dypt = "sin(" * 40 + "x" + ")" * 40
        logg = [{"navn": "derive", "resultat": {"resultat": "cos(" * 40 + "x" + ")" * 40}}]
        start = time.monotonic()
        self.assertFalse(samsvarer_med_verktoy(dypt, logg))
        self.assertLess(time.monotonic() - start, 20)


class TestEksakteSvarHarIngenSlakk(unittest.TestCase):
    """Den relative toleransen på 1e-8 ga 10 millioner i slakk på store tall."""

    def test_heltall_som_er_en_for_lite_underkjennes(self):
        svar = _valider("87654321*12345679", "1082152110028958", "beregning")
        self.assertEqual(svar["status"], FEILET)

    def test_riktig_heltall_godkjennes(self):
        svar = _valider("87654321*12345679", "1082152110028959", "beregning")
        self.assertEqual(svar["status"], VALIDERT)

    def test_stort_heltall_med_feil_pa_en_underkjennes(self):
        svar = _valider("factorial(20)", "2432902008176640001", "beregning")
        self.assertEqual(svar["status"], FEILET)

    def test_eksakt_determinant_med_feil_pa_en_underkjennes(self):
        matrise = "[[1000000, 1], [1, 1000000]]"
        self.assertEqual(_valider(matrise, "999999999998", "determinant")["status"], FEILET)
        self.assertEqual(_valider(matrise, "999999999999", "determinant")["status"], VALIDERT)

    def test_korrekt_avrundet_desimal_godkjennes(self):
        self.assertEqual(_valider("sqrt(2)", "1.4142", "beregning")["status"], VALIDERT)
        self.assertEqual(_valider("exp(x) - 3 = 0", "[1.0986]", "ligning", "x")["status"], VALIDERT)

    def test_feil_avrundet_siste_siffer_underkjennes(self):
        # 2,00007 avrundet til fire desimaler er 2,0001 – ikke 2,0000.
        self.assertEqual(_valider("2.00007", "2.0000", "beregning")["status"], FEILET)


if __name__ == "__main__":
    unittest.main()
