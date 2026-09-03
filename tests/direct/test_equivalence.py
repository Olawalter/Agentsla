"""TEST LAYER 5 — equivalence.

Direct mode runs only the leader, so these tests exercise the
normaliser and the decision fingerprint directly: those two functions
ARE the equivalence rule, and every validator runs exactly them. What is
proven here is what validators compare on chain.

The contract module is imported as plain Python with a minimal `genlayer`
stub — the pure functions never touch storage, decorators or the VM.
"""
import importlib.util
import pathlib
import sys
import types
from dataclasses import dataclass

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "agentsla_core.py"


def _identity(x=None, *args, **kwargs):
    if callable(x) and not args and not kwargs:
        return x
    return lambda f: f


def _build_stub() -> types.ModuleType:
    class UserError(Exception):
        def __init__(self, message=""):
            super().__init__(message)
            self.message = message

    class Return:
        def __init__(self, calldata=""):
            self.calldata = calldata

    mod = types.ModuleType("genlayer")
    vm = types.SimpleNamespace(
        UserError=UserError, Return=Return, Result=object,
        run_nondet_unsafe=lambda leader, validator: leader(),
    )
    nondet = types.SimpleNamespace(
        exec_prompt=lambda *a, **k: {},
        web=types.SimpleNamespace(get=lambda *a, **k: None),
    )
    evm = types.SimpleNamespace(contract_interface=_identity)
    public = types.SimpleNamespace(
        view=_identity,
        write=types.SimpleNamespace(payable=_identity),
    )
    # `@gl.public.write` used bare as well as `.payable`
    public.write = _identity
    public.write.payable = _identity

    gl = types.SimpleNamespace(
        Contract=object, vm=vm, nondet=nondet, evm=evm, public=public,
        message=types.SimpleNamespace(sender_address="0x0", value=0),
    )
    mod.gl = gl
    mod.Address = str
    mod.u256 = int
    mod.allow_storage = _identity
    mod.TreeMap = dict
    mod.DynArray = list
    return mod


@pytest.fixture(scope="module")
def core():
    stub = _build_stub()
    saved = sys.modules.get("genlayer")
    sys.modules["genlayer"] = stub
    try:
        spec = importlib.util.spec_from_file_location("agentsla_core_pure", CONTRACT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        if saved is not None:
            sys.modules["genlayer"] = saved
        else:
            sys.modules.pop("genlayer", None)


RIDS = ["R1", "R2", "R3"]


def _verdict(outcome, statuses, deadline_met=True, examined=None,
             reasoning="because", agreement_id="SLA-000001", **extra):
    v = {
        "agreement_id": agreement_id,
        "outcome": outcome,
        "requirements": [
            {"requirement_id": k, "status": s} for k, s in statuses.items()
        ],
        "deadline_met": deadline_met,
        "evidence_examined": examined if examined is not None else ["E1", "E2"],
        "reasoning": reasoning,
    }
    v.update(extra)
    return v


# ── 1 · legitimate equivalent results are accepted ──────────────────────────

def test_different_wording_same_decision_is_equivalent(core):
    """The whole point: validators need not write the same paragraph."""
    leader = core._normalize_verdict(
        _verdict("PARTIAL", {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
                 reasoning="R3 missed the deadline; R1 and R2 are supported."),
        "SLA-000001", RIDS)
    validator = core._normalize_verdict(
        _verdict("PARTIAL", {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
                 reasoning="Delivery was late so R3 fails. The dataset and "
                           "quality report satisfy R1 and R2."),
        "SLA-000001", RIDS)
    assert leader["reasoning"] != validator["reasoning"]
    assert core._decision_fingerprint(leader) == core._decision_fingerprint(validator)


def test_requirement_order_does_not_matter(core):
    a = core._normalize_verdict(
        _verdict("PARTIAL", {"R1": "PASS", "R2": "PASS", "R3": "FAIL"}),
        "SLA-000001", RIDS)
    b = core._normalize_verdict(
        _verdict("PARTIAL", {"R3": "FAIL", "R2": "PASS", "R1": "PASS"}),
        "SLA-000001", RIDS)
    assert core._decision_fingerprint(a) == core._decision_fingerprint(b)


def test_evidence_order_does_not_matter(core):
    a = core._normalize_verdict(
        _verdict("PASS", {"R1": "PASS", "R2": "PASS", "R3": "PASS"},
                 examined=["E1", "E2", "E3"]), "SLA-000001", RIDS)
    b = core._normalize_verdict(
        _verdict("PASS", {"R1": "PASS", "R2": "PASS", "R3": "PASS"},
                 examined=["E3", "E1", "E2"]), "SLA-000001", RIDS)
    assert core._decision_fingerprint(a) == core._decision_fingerprint(b)


# ── 2 · materially different results are NOT equivalent ─────────────────────

def test_different_requirement_status_breaks_equivalence(core):
    a = core._normalize_verdict(
        _verdict("PARTIAL", {"R1": "PASS", "R2": "PASS", "R3": "FAIL"}),
        "SLA-000001", RIDS)
    b = core._normalize_verdict(
        _verdict("PARTIAL", {"R1": "PASS", "R2": "FAIL", "R3": "PASS"}),
        "SLA-000001", RIDS)
    assert core._decision_fingerprint(a) != core._decision_fingerprint(b)


def test_different_outcome_breaks_equivalence(core):
    a = core._normalize_verdict(
        _verdict("PASS", {"R1": "PASS", "R2": "PASS", "R3": "PASS"}),
        "SLA-000001", RIDS)
    b = core._normalize_verdict(
        _verdict("PARTIAL", {"R1": "PASS", "R2": "PASS", "R3": "FAIL"}),
        "SLA-000001", RIDS)
    assert core._decision_fingerprint(a) != core._decision_fingerprint(b)


def test_different_deadline_met_breaks_equivalence(core):
    a = core._normalize_verdict(
        _verdict("PASS", {"R1": "PASS", "R2": "PASS", "R3": "PASS"},
                 deadline_met=True), "SLA-000001", RIDS)
    b = core._normalize_verdict(
        _verdict("PASS", {"R1": "PASS", "R2": "PASS", "R3": "PASS"},
                 deadline_met=False), "SLA-000001", RIDS)
    assert core._decision_fingerprint(a) != core._decision_fingerprint(b)


def test_different_examined_evidence_breaks_equivalence(core):
    a = core._normalize_verdict(
        _verdict("PASS", {"R1": "PASS", "R2": "PASS", "R3": "PASS"},
                 examined=["E1", "E2"]), "SLA-000001", RIDS)
    b = core._normalize_verdict(
        _verdict("PASS", {"R1": "PASS", "R2": "PASS", "R3": "PASS"},
                 examined=["E1"]), "SLA-000001", RIDS)
    assert core._decision_fingerprint(a) != core._decision_fingerprint(b)


# ── 3 · malformed results are rejected before comparison ────────────────────

@pytest.mark.parametrize("bad,expect", [
    ({"outcome": "PASS"}, "agreement_id mismatch"),
    (_verdict("NOPE", {"R1": "PASS", "R2": "PASS", "R3": "PASS"}), "invalid outcome"),
    (_verdict("PASS", {"R1": "PASS", "R2": "PASS"}), "omits requirement"),
    (_verdict("PASS", {"R1": "PASS", "R2": "PASS", "R3": "PASS", "RX": "PASS"}),
     "unknown requirement_id"),
])
def test_malformed_rejected(core, bad, expect):
    with pytest.raises(core.gl.vm.UserError) as ei:
        core._normalize_verdict(bad, "SLA-000001", RIDS)
    assert expect in str(ei.value)


def test_non_dict_rejected(core):
    with pytest.raises(core.gl.vm.UserError):
        core._normalize_verdict("just a string", "SLA-000001", RIDS)


# ── 4 · invented monetary fields cannot survive normalisation ───────────────

def test_payout_fields_are_stripped(core):
    """Attack H at the unit level: the normaliser has a fixed key set."""
    hostile = _verdict(
        "PARTIAL", {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
        provider_payout=999_999_999_999,
        client_refund=0,
        bonus_multiplier=1000,
        override_settlement=True,
    )
    norm = core._normalize_verdict(hostile, "SLA-000001", RIDS)
    assert set(norm.keys()) == {
        "agreement_id", "outcome", "requirements",
        "deadline_met", "evidence_examined", "reasoning",
    }
    for poison in ("provider_payout", "client_refund",
                   "bonus_multiplier", "override_settlement"):
        assert poison not in norm

    fp = core._decision_fingerprint(norm)
    assert "999999999999" not in fp
    assert "provider_payout" not in fp


def test_hostile_fields_do_not_affect_equivalence(core):
    """Two validators, one of which saw a poisoned field, must still
    agree — the poison is invisible to the fingerprint."""
    clean = core._normalize_verdict(
        _verdict("PARTIAL", {"R1": "PASS", "R2": "PASS", "R3": "FAIL"}),
        "SLA-000001", RIDS)
    poisoned = core._normalize_verdict(
        _verdict("PARTIAL", {"R1": "PASS", "R2": "PASS", "R3": "FAIL"},
                 provider_payout=10 ** 30),
        "SLA-000001", RIDS)
    assert core._decision_fingerprint(clean) == core._decision_fingerprint(poisoned)


# ── 5 · requirement parsing is deterministic ────────────────────────────────

def test_requirements_canonicalised_deterministically(core):
    import json as _json
    a = core._parse_requirements(_json.dumps([
        {"requirement_id": "R2", "description": "b", "weight": 60},
        {"requirement_id": "R1", "description": "a", "weight": 40},
    ]))
    b = core._parse_requirements(_json.dumps([
        {"requirement_id": "R1", "description": "a", "weight": 40},
        {"requirement_id": "R2", "description": "b", "weight": 60},
    ]))
    assert core._canon(a) == core._canon(b)
