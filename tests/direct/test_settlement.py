"""Required scenarios: E2E (§44), UNDETERMINED (§45), finality (§46),
plus settlement arithmetic and partial performance."""
import json

import pytest

from .conftest import (ESCROW, REQUIREMENTS_JSON, WINDOWS, commit_canonical_evidence,
                       pass_deadline,
                       json_identity, make_verdict, mock_panel)

# A re-run of the quality report, served from a second, stable endpoint.
REPORT_RERUN_URL = "https://validator.example/api/report/8822"
REPORT_RERUN = b'{"report_id": 8822, "summary": {"field_completeness_pct": 99.6}}'


def _ids(deployed, aid):
    return [e["evidence_id"] for e in deployed.get_evidence(aid)]


# ═══ §44 — the required end-to-end scenario ══════════════════════════════════

def test_E2E_partial_performance_70_30(
    direct_vm, deployed, direct_alice, direct_bob, submitted
):
    """100 GEN escrow, R1 PASS (40) + R2 PASS (30) + R3 FAIL (30)
    → provider 70, client 30, escrow 0, SETTLED."""
    aid = submitted

    # state before adjudication
    a = deployed.get_agreement(aid)
    assert a["status"] == "SUBMITTED"
    assert a["escrow_available"] == ESCROW

    # ── adjudication ──
    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
        deadline_met=True, evidence_examined=_ids(deployed, aid),
        reasoning="Dataset and quality report support R1/R2; delivery commit "
                  "postdates the service deadline so R3 fails."))
    direct_vm.sender = direct_alice
    vid = deployed.request_adjudication(aid)

    v = deployed.get_verdict(aid, vid)
    assert v["outcome"] == "PARTIAL"
    assert {r["requirement_id"]: r["status"] for r in v["requirements"]} == {
        "R1": "PASS", "R2": "PASS", "R3": "FAIL"}
    assert v["earned_weight"] == 70
    assert deployed.get_agreement(aid)["status"] == "ACCEPTED"

    # ── finality: ACCEPTED cannot settle ──
    with direct_vm.expect_revert("illegal transition from ACCEPTED"):
        deployed.settle(aid)

    pass_deadline(direct_vm, deployed, aid, "appeal_deadline")
    deployed.finalize(aid)
    assert deployed.get_agreement(aid)["status"] == "FINALIZED"
    assert deployed.get_state(aid)["can_settle"] is True

    # ── settlement ──
    deployed.settle(aid)

    a = deployed.get_agreement(aid)
    s = deployed.get_settlement(aid)

    expected_provider = ESCROW * 70 // 100
    expected_client = ESCROW - expected_provider

    assert a["status"] == "SETTLED"
    assert a["escrow_available"] == 0
    assert a["escrow_released"] == ESCROW
    assert s["provider_payout"] == expected_provider
    assert s["client_refund"] == expected_client
    assert s["earned_weight"] == 70
    assert s["total_weight"] == 100
    assert s["escrow_before"] == ESCROW
    assert s["escrow_after"] == 0
    assert s["provider_payout"] + s["client_refund"] == ESCROW
    # spec's headline numbers, in whole GEN
    assert expected_provider == 70 * (10 ** 18)
    assert expected_client == 30 * (10 ** 18)


# ═══ §45 — the required UNDETERMINED scenario ════════════════════════════════

def test_UNDETERMINED_protects_escrow_then_retries(
    direct_vm, deployed, direct_alice, direct_bob, submitted
):
    aid = submitted

    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "PASS", "R2": "UNDETERMINED", "R3": "PASS"},
        evidence_examined=_ids(deployed, aid),
        reasoning="The quality report link returns nothing verifiable; R2 "
                  "cannot be decided on the record as it stands."))
    direct_vm.sender = direct_alice
    v1 = deployed.request_adjudication(aid)

    a = deployed.get_agreement(aid)
    assert deployed.get_verdict(aid, v1)["outcome"] == "UNDETERMINED"
    assert a["status"] == "UNDETERMINED"
    assert a["escrow_available"] == ESCROW          # nothing moved
    assert a["adjudication_round"] == 1

    # no settlement path is open
    with direct_vm.expect_revert("illegal transition from UNDETERMINED"):
        deployed.settle(aid)
    with direct_vm.expect_revert("illegal transition from UNDETERMINED"):
        deployed.finalize(aid)
    with direct_vm.expect_revert("illegal transition from UNDETERMINED"):
        deployed.appeal(aid, "trying to skip ahead")

    # retry path: more evidence, then another round
    direct_vm.sender = direct_bob
    deployed.submit_evidence(
        aid, "R2", "API_RESULT", REPORT_RERUN_URL, json_identity(REPORT_RERUN),
        "Re-run of the quality report with a stable endpoint.")

    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "PASS", "R2": "PASS", "R3": "PASS"},
        evidence_examined=_ids(deployed, aid),
        reasoning="The re-run report resolves R2."),
        sources={REPORT_RERUN_URL: (200, REPORT_RERUN)})
    direct_vm.sender = direct_alice
    v2 = deployed.request_adjudication(aid)

    assert v2 != v1
    assert deployed.get_verdict(aid, v2)["outcome"] == "PASS"
    assert deployed.get_verdict(aid, v2)["earned_weight"] == 100
    assert deployed.get_agreement(aid)["status"] == "ACCEPTED"
    # both rounds retained
    assert deployed.list_verdicts(aid) == [v1, v2]


def test_UNDETERMINED_recovery_after_resolution_deadline(
    direct_vm, deployed, direct_alice, submitted
):
    """If nobody can resolve it, escrow still has an exit."""
    aid = submitted
    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "UNDETERMINED", "R2": "UNDETERMINED", "R3": "UNDETERMINED"},
        evidence_examined=_ids(deployed, aid)))
    direct_vm.sender = direct_alice
    deployed.request_adjudication(aid)
    assert deployed.get_agreement(aid)["status"] == "UNDETERMINED"

    pass_deadline(direct_vm, deployed, aid, "resolution_deadline")
    deployed.recover_escrow(aid)
    a = deployed.get_agreement(aid)
    assert a["status"] == "REFUNDED"
    assert a["escrow_available"] == 0


# ═══ §46 — the required finality scenario ════════════════════════════════════

def test_FINALITY_accepted_is_not_finalized(direct_vm, deployed, direct_alice, submitted):
    aid = submitted
    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "PASS", "R2": "PASS", "R3": "PASS"},
        evidence_examined=_ids(deployed, aid)))
    direct_vm.sender = direct_alice
    deployed.request_adjudication(aid)

    assert deployed.get_agreement(aid)["status"] == "ACCEPTED"
    assert deployed.get_state(aid)["can_settle"] is False
    with direct_vm.expect_revert("illegal transition from ACCEPTED"):
        deployed.settle(aid)

    pass_deadline(direct_vm, deployed, aid, "appeal_deadline")
    deployed.finalize(aid)
    assert deployed.get_state(aid)["can_settle"] is True
    deployed.settle(aid)
    assert deployed.get_agreement(aid)["status"] == "SETTLED"


def test_FINALITY_appeal_prevents_stale_settlement(
    direct_vm, deployed, direct_alice, direct_bob, submitted
):
    """An accepted PASS is appealed; the re-adjudication says PARTIAL.
    The original PASS must never be the basis for settlement."""
    aid = submitted
    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "PASS", "R2": "PASS", "R3": "PASS"},
        evidence_examined=_ids(deployed, aid)))
    direct_vm.sender = direct_alice
    v1 = deployed.request_adjudication(aid)
    assert deployed.get_verdict(aid, v1)["earned_weight"] == 100

    # client appeals inside the window
    deployed.appeal(aid, "R3 evidence postdates the deadline")
    assert deployed.get_agreement(aid)["status"] == "APPEALED"

    # the stale PASS cannot settle
    with direct_vm.expect_revert("illegal transition from APPEALED"):
        deployed.settle(aid)
    with direct_vm.expect_revert("illegal transition from APPEALED"):
        deployed.finalize(aid)

    # re-adjudication overturns it
    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
        evidence_examined=_ids(deployed, aid),
        reasoning="On review the delivery commit is after the deadline."))
    v2 = deployed.request_adjudication(aid)
    assert deployed.get_verdict(aid, v2)["earned_weight"] == 70

    pass_deadline(direct_vm, deployed, aid, "appeal_deadline")
    deployed.finalize(aid)
    deployed.settle(aid)

    s = deployed.get_settlement(aid)
    # settled on the SECOND verdict, not the stale first one
    assert s["verdict_id"] == v2
    assert s["earned_weight"] == 70
    assert s["provider_payout"] == ESCROW * 70 // 100


def test_appeal_after_window_closed_refused(direct_vm, deployed, direct_alice, submitted):
    aid = submitted
    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "PASS", "R2": "PASS", "R3": "PASS"},
        evidence_examined=_ids(deployed, aid)))
    direct_vm.sender = direct_alice
    deployed.request_adjudication(aid)
    pass_deadline(direct_vm, deployed, aid, "appeal_deadline")
    with direct_vm.expect_revert("appeal window closed"):
        deployed.appeal(aid, "too late")


# ═══ settlement arithmetic ═══════════════════════════════════════════════════

def test_full_pass_pays_provider_everything(direct_vm, deployed, direct_alice, submitted):
    aid = submitted
    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "PASS", "R2": "PASS", "R3": "PASS"},
        evidence_examined=_ids(deployed, aid)))
    direct_vm.sender = direct_alice
    deployed.request_adjudication(aid)
    pass_deadline(direct_vm, deployed, aid, "appeal_deadline")
    deployed.finalize(aid)
    deployed.settle(aid)
    s = deployed.get_settlement(aid)
    assert s["provider_payout"] == ESCROW
    assert s["client_refund"] == 0


def test_full_fail_refunds_client_everything(direct_vm, deployed, direct_alice, submitted):
    aid = submitted
    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "FAIL", "R2": "FAIL", "R3": "FAIL"},
        evidence_examined=_ids(deployed, aid)))
    direct_vm.sender = direct_alice
    deployed.request_adjudication(aid)
    pass_deadline(direct_vm, deployed, aid, "appeal_deadline")
    deployed.finalize(aid)
    deployed.settle(aid)
    s = deployed.get_settlement(aid)
    assert s["provider_payout"] == 0
    assert s["client_refund"] == ESCROW


def test_rounding_remainder_goes_to_client(
    direct_vm, deployed, direct_alice, direct_bob
):
    """An escrow that does not divide evenly by the earned weight must
    still balance exactly, with the remainder favouring the client."""
    odd_escrow = 1_000_000_000_000_000_007      # deliberately not divisible
    direct_vm.sender = direct_alice
    aid = deployed.create_agreement(
        str(direct_bob), "odd-amount job", REQUIREMENTS_JSON,
        odd_escrow, *WINDOWS)
    direct_vm.value = odd_escrow
    deployed.fund_agreement(aid)
    direct_vm.value = 0
    direct_vm.sender = direct_bob
    deployed.accept_agreement(aid)
    commit_canonical_evidence(deployed, aid)
    deployed.submit_deliverable(aid, "done")

    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
        evidence_examined=_ids(deployed, aid)))
    direct_vm.sender = direct_alice
    deployed.request_adjudication(aid)
    pass_deadline(direct_vm, deployed, aid, "appeal_deadline")
    deployed.finalize(aid)
    deployed.settle(aid)

    s = deployed.get_settlement(aid)
    expected_provider = odd_escrow * 70 // 100      # floor
    assert s["provider_payout"] == expected_provider
    assert s["client_refund"] == odd_escrow - expected_provider
    assert s["provider_payout"] + s["client_refund"] == odd_escrow
    assert s["escrow_after"] == 0


def _penalised_settlement(direct_vm, deployed, alice, bob, deliver_late: bool,
                          panel_says_deadline_met: bool):
    alice_sender, bob_sender = alice, bob
    direct_vm.sender = alice_sender
    aid = deployed.create_agreement(
        str(bob_sender), "penalised job", REQUIREMENTS_JSON,
        ESCROW, *WINDOWS, "", "", 1000)      # penalty_bps = 10%
    direct_vm.value = ESCROW
    deployed.fund_agreement(aid)
    direct_vm.value = 0
    direct_vm.sender = bob_sender
    deployed.accept_agreement(aid)
    commit_canonical_evidence(deployed, aid)
    if deliver_late:
        pass_deadline(direct_vm, deployed, aid, "service_deadline", by=3600)
    deployed.submit_deliverable(aid, "delivered")

    mock_panel(direct_vm, make_verdict(
        aid, {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
        deadline_met=panel_says_deadline_met, evidence_examined=_ids(deployed, aid)))
    direct_vm.sender = alice_sender
    deployed.request_adjudication(aid)
    pass_deadline(direct_vm, deployed, aid, "appeal_deadline")
    deployed.finalize(aid)
    deployed.settle(aid)
    return deployed.get_agreement(aid), deployed.get_settlement(aid)


def test_penalty_applies_when_delivery_transaction_was_late(
    direct_vm, deployed, direct_alice, direct_bob
):
    """Lateness is the recorded delivery datetime against the recorded
    service deadline — even when the panel's reading says on time."""
    a, s = _penalised_settlement(direct_vm, deployed, direct_alice, direct_bob,
                                 deliver_late=True, panel_says_deadline_met=True)
    assert a["delivered_late"] is True
    assert a["delivered_at"] == a["service_deadline"] + 3600
    gross = ESCROW * 70 // 100
    penalty = gross * 1000 // 10_000
    assert s["penalty"] == penalty
    assert s["provider_payout"] == gross - penalty
    assert s["provider_payout"] + s["client_refund"] == ESCROW


def test_no_penalty_for_on_time_delivery_whatever_the_panel_says(
    direct_vm, deployed, direct_alice, direct_bob
):
    """A model that believes delivery was late cannot cost the provider:
    whether time elapsed is not a question a model answers."""
    a, s = _penalised_settlement(direct_vm, deployed, direct_alice, direct_bob,
                                 deliver_late=False, panel_says_deadline_met=False)
    assert a["delivered_late"] is False
    assert s["penalty"] == 0
    assert s["provider_payout"] == ESCROW * 70 // 100


def test_settlement_requires_a_verdict(direct_vm, deployed, direct_alice, funded):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("illegal transition from FUNDED"):
        deployed.settle(funded)
