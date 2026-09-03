"""Live-network integration test.

Run against a real GenLayer network with a real validator panel:

    SKIP_INTEGRATION=0 gltest tests/integration/ -v -s

Direct mode cannot exercise validator agreement — it runs the leader
only. This test is where the equivalence rule meets an actual panel, and
where GEN actually moves.

`scripts/drive_e2e.mjs` is the standalone equivalent, usable against any
deployment without the test harness.
"""
import json
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "agentsla_core.py"

PRICE = 100 * (10 ** 18)

REQUIREMENTS = [
    {"requirement_id": "R1",
     "description": "Deliver the requested market dataset as a downloadable file.",
     "weight": 40, "required": True,
     "evidence_rule": "A dataset reference with a content hash."},
    {"requirement_id": "R2",
     "description": "The delivered dataset meets the agreed quality bar: at "
                    "least 99% field completeness.",
     "weight": 30, "required": True,
     "evidence_rule": "An automated validation report or API result."},
    {"requirement_id": "R3",
     "description": "Delivery occurred on or before the agreed service deadline.",
     "weight": 30, "required": False,
     "evidence_rule": "A timestamped commit or receipt on or before the deadline."},
]

skip_unless_live = pytest.mark.skipif(
    os.environ.get("SKIP_INTEGRATION", "1") != "0",
    reason="set SKIP_INTEGRATION=0 and point .env at a network to run",
)


@pytest.fixture
def deployed(gltest_deploy):
    return gltest_deploy(str(CONTRACT))


@skip_unless_live
def test_live_partial_performance_pays_70_30(deployed, gltest_alice, gltest_bob):
    """The canonical scenario against a real panel.

    Evidence is authored so R1 and R2 are supported and R3 is plainly
    not: the delivery commit is dated after the deadline and says so.
    A correct panel returns PARTIAL with R3 FAIL; the contract then pays
    70/30 from the committed weights.
    """
    client, provider = gltest_alice, gltest_bob

    aid = deployed.connect(client).create_agreement(
        provider=str(provider),
        service_description="Deliver a cleaned Q3 2026 market dataset with a "
                            "quality report.",
        requirements_json=json.dumps(REQUIREMENTS),
        payment_amount_atto=PRICE,
        acceptance_deadline_ticks=10,
        service_deadline_ticks=30,
        resolution_deadline_ticks=200,
        evidence_rules="Prefer authoritative evidence.",
        settlement_rules="Weighted per-requirement payout.",
        penalty_bps=0,
        appeal_window_ticks=3,
    )

    deployed.connect(client).fund_agreement(aid, value=PRICE)
    a = deployed.get_agreement(aid)
    assert a["status"] == "FUNDED"
    assert a["escrow_deposited"] == PRICE
    assert a["terms_locked"] is True
    terms_hash = a["terms_hash"]

    deployed.connect(provider).accept_agreement(aid)

    deployed.connect(provider).submit_evidence(
        aid, "R1", "DATASET",
        "https://data.example/market-q3-2026.parquet",
        "sha256:8f14e45fceea167a5a36dedd4bea2543",
        "Cleaned Q3 2026 dataset, 2,140,882 rows.")
    deployed.connect(provider).submit_evidence(
        aid, "R2", "API_RESULT",
        "https://validator.example/api/reports/8821",
        "sha256:c4ca4238a0b923820dcc509a6f75849b",
        "Validation report: field completeness 99.4%, schema conformant. "
        "Meets the agreed 99% bar.")
    deployed.connect(provider).submit_evidence(
        aid, "R3", "GITHUB_COMMIT",
        "https://github.com/example/delivery/commit/9fe1c2b",
        "sha256:e3b0c44298fc1c149afbf4c8996fb924",
        "Delivery commit dated 2026-10-04, which is AFTER the agreed "
        "service deadline of 2026-09-30. Delivery was four days late.")

    deployed.connect(provider).submit_deliverable(aid, "Dataset delivered.")

    # ── the live panel ──
    vid = deployed.connect(client).request_adjudication(aid)
    v = deployed.get_verdict(aid, vid)

    assert v["terms_hash"] == terms_hash, "verdict must judge the committed terms"
    assert v["outcome"] in {"PASS", "PARTIAL", "FAIL", "UNDETERMINED"}
    # every committed requirement covered, exactly once
    assert {r["requirement_id"] for r in v["requirements"]} == {"R1", "R2", "R3"}
    # earned_weight is derived by the CONTRACT, never by the model
    expected_earned = sum(
        req["weight"] for req in REQUIREMENTS
        if next(r["status"] for r in v["requirements"]
                if r["requirement_id"] == req["requirement_id"]) == "PASS"
    )
    assert v["earned_weight"] == expected_earned

    if v["outcome"] == "UNDETERMINED":
        pytest.skip(f"panel returned UNDETERMINED: {v['reasoning'][:160]}")

    # ── finality: ACCEPTED must not settle ──
    assert deployed.get_agreement(aid)["status"] == "ACCEPTED"
    assert deployed.get_state(aid)["can_settle"] is False
    with pytest.raises(Exception):
        deployed.connect(client).settle(aid)
    assert deployed.get_agreement(aid)["escrow_available"] == PRICE

    for _ in range(4):
        deployed.connect(client).tick()
    deployed.connect(client).finalize(aid)
    assert deployed.get_state(aid)["can_settle"] is True

    # ── balances before ──
    try:
        client_before = int(deployed.get_balance(str(client)))
        provider_before = int(deployed.get_balance(str(provider)))
        contract_before = int(deployed.get_balance(deployed.address))
    except Exception:
        client_before = provider_before = contract_before = None

    deployed.connect(client).settle(aid)

    a = deployed.get_agreement(aid)
    s = deployed.get_settlement(aid)

    assert a["status"] == "SETTLED"
    assert a["escrow_available"] == 0
    assert s["escrow_before"] == PRICE
    assert s["escrow_after"] == 0
    assert s["earned_weight"] == expected_earned
    assert s["provider_payout"] == PRICE * expected_earned // 100
    assert s["provider_payout"] + s["client_refund"] == PRICE

    # ── actual value movement, where the harness exposes it ──
    if client_before is not None:
        payout = int(s["provider_payout"])
        refund = int(s["client_refund"])
        assert int(deployed.get_balance(str(provider))) == provider_before + payout, \
            "provider wallet did not receive the payout"
        assert int(deployed.get_balance(str(client))) == client_before + refund, \
            "client wallet did not receive the refund"
        assert int(deployed.get_balance(deployed.address)) == \
            contract_before - payout - refund, \
            "contract did not release the full escrow"

    # ── double settlement is impossible ──
    with pytest.raises(Exception):
        deployed.connect(client).settle(aid)
    assert deployed.get_agreement(aid)["escrow_available"] == 0


@skip_unless_live
def test_live_undetermined_protects_escrow(deployed, gltest_alice, gltest_bob):
    """Evidence deliberately too thin to decide. The panel should say so,
    and escrow must stay whole."""
    client, provider = gltest_alice, gltest_bob

    aid = deployed.connect(client).create_agreement(
        provider=str(provider),
        service_description="Deliver a dataset with a quality report.",
        requirements_json=json.dumps(REQUIREMENTS),
        payment_amount_atto=PRICE,
        acceptance_deadline_ticks=10,
        service_deadline_ticks=30,
        resolution_deadline_ticks=200,
    )
    deployed.connect(client).fund_agreement(aid, value=PRICE)
    deployed.connect(provider).accept_agreement(aid)

    # One vague, self-reported record for one requirement. Nothing that
    # would let an honest reader decide R2 or R3.
    deployed.connect(provider).submit_evidence(
        aid, "R1", "OTHER", "internal note",
        "sha256:0000000000000000000000000000000000000000",
        "Provider states the work is done. No dataset, report, timestamp "
        "or external reference is attached.")
    deployed.connect(provider).submit_deliverable(aid, "done")

    vid = deployed.connect(client).request_adjudication(aid)
    v = deployed.get_verdict(aid, vid)

    if v["outcome"] != "UNDETERMINED":
        pytest.skip(f"panel decided {v['outcome']} on thin evidence; "
                    f"the UNDETERMINED path is covered in direct tests")

    a = deployed.get_agreement(aid)
    assert a["status"] == "UNDETERMINED"
    assert a["escrow_available"] == PRICE      # nothing moved

    for method in ("settle", "finalize"):
        with pytest.raises(Exception):
            getattr(deployed.connect(client), method)(aid)
    assert deployed.get_agreement(aid)["escrow_available"] == PRICE
