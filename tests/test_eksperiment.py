"""Tester for fasitsjekken i eksperimentet (scripts/eksperiment.py).

Tallene i EKSPERIMENT.md og refleksjonsnotatet hviler på denne sjekken. Sier
den «ja» om et galt svar, er hele Del B-konklusjonen feil – og det er nettopp
på de store tallene modellene bommer, så det er der sjekken må være strengest.
"""

import importlib.util
import unittest
from pathlib import Path

import sympy as sp

_STI = Path(__file__).resolve().parent.parent / "scripts" / "eksperiment.py"
_spec = importlib.util.spec_from_file_location("eksperiment", _STI)
eksperiment = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eksperiment)


class TestFasitsjekkStoreTall(unittest.TestCase):
    """Den relative toleransen på 1e-9 ga over en million i slakk."""

    FASIT = sp.Integer(1082152110028959)  # 87654321 * 12345679

    def test_riktig_svar_godkjennes(self):
        self.assertTrue(eksperiment._tall_lik_tekst(str(self.FASIT), self.FASIT))

    def test_svar_som_er_en_for_lite_underkjennes(self):
        self.assertFalse(eksperiment._tall_lik_tekst(str(self.FASIT - 1), self.FASIT))

    def test_modellens_faktiske_feilsvar_underkjennes(self):
        # Det den største modellen svarte uten verktøy – 87,7 millioner feil.
        self.assertFalse(eksperiment._tall_lik_tekst("1082152022374659", self.FASIT))

    def test_stort_fakultet_med_feil_pa_en_underkjennes(self):
        fasit = sp.factorial(20)
        self.assertTrue(eksperiment._tall_lik_tekst(str(fasit), fasit))
        self.assertFalse(eksperiment._tall_lik_tekst(str(fasit + 1), fasit))

    def test_desimalslakken_skaleres_ikke_med_fasiten(self):
        # «1082152000000000.0» er 110 millioner feil, men ble godkjent fordi
        # slakken ble ganget med fasitens størrelse.
        self.assertTrue(eksperiment._tall_lik_tekst(f"{self.FASIT}.0", self.FASIT))
        self.assertFalse(eksperiment._tall_lik_tekst("1082152000000000.0", self.FASIT))

    def test_korrekt_avrundet_desimalsvar_godkjennes_fortsatt(self):
        fasit = sp.Rational(1534, 10000)  # 0,1534
        self.assertTrue(eksperiment._tall_lik_tekst("0.1534", fasit))
        self.assertFalse(eksperiment._tall_lik_tekst("0.1535", fasit))

    def test_eksakte_symbolske_svar_godkjennes(self):
        self.assertTrue(
            eksperiment._tall_lik_tekst("64 + 64*sqrt(3)*I", 64 + 64 * sp.sqrt(3) * sp.I)
        )
        self.assertFalse(
            eksperiment._tall_lik_tekst("64 + 63*sqrt(3)*I", 64 + 64 * sp.sqrt(3) * sp.I)
        )


class TestLosningsformer(unittest.TestCase):
    """Modellene skriver samme svar på mange måter; alle riktige former skal telle."""

    FASIT = {"x": sp.Integer(3), "y": sp.Integer(-24), "z": sp.Integer(30)}

    def test_navngitte_verdier(self):
        self.assertTrue(eksperiment._losning_lik("x = 3, y = -24, z = 30", self.FASIT))
        self.assertTrue(eksperiment._losning_lik("{x: 3, y: -24, z: 30}", self.FASIT))

    def test_losningsvektor(self):
        self.assertTrue(eksperiment._losning_lik("[3, -24, 30]", self.FASIT))

    def test_feil_verdi_underkjennes(self):
        self.assertFalse(eksperiment._losning_lik("[3, -24, 31]", self.FASIT))
        self.assertFalse(eksperiment._losning_lik("x = 3, y = -24, z = 31", self.FASIT))

    def test_feil_rekkefolge_i_vektor_underkjennes(self):
        self.assertFalse(eksperiment._losning_lik("[30, -24, 3]", self.FASIT))


if __name__ == "__main__":
    unittest.main()
