# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# AgentSLA Core
# =============
# A reusable GenLayer Intelligent Contract primitive for evidence-based
# service agreements.
#
# THE ONE IDEA
# ------------
# A deterministic contract can enforce "if timestamp > X: transfer()".
# It cannot establish "did the provider actually satisfy this
# natural-language requirement, given this evidence?"  AgentSLA Core
# splits those two jobs and gives each to the layer that can do it:
#
#     GenLayer validator consensus  decides FACTS
#         (per-requirement PASS / FAIL / UNDETERMINED, deadline_met,
#          which evidence was actually examined)
#
#     Deterministic contract code   decides MONEY
#         (weight arithmetic, payout, refund, penalty, escrow zeroing,
#          the actual value transfer)
#
# The adjudicating model is never allowed to name an amount. It returns
# a structured verdict over the committed requirement set; the contract
# multiplies the earned weight by the real escrow balance. A verdict
# carrying `provider_payout: 999999999` changes nothing, because no code
# path reads such a field.
#
# GENERIC BY CONSTRUCTION
# -----------------------
# Nothing here knows about datasets, APIs, or any particular industry.
# The creator supplies the requirements, weights, deadlines, evidence
# rules and settlement rules; the contract hashes them into a terms
# commitment and adjudicates against that frozen version. The same
# primitive serves agent-to-agent work, API SLAs, data delivery,
# bounties, milestone work and procurement without modification.

from genlayer import *

import hashlib
import json
from dataclasses import dataclass


# ─── error taxonomy ──────────────────────────────────────────────────────────
# Prefixes let validators agree about FAILURES as well as successes:
# deterministic errors must match exactly, transient ones may both be
# transient, and LLM misbehaviour always disagrees so the round rotates.
ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM = "[LLM_ERROR]"


# ─── agreement lifecycle ─────────────────────────────────────────────────────
S_DRAFT = "DRAFT"                  # created, terms mutable, no escrow
S_FUNDED = "FUNDED"                # escrow received, terms LOCKED
S_ACTIVE = "ACTIVE"                # provider accepted
S_SUBMITTED = "SUBMITTED"          # provider declared delivery
S_ADJUDICATING = "ADJUDICATING"    # nondet round in flight
S_ACCEPTED = "ACCEPTED"            # verdict stored, appeal window OPEN
S_APPEALED = "APPEALED"            # a party contested; re-adjudication owed
S_UNDETERMINED = "UNDETERMINED"    # panel could not decide; escrow frozen
S_FINALIZED = "FINALIZED"          # appeal window closed; settlement legal
S_SETTLED = "SETTLED"              # escrow released, terminal
S_EXPIRED = "EXPIRED"              # deadline passed with no delivery
S_CANCELLED = "CANCELLED"          # cancelled pre-acceptance
S_REFUNDED = "REFUNDED"            # recovery path drained escrow to client

VALID_STATES = {
    S_DRAFT, S_FUNDED, S_ACTIVE, S_SUBMITTED, S_ADJUDICATING, S_ACCEPTED,
    S_APPEALED, S_UNDETERMINED, S_FINALIZED, S_SETTLED, S_EXPIRED,
    S_CANCELLED, S_REFUNDED,
}

# States in which escrow is still held and MUST NOT leak.
ESCROW_HELD_STATES = {
    S_FUNDED, S_ACTIVE, S_SUBMITTED, S_ADJUDICATING, S_ACCEPTED,
    S_APPEALED, S_UNDETERMINED, S_FINALIZED, S_EXPIRED,
}


# ─── verdict vocabulary ──────────────────────────────────────────────────────
O_PASS = "PASS"
O_PARTIAL = "PARTIAL"
O_FAIL = "FAIL"
O_UNDETERMINED = "UNDETERMINED"
VALID_OUTCOMES = {O_PASS, O_PARTIAL, O_FAIL, O_UNDETERMINED}

R_PASS = "PASS"
R_FAIL = "FAIL"
R_UNDETERMINED = "UNDETERMINED"
VALID_REQ_STATUSES = {R_PASS, R_FAIL, R_UNDETERMINED}


# ─── evidence vocabulary ─────────────────────────────────────────────────────
EVIDENCE_TYPES = {
    "URL", "API_RESULT", "GITHUB_COMMIT", "DOCUMENT", "JSON", "CSV",
    "DATASET", "BLOCKCHAIN_TX", "SIGNED_MESSAGE", "SERVICE_LOG",
    "AGENT_OUTPUT", "OTHER",
}

# Evidence types a third party can independently re-derive. The
# adjudication prompt is told to weight these above self-reported prose;
# see docs/EVIDENCE.md for why this list is what it is.
AUTHORITATIVE_TYPES = {
    "GITHUB_COMMIT", "BLOCKCHAIN_TX", "API_RESULT", "SIGNED_MESSAGE",
    "DATASET", "URL",
}

EV_ACTIVE = "ACTIVE"
EV_SUPERSEDED = "SUPERSEDED"
EV_CHALLENGED = "CHALLENGED"


# ─── protocol bounds ─────────────────────────────────────────────────────────
WEIGHT_TOTAL = 100          # requirement weights must sum to exactly this
MAX_REQUIREMENTS = 32
MAX_EVIDENCE = 128
MAX_STR = 4096
MAX_SHORT = 256
MAX_ID = 64

APPEAL_WINDOW_TICKS = 3     # ACCEPTED must sit this long before FINALIZED
MAX_ADJUDICATION_ROUNDS = 5 # bounds re-adjudication so escrow can't be pinned


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canon(obj) -> str:
    """Canonical JSON: sorted keys, no incidental whitespace. Two nodes
    building the same logical object always produce identical bytes,
    which is what makes the terms hash reproducible."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _clip(s: str, maxlen: int) -> str:
    s = str(s or "")
    return s if len(s) <= maxlen else s[:maxlen]


# ─── storage records ─────────────────────────────────────────────────────────
# Note on shape: no dataclass below contains a DynArray or a nested
# dataclass. GenVM refuses to let contract code instantiate those, so
# every list lives as canonical JSON in a str field and is parsed at the
# view boundary. This is a deliberate, load-bearing choice.

@allow_storage
@dataclass
class Agreement:
    agreement_id: str
    client: Address
    provider: Address
    service_description: str
    # Canonical JSON array of requirement objects. Frozen at funding.
    requirements_json: str
    requirement_count: u256
    payment_amount_atto: u256          # agreed price (a TERM)
    escrow_deposited_atto: u256        # what actually arrived (the MONEY)
    escrow_released_atto: u256
    penalty_bps: u256                  # optional, locked with the terms
    acceptance_deadline_tick: u256
    service_deadline_tick: u256
    resolution_deadline_tick: u256
    appeal_window_ticks: u256
    evidence_rules: str
    settlement_rules: str
    terms_hash: str                    # sha256 over the frozen term set
    terms_locked: bool
    status: str
    adjudication_round: u256           # how many verdicts have been produced
    latest_verdict_id: u256
    appeal_deadline_tick: u256         # earliest tick finalize() is legal
    created_tick: u256
    updated_tick: u256


@allow_storage
@dataclass
class Evidence:
    evidence_id: str
    agreement_id: str                  # binding — checked on every read
    requirement_id: str
    submitter: Address
    evidence_type: str
    source_reference: str
    content_hash: str                  # the commitment; never mutated
    description: str
    submitted_tick: u256
    version: u256
    status: str                        # ACTIVE | SUPERSEDED | CHALLENGED


@allow_storage
@dataclass
class Verdict:
    verdict_id: u256
    agreement_id: str
    round_number: u256
    outcome: str                       # PASS | PARTIAL | FAIL | UNDETERMINED
    requirement_results_json: str      # [{"requirement_id","status"}, ...]
    deadline_met: bool
    evidence_examined_json: str        # ["E1","E2", ...]
    reasoning: str                     # explanatory ONLY — never compared
    earned_weight: u256                # derived by the CONTRACT, not the model
    terms_hash: str                    # the terms this verdict judged
    evaluated_tick: u256
    raw_json: str


@allow_storage
@dataclass
class Settlement:
    agreement_id: str
    verdict_id: u256
    provider_payout_atto: u256
    client_refund_atto: u256
    penalty_atto: u256
    escrow_before_atto: u256
    escrow_after_atto: u256
    earned_weight: u256
    total_weight: u256
    status: str                        # SETTLED
    settled_tick: u256


# ─── single audited value-emission channel ───────────────────────────────────
@gl.evm.contract_interface
class _Recipient:
    class View:
        pass

    class Write:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# Module-level pure helpers.
#
# These live outside the class on purpose: the nondeterministic block
# must not close over `self`, because GenVM forbids storage reads inside
# an equivalence-principle body. Keeping the parser and normaliser here
# means leader and validator run byte-identical logic over their own
# model output, with no hidden dependency on contract state.
# ═════════════════════════════════════════════════════════════════════════════

def _parse_requirements(requirements_json: str) -> list:
    """Validate and canonicalise a requirement set.

    Enforces: unique non-empty ids, non-empty descriptions, integer
    weights in 1..100, weights summing to exactly WEIGHT_TOTAL. Integer
    arithmetic throughout — no float ever touches a weight.
    """
    try:
        raw = json.loads(requirements_json)
    except Exception:
        raise gl.vm.UserError(f"{ERROR_EXPECTED} requirements must be valid JSON")
    if not isinstance(raw, list):
        raise gl.vm.UserError(f"{ERROR_EXPECTED} requirements must be a JSON array")
    if len(raw) == 0:
        raise gl.vm.UserError(f"{ERROR_EXPECTED} at least one requirement is needed")
    if len(raw) > MAX_REQUIREMENTS:
        raise gl.vm.UserError(
            f"{ERROR_EXPECTED} too many requirements (max {MAX_REQUIREMENTS})")

    seen = set()
    out = []
    total = 0
    for item in raw:
        if not isinstance(item, dict):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} each requirement must be an object")
        rid = _clip(item.get("requirement_id", ""), MAX_ID).strip()
        desc = _clip(item.get("description", ""), MAX_STR).strip()
        if not rid:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} requirement_id required")
        if not desc:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} description required for {rid}")
        if rid in seen:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} duplicate requirement_id: {rid}")
        seen.add(rid)
        try:
            weight = int(item.get("weight"))
        except Exception:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} weight must be an integer for {rid}")
        if weight <= 0 or weight > WEIGHT_TOTAL:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} weight for {rid} must be 1..{WEIGHT_TOTAL}")
        total += weight
        out.append({
            "requirement_id": rid,
            "description": desc,
            "weight": weight,
            "required": bool(item.get("required", False)),
            "evidence_rule": _clip(item.get("evidence_rule", ""), MAX_STR),
        })

    if total != WEIGHT_TOTAL:
        raise gl.vm.UserError(
            f"{ERROR_EXPECTED} weights must sum to exactly {WEIGHT_TOTAL} (got {total})")

    out.sort(key=lambda r: r["requirement_id"])
    return out


def _normalize_verdict(obj, expected_agreement_id: str,
                       expected_requirement_ids: list) -> dict:
    """Reduce raw model output to the consensus-critical fields, refusing
    anything that does not describe the committed requirement set.

    This is where Attack H dies: the returned dict has a fixed key set,
    and `provider_payout` (or any other invented field) is simply never
    read. There is no code path from model output to an amount.
    """
    if not isinstance(obj, dict):
        raise gl.vm.UserError(f"{ERROR_LLM} verdict is not an object")

    try:
        aid = str(obj.get("agreement_id", "")).strip()
        if aid != expected_agreement_id:
            raise gl.vm.UserError(
                f"{ERROR_LLM} agreement_id mismatch: got {aid!r}, "
                f"expected {expected_agreement_id!r}")

        outcome = str(obj.get("outcome", "")).strip().upper()
        if outcome not in VALID_OUTCOMES:
            raise gl.vm.UserError(f"{ERROR_LLM} invalid outcome: {outcome!r}")

        raw_reqs = obj.get("requirements")
        if not isinstance(raw_reqs, list):
            raise gl.vm.UserError(f"{ERROR_LLM} `requirements` must be a list")

        expected_ids = set(expected_requirement_ids)
        seen = set()
        results = []
        for r in raw_reqs:
            if not isinstance(r, dict):
                raise gl.vm.UserError(f"{ERROR_LLM} requirement entry must be an object")
            rid = str(r.get("requirement_id", "")).strip()
            status = str(r.get("status", "")).strip().upper()
            if rid not in expected_ids:
                raise gl.vm.UserError(f"{ERROR_LLM} unknown requirement_id: {rid!r}")
            if rid in seen:
                raise gl.vm.UserError(f"{ERROR_LLM} duplicate requirement_id: {rid!r}")
            if status not in VALID_REQ_STATUSES:
                raise gl.vm.UserError(
                    f"{ERROR_LLM} invalid status {status!r} for {rid}")
            seen.add(rid)
            results.append({"requirement_id": rid, "status": status})

        missing = expected_ids - seen
        if missing:
            raise gl.vm.UserError(
                f"{ERROR_LLM} verdict omits requirement(s): {sorted(missing)}")

        deadline_met = obj.get("deadline_met")
        if not isinstance(deadline_met, bool):
            raise gl.vm.UserError(f"{ERROR_LLM} `deadline_met` must be a boolean")

        raw_ev = obj.get("evidence_examined")
        if not isinstance(raw_ev, list):
            raise gl.vm.UserError(f"{ERROR_LLM} `evidence_examined` must be a list")
        examined = sorted({
            str(x).strip() for x in raw_ev if isinstance(x, (str, int))
        })

        reasoning = _clip(str(obj.get("reasoning", "")).strip(), MAX_STR)
        if not reasoning:
            raise gl.vm.UserError(f"{ERROR_LLM} `reasoning` required")

        # Internal coherence: an outcome that contradicts its own
        # per-requirement statuses is a malformed verdict, not a
        # judgement call.
        statuses = [r["status"] for r in results]
        if outcome == O_UNDETERMINED:
            if R_UNDETERMINED not in statuses:
                raise gl.vm.UserError(
                    f"{ERROR_LLM} UNDETERMINED outcome needs at least one "
                    f"UNDETERMINED requirement")
        else:
            if R_UNDETERMINED in statuses:
                raise gl.vm.UserError(
                    f"{ERROR_LLM} outcome {outcome} cannot carry an "
                    f"UNDETERMINED requirement")
            if outcome == O_PASS and R_FAIL in statuses:
                raise gl.vm.UserError(f"{ERROR_LLM} PASS cannot carry a FAIL requirement")
            if outcome == O_FAIL and R_PASS in statuses:
                raise gl.vm.UserError(f"{ERROR_LLM} FAIL cannot carry a PASS requirement")
            if outcome == O_PARTIAL and not (R_PASS in statuses and R_FAIL in statuses):
                raise gl.vm.UserError(
                    f"{ERROR_LLM} PARTIAL requires at least one PASS and one FAIL")

        # NOTE the fixed key set. Anything else the model invented —
        # `provider_payout`, `bonus`, `override` — is dropped here and
        # never reaches storage or settlement.
        return {
            "agreement_id": aid,
            "outcome": outcome,
            "requirements": sorted(results, key=lambda r: r["requirement_id"]),
            "deadline_met": deadline_met,
            "evidence_examined": examined,
            "reasoning": reasoning,
        }
    except gl.vm.UserError:
        raise
    except Exception as e:
        raise gl.vm.UserError(f"{ERROR_LLM} malformed verdict: {e}")


def _decision_fingerprint(norm: dict) -> str:
    """The consensus-critical projection of a verdict.

    Everything in here must match across validators. `reasoning` is
    deliberately excluded: two honest validators reading the same
    evidence will reach the same verdict but will not write the same
    paragraph, and demanding identical prose would make consensus fail
    for the wrong reason. See docs/CONSENSUS.md.
    """
    return _canon({
        "agreement_id": norm["agreement_id"],
        "outcome": norm["outcome"],
        "requirements": norm["requirements"],
        "deadline_met": norm["deadline_met"],
        "evidence_examined": norm["evidence_examined"],
    })


def _handle_leader_error(leaders_res, leader_fn) -> bool:
    """Agree about failure only when failure is deterministic."""
    leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
    try:
        leader_fn()
        return False            # leader failed, we succeeded — disagree
    except gl.vm.UserError as e:
        msg = e.message if hasattr(e, "message") else str(e)
        if msg.startswith(ERROR_EXPECTED) or msg.startswith(ERROR_EXTERNAL):
            return msg == leader_msg
        if msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
            return True
        return False            # LLM error — always disagree, force rotation
    except Exception:
        return False


# ═════════════════════════════════════════════════════════════════════════════
class AgentSLACore(gl.Contract):
    """AgentSLA Core — evidence-based service agreements with GenLayer
    adjudication and deterministic settlement."""

    # protocol
    owner: Address
    version: str
    current_tick: u256

    # registry
    agreements: TreeMap[str, Agreement]
    agreement_ids: DynArray[str]
    agreement_count: u256

    # Evidence lives in exactly ONE place: the per-agreement DynArray.
    # The two TreeMaps below are pure indexes into it — an owner lookup
    # (which agreement an id belongs to, used to reject cross-agreement
    # references) and a position lookup. Storing the record itself twice
    # would let the copies drift: a status change written to one would be
    # invisible through the other.
    evidence_by_agreement: TreeMap[str, DynArray[Evidence]]
    evidence_owner: TreeMap[str, str]      # evidence_id -> agreement_id
    evidence_index: TreeMap[str, u256]     # evidence_id -> position in DynArray
    evidence_counter: TreeMap[str, u256]

    # verdict history — every round is kept, nothing is overwritten
    verdicts: TreeMap[str, TreeMap[u256, Verdict]]
    verdict_ids: TreeMap[str, DynArray[u256]]

    # settlements
    settlements: TreeMap[str, Settlement]

    def __init__(self):
        self.owner = gl.message.sender_address
        self.version = "AgentSLA-Core-1.0.0"
        self.current_tick = u256(0)
        self.agreement_count = u256(0)

    # ─── internal helpers ────────────────────────────────────────────────────

    def _sender(self) -> Address:
        return gl.message.sender_address

    def _key(self, addr) -> str:
        return str(addr).lower()

    def _tick(self) -> int:
        """Deterministic protocol clock.

        Every state-changing call advances it by one. A tick counter
        rather than a wall clock because `gl.message.datetime` is not
        populated in every runtime this contract must work in, and a
        deadline that silently reads zero is worse than one that is
        explicitly abstract. Deadlines are therefore expressed in ticks
        and the CONTRACT — never the caller — decides whether one has
        passed.
        """
        n = int(self.current_tick) + 1
        self.current_tick = u256(n)
        return n

    def _require_agreement(self, aid: str) -> Agreement:
        if aid not in self.agreements:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} unknown agreement: {aid}")
        return self.agreements[aid]

    def _require_client(self, a: Agreement) -> None:
        if self._key(self._sender()) != self._key(a.client):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} client only")

    def _require_provider(self, a: Agreement) -> None:
        if self._key(self._sender()) != self._key(a.provider):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} provider only")

    def _require_party(self, a: Agreement) -> None:
        s = self._key(self._sender())
        if s != self._key(a.client) and s != self._key(a.provider):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} not a party to this agreement")

    def _require_state(self, a: Agreement, allowed) -> None:
        if a.status not in allowed:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} illegal transition from {a.status}; "
                f"expected one of {sorted(allowed)}")

    def _set_state(self, a: Agreement, new_state: str) -> None:
        if new_state not in VALID_STATES:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} invalid state: {new_state}")
        a.status = new_state
        a.updated_tick = u256(int(self.current_tick))

    def _escrow_available(self, a: Agreement) -> int:
        return int(a.escrow_deposited_atto) - int(a.escrow_released_atto)

    def _resolve_evidence(self, evidence_id: str, expected_agreement_id: str):
        """Resolve an evidence id to the LIVE record in its DynArray.

        Returns the storage-backed object, so a caller mutating `.status`
        mutates what `get_evidence` reads. Also enforces the agreement
        binding: a record belonging to another agreement is refused even
        when the caller is a legitimate party to this one (Attack I).
        """
        if evidence_id not in self.evidence_owner:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} unknown evidence: {evidence_id}")
        owner = self.evidence_owner[evidence_id]
        if owner != expected_agreement_id:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} evidence {evidence_id} belongs to {owner}, "
                f"not {expected_agreement_id}")
        idx = int(self.evidence_index[evidence_id])
        records = self.evidence_by_agreement[owner]
        if idx < 0 or idx >= len(records):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} evidence index corrupt for {evidence_id}")
        rec = records[idx]
        if rec.evidence_id != evidence_id:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} evidence index mismatch for {evidence_id}")
        return rec

    def _compute_terms_hash(self, a: Agreement) -> str:
        """Hash exactly the fields a verdict is allowed to depend on.

        If any of these change, the hash changes, and an adjudication
        bound to the old hash can no longer settle. That is the whole
        anti-tampering mechanism (Attack B).
        """
        return _sha256_hex(_canon({
            "agreement_id": a.agreement_id,
            "client": self._key(a.client),
            "provider": self._key(a.provider),
            "service_description": a.service_description,
            "requirements": json.loads(a.requirements_json),
            "payment_amount_atto": int(a.payment_amount_atto),
            "penalty_bps": int(a.penalty_bps),
            "acceptance_deadline_tick": int(a.acceptance_deadline_tick),
            "service_deadline_tick": int(a.service_deadline_tick),
            "resolution_deadline_tick": int(a.resolution_deadline_tick),
            "appeal_window_ticks": int(a.appeal_window_ticks),
            "evidence_rules": a.evidence_rules,
            "settlement_rules": a.settlement_rules,
        }).encode("utf-8"))

    def _require_terms_intact(self, a: Agreement) -> None:
        """Recompute and compare. Cheap, and it turns any storage-level
        tampering into a refusal rather than a wrong payout."""
        if not a.terms_locked:
            return
        actual = self._compute_terms_hash(a)
        if actual != a.terms_hash:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} terms commitment broken: stored {a.terms_hash}, "
                f"recomputed {actual}")

    # ═══ 1 · agreement creation and terms ═════════════════════════════════════

    @gl.public.write
    def create_agreement(self, provider: str, service_description: str,
                         requirements_json: str, payment_amount_atto: int,
                         acceptance_deadline_ticks: int,
                         service_deadline_ticks: int,
                         resolution_deadline_ticks: int,
                         evidence_rules: str = "", settlement_rules: str = "",
                         penalty_bps: int = 0,
                         appeal_window_ticks: int = APPEAL_WINDOW_TICKS) -> str:
        """Create a DRAFT agreement. Caller becomes the client.

        Terms stay mutable until funding, so a creator can fix a typo
        before money is involved. The moment escrow lands they freeze.
        """
        client = self._sender()
        try:
            provider_addr = Address(str(provider))
        except Exception:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} invalid provider address")
        if self._key(provider_addr) == self._key(client):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} client and provider must differ")

        desc = _clip(service_description, MAX_STR).strip()
        if not desc:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} service_description required")

        reqs = _parse_requirements(requirements_json)

        amount = int(payment_amount_atto)
        if amount <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} payment_amount must be > 0")

        pen = int(penalty_bps)
        if pen < 0 or pen > 10_000:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} penalty_bps must be 0..10000")

        acc = int(acceptance_deadline_ticks)
        svc = int(service_deadline_ticks)
        res = int(resolution_deadline_ticks)
        if not (0 < acc < svc < res):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} deadlines must satisfy "
                f"0 < acceptance ({acc}) < service ({svc}) < resolution ({res})")

        appeal = int(appeal_window_ticks)
        if appeal < 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} appeal_window_ticks must be >= 0")

        now = self._tick()
        idx = int(self.agreement_count) + 1
        aid = f"SLA-{idx:06d}"

        a = Agreement(
            agreement_id=aid,
            client=client,
            provider=provider_addr,
            service_description=desc,
            requirements_json=_canon(reqs),
            requirement_count=u256(len(reqs)),
            payment_amount_atto=u256(amount),
            escrow_deposited_atto=u256(0),
            escrow_released_atto=u256(0),
            penalty_bps=u256(pen),
            acceptance_deadline_tick=u256(now + acc),
            service_deadline_tick=u256(now + svc),
            resolution_deadline_tick=u256(now + res),
            appeal_window_ticks=u256(appeal),
            evidence_rules=_clip(evidence_rules, MAX_STR),
            settlement_rules=_clip(settlement_rules, MAX_STR),
            terms_hash="",
            terms_locked=False,
            status=S_DRAFT,
            adjudication_round=u256(0),
            latest_verdict_id=u256(0),
            appeal_deadline_tick=u256(0),
            created_tick=u256(now),
            updated_tick=u256(now),
        )
        a.terms_hash = self._compute_terms_hash(a)

        self.agreements[aid] = a
        self.agreement_ids.append(aid)
        self.agreement_count = u256(idx)
        self.evidence_counter[aid] = u256(0)
        return aid

    @gl.public.write
    def update_terms(self, agreement_id: str, service_description: str,
                     requirements_json: str, evidence_rules: str,
                     settlement_rules: str) -> str:
        """Amend a DRAFT agreement. Refused once terms are locked.

        This method exists so Attack B has something concrete to fail
        against: there IS an amendment path, and it is closed the moment
        escrow arrives.
        """
        a = self._require_agreement(agreement_id)
        self._require_client(a)
        self._require_state(a, {S_DRAFT})
        if a.terms_locked:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} terms are locked")

        desc = _clip(service_description, MAX_STR).strip()
        if not desc:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} service_description required")
        reqs = _parse_requirements(requirements_json)

        a.service_description = desc
        a.requirements_json = _canon(reqs)
        a.requirement_count = u256(len(reqs))
        a.evidence_rules = _clip(evidence_rules, MAX_STR)
        a.settlement_rules = _clip(settlement_rules, MAX_STR)
        a.terms_hash = self._compute_terms_hash(a)
        a.updated_tick = u256(self._tick())
        return a.terms_hash

    # ═══ 2 · escrow ═══════════════════════════════════════════════════════════

    @gl.public.write.payable
    def fund_agreement(self, agreement_id: str) -> None:
        """Client deposits the exact payment amount. Terms lock here.

        The deposited figure comes from `gl.message.value` — the amount
        the chain actually moved — never from an argument. A caller
        cannot claim to have funded more than they sent.
        """
        a = self._require_agreement(agreement_id)
        self._require_client(a)
        self._require_state(a, {S_DRAFT})

        sent = int(gl.message.value)
        required = int(a.payment_amount_atto)
        if sent <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} funding must be > 0")
        if sent != required:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} funding must equal payment amount {required} "
                f"(received {sent})")

        a.escrow_deposited_atto = u256(int(a.escrow_deposited_atto) + sent)
        a.terms_hash = self._compute_terms_hash(a)
        a.terms_locked = True
        self._set_state(a, S_FUNDED)
        self._tick()

    # ═══ 3 · lifecycle ════════════════════════════════════════════════════════

    @gl.public.write
    def accept_agreement(self, agreement_id: str) -> None:
        """Provider accepts. Only the designated provider wallet may."""
        a = self._require_agreement(agreement_id)
        self._require_provider(a)
        self._require_state(a, {S_FUNDED})
        self._require_terms_intact(a)
        now = self._tick()
        if now > int(a.acceptance_deadline_tick):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} acceptance deadline passed at tick "
                f"{int(a.acceptance_deadline_tick)} (now {now})")
        self._set_state(a, S_ACTIVE)

    @gl.public.write
    def submit_deliverable(self, agreement_id: str, note: str = "") -> None:
        """Provider declares delivery.

        This is a CLAIM, not evidence, and the contract labels it as
        such: it moves the state machine forward and nothing else. The
        panel is told explicitly not to treat a party's assertion as
        proof — see the adjudication prompt.
        """
        a = self._require_agreement(agreement_id)
        self._require_provider(a)
        self._require_state(a, {S_ACTIVE})
        self._require_terms_intact(a)
        if a.agreement_id not in self.evidence_by_agreement or \
                len(self.evidence_by_agreement[a.agreement_id]) == 0:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} submit evidence before declaring delivery")
        now = self._tick()
        if now > int(a.service_deadline_tick):
            # Late delivery is allowed to reach adjudication; the panel
            # reports deadline_met=False and the settlement rules decide
            # what that costs. The contract does not silently forgive it.
            pass
        self._set_state(a, S_SUBMITTED)

    @gl.public.write
    def expire_agreement(self, agreement_id: str) -> None:
        """Mark an agreement expired once the service deadline passes
        with no delivery. Either party may call; the contract checks the
        clock itself."""
        a = self._require_agreement(agreement_id)
        self._require_party(a)
        self._require_state(a, {S_FUNDED, S_ACTIVE})
        now = self._tick()
        if now <= int(a.service_deadline_tick):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} service deadline not reached "
                f"(tick {int(a.service_deadline_tick)}, now {now})")
        self._set_state(a, S_EXPIRED)

    @gl.public.write
    def cancel_agreement(self, agreement_id: str) -> None:
        """Client cancels before the provider accepts; escrow refunds."""
        a = self._require_agreement(agreement_id)
        self._require_client(a)
        self._require_state(a, {S_DRAFT, S_FUNDED})
        refund = self._escrow_available(a)
        if refund > 0:
            a.escrow_released_atto = u256(int(a.escrow_released_atto) + refund)
            self._set_state(a, S_CANCELLED)
            self._tick()
            self._send_gen(a.client, refund)
        else:
            self._set_state(a, S_CANCELLED)
            self._tick()

    # ═══ 4 · evidence ═════════════════════════════════════════════════════════

    @gl.public.write
    def submit_evidence(self, agreement_id: str, requirement_id: str,
                        evidence_type: str, source_reference: str,
                        content_hash: str, description: str = "") -> str:
        """Commit an evidence record against one requirement.

        The content hash is the commitment. It is written once and never
        mutated; superseding evidence is an explicit new record with an
        incremented version, so history stays auditable (Attack C).
        """
        a = self._require_agreement(agreement_id)
        self._require_party(a)
        self._require_state(a, {S_ACTIVE, S_SUBMITTED, S_ACCEPTED,
                                S_APPEALED, S_UNDETERMINED})
        self._require_terms_intact(a)

        rid = _clip(requirement_id, MAX_ID).strip()
        known = {r["requirement_id"] for r in json.loads(a.requirements_json)}
        if rid not in known:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} unknown requirement_id {rid!r} for {agreement_id}")

        etype = _clip(evidence_type, 32).strip().upper()
        if etype not in EVIDENCE_TYPES:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} unsupported evidence_type {etype!r}")

        chash = _clip(content_hash, 128).strip()
        if not chash:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} content_hash required")

        if agreement_id not in self.evidence_by_agreement:
            self.evidence_by_agreement.get_or_insert_default(agreement_id)
        if len(self.evidence_by_agreement[agreement_id]) >= MAX_EVIDENCE:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} evidence limit reached")

        idx = int(self.evidence_counter[agreement_id]) + 1
        self.evidence_counter[agreement_id] = u256(idx)
        eid = f"{agreement_id}-E{idx:04d}"

        e = Evidence(
            evidence_id=eid,
            agreement_id=agreement_id,
            requirement_id=rid,
            submitter=self._sender(),
            evidence_type=etype,
            source_reference=_clip(source_reference, 512),
            content_hash=chash,
            description=_clip(description, MAX_STR),
            submitted_tick=u256(self._tick()),
            version=u256(1),
            status=EV_ACTIVE,
        )
        self.evidence_by_agreement[agreement_id].append(e)
        self.evidence_owner[eid] = agreement_id
        self.evidence_index[eid] = u256(len(self.evidence_by_agreement[agreement_id]) - 1)
        return eid

    @gl.public.write
    def supersede_evidence(self, agreement_id: str, evidence_id: str,
                           source_reference: str, content_hash: str,
                           description: str = "") -> str:
        """Explicitly replace an earlier record with a NEW one.

        The original is marked SUPERSEDED but its bytes and hash stay
        readable forever. There is no in-place edit anywhere in this
        contract — that is what makes evidence substitution detectable.
        """
        a = self._require_agreement(agreement_id)
        self._require_party(a)
        self._require_state(a, {S_ACTIVE, S_SUBMITTED, S_ACCEPTED,
                                S_APPEALED, S_UNDETERMINED})

        # Resolves through the index and enforces the agreement binding,
        # so a foreign record is refused before anything is written.
        old = self._resolve_evidence(evidence_id, agreement_id)
        if self._key(old.submitter) != self._key(self._sender()):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} only the original submitter may supersede")
        if old.status == EV_SUPERSEDED:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} already superseded")

        chash = _clip(content_hash, 128).strip()
        if not chash:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} content_hash required")

        idx = int(self.evidence_counter[agreement_id]) + 1
        self.evidence_counter[agreement_id] = u256(idx)
        new_id = f"{agreement_id}-E{idx:04d}"

        e = Evidence(
            evidence_id=new_id,
            agreement_id=agreement_id,
            requirement_id=old.requirement_id,
            submitter=self._sender(),
            evidence_type=old.evidence_type,
            source_reference=_clip(source_reference, 512),
            content_hash=chash,
            description=_clip(description, MAX_STR),
            submitted_tick=u256(self._tick()),
            version=u256(int(old.version) + 1),
            status=EV_ACTIVE,
        )
        # Mutating the resolved record mutates the single stored copy,
        # so the change is visible through get_evidence immediately.
        old.status = EV_SUPERSEDED
        self.evidence_by_agreement[agreement_id].append(e)
        self.evidence_owner[new_id] = agreement_id
        self.evidence_index[new_id] = u256(
            len(self.evidence_by_agreement[agreement_id]) - 1)
        return new_id

    @gl.public.write
    def challenge_evidence(self, agreement_id: str, evidence_id: str,
                           reason: str) -> None:
        """Flag a record as contested. Visible to the panel; does not
        delete anything."""
        a = self._require_agreement(agreement_id)
        self._require_party(a)
        e = self._resolve_evidence(evidence_id, agreement_id)
        if not _clip(reason, MAX_STR).strip():
            raise gl.vm.UserError(f"{ERROR_EXPECTED} challenge reason required")
        e.status = EV_CHALLENGED
        e.description = _clip(
            f"{e.description}\n[CHALLENGED] {_clip(reason, MAX_SHORT)}", MAX_STR)
        self._tick()

    # ═══ 5 · adjudication ═════════════════════════════════════════════════════

    def _evidence_snapshot(self, aid: str) -> list:
        out = []
        if aid in self.evidence_by_agreement:
            for e in self.evidence_by_agreement[aid]:
                out.append({
                    "evidence_id": e.evidence_id,
                    "requirement_id": e.requirement_id,
                    "evidence_type": e.evidence_type,
                    "authoritative": e.evidence_type in AUTHORITATIVE_TYPES,
                    "source_reference": e.source_reference,
                    "content_hash": e.content_hash,
                    "description": e.description,
                    "submitted_tick": int(e.submitted_tick),
                    "version": int(e.version),
                    "status": e.status,
                    "submitted_by_role": (
                        "provider" if self._key(e.submitter) ==
                        self._key(self.agreements[aid].provider) else "client"
                    ),
                })
        return out

    def _build_prompt(self, a: Agreement, evidence: list, now_tick: int) -> str:
        payload = {
            "agreement_id": a.agreement_id,
            "terms_hash": a.terms_hash,
            "service_description": a.service_description,
            "requirements": json.loads(a.requirements_json),
            "evidence_rules": a.evidence_rules,
            "settlement_rules": a.settlement_rules,
            "service_deadline_tick": int(a.service_deadline_tick),
            "current_tick": now_tick,
            "evidence": evidence,
        }
        instructions = (
            "You are an independent adjudicator on a GenLayer validator\n"
            "panel. Evaluate a service agreement against its committed\n"
            "requirements and the evidence recorded on chain.\n"
            "\n"
            "RULES\n"
            "1.  Judge ONLY the requirements listed in INPUT. Use their exact\n"
            "    requirement_id values.\n"
            "2.  Evaluate every requirement independently. One failure does\n"
            "    not condemn the others; one success does not excuse them.\n"
            "3.  Weigh EVIDENCE, not assertions. A party stating 'I completed\n"
            "    the work' is a claim and proves nothing on its own. Records\n"
            "    marked \"authoritative\": true (commits, chain transactions,\n"
            "    API results, signed messages, datasets, retrievable URLs) are\n"
            "    independently checkable and carry more weight than prose.\n"
            "4.  Evidence marked SUPERSEDED has been replaced; judge the\n"
            "    current version. Evidence marked CHALLENGED is contested —\n"
            "    say so in your reasoning rather than silently ignoring it.\n"
            "5.  Never invent evidence, facts, dates or identifiers. If you\n"
            "    need something that is not present, that requirement is\n"
            "    UNDETERMINED.\n"
            "6.  Set deadline_met from the evidence about WHEN delivery\n"
            "    happened. If any evidence states or shows that delivery\n"
            "    occurred after the agreed deadline, deadline_met is false.\n"
            "    Do not guess at wall-clock time.\n"
            "7.  Return UNDETERMINED — for a requirement, and for the overall\n"
            "    outcome — when the evidence genuinely does not support a\n"
            "    reliable conclusion. This is a correct answer, not a failure.\n"
            "    Do not force a verdict you cannot defend.\n"
            "8.  You do NOT decide money. Never output an amount, payout,\n"
            "    percentage or penalty. The contract computes settlement from\n"
            "    the committed weights and the real escrow balance; any\n"
            "    monetary field you emit is discarded.\n"
            "9.  evidence_examined is MECHANICAL, not a judgement call: list\n"
            "    the evidence_id of EVERY record in INPUT.evidence, exactly\n"
            "    once each, with no additions. You were given them, so you\n"
            "    examined them. This field is compared across validators, and\n"
            "    it must not depend on which records you found persuasive.\n"
            "\n"
            "OUTCOME\n"
            "  PASS          every requirement PASS\n"
            "  PARTIAL       at least one PASS and at least one FAIL\n"
            "  FAIL          every requirement FAIL\n"
            "  UNDETERMINED  at least one requirement UNDETERMINED\n"
            "\n"
            "Return ONLY this JSON object:\n"
            "{\n"
            '  "agreement_id": "<exact id from INPUT>",\n'
            '  "outcome": "PASS" | "PARTIAL" | "FAIL" | "UNDETERMINED",\n'
            '  "requirements": [\n'
            '    {"requirement_id": "<id>", "status": "PASS"|"FAIL"|"UNDETERMINED"}\n'
            "  ],\n"
            '  "deadline_met": true | false,\n'
            '  "evidence_examined": ["<every evidence_id from INPUT.evidence>"],\n'
            '  "reasoning": "<why, referencing evidence ids>"\n'
            "}\n"
            "\n"
            "The evidence_ids you must list in evidence_examined are exactly:\n"
            + _canon([e["evidence_id"] for e in evidence]) + "\n"
        )
        return instructions + "\nINPUT:\n" + _canon(payload)

    def _adjudicate_nondet(self, prompt: str, agreement_id: str,
                           requirement_ids: list) -> dict:
        """The nondeterministic round.

        Leader and validators each run the SAME work independently: same
        prompt, same parser, same normaliser. The validator does not
        inspect the leader's answer for well-formedness and call that
        verification — it produces its own verdict and compares decision
        fingerprints. Agreement means two nodes reading the same evidence
        reached the same factual conclusions, not that one node's JSON
        parsed.
        """
        p = prompt
        aid = agreement_id
        rids = list(requirement_ids)
        normalize = _normalize_verdict
        fingerprint = _decision_fingerprint

        def leader_fn():
            raw = gl.nondet.exec_prompt(p, response_format="json")
            if not isinstance(raw, dict):
                raise gl.vm.UserError(f"{ERROR_LLM} panel returned non-dict")
            norm = normalize(raw, aid, rids)
            return {"normalized": norm, "raw": raw}

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return _handle_leader_error(leaders_res, leader_fn)
            try:
                raw = gl.nondet.exec_prompt(p, response_format="json")
                if not isinstance(raw, dict):
                    return False
                mine = normalize(raw, aid, rids)
            except gl.vm.UserError:
                return False
            except Exception:
                return False

            theirs = leaders_res.calldata.get("normalized") or {}
            try:
                return fingerprint(theirs) == fingerprint(mine)
            except Exception:
                return False

        return gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

    @gl.public.write
    def request_adjudication(self, agreement_id: str) -> int:
        """Run a GenLayer adjudication round over the committed terms.

        Legal from SUBMITTED (first round), APPEALED (contested result)
        and UNDETERMINED (retry after more evidence). Each round writes a
        new verdict; none is ever overwritten.
        """
        a = self._require_agreement(agreement_id)
        self._require_party(a)
        self._require_state(a, {S_SUBMITTED, S_APPEALED, S_UNDETERMINED})
        self._require_terms_intact(a)

        if int(a.adjudication_round) >= MAX_ADJUDICATION_ROUNDS:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} adjudication round limit "
                f"({MAX_ADJUDICATION_ROUNDS}) reached")

        evidence = self._evidence_snapshot(agreement_id)
        if not evidence:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} no evidence to adjudicate")

        reqs = json.loads(a.requirements_json)
        rids = [r["requirement_id"] for r in reqs]
        known_evidence = {e["evidence_id"] for e in evidence}
        now = self._tick()
        prompt = self._build_prompt(a, evidence, now)

        prior_state = a.status
        self._set_state(a, S_ADJUDICATING)
        try:
            result = self._adjudicate_nondet(prompt, agreement_id, rids)
        except gl.vm.UserError:
            self._set_state(a, prior_state)     # leave no stuck state
            raise

        norm = result["normalized"]
        raw = result["raw"]

        # Post-consensus binding check: every cited evidence id must
        # belong to THIS agreement. The panel agreeing on a reference
        # does not make the reference legitimate (Attack I).
        for eid in norm["evidence_examined"]:
            if eid not in known_evidence:
                self._set_state(a, prior_state)
                raise gl.vm.UserError(
                    f"{ERROR_EXPECTED} verdict cites evidence {eid!r} that does "
                    f"not belong to {agreement_id}")

        # Earned weight is derived HERE, by the contract, from the
        # committed weights and the panel's per-requirement statuses.
        # The model never sees a number and never returns one.
        weights = {r["requirement_id"]: int(r["weight"]) for r in reqs}
        earned = 0
        for r in norm["requirements"]:
            if r["status"] == R_PASS:
                earned += weights[r["requirement_id"]]

        round_no = int(a.adjudication_round) + 1
        verdict_id = round_no
        v = Verdict(
            verdict_id=u256(verdict_id),
            agreement_id=agreement_id,
            round_number=u256(round_no),
            outcome=str(norm["outcome"]),
            requirement_results_json=_canon(norm["requirements"]),
            deadline_met=bool(norm["deadline_met"]),
            evidence_examined_json=_canon(norm["evidence_examined"]),
            reasoning=str(norm["reasoning"]),
            earned_weight=u256(earned),
            terms_hash=a.terms_hash,
            evaluated_tick=u256(int(self.current_tick)),
            raw_json=_canon(raw),
        )
        if agreement_id not in self.verdicts:
            self.verdicts.get_or_insert_default(agreement_id)
        if agreement_id not in self.verdict_ids:
            self.verdict_ids.get_or_insert_default(agreement_id)
        self.verdicts[agreement_id][u256(verdict_id)] = v
        self.verdict_ids[agreement_id].append(u256(verdict_id))

        a.adjudication_round = u256(round_no)
        a.latest_verdict_id = u256(verdict_id)

        # UNDETERMINED is a first-class destination, not a soft PASS or
        # FAIL. It parks the agreement with escrow intact and offers only
        # two exits: more evidence + another round, or the recovery path
        # after the resolution deadline.
        if norm["outcome"] == O_UNDETERMINED:
            self._set_state(a, S_UNDETERMINED)
            a.appeal_deadline_tick = u256(0)
        else:
            self._set_state(a, S_ACCEPTED)
            a.appeal_deadline_tick = u256(
                int(self.current_tick) + int(a.appeal_window_ticks))
        return verdict_id

    # ═══ 6 · appeal and finality ══════════════════════════════════════════════

    @gl.public.write
    def appeal(self, agreement_id: str, reason: str) -> None:
        """Contest an ACCEPTED verdict before the window closes.

        Appealing moves the agreement OUT of ACCEPTED, which is what
        stops a stale accepted result from later settling: settle()
        only reads FINALIZED, and the path back to FINALIZED runs
        through a fresh adjudication round.
        """
        a = self._require_agreement(agreement_id)
        self._require_party(a)
        self._require_state(a, {S_ACCEPTED})
        if not _clip(reason, MAX_STR).strip():
            raise gl.vm.UserError(f"{ERROR_EXPECTED} appeal reason required")
        now = self._tick()
        if now > int(a.appeal_deadline_tick):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} appeal window closed at tick "
                f"{int(a.appeal_deadline_tick)} (now {now})")
        a.appeal_deadline_tick = u256(0)
        self._set_state(a, S_APPEALED)

    @gl.public.write
    def finalize(self, agreement_id: str) -> None:
        """Close the appeal window: ACCEPTED → FINALIZED.

        This is the whole reason ACCEPTED and FINALIZED are separate
        states. A verdict that has been accepted by consensus is not yet
        spendable; it becomes spendable only after the window in which a
        party could contest it has actually elapsed.
        """
        a = self._require_agreement(agreement_id)
        self._require_party(a)
        self._require_state(a, {S_ACCEPTED})
        self._require_terms_intact(a)
        now = self._tick()
        if now < int(a.appeal_deadline_tick):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} appeal window open until tick "
                f"{int(a.appeal_deadline_tick)} (now {now})")
        self._set_state(a, S_FINALIZED)

    # ═══ 7 · deterministic settlement ═════════════════════════════════════════

    def _compute_settlement(self, a: Agreement, v: Verdict) -> tuple:
        """Pure integer arithmetic over committed weights and real escrow.

            provider_gross = escrow * earned_weight / 100      (floor)
            penalty        = provider_gross * penalty_bps / 10000
                             — only when the deadline was missed
            provider_net   = provider_gross - penalty
            client_refund  = escrow - provider_net

        Rounding: integer floor division, so any remainder falls to the
        CLIENT. That direction is deliberate — the party who is owed a
        refund should never be short by a rounding artefact, and the
        provider cannot gain from one. The identity
        provider_net + client_refund == escrow holds exactly, always.
        """
        escrow = self._escrow_available(a)
        earned = int(v.earned_weight)
        if earned < 0 or earned > WEIGHT_TOTAL:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} earned weight {earned} out of range")

        provider_gross = (escrow * earned) // WEIGHT_TOTAL

        penalty = 0
        if not bool(v.deadline_met) and int(a.penalty_bps) > 0:
            penalty = (provider_gross * int(a.penalty_bps)) // 10_000
        provider_net = provider_gross - penalty
        if provider_net < 0:
            provider_net = 0

        client_refund = escrow - provider_net

        # Invariants, asserted rather than assumed.
        if provider_net + client_refund != escrow:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} settlement does not balance: "
                f"{provider_net}+{client_refund}!={escrow}")
        if provider_net > escrow or client_refund > escrow:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} settlement exceeds escrow")
        return provider_net, client_refund, penalty, escrow

    @gl.public.write
    def settle(self, agreement_id: str) -> None:
        """Release escrow according to the FINALIZED verdict.

        Ordering is strict and load-bearing:
          read ledger → validate → compute → zero ledger → persist →
          only then emit value.
        A second call finds status SETTLED and is refused by the state
        machine before it can reach any transfer.
        """
        a = self._require_agreement(agreement_id)
        self._require_party(a)
        self._require_state(a, {S_FINALIZED})
        self._require_terms_intact(a)

        if int(a.latest_verdict_id) == 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} no verdict to settle")
        v = self.verdicts[agreement_id][u256(int(a.latest_verdict_id))]

        # A verdict may only settle the terms it actually judged.
        if v.terms_hash != a.terms_hash:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} verdict judged terms {v.terms_hash}, "
                f"agreement now has {a.terms_hash}")
        if v.outcome == O_UNDETERMINED:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} cannot settle an UNDETERMINED verdict")

        escrow_before = self._escrow_available(a)
        if escrow_before <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} no escrow to release")

        provider_amt, client_amt, penalty, escrow = self._compute_settlement(a, v)

        # ── zero before transfer ──
        a.escrow_released_atto = u256(int(a.escrow_released_atto) + escrow)
        s = Settlement(
            agreement_id=agreement_id,
            verdict_id=u256(int(v.verdict_id)),
            provider_payout_atto=u256(provider_amt),
            client_refund_atto=u256(client_amt),
            penalty_atto=u256(penalty),
            escrow_before_atto=u256(escrow_before),
            escrow_after_atto=u256(0),
            earned_weight=u256(int(v.earned_weight)),
            total_weight=u256(WEIGHT_TOTAL),
            status="SETTLED",
            settled_tick=u256(self._tick()),
        )
        self.settlements[agreement_id] = s
        self._set_state(a, S_SETTLED)

        # ── only now does value move ──
        if provider_amt > 0:
            self._send_gen(a.provider, provider_amt)
        if client_amt > 0:
            self._send_gen(a.client, client_amt)

    @gl.public.write
    def recover_escrow(self, agreement_id: str) -> None:
        """Escape hatch: refund the client from a terminally stuck
        agreement once the resolution deadline has passed.

        Without this, an UNDETERMINED verdict or an abandoned provider
        could hold escrow forever. With it, funds always have an exit
        that does not depend on the counterparty cooperating.
        """
        a = self._require_agreement(agreement_id)
        self._require_party(a)
        self._require_state(a, {S_EXPIRED, S_UNDETERMINED, S_APPEALED})
        now = self._tick()
        if now <= int(a.resolution_deadline_tick):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} resolution deadline not reached "
                f"(tick {int(a.resolution_deadline_tick)}, now {now})")
        refund = self._escrow_available(a)
        if refund <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} no escrow to recover")
        a.escrow_released_atto = u256(int(a.escrow_released_atto) + refund)
        self._set_state(a, S_REFUNDED)
        self._send_gen(a.client, refund)

    @gl.public.write
    def tick(self) -> int:
        """Advance the protocol clock. Any account may call; used to age
        past a deadline or an appeal window."""
        return self._tick()

    # ─── the one place value leaves this contract ────────────────────────────
    def _send_gen(self, to_address, amount_atto: int) -> None:
        if amount_atto <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} transfer amount must be positive")
        addr = str(to_address)
        if not addr:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} missing recipient address")
        _Recipient(Address(addr)).emit_transfer(
            value=u256(int(amount_atto)), on="finalized")

    # ═══ 8 · views ════════════════════════════════════════════════════════════

    @gl.public.view
    def get_protocol_info(self) -> dict:
        return {
            "version": self.version,
            "agreement_count": int(self.agreement_count),
            "current_tick": int(self.current_tick),
            "weight_total": WEIGHT_TOTAL,
            "default_appeal_window_ticks": APPEAL_WINDOW_TICKS,
            "max_adjudication_rounds": MAX_ADJUDICATION_ROUNDS,
            "evidence_types": sorted(EVIDENCE_TYPES),
            "authoritative_types": sorted(AUTHORITATIVE_TYPES),
        }

    @gl.public.view
    def get_agreement(self, agreement_id: str) -> dict:
        a = self._require_agreement(agreement_id)
        return {
            "agreement_id": a.agreement_id,
            "client": str(a.client),
            "provider": str(a.provider),
            "service_description": a.service_description,
            "requirement_count": int(a.requirement_count),
            "payment_amount_atto": int(a.payment_amount_atto),
            "escrow_deposited": int(a.escrow_deposited_atto),
            "escrow_released": int(a.escrow_released_atto),
            "escrow_available": self._escrow_available(a),
            "penalty_bps": int(a.penalty_bps),
            "acceptance_deadline_tick": int(a.acceptance_deadline_tick),
            "service_deadline_tick": int(a.service_deadline_tick),
            "resolution_deadline_tick": int(a.resolution_deadline_tick),
            "appeal_window_ticks": int(a.appeal_window_ticks),
            "appeal_deadline_tick": int(a.appeal_deadline_tick),
            "evidence_rules": a.evidence_rules,
            "settlement_rules": a.settlement_rules,
            "terms_hash": a.terms_hash,
            "terms_locked": bool(a.terms_locked),
            "status": a.status,
            "adjudication_round": int(a.adjudication_round),
            "latest_verdict_id": int(a.latest_verdict_id),
            "created_tick": int(a.created_tick),
            "updated_tick": int(a.updated_tick),
        }

    @gl.public.view
    def get_requirements(self, agreement_id: str) -> list:
        a = self._require_agreement(agreement_id)
        try:
            return json.loads(a.requirements_json)
        except Exception:
            return []

    @gl.public.view
    def get_evidence(self, agreement_id: str) -> list:
        self._require_agreement(agreement_id)
        out = []
        if agreement_id in self.evidence_by_agreement:
            for e in self.evidence_by_agreement[agreement_id]:
                out.append({
                    "evidence_id": e.evidence_id,
                    "agreement_id": e.agreement_id,
                    "requirement_id": e.requirement_id,
                    "submitter": str(e.submitter),
                    "evidence_type": e.evidence_type,
                    "authoritative": e.evidence_type in AUTHORITATIVE_TYPES,
                    "source_reference": e.source_reference,
                    "content_hash": e.content_hash,
                    "description": e.description,
                    "submitted_tick": int(e.submitted_tick),
                    "version": int(e.version),
                    "status": e.status,
                })
        return out

    @gl.public.view
    def list_verdicts(self, agreement_id: str) -> list:
        out = []
        if agreement_id in self.verdict_ids:
            for i in self.verdict_ids[agreement_id]:
                out.append(int(i))
        return out

    @gl.public.view
    def get_verdict(self, agreement_id: str, verdict_id: int) -> dict:
        self._require_agreement(agreement_id)
        vid = u256(int(verdict_id))
        if agreement_id not in self.verdicts or vid not in self.verdicts[agreement_id]:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} unknown verdict")
        v = self.verdicts[agreement_id][vid]
        try:
            reqs = json.loads(v.requirement_results_json)
        except Exception:
            reqs = []
        try:
            examined = json.loads(v.evidence_examined_json)
        except Exception:
            examined = []
        return {
            "verdict_id": int(v.verdict_id),
            "agreement_id": v.agreement_id,
            "round_number": int(v.round_number),
            "outcome": v.outcome,
            "requirements": reqs,
            "deadline_met": bool(v.deadline_met),
            "evidence_examined": examined,
            "reasoning": v.reasoning,
            "earned_weight": int(v.earned_weight),
            "total_weight": WEIGHT_TOTAL,
            "terms_hash": v.terms_hash,
            "evaluated_tick": int(v.evaluated_tick),
            "raw_json": v.raw_json,
        }

    @gl.public.view
    def get_escrow(self, agreement_id: str) -> dict:
        a = self._require_agreement(agreement_id)
        return {
            "agreement_id": a.agreement_id,
            "payment_amount_atto": int(a.payment_amount_atto),
            "deposited": int(a.escrow_deposited_atto),
            "released": int(a.escrow_released_atto),
            "available": self._escrow_available(a),
            "held": a.status in ESCROW_HELD_STATES,
        }

    @gl.public.view
    def get_settlement(self, agreement_id: str) -> dict:
        self._require_agreement(agreement_id)
        if agreement_id not in self.settlements:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} no settlement for {agreement_id}")
        s = self.settlements[agreement_id]
        return {
            "agreement_id": s.agreement_id,
            "verdict_id": int(s.verdict_id),
            "provider_payout": int(s.provider_payout_atto),
            "client_refund": int(s.client_refund_atto),
            "penalty": int(s.penalty_atto),
            "escrow_before": int(s.escrow_before_atto),
            "escrow_after": int(s.escrow_after_atto),
            "earned_weight": int(s.earned_weight),
            "total_weight": int(s.total_weight),
            "status": s.status,
            "settled_tick": int(s.settled_tick),
        }

    @gl.public.view
    def get_state(self, agreement_id: str) -> dict:
        """Compact lifecycle probe — what a keeper or UI polls."""
        a = self._require_agreement(agreement_id)
        return {
            "agreement_id": a.agreement_id,
            "status": a.status,
            "terms_locked": bool(a.terms_locked),
            "escrow_available": self._escrow_available(a),
            "adjudication_round": int(a.adjudication_round),
            "latest_verdict_id": int(a.latest_verdict_id),
            "appeal_deadline_tick": int(a.appeal_deadline_tick),
            "current_tick": int(self.current_tick),
            "can_settle": a.status == S_FINALIZED,
        }

    @gl.public.view
    def list_agreements(self, offset: int = 0, limit: int = 50) -> dict:
        n = len(self.agreement_ids)
        start = max(0, int(offset))
        end = min(n, start + max(1, int(limit)))
        rows = []
        for i in range(start, end):
            aid = self.agreement_ids[i]
            a = self.agreements[aid]
            rows.append({
                "agreement_id": a.agreement_id,
                "client": str(a.client),
                "provider": str(a.provider),
                "status": a.status,
                "payment_amount_atto": int(a.payment_amount_atto),
                "escrow_available": self._escrow_available(a),
                "adjudication_round": int(a.adjudication_round),
                "created_tick": int(a.created_tick),
            })
        return {"total": n, "offset": start, "count": len(rows), "rows": rows}
