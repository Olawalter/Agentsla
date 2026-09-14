"""TEST LAYER 1 — agreement state, requirements, authorization, terms."""
import json

from .conftest import ESCROW, REQUIREMENTS, REQUIREMENTS_JSON, pass_deadline


def test_protocol_info(direct_vm, deployed):
    info = deployed.get_protocol_info()
    assert info["version"] == "AgentSLA-Core-1.2.0"
    assert info["time_source"] == "GenLayer transaction datetime (Unix seconds)"
    assert "current_tick" not in info
    assert info["weight_total"] == 100
    assert info["agreement_count"] == 0
    assert "GITHUB_COMMIT" in info["authoritative_types"]
    # every type has exactly one acquisition method, stated not implied
    assert set(info["acquisition_by_type"]) == set(info["evidence_types"])
    assert info["acquisition_by_type"]["SIGNED_MESSAGE"] == "UNSUPPORTED"
    assert info["acquisition_by_type"]["API_RESULT"] == "HTTPS_JSON"


def test_create_agreement(direct_vm, deployed, direct_alice, direct_bob, drafted):
    a = deployed.get_agreement(drafted)
    assert drafted == "SLA-000001"
    assert a["status"] == "DRAFT"
    assert a["client"].lower() == str(direct_alice).lower()
    assert a["provider"].lower() == str(direct_bob).lower()
    assert a["payment_amount_atto"] == ESCROW
    assert a["requirement_count"] == 3
    assert a["terms_locked"] is False
    assert a["terms_hash"].startswith("sha256:")


def test_requirements_stored_and_sorted(direct_vm, deployed, drafted):
    reqs = deployed.get_requirements(drafted)
    assert [r["requirement_id"] for r in reqs] == ["R1", "R2", "R3"]
    assert sum(r["weight"] for r in reqs) == 100


def test_client_and_provider_must_differ(direct_vm, deployed, direct_alice):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("client and provider must differ"):
        deployed.create_agreement(
            str(direct_alice), "svc", REQUIREMENTS_JSON, 100, 5, 10, 20)


def test_weights_must_sum_to_100(direct_vm, deployed, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    bad = json.dumps([
        {"requirement_id": "R1", "description": "d", "weight": 40},
        {"requirement_id": "R2", "description": "d", "weight": 30},
    ])
    with direct_vm.expect_revert("weights must sum to exactly 100"):
        deployed.create_agreement(str(direct_bob), "svc", bad, 100, 5, 10, 20)


def test_duplicate_requirement_id_rejected(direct_vm, deployed, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    bad = json.dumps([
        {"requirement_id": "R1", "description": "d", "weight": 50},
        {"requirement_id": "R1", "description": "d", "weight": 50},
    ])
    with direct_vm.expect_revert("duplicate requirement_id"):
        deployed.create_agreement(str(direct_bob), "svc", bad, 100, 5, 10, 20)


def test_empty_description_rejected(direct_vm, deployed, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    bad = json.dumps([{"requirement_id": "R1", "description": "", "weight": 100}])
    with direct_vm.expect_revert("description required"):
        deployed.create_agreement(str(direct_bob), "svc", bad, 100, 5, 10, 20)


def test_zero_weight_rejected(direct_vm, deployed, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    bad = json.dumps([
        {"requirement_id": "R1", "description": "d", "weight": 0},
        {"requirement_id": "R2", "description": "d", "weight": 100},
    ])
    with direct_vm.expect_revert("must be 1..100"):
        deployed.create_agreement(str(direct_bob), "svc", bad, 100, 5, 10, 20)


def test_empty_requirements_rejected(direct_vm, deployed, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("at least one requirement"):
        deployed.create_agreement(str(direct_bob), "svc", "[]", 100, 5, 10, 20)


def test_malformed_requirements_json_rejected(direct_vm, deployed, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("requirements must be valid JSON"):
        deployed.create_agreement(str(direct_bob), "svc", "{nope", 100, 5, 10, 20)


def test_windows_are_bounded_durations(direct_vm, deployed, direct_alice, direct_bob):
    """A window is a term in seconds, bounded both ways. Too short to act in,
    or so long it is a disguised absolute timestamp, is refused."""
    direct_vm.sender = direct_alice
    day = 24 * 3600
    cases = [
        ((59, day, day), "acceptance_window_seconds must be 60.."),
        ((3600, 0, day), "service_window_seconds must be 60.."),
        ((3600, day, -5), "resolution_window_seconds must be 60.."),
        # a far-future Unix timestamp passed where a duration belongs
        ((3600, 4_102_444_800, day), "service_window_seconds must be 60.."),
    ]
    for windows, message in cases:
        with direct_vm.expect_revert(message):
            deployed.create_agreement(str(direct_bob), "svc", REQUIREMENTS_JSON, 100, *windows)
    with direct_vm.expect_revert("appeal_window_seconds must be 600.."):
        deployed.create_agreement(str(direct_bob), "svc", REQUIREMENTS_JSON, 100,
                                  3600, day, day, "", "", 0, 599)


def test_zero_payment_rejected(direct_vm, deployed, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("payment_amount must be > 0"):
        deployed.create_agreement(
            str(direct_bob), "svc", REQUIREMENTS_JSON, 0, 5, 10, 20)


def test_update_terms_in_draft_changes_hash(direct_vm, deployed, direct_alice, drafted):
    before = deployed.get_agreement(drafted)["terms_hash"]
    direct_vm.sender = direct_alice
    after = deployed.update_terms(
        drafted, "Revised service description", REQUIREMENTS_JSON, "rules", "settle")
    assert after != before
    assert deployed.get_agreement(drafted)["terms_hash"] == after


def test_only_client_may_update_terms(direct_vm, deployed, direct_bob, drafted):
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("client only"):
        deployed.update_terms(drafted, "hijack", REQUIREMENTS_JSON, "", "")


def test_accept_requires_designated_provider(
    direct_vm, deployed, direct_charlie, funded
):
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("provider only"):
        deployed.accept_agreement(funded)


def test_accept_moves_to_active(direct_vm, deployed, direct_bob, funded):
    direct_vm.sender = direct_bob
    deployed.accept_agreement(funded)
    assert deployed.get_agreement(funded)["status"] == "ACTIVE"


def test_accept_after_deadline_refused(direct_vm, deployed, direct_bob, funded):
    pass_deadline(direct_vm, deployed, funded, "acceptance_deadline")
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("acceptance deadline passed"):
        deployed.accept_agreement(funded)


def test_submit_deliverable_requires_evidence(direct_vm, deployed, direct_bob, active):
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("submit evidence before declaring delivery"):
        deployed.submit_deliverable(active, "done")


def test_expire_before_deadline_refused(direct_vm, deployed, direct_alice, active):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("service deadline not reached"):
        deployed.expire_agreement(active)


def test_expire_after_deadline(direct_vm, deployed, direct_alice, active):
    pass_deadline(direct_vm, deployed, active, "service_deadline")
    direct_vm.sender = direct_alice
    deployed.expire_agreement(active)
    assert deployed.get_agreement(active)["status"] == "EXPIRED"


def test_get_state_probe(direct_vm, deployed, funded):
    st = deployed.get_state(funded)
    assert st["status"] == "FUNDED"
    assert st["terms_locked"] is True
    assert st["can_settle"] is False
    assert st["escrow_available"] == ESCROW


def test_list_agreements(direct_vm, deployed, direct_alice, direct_bob, drafted):
    direct_vm.sender = direct_alice
    deployed.create_agreement(
        str(direct_bob), "second", REQUIREMENTS_JSON, 500, 3600, 86400, 86400)
    page = deployed.list_agreements(0, 10)
    assert page["total"] == 2
    assert [r["agreement_id"] for r in page["rows"]] == ["SLA-000001", "SLA-000002"]
