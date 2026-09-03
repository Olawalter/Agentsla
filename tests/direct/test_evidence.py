"""TEST LAYER 3 — evidence binding, immutability, versioning."""
from .conftest import ESCROW, REQUIREMENTS_JSON


def test_submit_evidence_binds_to_agreement_and_requirement(
    direct_vm, deployed, direct_bob, active
):
    direct_vm.sender = direct_bob
    eid = deployed.submit_evidence(
        active, "R1", "DATASET", "https://data.example/x", "sha256:aa", "dataset")
    ev = deployed.get_evidence(active)
    assert len(ev) == 1
    assert ev[0]["evidence_id"] == eid
    assert ev[0]["agreement_id"] == active
    assert ev[0]["requirement_id"] == "R1"
    assert ev[0]["version"] == 1
    assert ev[0]["status"] == "ACTIVE"


def test_authoritative_flag_derived(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    deployed.submit_evidence(
        active, "R1", "GITHUB_COMMIT", "https://github.com/x/y/commit/abc",
        "sha256:aa", "commit")
    deployed.submit_evidence(
        active, "R2", "OTHER", "self-reported note", "sha256:bb", "claim")
    ev = deployed.get_evidence(active)
    by_type = {e["evidence_type"]: e["authoritative"] for e in ev}
    assert by_type["GITHUB_COMMIT"] is True
    assert by_type["OTHER"] is False


def test_unknown_requirement_rejected(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("unknown requirement_id"):
        deployed.submit_evidence(
            active, "R99", "DATASET", "u", "sha256:aa", "d")


def test_unsupported_evidence_type_rejected(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("unsupported evidence_type"):
        deployed.submit_evidence(
            active, "R1", "TELEPATHY", "u", "sha256:aa", "d")


def test_empty_content_hash_rejected(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("content_hash required"):
        deployed.submit_evidence(active, "R1", "DATASET", "u", "", "d")


def test_non_party_cannot_submit(direct_vm, deployed, direct_charlie, active):
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("not a party to this agreement"):
        deployed.submit_evidence(
            active, "R1", "DATASET", "u", "sha256:aa", "d")


def test_supersede_creates_new_record_and_preserves_original(
    direct_vm, deployed, direct_bob, active
):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(
        active, "R1", "DATASET", "https://old", "sha256:old", "v1")
    e2 = deployed.supersede_evidence(
        active, e1, "https://new", "sha256:new", "v2")

    ev = {e["evidence_id"]: e for e in deployed.get_evidence(active)}
    # original still readable, hash unchanged, now marked SUPERSEDED
    assert ev[e1]["content_hash"] == "sha256:old"
    assert ev[e1]["status"] == "SUPERSEDED"
    assert ev[e1]["version"] == 1
    # replacement is a distinct record with an incremented version
    assert ev[e2]["content_hash"] == "sha256:new"
    assert ev[e2]["status"] == "ACTIVE"
    assert ev[e2]["version"] == 2
    assert e1 != e2


def test_only_submitter_may_supersede(
    direct_vm, deployed, direct_alice, direct_bob, active
):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(active, "R1", "DATASET", "u", "sha256:aa", "d")
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("only the original submitter may supersede"):
        deployed.supersede_evidence(active, e1, "u2", "sha256:bb", "d2")


def test_double_supersede_rejected(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(active, "R1", "DATASET", "u", "sha256:aa", "d")
    deployed.supersede_evidence(active, e1, "u2", "sha256:bb", "d2")
    with direct_vm.expect_revert("already superseded"):
        deployed.supersede_evidence(active, e1, "u3", "sha256:cc", "d3")


def test_challenge_marks_evidence_without_deleting(
    direct_vm, deployed, direct_alice, direct_bob, active
):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(active, "R1", "DATASET", "u", "sha256:aa", "d")
    direct_vm.sender = direct_alice
    deployed.challenge_evidence(active, e1, "hash does not match the linked file")
    ev = {e["evidence_id"]: e for e in deployed.get_evidence(active)}
    assert ev[e1]["status"] == "CHALLENGED"
    assert ev[e1]["content_hash"] == "sha256:aa"      # commitment untouched
    assert "CHALLENGED" in ev[e1]["description"]


def test_challenge_requires_reason(direct_vm, deployed, direct_alice, direct_bob, active):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(active, "R1", "DATASET", "u", "sha256:aa", "d")
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("challenge reason required"):
        deployed.challenge_evidence(active, e1, "")


def test_evidence_ids_are_sequential(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(active, "R1", "DATASET", "u", "sha256:aa", "d")
    e2 = deployed.submit_evidence(active, "R2", "DATASET", "u", "sha256:bb", "d")
    assert e1.endswith("-E0001")
    assert e2.endswith("-E0002")
