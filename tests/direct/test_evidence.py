"""TEST LAYER 3 — evidence binding, immutability, versioning, and the
commitment shape each evidence type's acquisition method can verify."""
from .conftest import COMMIT_REF, COMMIT_SHA

H_A = "sha256:" + "a" * 64
H_B = "sha256:" + "b" * 64
H_C = "sha256:" + "c" * 64
URL_A = "https://data.example/a.csv"
URL_B = "https://data.example/b.csv"
URL_C = "https://data.example/c.csv"


def test_submit_evidence_binds_to_agreement_and_requirement(
    direct_vm, deployed, direct_bob, active
):
    direct_vm.sender = direct_bob
    eid = deployed.submit_evidence(active, "R1", "DATASET", URL_A, H_A, "dataset")
    ev = deployed.get_evidence(active)
    assert len(ev) == 1
    assert ev[0]["evidence_id"] == eid
    assert ev[0]["agreement_id"] == active
    assert ev[0]["requirement_id"] == "R1"
    assert ev[0]["version"] == 1
    assert ev[0]["status"] == "ACTIVE"
    assert ev[0]["acquisition"] == "HTTPS_BYTES"


def test_authoritative_flag_derived(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    deployed.submit_evidence(
        active, "R1", "GITHUB_COMMIT", COMMIT_REF, "git:" + COMMIT_SHA, "commit")
    deployed.submit_evidence(
        active, "R2", "OTHER", "self-reported note", "sha256:bb", "claim")
    ev = deployed.get_evidence(active)
    by_type = {e["evidence_type"]: e for e in ev}
    assert by_type["GITHUB_COMMIT"]["authoritative"] is True
    assert by_type["OTHER"]["authoritative"] is False
    assert by_type["GITHUB_COMMIT"]["acquisition"] == "GITHUB_COMMIT"
    assert by_type["OTHER"]["acquisition"] == "UNSUPPORTED"


def test_unknown_requirement_rejected(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("unknown requirement_id"):
        deployed.submit_evidence(active, "R99", "DATASET", URL_A, H_A, "d")


def test_unsupported_evidence_type_rejected(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("unsupported evidence_type"):
        deployed.submit_evidence(active, "R1", "TELEPATHY", URL_A, H_A, "d")


def test_empty_content_hash_rejected(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("content_hash required"):
        deployed.submit_evidence(active, "R1", "DATASET", URL_A, "", "d")


def test_non_party_cannot_submit(direct_vm, deployed, direct_charlie, active):
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("not a party to this agreement"):
        deployed.submit_evidence(active, "R1", "DATASET", URL_A, H_A, "d")


# ─── the commitment must be something acquisition can verify ─────────────

def test_fetchable_type_needs_an_https_reference(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    for ref in ("internal note", "http://data.example/a.csv", "https://",
                "https://data.example/a b.csv", "ftp://data.example/a.csv"):
        with direct_vm.expect_revert("needs an https:// source_reference"):
            deployed.submit_evidence(active, "R1", "DATASET", ref, H_A, "d")


def test_fetchable_type_needs_a_sha256_identity(direct_vm, deployed, direct_bob, active):
    """A hash that could never be compared against retrieved bytes is refused
    at the door — it is not accepted now and discovered to be useless later."""
    direct_vm.sender = direct_bob
    for bad in ("sha256:aa", "md5:" + "a" * 32, "a" * 64, "sha256:" + "g" * 64):
        with direct_vm.expect_revert("expected identity must be sha256"):
            deployed.submit_evidence(active, "R1", "DATASET", URL_A, bad, "d")


def test_identity_is_stored_in_canonical_lowercase(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    deployed.submit_evidence(active, "R1", "DATASET", URL_A, "SHA256:" + "A" * 64, "d")
    assert deployed.get_evidence(active)[0]["content_hash"] == H_A


def test_github_commit_reference_and_identity_must_agree(
    direct_vm, deployed, direct_bob, active
):
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("GITHUB_COMMIT source_reference must be"):
        deployed.submit_evidence(
            active, "R3", "GITHUB_COMMIT", "https://github.com/example/delivery/commit/9fe1c2b",
            "git:9fe1c2b", "short sha")
    with direct_vm.expect_revert("GITHUB_COMMIT expected identity must be"):
        deployed.submit_evidence(
            active, "R3", "GITHUB_COMMIT", COMMIT_REF, "git:" + "0" * 40, "other sha")
    with direct_vm.expect_revert("GITHUB_COMMIT expected identity must be"):
        deployed.submit_evidence(active, "R3", "GITHUB_COMMIT", COMMIT_REF, H_A, "sha256")


def test_byte_artifact_reference_may_not_carry_a_fragment(
    direct_vm, deployed, direct_bob, active
):
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("may not carry a #fragment"):
        deployed.submit_evidence(active, "R1", "DATASET", URL_A + "#rows", H_A, "d")


def test_json_reference_fragment_must_be_a_pointer(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("must be a JSON Pointer"):
        deployed.submit_evidence(
            active, "R2", "API_RESULT", "https://api.example/r#summary", H_A, "d")
    eid = deployed.submit_evidence(
        active, "R2", "API_RESULT", "https://api.example/r#/summary", H_A, "d")
    assert eid


def test_long_reference_is_refused_not_truncated(direct_vm, deployed, direct_bob, active):
    """A silently shortened URL points at a different artifact."""
    direct_vm.sender = direct_bob
    long_ref = "https://data.example/" + "x" * 600
    with direct_vm.expect_revert("exceeds 512 characters"):
        deployed.submit_evidence(active, "R1", "DATASET", long_ref, H_A, "d")


# ─── immutability and versioning ─────────────────────────────────────────

def test_supersede_creates_new_record_and_preserves_original(
    direct_vm, deployed, direct_bob, active
):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(active, "R1", "DATASET", URL_A, H_A, "v1")
    e2 = deployed.supersede_evidence(active, e1, URL_B, H_B, "v2")

    ev = {e["evidence_id"]: e for e in deployed.get_evidence(active)}
    # original still readable, reference and hash unchanged, now SUPERSEDED
    assert ev[e1]["content_hash"] == H_A
    assert ev[e1]["source_reference"] == URL_A
    assert ev[e1]["status"] == "SUPERSEDED"
    assert ev[e1]["version"] == 1
    # replacement is a distinct record with an incremented version
    assert ev[e2]["content_hash"] == H_B
    assert ev[e2]["status"] == "ACTIVE"
    assert ev[e2]["version"] == 2
    assert e1 != e2


def test_supersede_applies_the_same_commitment_rules(
    direct_vm, deployed, direct_bob, active
):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(active, "R1", "DATASET", URL_A, H_A, "v1")
    with direct_vm.expect_revert("expected identity must be sha256"):
        deployed.supersede_evidence(active, e1, URL_B, "sha256:new", "v2")
    with direct_vm.expect_revert("needs an https:// source_reference"):
        deployed.supersede_evidence(active, e1, "somewhere else", H_B, "v2")


def test_only_submitter_may_supersede(
    direct_vm, deployed, direct_alice, direct_bob, active
):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(active, "R1", "DATASET", URL_A, H_A, "d")
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("only the original submitter may supersede"):
        deployed.supersede_evidence(active, e1, URL_B, H_B, "d2")


def test_double_supersede_rejected(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(active, "R1", "DATASET", URL_A, H_A, "d")
    deployed.supersede_evidence(active, e1, URL_B, H_B, "d2")
    with direct_vm.expect_revert("already superseded"):
        deployed.supersede_evidence(active, e1, URL_C, H_C, "d3")


def test_challenge_marks_evidence_without_deleting(
    direct_vm, deployed, direct_alice, direct_bob, active
):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(active, "R1", "DATASET", URL_A, H_A, "d")
    direct_vm.sender = direct_alice
    deployed.challenge_evidence(active, e1, "hash does not match the linked file")
    ev = {e["evidence_id"]: e for e in deployed.get_evidence(active)}
    assert ev[e1]["status"] == "CHALLENGED"
    assert ev[e1]["content_hash"] == H_A      # commitment untouched
    assert "CHALLENGED" in ev[e1]["description"]


def test_challenge_requires_reason(direct_vm, deployed, direct_alice, direct_bob, active):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(active, "R1", "DATASET", URL_A, H_A, "d")
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("challenge reason required"):
        deployed.challenge_evidence(active, e1, "")


def test_evidence_ids_are_sequential(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    e1 = deployed.submit_evidence(active, "R1", "DATASET", URL_A, H_A, "d")
    e2 = deployed.submit_evidence(active, "R2", "DATASET", URL_B, H_B, "d")
    assert e1.endswith("-E0001")
    assert e2.endswith("-E0002")
