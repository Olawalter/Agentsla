"""TEST LAYER 4 — verdict production and validation."""
import json

from .conftest import ESCROW, make_verdict, mock_panel


def _evidence_ids(deployed, aid):
    return [e["evidence_id"] for e in deployed.get_evidence(aid)]


def test_pass_verdict(direct_vm, deployed, direct_alice, submitted):
    ids = _evidence_ids(deployed, submitted)
    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "PASS"},
        evidence_examined=ids))
    direct_vm.sender = direct_alice
    vid = deployed.request_adjudication(submitted)
    v = deployed.get_verdict(submitted, vid)
    assert v["outcome"] == "PASS"
    assert v["earned_weight"] == 100
    assert deployed.get_agreement(submitted)["status"] == "ACCEPTED"


def test_partial_verdict_earns_weighted_subset(
    direct_vm, deployed, direct_alice, submitted
):
    ids = _evidence_ids(deployed, submitted)
    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
        evidence_examined=ids))
    direct_vm.sender = direct_alice
    vid = deployed.request_adjudication(submitted)
    v = deployed.get_verdict(submitted, vid)
    assert v["outcome"] == "PARTIAL"
    assert v["earned_weight"] == 70          # 40 + 30, contract-derived
    assert v["total_weight"] == 100


def test_fail_verdict(direct_vm, deployed, direct_alice, submitted):
    ids = _evidence_ids(deployed, submitted)
    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "FAIL", "R2": "FAIL", "R3": "FAIL"},
        evidence_examined=ids))
    direct_vm.sender = direct_alice
    vid = deployed.request_adjudication(submitted)
    v = deployed.get_verdict(submitted, vid)
    assert v["outcome"] == "FAIL"
    assert v["earned_weight"] == 0


def test_undetermined_verdict_parks_agreement(
    direct_vm, deployed, direct_alice, submitted
):
    ids = _evidence_ids(deployed, submitted)
    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "PASS", "R2": "UNDETERMINED", "R3": "PASS"},
        evidence_examined=ids))
    direct_vm.sender = direct_alice
    vid = deployed.request_adjudication(submitted)
    v = deployed.get_verdict(submitted, vid)
    assert v["outcome"] == "UNDETERMINED"
    a = deployed.get_agreement(submitted)
    assert a["status"] == "UNDETERMINED"
    assert a["escrow_available"] == ESCROW      # money untouched


def test_wrong_agreement_id_rejected(direct_vm, deployed, direct_alice, submitted):
    mock_panel(direct_vm, make_verdict(
        "SLA-999999", {"R1": "PASS", "R2": "PASS", "R3": "PASS"}))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("agreement_id mismatch"):
        deployed.request_adjudication(submitted)
    # state rolled back, not stuck in ADJUDICATING
    assert deployed.get_agreement(submitted)["status"] == "SUBMITTED"


def test_unknown_requirement_rejected(direct_vm, deployed, direct_alice, submitted):
    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "PASS", "R99": "PASS"}))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("unknown requirement_id"):
        deployed.request_adjudication(submitted)


def test_missing_requirement_rejected(direct_vm, deployed, direct_alice, submitted):
    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS"}))   # R3 omitted
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("omits requirement"):
        deployed.request_adjudication(submitted)


def test_duplicate_requirement_rejected(direct_vm, deployed, direct_alice, submitted):
    raw = json.loads(make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "PASS"}))
    raw["requirements"].append({"requirement_id": "R1", "status": "FAIL"})
    mock_panel(direct_vm, json.dumps(raw))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("duplicate requirement_id"):
        deployed.request_adjudication(submitted)


def test_invalid_outcome_rejected(direct_vm, deployed, direct_alice, submitted):
    raw = json.loads(make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "PASS"}))
    raw["outcome"] = "MAYBE"
    mock_panel(direct_vm, json.dumps(raw))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("invalid outcome"):
        deployed.request_adjudication(submitted)


def test_invalid_requirement_status_rejected(direct_vm, deployed, direct_alice, submitted):
    raw = json.loads(make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "PASS"}))
    raw["requirements"][0]["status"] = "PROBABLY"
    mock_panel(direct_vm, json.dumps(raw))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("invalid status"):
        deployed.request_adjudication(submitted)


def test_missing_deadline_met_rejected(direct_vm, deployed, direct_alice, submitted):
    raw = json.loads(make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "PASS"}))
    del raw["deadline_met"]
    mock_panel(direct_vm, json.dumps(raw))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("`deadline_met` must be a boolean"):
        deployed.request_adjudication(submitted)


def test_missing_reasoning_rejected(direct_vm, deployed, direct_alice, submitted):
    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "PASS"}, reasoning=""))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("`reasoning` required"):
        deployed.request_adjudication(submitted)


def test_incoherent_pass_with_fail_rejected(direct_vm, deployed, direct_alice, submitted):
    """Outcome PASS while a requirement FAILs is internally contradictory."""
    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "FAIL"}, outcome="PASS"))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("PASS cannot carry a FAIL requirement"):
        deployed.request_adjudication(submitted)


def test_incoherent_partial_all_pass_rejected(direct_vm, deployed, direct_alice, submitted):
    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "PASS"}, outcome="PARTIAL"))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("PARTIAL requires at least one PASS and one FAIL"):
        deployed.request_adjudication(submitted)


def test_undetermined_requirement_in_decided_outcome_rejected(
    direct_vm, deployed, direct_alice, submitted
):
    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "PASS", "R2": "UNDETERMINED", "R3": "FAIL"},
        outcome="PARTIAL"))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("cannot carry an\nUNDETERMINED requirement".replace("\n", " ")):
        deployed.request_adjudication(submitted)


def test_adjudicate_needs_evidence(direct_vm, deployed, direct_alice, active):
    """ACTIVE with no evidence cannot even reach SUBMITTED, and
    request_adjudication is illegal from ACTIVE anyway."""
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("illegal transition from ACTIVE"):
        deployed.request_adjudication(active)


def test_verdict_records_terms_hash(direct_vm, deployed, direct_alice, submitted):
    ids = _evidence_ids(deployed, submitted)
    mock_panel(direct_vm, make_verdict(
        submitted, {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
        evidence_examined=ids))
    direct_vm.sender = direct_alice
    vid = deployed.request_adjudication(submitted)
    v = deployed.get_verdict(submitted, vid)
    a = deployed.get_agreement(submitted)
    assert v["terms_hash"] == a["terms_hash"]
