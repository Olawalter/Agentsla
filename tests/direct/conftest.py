"""Shared fixtures for the AgentSLA Core direct suite.

Direct mode runs the contract inside a real GenVM runner. A contract call
runs the LEADER closure; the validator closure is captured and replayed
with `direct_vm.run_validator()` in tests/direct/test_evidence_verification.py.

Evidence here is REAL BYTES served through `direct_vm.mock_web`. Every
fixture commits the identity of those bytes, computed below by an
implementation written independently of the contract's, and the contract
must fetch them and verify them itself. No mock ever says "verified".
"""
import datetime
import hashlib
import json
import pathlib
import re

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


# ─── transaction time ─────────────────────────────────────────────────────
#
# The contract reads time ONLY from the GenLayer transaction datetime.
# Tests move that datetime with gltest's own `direct_vm.warp()` — test
# infrastructure, which has no counterpart in the deployed contract.
# Every suite starts from a fixed instant so results never depend on the
# machine running them.
T0 = 1_789_344_000                      # 2026-09-14T00:00:00Z
ACCEPTANCE_WINDOW = 3600                # 1 hour
SERVICE_WINDOW = 7 * 24 * 3600          # 7 days
RESOLUTION_WINDOW = 14 * 24 * 3600      # 14 days
APPEAL_WINDOW = 24 * 3600               # 1 day
WINDOWS = (ACCEPTANCE_WINDOW, SERVICE_WINDOW, RESOLUTION_WINDOW)


def iso(unix_seconds: int) -> str:
    return datetime.datetime.fromtimestamp(
        int(unix_seconds), tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def tx_time(direct_vm) -> int:
    """The transaction datetime the next call will carry."""
    return int(datetime.datetime.fromisoformat(
        direct_vm._datetime.replace("Z", "+00:00")).timestamp())


def warp_to(direct_vm, unix_seconds: int) -> None:
    direct_vm.warp(iso(unix_seconds))


def advance(direct_vm, seconds: int) -> int:
    target = tx_time(direct_vm) + int(seconds)
    warp_to(direct_vm, target)
    return target


def pass_deadline(direct_vm, deployed, agreement_id: str, field: str,
                  by: int = 1) -> int:
    """Warp to `by` seconds after the agreement's OWN stored deadline."""
    deadline = deployed.get_agreement(agreement_id)[field]
    assert deadline > 0, f"{field} has not been set"
    warp_to(direct_vm, deadline + by)
    return deadline


# ─── identities, computed independently of the contract ───────────────────

def sha256_identity(data: bytes) -> str:
    """HTTPS_BYTES: sha256 over the exact bytes served."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def json_identity(body: bytes, pointer: str = "") -> str:
    """HTTPS_JSON: sha256 over canonical JSON of the (pointed-to) value."""
    value = json.loads(body.decode("utf-8-sig"))
    for token in [t for t in pointer.split("/")[1:]] if pointer else []:
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ─── the canonical artifacts ──────────────────────────────────────────────

DATASET_URL = "https://data.example/market-2026-q3.csv"
DATASET = (
    "date,ticker,close,volume\n"
    "2026-07-01,ACME,101.20,120400\n"
    "2026-07-02,ACME,102.05,98200\n"
    "2026-07-03,ACME,100.88,143900\n"
).encode("utf-8")

REPORT_URL = "https://validator.example/api/report/8821"
REPORT = json.dumps({
    "report_id": 8821,
    "dataset": "market-2026-q3.csv",
    "summary": {"field_completeness_pct": 99.4, "schema_conformant": True,
                "duplicate_keys": 0},
    "verdict": "meets the agreed 99% completeness bar",
}, indent=2).encode("utf-8")

COMMIT_SHA = "9fe1c2b0a4d3e6f7081928374655647382910abc"
COMMIT_REF = f"https://github.com/example/delivery/commit/{COMMIT_SHA}"
COMMIT_PATCH_URL = COMMIT_REF + ".patch"
COMMIT_PATCH = (
    f"From {COMMIT_SHA} Mon Sep 17 00:00:00 2001\n"
    "From: Provider Agent <provider@example.com>\n"
    "Date: Sun, 4 Oct 2026 10:12:00 +0000\n"
    "Subject: [PATCH] Deliver market dataset\n"
    "\n"
    "---\n"
    " market-2026-q3.csv | 4 ++++\n"
    " 1 file changed, 4 insertions(+)\n"
    "\n"
    "diff --git a/market-2026-q3.csv b/market-2026-q3.csv\n"
    "+date,ticker,close,volume\n"
).encode("utf-8")

# fetch url -> (http status, body). What an honest source serves.
ARTIFACTS = {
    DATASET_URL: (200, DATASET),
    REPORT_URL: (200, REPORT),
    COMMIT_PATCH_URL: (200, COMMIT_PATCH),
}


def mock_sources(direct_vm, overrides=None) -> None:
    """Serve artifacts by exact URL. `overrides` wins over ARTIFACTS, so a
    test can make a source lie, vanish or change. Anything not registered
    raises in direct mode, which the contract treats as unavailable."""
    served = dict(ARTIFACTS)
    served.update(overrides or {})
    for url, (status, body) in served.items():
        direct_vm.mock_web("^" + re.escape(url) + "$", {"status": status, "body": body})


def commit_canonical_evidence(deployed, agreement_id: str) -> list:
    """R1 dataset, R2 quality report, R3 delivery commit — each committed
    with the identity of the bytes its source actually serves."""
    return [
        deployed.submit_evidence(
            agreement_id, "R1", "DATASET", DATASET_URL, sha256_identity(DATASET),
            "Cleaned dataset, 2.1M rows."),
        deployed.submit_evidence(
            agreement_id, "R2", "API_RESULT", REPORT_URL, json_identity(REPORT),
            "Automated quality report: 99.4% field completeness."),
        deployed.submit_evidence(
            agreement_id, "R3", "GITHUB_COMMIT", COMMIT_REF, "git:" + COMMIT_SHA,
            "Delivery commit."),
    ]


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


def mock_panel(direct_vm, verdict_json: str, sources=None) -> None:
    """Register the sources and the model's answer. Mocks are
    first-registered-wins in direct mode, so always clear first."""
    direct_vm.clear_mocks()
    mock_sources(direct_vm, sources)
    direct_vm.mock_llm(r".*independent adjudicator on a GenLayer.*", verdict_json)


@pytest.fixture
def contract_path():
    return str(CONTRACT)


@pytest.fixture
def deployed(direct_vm, direct_deploy, contract_path):
    warp_to(direct_vm, T0)
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
        acceptance_window_seconds=ACCEPTANCE_WINDOW,
        service_window_seconds=SERVICE_WINDOW,
        resolution_window_seconds=RESOLUTION_WINDOW,
        evidence_rules="Prefer authoritative sources: commits, API results, datasets.",
        settlement_rules="Weighted per-requirement payout; remainder to client.",
        penalty_bps=0,
        appeal_window_seconds=APPEAL_WINDOW,
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
    commit_canonical_evidence(deployed, active)
    deployed.submit_deliverable(active, "Dataset delivered.")
    return active
