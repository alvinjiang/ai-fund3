"""Smoke test the shared fakes (SPEC-DOMAIN §9.4). They must not open sockets."""

from __future__ import annotations

from tests.fakes import (
    FakeClock,
    FakeHarnessDriver,
    make_coverage,
    make_house,
    make_run,
    make_stage,
)


def test_fake_clock_advances():
    clk = FakeClock()
    t0 = clk.now()
    clk.advance(seconds=90)
    assert (clk.now() - t0).total_seconds() == 90
    assert clk.now().tzinfo is not None
    assert clk().tzinfo.utcoffset(clk()).total_seconds() == 0  # callable form is UTC


def test_fake_harness_driver_returns_canned_result_per_role():
    drv = FakeHarnessDriver(
        results={"verifier": {"status": "completed", "changes": ["v"], "predictions": []}}
    )
    aid = drv.launch({"role": "verifier"})
    assert drv.collect(aid)["changes"] == ["v"]
    # unknown role falls back to a default success result
    drv.launch({"role": "author"})
    out = drv.collect(aid)
    assert out["status"] == "completed"


def test_factories_build_a_runnable_chain(session):
    make_house(session, "gpt")
    cov = make_coverage(session, lead_house="gpt")
    run = make_run(session, cov.id, house="gpt")
    stage = make_stage(session, run.id, role="author", house="gpt")
    session.commit()
    assert stage.id is not None
    assert stage.role == "author"
    assert run.mutates_dossier is True
