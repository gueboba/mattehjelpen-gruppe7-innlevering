"""Tester for trygg tolking av matteuttrykk (backend/parsing.py)."""

import unittest

import sympy as sp

from backend.parsing import (
    TolkningsFeil,
    tolk_betingelser,
    tolk_likninger,
    tolk_matrise,
    tolk_ode,
    tolk_symbol,
    tolk_uttrykk,
    tolk_vektor,
)


class TestVanligMatte(unittest.TestCase):
    def test_uttrykk(self):
        x = sp.Symbol("x")
        self.assertEqual(tolk_uttrykk("x**2*sin(3*x)"), x**2 * sp.sin(3 * x))

    def test_potens_med_taksymbol(self):
        self.assertEqual(tolk_uttrykk("x^2"), sp.Symbol("x") ** 2)

    def test_e_er_eulers_tall_og_ln_er_log(self):
        self.assertEqual(tolk_uttrykk("e**x"), sp.exp(sp.Symbol("x")))
        self.assertEqual(tolk_uttrykk("ln(x)"), sp.log(sp.Symbol("x")))

    def test_arcsin_er_asin(self):
        self.assertEqual(tolk_uttrykk("arcsin(x)"), sp.asin(sp.Symbol("x")))

    def test_unicode_normaliseres(self):
        self.assertEqual(tolk_uttrykk("2·x"), 2 * sp.Symbol("x"))
        self.assertEqual(tolk_uttrykk("x−1"), sp.Symbol("x") - 1)

    def test_ukjent_funksjon_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_uttrykk("solve(x**2 - 4, x)")

    def test_ukjent_funksjon_tillates_i_ode(self):
        uttrykk = tolk_uttrykk("y(x).diff(x) + 4*y(x)", tillat_ukjente_funksjoner=True)
        self.assertTrue(uttrykk.has(sp.Derivative))


class TestTrygghet(unittest.TestCase):
    """Teksten kommer fra en språkmodell som styres av brukeren – vi stoler ikke på den."""

    def test_import_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_uttrykk("__import__('os').system('ls')")

    def test_innebygde_funksjoner_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_uttrykk("open('/etc/passwd')")

    def test_dunder_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_uttrykk("x.__class__")

    def test_ukjent_metode_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_uttrykk("x.system(1)")

    def test_enorm_potens_avvises_uten_a_regnes_ut(self):
        with self.assertRaises(TolkningsFeil):
            tolk_uttrykk("9**9**9**9")

    def test_stort_fakultet_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_uttrykk("factorial(100000)")

    def test_for_stor_matrise_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_matrise([[1] * 11] * 11)


class TestTvetydighet(unittest.TestCase):
    def test_sin_invers_avvises_med_forklaring(self):
        with self.assertRaises(TolkningsFeil) as feil:
            tolk_uttrykk("sin^-1(x)")
        self.assertIn("arcsin", str(feil.exception))

    def test_trigpotens_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_uttrykk("sin^2(x)")

    def test_implisitt_multiplikasjon_avvises_med_hjelp(self):
        with self.assertRaises(TolkningsFeil) as feil:
            tolk_uttrykk("2x")
        self.assertIn("2*x", str(feil.exception))


class TestLempeligTolking(unittest.TestCase):
    """Validatoren (aldri verktøyene) godtar implisitt multiplikasjon."""

    def test_strengt_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_uttrykk("x*e^(2x)")

    def test_lempelig_godtas(self):
        x = sp.Symbol("x")
        self.assertEqual(tolk_uttrykk("x*e^(2x)", lempelig=True), x * sp.exp(2 * x))
        self.assertEqual(tolk_uttrykk("2x + 3", lempelig=True), 2 * x + 3)

    def test_lempelig_odelegger_ikke_ode(self):
        # Poenget med å holde verktøyene strenge: implisitt multiplikasjon gjør
        # y(x) om til y*x, og da forsvinner differensialligningen.
        likning, _, _ = tolk_ode("Eq(y(x).diff(x, 2) + 2*y(x), 0)")
        self.assertTrue(likning.has(sp.Derivative))


class TestLigninger(unittest.TestCase):
    def test_likhetstegn_blir_eq(self):
        self.assertEqual(tolk_likninger("x**2 - 2*x - 8 = 0")[0], sp.Eq(sp.Symbol("x") ** 2 - 2 * sp.Symbol("x") - 8, 0))

    def test_uttrykk_uten_likhetstegn_betyr_null(self):
        self.assertEqual(tolk_likninger("x**2 - 4")[0].rhs, 0)

    def test_system_med_semikolon(self):
        self.assertEqual(len(tolk_likninger("x + y = 3; x - y = 1")), 2)

    def test_to_likhetstegn_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_likninger("x = 2 = 3")


class TestDifferensialligninger(unittest.TestCase):
    def test_sympy_syntaks(self):
        likning, y, x = tolk_ode("Eq(y(x).diff(x, 2) + 2*y(x), 0)")
        self.assertEqual(str(y), "y(x)")
        self.assertEqual(str(x), "x")
        self.assertTrue(likning.has(sp.Derivative))

    def test_primtegn_og_implisitt_gange(self):
        likning, _, _ = tolk_ode("y'' - 3y' + 2y = 0")
        self.assertEqual(sp.ode_order(likning, sp.Function("y")), 2)

    def test_annen_variabel(self):
        _, y, x = tolk_ode("Eq(diff(x(t), t, 2) + 2*x(t), 0)")
        self.assertEqual((str(y), str(x)), ("x(t)", "t"))

    def test_uten_derivert_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_ode("x**2 + 1 = 0")

    def test_flere_ukjente_funksjoner_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_ode("y'' + z' = 0")

    def test_startbetingelser(self):
        x = sp.Symbol("x")
        y = sp.Function("y")(x)
        self.assertEqual(tolk_betingelser("y(0)=1, y'(0)=0", y, x), [(0, 0, 1), (1, 0, 0)])

    def test_betingelse_for_feil_funksjon_avvises(self):
        x = sp.Symbol("x")
        y = sp.Function("y")(x)
        with self.assertRaises(TolkningsFeil):
            tolk_betingelser("z(0)=1", y, x)


class TestMatriser(unittest.TestCase):
    def test_liste_og_tekst_gir_samme_matrise(self):
        self.assertEqual(tolk_matrise([[1, 2], [3, 4]]), tolk_matrise("[[1, 2], [3, 4]]"))

    def test_brok_som_tekst_blir_eksakt(self):
        self.assertEqual(tolk_matrise("[[1, 1/3]]")[0, 1], sp.Rational(1, 3))

    def test_desimal_gjenkjennes_som_brok(self):
        self.assertEqual(tolk_matrise([[1, 0.3333333333333333]])[0, 1], sp.Rational(1, 3))

    def test_ujevne_rader_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_matrise([[1, 2], [3]])

    def test_vektor(self):
        self.assertEqual(tolk_vektor([1, "1/2"]), sp.Matrix([1, sp.Rational(1, 2)]))


class TestSymboler(unittest.TestCase):
    def test_gyldig_symbol(self):
        self.assertEqual(tolk_symbol("x1"), sp.Symbol("x1"))

    def test_reservert_navn_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_symbol("sin")


class TestStilleFeiltolkning(unittest.TestCase):
    """Det farlige er ikke det som avvises, men det som tolkes som noe annet."""

    def test_norsk_desimalkomma_avvises_med_nyttig_melding(self):
        # «0,5» ble Python-tuppelen (0, 5) og delte uttrykket i to.
        for tekst in ("0,5", "2,5*x", "x**2 + 0,5*x"):
            with self.subTest(tekst=tekst):
                with self.assertRaises(TolkningsFeil) as feil:
                    tolk_uttrykk(tekst)
                self.assertIn("punktum", str(feil.exception))

    def test_komma_som_skilletegn_virker_fortsatt(self):
        self.assertEqual(tolk_uttrykk("[[1,2],[3,4]]"), [[1, 2], [3, 4]])
        self.assertEqual(tolk_uttrykk("[-2, 4]"), [-2, 4])
        self.assertEqual(
            tolk_uttrykk("Integral(x, (x, 0, 1))"),
            sp.Integral(sp.Symbol("x"), (sp.Symbol("x"), 0, 1)),
        )

    def test_usynlige_tegn_fjernes(self):
        """Hardt mellomrom fra en kopiert PDF ga to tullesymboler i stedet for x + 1."""
        x = sp.Symbol("x")
        self.assertEqual(tolk_uttrykk("x + 1"), x + 1)
        self.assertEqual(tolk_uttrykk("x​+1"), x + 1)
        self.assertEqual(tolk_uttrykk("x * sin(x)"), x * sp.sin(x))
        self.assertEqual(tolk_uttrykk("x​"), x)

    def test_rottegn_uten_parentes(self):
        # «√4» ble Symbol('√4') – et navn, ikke en kvadratrot.
        self.assertEqual(tolk_uttrykk("√4"), sp.Integer(2))
        self.assertEqual(tolk_uttrykk("√x"), sp.sqrt(sp.Symbol("x")))

    def test_symbol_med_ukjente_tegn_avvises(self):
        with self.assertRaises(TolkningsFeil):
            tolk_uttrykk("x + ∮")

    def test_unicode_operatorer_tolkes_riktig(self):
        self.assertEqual(tolk_uttrykk("2−5"), sp.Integer(-3))
        self.assertEqual(tolk_uttrykk("2×5"), sp.Integer(10))
        self.assertEqual(tolk_uttrykk("2÷5"), sp.Rational(2, 5))
        self.assertEqual(tolk_uttrykk("x²"), sp.Symbol("x") ** 2)
        self.assertEqual(tolk_uttrykk("π"), sp.pi)


if __name__ == "__main__":
    unittest.main()
