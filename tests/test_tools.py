"""Tester for SymPy-verktøyene (backend/tools.py).

Her sjekker vi at verktøyene faktisk regner riktig – selvtesten sjekker bare
at formatet på svaret stemmer.
"""

import time
import unittest

import sympy as sp

from backend import tools


def r(navn, **argumenter):
    return tools.kjor_verktoy(navn, argumenter)


class TestDerivasjonOgIntegrasjon(unittest.TestCase):
    def test_produkt_og_kjerneregel(self):
        svar = r("derive", uttrykk="x**2*sin(3*x)")
        x = sp.Symbol("x")
        fasit = 2 * x * sp.sin(3 * x) + 3 * x**2 * sp.cos(3 * x)
        self.assertEqual(sp.simplify(sp.sympify(svar["resultat"]) - fasit), 0)

    def test_delvis_integrasjon(self):
        svar = r("integrate", uttrykk="x*exp(2*x)")
        self.assertIn("+ C", svar["resultat"])
        antiderivert = sp.sympify(svar["uten_konstant"])
        x = sp.Symbol("x")
        self.assertEqual(sp.simplify(sp.diff(antiderivert, x) - x * sp.exp(2 * x)), 0)

    def test_integral_uten_lukket_form_sier_fra(self):
        svar = r("integrate", uttrykk="exp(x**2)*sin(x)")
        self.assertIn("merknad", svar)
        self.assertIn("lukket form", svar["merknad"])

    def test_bestemt_integral(self):
        svar = r("definite_integral", uttrykk="x**3/(x**2 + 1)", variabel="x", nedre="0", ovre="1")
        self.assertEqual(sp.simplify(sp.sympify(svar["resultat"]) - (sp.Rational(1, 2) - sp.log(2) / 2)), 0)

    def test_grenseverdi(self):
        self.assertEqual(r("limit", uttrykk="sin(x)/x", variabel="x", punkt="0")["resultat"], "1")

    def test_tosidig_grense_som_ikke_finnes_forklares(self):
        svar = r("limit", uttrykk="1/x", variabel="x", punkt="0")
        self.assertIn("zoo", svar["resultat"])
        self.assertIn("finnes ikke", svar["merknad"])


class TestLigninger(unittest.TestCase):
    def test_andregradsligning(self):
        self.assertEqual(r("solve_equation", ligning="x**2 - 2*x - 8 = 0", variabel="x")["resultat"], "[-2, 4]")

    def test_system_med_broker(self):
        svar = r(
            "solve_equation",
            ligning="x + y/2 + z/3 = 1; x/2 + y/3 + z/4 = 1; x/3 + y/4 + z/5 = 1",
            variabel="x, y, z",
        )
        self.assertEqual(svar["resultat"], "[{x: 3, y: -24, z: 30}]")

    def test_periodisk_losning_far_merknad(self):
        svar = r("solve_equation", ligning="sin(x) = 0", variabel="x")
        self.assertIn("full_losningsmengde", svar)
        self.assertIn("uendelig", svar["merknad"])


class TestDifferensialligninger(unittest.TestCase):
    def test_generell_losning_og_karakteristisk_ligning(self):
        svar = r("solve_ode", ligning="Eq(y(x).diff(x, 2) + 2*y(x), 0)")
        self.assertIn("C1", svar["resultat"])
        self.assertEqual(svar["karakteristisk_ligning"], "r**2 + 2 = 0")
        self.assertEqual(svar["orden"], 2)

    def test_initialverdiproblem(self):
        svar = r("solve_ode_ivp", ligning="y'' - 3*y' + 2*y = 0", betingelser="y(0)=1, y'(0)=0")
        x = sp.Symbol("x")
        losning = sp.sympify(svar["resultat"]).rhs
        self.assertEqual(sp.simplify(losning - (2 * sp.exp(x) - sp.exp(2 * x))), 0)

    def test_ivp_uten_betingelser_avvises(self):
        self.assertIn("feil", r("solve_ode_ivp", ligning="y' + y = 0", betingelser=""))


class TestMatriser(unittest.TestCase):
    def test_determinant(self):
        self.assertEqual(r("matrix_op", operasjon="determinant", matrise="[[1, 2], [3, 4]]")["resultat"], "-2")

    def test_egenverdier(self):
        svar = r("matrix_op", operasjon="egenverdier", matrise="[[2, 1, 0], [1, 3, 1], [0, 1, 4]]")
        verdier = {sp.simplify(sp.sympify(v)) for v in svar["verdier"]}
        self.assertEqual(verdier, {sp.Integer(3), 3 - sp.sqrt(3), 3 + sp.sqrt(3)})

    def test_singular_matrise_gir_forklarende_feil(self):
        svar = r("matrix_op", operasjon="invers", matrise="[[1, 2], [2, 4]]")
        self.assertIn("singulær", svar["feil"])

    def test_ax_b_med_broker(self):
        svar = r(
            "matrix_op",
            operasjon="solve_ax_b",
            matrise="[[[1, 1/2, 1/3], [1/2, 1/3, 1/4], [1/3, 1/4, 1/5]], [1, 1, 1]]",
        )
        self.assertEqual(svar["resultat"], "[[3], [-24], [30]]")

    def test_ikke_kvadratisk_avvises(self):
        self.assertIn("feil", r("matrix_op", operasjon="determinant", matrise="[[1, 2, 3], [4, 5, 6]]"))


class TestKomplekseTall(unittest.TestCase):
    def test_polarform(self):
        svar = r("complex_op", operasjon="polar", tall="1+I")
        self.assertEqual(svar["modulus"], "sqrt(2)")
        self.assertEqual(svar["argument"], "pi/4")

    def test_de_moivre(self):
        svar = r("complex_op", operasjon="potens", tall="1+sqrt(3)*I, 7")
        self.assertEqual(sp.simplify(sp.sympify(svar["resultat"]) - (64 + 64 * sp.sqrt(3) * sp.I)), 0)

    def test_rotter(self):
        svar = r("complex_op", operasjon="rotter", tall="-8, 3")
        rotter = [sp.simplify(sp.sympify(t)) for t in svar["resultat"].strip("[]").split(", ")]
        for rot in rotter:
            self.assertEqual(sp.simplify(rot**3 + 8), 0)

    def test_rektangulaer_beholder_hele_uttrykket(self):
        self.assertEqual(r("complex_op", operasjon="rektangulaer", tall="(1+I)**10")["resultat"], "32*I")

    def test_altfor_mange_rotter_avvises_raskt(self):
        """n = 1000 brukte 31 s og spiste hele tidsgrensen for verktøykallet."""
        start = time.monotonic()
        svar = r("complex_op", operasjon="rotter", tall="1, 1000")
        self.assertIn("for mange", svar["feil"])
        self.assertLess(time.monotonic() - start, 5)


class TestFeilhandtering(unittest.TestCase):
    def test_ukjent_verktoy(self):
        self.assertIn("Ukjent verktøy", r("finnesikke", uttrykk="x")["feil"])

    def test_ukjent_argument(self):
        self.assertIn("Ukjente argumenter", r("derive", tulleargument="x")["feil"])

    def test_tvetydig_notasjon_gir_forklaring_til_modellen(self):
        self.assertIn("arcsin", r("derive", uttrykk="sin^-1(x)")["feil"])

    def test_deling_pa_null(self):
        self.assertIn("udefinert", r("calculate", uttrykk="1/0")["feil"])


class TestTidsgrense(unittest.TestCase):
    """Verktøykall kjøres i egen prosess, slik at tunge beregninger ikke henger appen."""

    def test_vanlig_kall_gar_gjennom_prosessen(self):
        svar = tools.kjor_verktoy_med_tidsgrense("derive", {"uttrykk": "x**2"})
        self.assertEqual(svar["resultat"], "2*x")

    def test_for_treg_beregning_avbrytes(self):
        svar = tools.kjor_verktoy_med_tidsgrense(
            "solve_equation",
            {"ligning": "exp(x) + x**5 + sin(x**3) = cos(x)/x", "variabel": "x"},
            sekunder=3,
        )
        self.assertIn("avbrutt", svar["feil"])


class TestVerktoydefinisjoner(unittest.TestCase):
    def test_alle_verktoy_har_definisjon(self):
        definerte = {d["function"]["name"] for d in tools.TOOL_DEFINITIONS}
        self.assertEqual(definerte, set(tools.VERKTOY))

    def test_definisjonene_har_beskrivelse_og_parametere(self):
        for definisjon in tools.TOOL_DEFINITIONS:
            funksjon = definisjon["function"]
            self.assertTrue(funksjon["description"])
            self.assertEqual(definisjon["type"], "function")
            self.assertIn("properties", funksjon["parameters"])
            for pakrevd in funksjon["parameters"]["required"]:
                self.assertIn(pakrevd, funksjon["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
