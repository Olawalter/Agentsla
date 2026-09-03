"""Shared fixtures for the AgentSLA Core direct suite.

Direct mode runs the contract inside a real GenVM runner but exercises
the LEADER path only; validator agreement is covered separately in
tests/direct/test_equivalence.py (which drives the normaliser and
fingerprint directly) and in the integration suite against a live panel.
"""
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "agentsla_core.py"

# The canonical scenario from the spec: 100 GEN, three weighted
# requirements, R1+R2 pass and R3 fails -> provider 70 / client 30.
ESCROW = 100 * (10 ** 18)          # 100 GEN in atto

REQUIREMENTS = [
    {"requirement_id": "R1", "description": "Deliver requested dataset",
     "weight": 40, "required": True,
     "evidence_rule": "Dataset URL plus content hash"},
    {"requirement_id": "R2", "description": "Dataset meets quality requirements",
     "weight": 30, "required": True,
     "evidence_rule": "Validation report or API result"},
    {"requirement_id": "R3", "description": "Delivery before deadline",
     "weight": 30, "required": False,
     "evidence_rule": "Timestamped commit or receipt"},
]

REQUIREMENTS_JSON = json.dumps(REQUIREMENTS)


def make_verdict(agreement_id: str, statuses: dict,
                 outcome: str | None = None,
                 deadline_met: bool = True,
                 evidence_examined=None,
                 reasoning: str = "Panel evaluated each requirement.",
                 **extra) -> str:
    """Build a verdict payload.

    `statuses` maps requirement_id -> PASS/FAIL/UNDETERMINED. `outcome`
    is derived when omitted. `extra` lets a test inject hostile fields
    (e.g. provider_payout) to prove they are ignored.
    """
    reqs = [{"requirement_id": rid, "status": st} for rid, st in statuses.items()]
    vals = list(statuses.values())
    if outcome is None:
        if "UNDETERMINED" in vals:
            outcome = "UNDETERMINED"
        elif all(v == "PASS" for v in vals):
            outcome = "PASS"
        elif all(v == "FAIL" for v in vals):
            outcome = "FAIL"
        else:
            outcome = "PARTIAL"
    payload = {
        "agreement_id": agreement_id,
        "outcome": outcome,
        "requirements": reqs,
        "deadline_met": deadline_met,
        "evidence_examined": evidence_examined if evidence_examined is not None else [],
        "reasoning": reasoning,
    }
    payload.update(extra)
    return json.dumps(payload)


def mock_panel(direct_vm, verdict_json: str) -> None:
    """Register the adjudication response. LLM mocks are
    first-registered-wins in direct mode, so always clear first."""
    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*independent adjudicator on a GenLayer.*", verdict_json)


@pytest.fixture
def contract_path():
    return str(CONTRACT)


@pytest.fixture
def deployed(direct_deploy, contract_path):
    return direct_deploy(contract_path)


@pytest.fixture
def drafted(direct_vm, deployed, direct_alice, direct_bob):
    """A DRAFT agreement: Alice is client, Bob is provider."""
    direct_vm.sender = direct_alice
    return deployed.create_agreement(
        provider=str(direct_bob),
        service_description="Deliver a cleaned market dataset with validation report.",
        requirements_json=REQUIREMENTS_JSON,
        payment_amount_atto=ESCROW,
        acceptance_deadline_ticks=10,
        service_deadline_ticks=50,
        resolution_deadline_ticks=100,
        evidence_rules="Prefer authoritative sources: commits, API results, datasets.",
        settlement_rules="Weighted per-requirement payout; remainder to client.",
        penalty_bps=0,
        appeal_window_ticks=3,
    )


@pytest.fixture
def funded(direct_vm, deployed, direct_alice, drafted):
    direct_vm.sender = direct_alice
    direct_vm.value = ESCROW
    deployed.fund_agreement(drafted)
    direct_vm.value = 0
    return drafted


@pytest.fixture
def active(direct_vm, deployed, direct_bob, funded):
    direct_vm.sender = direct_bob
    deployed.accept_agreement(funded)
    return funded


@pytest.fixture
def submitted(direct_vm, deployed, direct_bob, active):
    """ACTIVE + evidence for all three requirements + delivery declared."""
    direct_vm.sender = direct_bob
    deployed.submit_evidence(
        active, "R1", "DATASET",
        "https://data.example/market-2026-q3.parquet", "sha256:d1",
        "Cleaned dataset, 2.1M rows.")
    deployed.submit_evidence(
        active, "R2", "API_RESULT",
        "https://validator.example/api/report/8821", "sha256:d2",
        "Automated quality report: 99.4% field completeness.")
    deployed.submit_evidence(
        active, "R3", "GITHUB_COMMIT",
        "https://github.com/example/delivery/commit/9fe1c2b", "sha256:d3",
        "Delivery commit.")
    deployed.submit_deliverable(active, "Dataset delivered.")
    return active
