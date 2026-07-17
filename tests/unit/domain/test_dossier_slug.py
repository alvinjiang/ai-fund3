"""dossier_slug — SPEC-DOMAIN §4.2.

``f"{exchange}_{ticker}"`` lowercased with every run of non-alphanumerics collapsed to a
single ``_``. Computed once at coverage creation; every other module reads the column.
"""

import pytest

from core.domain.dossier_slug import compute_dossier_slug


@pytest.mark.parametrize(
    "exchange, ticker, expected",
    [
        ("tse", "2267", "tse_2267"),
        ("NYSE", "BRK.A", "nyse_brk_a"),
        ("LSE", "SHEL", "lse_shel"),
        ("SGX", "D05", "sgx_d05"),
        ("nasdaq", "AAPL", "nasdaq_aapl"),
        # non-alphanumerics collapse: "A-B.C" -> "a_b_c"; multiple -> single _
        ("x", "A-B.C", "x_a_b_c"),
        ("x", "A---B", "x_a_b"),
        # whitespace and symbols treated as separators
        ("HK EX", "0700.HK", "hk_ex_0700_hk"),
    ],
)
def test_compute_dossier_slug(exchange, ticker, expected):
    assert compute_dossier_slug(exchange, ticker) == expected


def test_slug_is_stable_idempotent():
    s = compute_dossier_slug("TSE", "2267")
    assert s == "tse_2267"
    # re-running on the already-computed slug does not change it
    assert compute_dossier_slug("tse", s.split("_", 1)[1]) == s
