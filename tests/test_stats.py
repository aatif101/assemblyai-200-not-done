import pytest

from twohundred.stats import wilson


def test_wilson_known_values():
    lo, hi = wilson(10, 40)           # 25%
    assert lo == pytest.approx(0.1419, abs=1e-3) and hi == pytest.approx(0.4019, abs=1e-3)
    lo, hi = wilson(0, 40)
    assert lo == 0.0 and hi == pytest.approx(0.0876, abs=1e-3)
    lo, hi = wilson(40, 40)
    assert hi == 1.0 and lo == pytest.approx(0.9124, abs=1e-3)


def test_wilson_rejects_bad_input():
    with pytest.raises(ValueError):
        wilson(1, 0)
    with pytest.raises(ValueError):
        wilson(5, 4)
