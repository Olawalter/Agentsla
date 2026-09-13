"""Live integration: contract-side evidence acquisition on a real panel.

    SKIP_INTEGRATION=0 pytest tests/integration -v -s

The evidence is real and hosted, pinned to one commit of this repository
(branch `live-evidence`): a daily price dataset, a quality report about it,
and the commit that delivered them. The agreement's stated deadline
(2026-09-01) is before that commit's date, so a correct panel fails R3.

The provider commits identities computed by scripts/evidence_identity.py
from what the sources serve. The contract is then on its own: during
adjudication the leader and every validator fetch the three references,
verify the bytes, and judge the verified artifacts. The descriptions say
nothing useful on purpose — the verdict has to come from the artifacts.

Scenario 1 — PARTIAL: R1 PASS, R2 PASS, R3 FAIL, finalised, settled 70/30.
Scenario 2 — nothing verifiable: HASH_MISMATCH, 404, UNSUPPORTED →
UNDETERMINED with no model consulted, escrow untouched.
"""
import hashlib
import importlib.util
import json
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]

EVIDENCE_COMMIT = "8517e9ab0848558b790cee8f8c9a0e533ec7cc3a"
RAW = f"https://raw.githubusercontent.com/Olawalter/Agentsla/{EVIDENCE_COMMIT}/evidence"
DATASET_URL = f"{RAW}/acme-2026-07-daily.csv"
REPORT_URL = f"{RAW}/quality-report.json#/summary"
MISSING_URL = f"{RAW}/quality-report-v2.json"
COMMIT_REF = f"https://github.com/Olawalter/Agentsla/commit/{EVIDENCE_COMMIT}"

PRICE = 10 ** 17        # 0.1 GEN: large enough that 70/30 is exact

REQUIREMENTS = [
    {"requirement_id": "R1", "weight": 40, "required": True,
     "description": "Deliver ACME daily closing price and volume data for July "
                    "2026 as a downloadable CSV file.",
     "evidence_rule": "The CSV file itself."},
    {"requirement_id": "R2", "weight": 30, "required": True,
     "description": "An automated quality check of the delivered dataset reports "
                    "field completeness of at least 99%.",
     "evidence_rule": "The quality report's summary."},
    {"requirement_id": "R3", "weight": 30, "required": False,
     "description": "Delivery occurred on or before the service deadline of "
                    "2026-09-01T00:00:00Z.",
     "evidence_rule": "The date of the commit that delivered the files."},
]


def _identity_tool():
    spec = importlib.util.spec_from_file_location(
        "evidence_identity", ROOT / "scripts" / "evidence_identity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _served(tool, etype, reference):
    req = urllib.request.Request(tool.fetch_url(etype, reference),
                                 headers={"User-Agent": "agentsla-integration"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _agreement(live, description):
    before = live.read("get_protocol_info")["agreement_count"]
    live.write(live.client_acct, "create_agreement",
               live.provider_acct.address, description, json.dumps(REQUIREMENTS),
               PRICE, 10, 30, 200,
               "Only artifacts retrieved and verified during adjudication count.",
               "Weighted per-requirement payout; rounding remainder to the client.",
               0, 3, step="create_agreement")
    aid = f"SLA-{before + 1:06d}"
    assert live.read("get_agreement", aid)["status"] == "DRAFT"
    live.write(live.client_acct, "fund_agreement", aid, value=PRICE)
    live.write(live.provider_acct, "accept_agreement", aid)
    return aid


def test_live_verified_evidence_partial_settles_70_30(live):
    tool = _identity_tool()
    dataset_id = tool.identity("DATASET", DATASET_URL, _served(tool, "DATASET", DATASET_URL))
    report_id = tool.identity("API_RESULT", REPORT_URL, _served(tool, "API_RESULT", REPORT_URL))
    commit_id = tool.identity("GITHUB_COMMIT", COMMIT_REF, _served(tool, "GITHUB_COMMIT", COMMIT_REF))

    print("\nSCENARIO 1 — verified evidence, partial performance")
    aid = _agreement(live, "Deliver the ACME July 2026 daily dataset with a quality "
                           "report, by 2026-09-01T00:00:00Z.")
    live.write(live.provider_acct, "submit_evidence", aid, "R1", "DATASET", DATASET_URL,
               dataset_id, "Dataset delivered.", step="submit_evidence R1")
    live.write(live.provider_acct, "submit_evidence", aid, "R2", "API_RESULT", REPORT_URL,
               report_id, "Quality report.", step="submit_evidence R2")
    live.write(live.provider_acct, "submit_evidence", aid, "R3", "GITHUB_COMMIT", COMMIT_REF,
               commit_id, "Delivered on time.", step="submit_evidence R3")
    live.write(live.provider_acct, "submit_deliverable", aid, "Delivered.")

    adj = live.write(live.client_acct, "request_adjudication", aid)
    assert not adj["reverted"], adj.get("refusal")
    state = live.read("get_agreement", aid)
    assert state["latest_verdict_id"] == 1, "the round must have committed a verdict"
    v = live.read("get_verdict", aid, 1)
    live.record["scenario_1"] = {"agreement_id": aid, "verdict": v}

    rows = {r["requirement_id"]: r for r in v["evidence_verification"]}
    for rid in ("R1", "R2", "R3"):
        assert rows[rid]["status"] == "VERIFIED", rows[rid]
        assert rows[rid]["observed_identity"] == rows[rid]["expected_identity"]
    assert {r["requirement_id"]: r["status"] for r in v["requirements"]} == {
        "R1": "PASS", "R2": "PASS", "R3": "FAIL"}, v["reasoning"]
    assert v["outcome"] == "PARTIAL"
    assert v["earned_weight"] == 70
    assert state["status"] == "ACCEPTED"

    early = live.write(live.client_acct, "settle", aid, step="settle while ACCEPTED")
    assert early["reverted"] and "illegal transition from ACCEPTED" in early["refusal"]
    assert live.read("get_agreement", aid)["escrow_available"] == PRICE

    for i in range(4):
        live.write(live.client_acct, "tick", step=f"tick {i + 1}")
    live.write(live.client_acct, "finalize", aid)
    assert live.read("get_agreement", aid)["status"] == "FINALIZED"

    provider_before = live.balance(live.provider_acct.address)
    contract_before = live.balance(live.address)
    live.write(live.client_acct, "settle", aid, wait="FINALIZED")
    s = live.read("get_settlement", aid)
    live.record["scenario_1"]["settlement"] = s
    assert s["provider_payout"] == PRICE * 70 // 100
    assert s["client_refund"] == PRICE - PRICE * 70 // 100
    assert s["escrow_after"] == 0
    assert live.read("get_agreement", aid)["status"] == "SETTLED"

    def paid():
        return live.balance(live.provider_acct.address) == provider_before + s["provider_payout"]
    live._await(paid, "provider payout", tries=60)
    live.record["scenario_1"]["balances"] = {
        "provider_delta": live.balance(live.provider_acct.address) - provider_before,
        "contract_delta": live.balance(live.address) - contract_before,
    }
    assert live.record["scenario_1"]["balances"]["contract_delta"] == -PRICE


def test_live_unverifiable_evidence_is_undetermined(live):
    tool = _identity_tool()
    served_dataset = _served(tool, "DATASET", DATASET_URL)
    wrong_id = "sha256:" + hashlib.sha256(served_dataset + b"\n# altered").hexdigest()
    report_id = tool.identity("API_RESULT", REPORT_URL, _served(tool, "API_RESULT", REPORT_URL))

    print("\nSCENARIO 2 — nothing verifiable")
    aid = _agreement(live, "Same deliverable, evidenced badly.")
    live.write(live.provider_acct, "submit_evidence", aid, "R1", "DATASET", DATASET_URL,
               wrong_id, "Requirement completed.", step="submit_evidence R1 (wrong hash)")
    live.write(live.provider_acct, "submit_evidence", aid, "R2", "API_RESULT", MISSING_URL,
               report_id, "Requirement completed.", step="submit_evidence R2 (404)")
    live.write(live.provider_acct, "submit_evidence", aid, "R3", "SIGNED_MESSAGE",
               "provider-signature", "sig:0x5f2a", "Requirement completed.",
               step="submit_evidence R3 (unsupported)")
    live.write(live.provider_acct, "submit_deliverable", aid, "Delivered.")

    adj = live.write(live.client_acct, "request_adjudication", aid)
    assert not adj["reverted"], adj.get("refusal")
    v = live.read("get_verdict", aid, 1)
    live.record["scenario_2"] = {"agreement_id": aid, "verdict": v}

    rows = {r["requirement_id"]: r for r in v["evidence_verification"]}
    assert rows["R1"]["status"] == "HASH_MISMATCH"
    assert rows["R1"]["observed_identity"] == "sha256:" + hashlib.sha256(served_dataset).hexdigest()
    assert rows["R2"]["status"] == "SOURCE_UNAVAILABLE" and rows["R2"]["detail"] == "HTTP 404"
    assert rows["R3"]["status"] == "UNSUPPORTED"
    assert v["outcome"] == "UNDETERMINED"
    assert {r["status"] for r in v["requirements"]} == {"UNDETERMINED"}
    assert v["raw_json"] == "{}", "no model is consulted when nothing verified"
    assert live.read("get_agreement", aid)["status"] == "UNDETERMINED"

    for fn in ("settle", "finalize"):
        attempt = live.write(live.client_acct, fn, aid, step=f"{fn} while UNDETERMINED")
        assert attempt["reverted"] and "illegal transition from UNDETERMINED" in attempt["refusal"]
    assert live.read("get_agreement", aid)["escrow_available"] == PRICE
