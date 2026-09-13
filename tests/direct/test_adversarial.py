"""MANDATORY ADVERSARIAL TESTS — attacks A through I.

Each test names the attack it defends against and asserts the money did
not move, not merely that a status string changed.
"""
import json

import pytest

from .conftest import ESCROW, REQUIREMENTS_JSON, make_verdict, mock_panel

H_ORIG = "sha256:" + "1" * 64
H_NEW = "sha256:" + "2" * 64
H_OTHER = "sha256:" + "3" * 64


def _ids(deployed, aid):
    return [e["evidence_id"] for e in deployed.get_evidence(aid)]


def _adjudicate_partial(direct_vm, deployed, sender, aid):
    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
        evidence_examined=_ids(deployed, aid)))
    direct_vm.sender = sender
    return deployed.request_adjudication(aid)


# ── Attack A — fake frontend verdict ────────────────────────────────────────

def test_A_frontend_cannot_inject_a_verdict(direct_vm, deployed, submitted):
    """There is no public method that accepts a verdict as an argument.
    A frontend claiming PASS has nowhere to put it."""
    schema_methods = [m for m in dir(deployed) if not m.startswith("_")]
    for forbidden in ("set_verdict", "submit_verdict", "record_verdict",
                      "force_outcome", "set_outcome", "apply_verdict"):
        assert forbidden not in schema_methods, \
            f"contract exposes {forbidden} — a caller could inject a verdict"

    # The only route to a verdict is request_adjudication, which runs the
    # nondet block; its result is whatever the panel returned.
    a = deployed.get_agreement(submitted)
    assert a["latest_verdict_id"] == 0


# ── Attack B — modified terms after funding ─────────────────────────────────

def test_B_terms_cannot_change_after_funding(direct_vm, deployed, direct_alice, funded):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("illegal transition from FUNDED"):
        deployed.update_terms(funded, "cheaper service", REQUIREMENTS_JSON, "", "")


def test_B_terms_hash_is_stable_across_lifecycle(
    direct_vm, deployed, direct_alice, submitted
):
    before = deployed.get_agreement(submitted)["terms_hash"]
    _adjudicate_partial(direct_vm, deployed, direct_alice, submitted)
    after = deployed.get_agreement(submitted)["terms_hash"]
    assert before == after
    # and the verdict is bound to exactly that commitment
    v = deployed.get_verdict(submitted, 1)
    assert v["terms_hash"] == before


def test_B_reweighting_requirements_is_impossible_post_funding(
    direct_vm, deployed, direct_alice, funded
):
    """Attempting to shift weight to a requirement the provider passed."""
    rigged = json.dumps([
        {"requirement_id": "R1", "description": "Deliver requested dataset", "weight": 90},
        {"requirement_id": "R2", "description": "Dataset meets quality requirements", "weight": 5},
        {"requirement_id": "R3", "description": "Delivery before deadline", "weight": 5},
    ])
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("illegal transition from FUNDED"):
        deployed.update_terms(funded, "svc", rigged, "", "")


# ── Attack C — evidence replacement ─────────────────────────────────────────

def test_C_committed_evidence_cannot_be_edited(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    eid = deployed.submit_evidence(
        active, "R1", "DATASET", "https://data.example/orig.csv", H_ORIG, "v1")

    # No mutating method exists…
    schema_methods = [m for m in dir(deployed) if not m.startswith("_")]
    for forbidden in ("update_evidence", "edit_evidence", "set_evidence_hash",
                      "delete_evidence", "remove_evidence"):
        assert forbidden not in schema_methods

    # …and superseding leaves the original commitment intact.
    deployed.supersede_evidence(active, eid, "https://data.example/new.csv", H_NEW, "v2")
    ev = {e["evidence_id"]: e for e in deployed.get_evidence(active)}
    assert ev[eid]["content_hash"] == H_ORIG
    assert ev[eid]["source_reference"] == "https://data.example/orig.csv"
    assert ev[eid]["status"] == "SUPERSEDED"


# ── Attack D — unauthorized settlement ──────────────────────────────────────

def test_D_third_party_cannot_settle(
    direct_vm, deployed, direct_alice, direct_charlie, submitted
):
    _adjudicate_partial(direct_vm, deployed, direct_alice, submitted)
    for _ in range(4):
        deployed.tick()
    direct_vm.sender = direct_alice
    deployed.finalize(submitted)

    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("not a party to this agreement"):
        deployed.settle(submitted)

    # escrow untouched by the attempt
    assert deployed.get_agreement(submitted)["escrow_available"] == ESCROW


def test_D_third_party_cannot_finalize(
    direct_vm, deployed, direct_alice, direct_charlie, submitted
):
    _adjudicate_partial(direct_vm, deployed, direct_alice, submitted)
    for _ in range(4):
        deployed.tick()
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("not a party to this agreement"):
        deployed.finalize(submitted)


# ── Attack E — double settlement ────────────────────────────────────────────

def test_E_second_settlement_fails(direct_vm, deployed, direct_alice, submitted):
    _adjudicate_partial(direct_vm, deployed, direct_alice, submitted)
    for _ in range(4):
        deployed.tick()
    direct_vm.sender = direct_alice
    deployed.finalize(submitted)
    deployed.settle(submitted)

    a = deployed.get_agreement(submitted)
    assert a["status"] == "SETTLED"
    assert a["escrow_available"] == 0

    with direct_vm.expect_revert("illegal transition from SETTLED"):
        deployed.settle(submitted)

    # still zero — the failed second call moved nothing
    assert deployed.get_agreement(submitted)["escrow_available"] == 0


# ── Attack F — premature settlement (ACCEPTED != FINALIZED) ─────────────────

def test_F_accepted_cannot_settle(direct_vm, deployed, direct_alice, submitted):
    _adjudicate_partial(direct_vm, deployed, direct_alice, submitted)
    a = deployed.get_agreement(submitted)
    assert a["status"] == "ACCEPTED"

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("illegal transition from ACCEPTED"):
        deployed.settle(submitted)
    assert deployed.get_agreement(submitted)["escrow_available"] == ESCROW


def test_F_finalize_blocked_inside_appeal_window(
    direct_vm, deployed, direct_alice, submitted
):
    _adjudicate_partial(direct_vm, deployed, direct_alice, submitted)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("appeal window open until tick"):
        deployed.finalize(submitted)


# ── Attack G — settling an UNDETERMINED verdict ─────────────────────────────

def test_G_undetermined_cannot_settle(direct_vm, deployed, direct_alice, submitted):
    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "PASS", "R2": "UNDETERMINED", "R3": "PASS"},
        evidence_examined=_ids(deployed, submitted)))
    direct_vm.sender = direct_alice
    deployed.request_adjudication(submitted)

    a = deployed.get_agreement(submitted)
    assert a["status"] == "UNDETERMINED"

    with direct_vm.expect_revert("illegal transition from UNDETERMINED"):
        deployed.settle(submitted)
    with direct_vm.expect_revert("illegal transition from UNDETERMINED"):
        deployed.finalize(submitted)

    assert deployed.get_agreement(submitted)["escrow_available"] == ESCROW


# ── Attack H — malicious LLM payout field ───────────────────────────────────

def test_H_llm_payout_field_is_ignored(direct_vm, deployed, direct_alice, submitted):
    """The panel returns an enormous provider_payout. Settlement must be
    computed purely from committed weights: 70/30, not 999999999999."""
    hostile = json.loads(make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
        evidence_examined=_ids(deployed, submitted)))
    hostile["provider_payout"] = 999_999_999_999
    hostile["client_refund"] = 0
    hostile["earned_weight"] = 100          # try to override the derivation
    hostile["override_settlement"] = True
    mock_panel(direct_vm, json.dumps(hostile))

    direct_vm.sender = direct_alice
    vid = deployed.request_adjudication(submitted)

    # contract-derived weight, not the model's claim
    v = deployed.get_verdict(submitted, vid)
    assert v["earned_weight"] == 70

    for _ in range(4):
        deployed.tick()
    deployed.finalize(submitted)
    deployed.settle(submitted)

    s = deployed.get_settlement(submitted)
    assert s["provider_payout"] == ESCROW * 70 // 100
    assert s["client_refund"] == ESCROW - (ESCROW * 70 // 100)
    assert s["provider_payout"] + s["client_refund"] == ESCROW
    assert s["provider_payout"] != 999_999_999_999


# ── Attack I — cross-agreement evidence ─────────────────────────────────────

def test_I_evidence_from_another_agreement_rejected(
    direct_vm, deployed, direct_alice, direct_bob, active
):
    # second agreement, same parties
    direct_vm.sender = direct_alice
    other = deployed.create_agreement(
        str(direct_bob), "unrelated work", REQUIREMENTS_JSON,
        ESCROW, 10, 50, 100)
    direct_vm.value = ESCROW
    deployed.fund_agreement(other)
    direct_vm.value = 0
    direct_vm.sender = direct_bob
    deployed.accept_agreement(other)
    foreign = deployed.submit_evidence(
        other, "R1", "DATASET", "https://data.example/other.csv", H_OTHER, "belongs to other")

    # superseding a foreign record through THIS agreement must fail
    with direct_vm.expect_revert("belongs to"):
        deployed.supersede_evidence(active, foreign, "https://data.example/x.csv", H_NEW, "d")

    # challenging a foreign record through THIS agreement must fail
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("belongs to"):
        deployed.challenge_evidence(active, foreign, "not yours")


def test_I_verdict_citing_foreign_evidence_rejected(
    direct_vm, deployed, direct_alice, direct_bob, submitted
):
    """A panel that agrees on a foreign evidence id is still refused —
    consensus on a reference does not make the reference legitimate."""
    direct_vm.sender = direct_alice
    other = deployed.create_agreement(
        str(direct_bob), "unrelated", REQUIREMENTS_JSON, ESCROW, 10, 50, 100)
    direct_vm.value = ESCROW
    deployed.fund_agreement(other)
    direct_vm.value = 0
    direct_vm.sender = direct_bob
    deployed.accept_agreement(other)
    foreign = deployed.submit_evidence(
        other, "R1", "DATASET", "https://data.example/other.csv", H_OTHER, "foreign")

    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
        evidence_examined=_ids(deployed, submitted) + [foreign]))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("does not belong to"):
        deployed.request_adjudication(submitted)

    # rolled back cleanly and escrow intact
    assert deployed.get_agreement(submitted)["status"] == "SUBMITTED"
    assert deployed.get_agreement(submitted)["escrow_available"] == ESCROW
