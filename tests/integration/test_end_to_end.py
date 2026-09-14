"""Live integration on StudioNet: real transaction time, real panel, real GEN.

    SKIP_INTEGRATION=0 pytest tests/integration -v -s

No clock is advanced anywhere in this file — there is none to advance.
Every deadline below is created by the contract from a transaction's own
datetime, and the suite reaches the far side of a deadline the only way
anyone can: by waiting for real time to pass. Each premature attempt is a
real transaction whose refusal the contract wrote to its receipt.

Scenario 1 — fund safety: the acceptance window lapses in real time. An
unrelated funded account tries to advance the clock and to expire/recover
someone else's escrow; the client tries to expire early; the provider tries
to accept late. Then the legitimate expiry and refund.

Scenario 2 — verified evidence, PARTIAL: R1 PASS, R2 PASS, R3 FAIL. Settling
and finalizing inside the real appeal window are refused; after it closes,
finalize and a 70/30 settlement that moves real GEN.

Scenario 3 — nothing verifiable: UNDETERMINED with no model consulted;
settle, finalize and an early escrow recovery are all refused.

The evidence is real and hosted, pinned to one commit of this repository
(branch `live-evidence`): a daily price dataset, a quality report about it,
and the commit that delivered them. R3's stated calendar deadline
(2026-09-01) is before that commit's date — a question the panel answers
from the verified artifact, not from protocol time.
"""
import hashlib
import importlib.util
import json
import pathlib
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]

EVIDENCE_COMMIT = "8517e9ab0848558b790cee8f8c9a0e533ec7cc3a"
RAW = f"https://raw.githubusercontent.com/Olawalter/Agentsla/{EVIDENCE_COMMIT}/evidence"
DATASET_URL = f"{RAW}/acme-2026-07-daily.csv"
REPORT_URL = f"{RAW}/quality-report.json#/summary"
MISSING_URL = f"{RAW}/quality-report-v2.json"
COMMIT_REF = f"https://github.com/Olawalter/Agentsla/commit/{EVIDENCE_COMMIT}"

PRICE = 10 ** 17        # 0.1 GEN: large enough that 70/30 is exact
HOUR = 3600

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


def _unix(entry):
    """The network's own timestamp for a transaction, in seconds."""
    ts = entry.get("tx_created_timestamp")
    if ts is None:
        return None
    ts = int(float(ts))
    return ts // 1000 if ts > 10 ** 12 else ts


def _bound_to_transaction(live, contract_value, entry, what):
    """The contract's recorded time is the transaction's datetime — within
    the network's own gap between creating and executing it."""
    tx_ts = _unix(entry)
    gap = None if tx_ts is None else contract_value - tx_ts
    live.record.setdefault("time_bindings", []).append({
        "field": what, "contract_value": contract_value, "tx": entry["tx"],
        "tx_created_timestamp": tx_ts, "difference_seconds": gap})
    print(f"  {what:<22} contract {contract_value}  tx {tx_ts}  difference {gap}s")
    if gap is not None:
        assert abs(gap) <= 900, f"{what} is not this transaction's time"


def _refused(entry, message):
    assert entry.get("reverted") or entry.get("rejected_before_execution"), entry
    if message:
        assert message in (entry.get("refusal") or ""), entry.get("refusal")


def _agreement(live, description, acceptance, service, resolution, appeal):
    before = live.read("get_protocol_info")["agreement_count"]
    live.write(live.client_acct, "create_agreement",
               live.provider_acct.address, description, json.dumps(REQUIREMENTS),
               PRICE, acceptance, service, resolution,
               "Only artifacts retrieved and verified during adjudication count.",
               "Weighted per-requirement payout; rounding remainder to the client.",
               0, appeal, step="create_agreement")
    aid = f"SLA-{before + 1:06d}"
    assert live.read("get_agreement", aid)["status"] == "DRAFT"
    fund = live.write(live.client_acct, "fund_agreement", aid, value=PRICE)
    a = live.read("get_agreement", aid)
    _bound_to_transaction(live, a["funded_at"], fund, f"{aid} funded_at")
    assert a["acceptance_deadline"] == a["funded_at"] + acceptance
    return aid, a


# ═══ Scenario 1 — no one can manufacture elapsed time ═══════════════════════

def test_live_acceptance_window_lapses_only_in_real_time(live):
    print("\nSCENARIO 1 — the acceptance window, attacked and then really lapsing")
    assert "current_tick" not in live.read("get_protocol_info")

    aid, a = _agreement(live, "Deliver the ACME July 2026 daily dataset.",
                        300, HOUR, HOUR, 600)
    deadline = a["acceptance_deadline"]
    live.record["scenario_1"] = {"agreement_id": aid, "acceptance_deadline": deadline}

    # the client tries to expire it early
    early = live.write(live.client_acct, "expire_agreement", aid, step="expire before deadline")
    assert _unix(early) is None or _unix(early) < deadline, "not early after all"
    _refused(early, "acceptance deadline not reached")

    # an unrelated account tries the old clock, then someone else's escrow
    _refused(live.attempt(live.attacker_acct, "tick", step="attacker: tick()"), "")
    for fn in ("expire_agreement", "recover_escrow"):
        _refused(live.write(live.attacker_acct, fn, aid, step=f"attacker: {fn}"),
                 "not a party to this agreement")
    a = live.read("get_agreement", aid)
    assert a["status"] == "FUNDED" and a["escrow_available"] == PRICE
    assert a["acceptance_deadline"] == deadline, "no activity moved the deadline"

    live.sleep_past(deadline)

    late = live.write(live.provider_acct, "accept_agreement", aid, step="accept after deadline")
    _refused(late, "acceptance deadline passed")
    assert live.read("get_agreement", aid)["status"] == "FUNDED"

    client_before = live.balance(live.client_acct.address)
    live.write(live.client_acct, "expire_agreement", aid, step="expire after deadline")
    assert live.read("get_agreement", aid)["status"] == "EXPIRED"
    live.write(live.client_acct, "recover_escrow", aid, wait="FINALIZED")
    a = live.read("get_agreement", aid)
    assert a["status"] == "REFUNDED" and a["escrow_available"] == 0

    live._await(lambda: live.balance(live.client_acct.address) > client_before,
                "client refund", tries=60)
    live.record["scenario_1"]["client_balance_delta"] = (
        live.balance(live.client_acct.address) - client_before)


# ═══ Scenario 2 — verified evidence, real appeal window ═════════════════════

def test_live_verified_evidence_partial_settles_after_real_appeal_window(live):
    tool = _identity_tool()
    dataset_id = tool.identity("DATASET", DATASET_URL, _served(tool, "DATASET", DATASET_URL))
    report_id = tool.identity("API_RESULT", REPORT_URL, _served(tool, "API_RESULT", REPORT_URL))
    commit_id = tool.identity("GITHUB_COMMIT", COMMIT_REF, _served(tool, "GITHUB_COMMIT", COMMIT_REF))

    print("\nSCENARIO 2 — verified evidence, partial performance, real appeal window")
    aid, _ = _agreement(live, "Deliver the ACME July 2026 daily dataset with a quality "
                              "report, by 2026-09-01T00:00:00Z.", HOUR, HOUR, HOUR, 600)
    accept = live.write(live.provider_acct, "accept_agreement", aid)
    a = live.read("get_agreement", aid)
    _bound_to_transaction(live, a["accepted_at"], accept, f"{aid} accepted_at")
    assert a["service_deadline"] == a["accepted_at"] + HOUR
    assert a["resolution_deadline"] == a["service_deadline"] + HOUR

    live.write(live.provider_acct, "submit_evidence", aid, "R1", "DATASET", DATASET_URL,
               dataset_id, "Dataset delivered.", step="submit_evidence R1")
    live.write(live.provider_acct, "submit_evidence", aid, "R2", "API_RESULT", REPORT_URL,
               report_id, "Quality report.", step="submit_evidence R2")
    live.write(live.provider_acct, "submit_evidence", aid, "R3", "GITHUB_COMMIT", COMMIT_REF,
               commit_id, "Delivered on time.", step="submit_evidence R3")
    deliver = live.write(live.provider_acct, "submit_deliverable", aid, "Delivered.")
    a = live.read("get_agreement", aid)
    _bound_to_transaction(live, a["delivered_at"], deliver, f"{aid} delivered_at")
    assert a["delivered_late"] is False

    adj = live.write(live.client_acct, "request_adjudication", aid)
    assert not adj["reverted"], adj.get("refusal")
    state = live.read("get_agreement", aid)
    assert state["latest_verdict_id"] == 1, "the round must have committed a verdict"
    _bound_to_transaction(live, state["verdict_at"], adj, f"{aid} verdict_at")
    assert state["appeal_deadline"] == state["verdict_at"] + 600
    v = live.read("get_verdict", aid, 1)
    live.record["scenario_2"] = {"agreement_id": aid, "verdict": v,
                                 "appeal_deadline": state["appeal_deadline"]}

    rows = {r["requirement_id"]: r for r in v["evidence_verification"]}
    for rid in ("R1", "R2", "R3"):
        assert rows[rid]["status"] == "VERIFIED", rows[rid]
        assert rows[rid]["observed_identity"] == rows[rid]["expected_identity"]
    assert {r["requirement_id"]: r["status"] for r in v["requirements"]} == {
        "R1": "PASS", "R2": "PASS", "R3": "FAIL"}, v["reasoning"]
    assert v["outcome"] == "PARTIAL" and v["earned_weight"] == 70
    assert state["status"] == "ACCEPTED"

    # inside the appeal window: nobody finalizes, nothing pays
    _refused(live.write(live.client_acct, "settle", aid, step="settle while ACCEPTED"),
             "illegal transition from ACCEPTED")
    early = live.write(live.client_acct, "finalize", aid, step="finalize inside appeal window")
    assert _unix(early) is None or _unix(early) < state["appeal_deadline"], "not early after all"
    _refused(early, "appeal window open until")
    _refused(live.write(live.attacker_acct, "finalize", aid, step="attacker: finalize"),
             "not a party to this agreement")
    _refused(live.attempt(live.attacker_acct, "tick", step="attacker: tick()"), "")
    a = live.read("get_agreement", aid)
    assert a["status"] == "ACCEPTED" and a["finalized_at"] == 0
    assert a["escrow_available"] == PRICE

    live.sleep_past(state["appeal_deadline"])

    final = live.write(live.client_acct, "finalize", aid, step="finalize after appeal window")
    assert not final["reverted"], final.get("refusal")
    a = live.read("get_agreement", aid)
    assert a["status"] == "FINALIZED" and a["finalized_at"] > a["appeal_deadline"]
    _bound_to_transaction(live, a["finalized_at"], final, f"{aid} finalized_at")

    provider_before = live.balance(live.provider_acct.address)
    contract_before = live.balance(live.address)
    live.write(live.client_acct, "settle", aid, wait="FINALIZED")
    s = live.read("get_settlement", aid)
    live.record["scenario_2"]["settlement"] = s
    assert s["provider_payout"] == PRICE * 70 // 100
    assert s["client_refund"] == PRICE - PRICE * 70 // 100
    assert s["penalty"] == 0 and s["escrow_after"] == 0
    assert live.read("get_agreement", aid)["status"] == "SETTLED"

    def paid():
        return live.balance(live.provider_acct.address) == provider_before + s["provider_payout"]
    live._await(paid, "provider payout", tries=60)
    live.record["scenario_2"]["balances"] = {
        "provider_delta": live.balance(live.provider_acct.address) - provider_before,
        "contract_delta": live.balance(live.address) - contract_before,
    }
    assert live.record["scenario_2"]["balances"]["contract_delta"] == -PRICE


# ═══ Scenario 3 — nothing verifiable, recovery not early ════════════════════

def test_live_unverifiable_evidence_is_undetermined_and_escrow_holds(live):
    tool = _identity_tool()
    served_dataset = _served(tool, "DATASET", DATASET_URL)
    wrong_id = "sha256:" + hashlib.sha256(served_dataset + b"\n# altered").hexdigest()
    report_id = tool.identity("API_RESULT", REPORT_URL, _served(tool, "API_RESULT", REPORT_URL))

    print("\nSCENARIO 3 — nothing verifiable")
    aid, _ = _agreement(live, "Same deliverable, evidenced badly.", HOUR, HOUR, HOUR, 600)
    live.write(live.provider_acct, "accept_agreement", aid)
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
    a = live.read("get_agreement", aid)
    live.record["scenario_3"] = {"agreement_id": aid, "verdict": v,
                                 "resolution_deadline": a["resolution_deadline"]}

    rows = {r["requirement_id"]: r for r in v["evidence_verification"]}
    assert rows["R1"]["status"] == "HASH_MISMATCH"
    assert rows["R1"]["observed_identity"] == "sha256:" + hashlib.sha256(served_dataset).hexdigest()
    assert rows["R2"]["status"] == "SOURCE_UNAVAILABLE" and rows["R2"]["detail"] == "HTTP 404"
    assert rows["R3"]["status"] == "UNSUPPORTED"
    assert v["outcome"] == "UNDETERMINED"
    assert {r["status"] for r in v["requirements"]} == {"UNDETERMINED"}
    assert v["raw_json"] == "{}", "no model is consulted when nothing verified"
    assert a["status"] == "UNDETERMINED"

    for fn in ("settle", "finalize"):
        _refused(live.write(live.client_acct, fn, aid, step=f"{fn} while UNDETERMINED"),
                 "illegal transition from UNDETERMINED")
    _refused(live.write(live.client_acct, "recover_escrow", aid,
                        step="recover before resolution deadline"),
             "resolution deadline not reached")
    _refused(live.write(live.attacker_acct, "recover_escrow", aid, step="attacker: recover"),
             "not a party to this agreement")
    assert live.read("get_agreement", aid)["escrow_available"] == PRICE
