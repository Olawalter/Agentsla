"""STEWARD FIX — a non-manipulable protocol clock.

The rejection: `current_tick` was one global counter that ANY account
could advance — explicitly through the public `tick()`, and incidentally
through every state-changing call, since each one called `_tick()`. The
acceptance, service, resolution and appeal deadlines were all measured
in those ticks, so an outsider could manufacture elapsed time: lapse a
provider's acceptance window, expire an agreement mid-service, close an
appeal window before the losing party could appeal, and unlock escrow
recovery.

Now there is no clock in storage. `_now()` reads the GenLayer transaction
datetime, which every validator sees identically and no caller chooses.
Deadlines are derived from the datetime of the lifecycle event that opens
each window. These tests move transaction time only through gltest's own
`direct_vm.warp()` — test infrastructure with no counterpart in the
deployed contract — and every premature attempt asserts that state,
escrow and finality did not move.
"""
import pytest

from .conftest import (
    ACCEPTANCE_WINDOW, APPEAL_WINDOW, ESCROW, REQUIREMENTS_JSON, SERVICE_WINDOW,
    RESOLUTION_WINDOW, T0, WINDOWS, commit_canonical_evidence, make_verdict,
    mock_panel, pass_deadline, tx_time, warp_to,
)

DEADLINES = ("acceptance_deadline", "service_deadline", "resolution_deadline",
             "appeal_deadline")
PARTIAL = {"R1": "PASS", "R2": "PASS", "R3": "FAIL"}
UNDECIDED = {"R1": "PASS", "R2": "UNDETERMINED", "R3": "PASS"}


def ledger(deployed, aid) -> dict:
    """Everything a premature call must leave untouched."""
    a = deployed.get_agreement(aid)
    keys = ("status", "escrow_available", "escrow_released", "finalized_at",
            "terms_hash", "latest_verdict_id", *DEADLINES,
            "acceptance_window_seconds", "service_window_seconds",
            "resolution_window_seconds", "appeal_window_seconds")
    return {k: a[k] for k in keys}


def ids(deployed, aid):
    return [e["evidence_id"] for e in deployed.get_evidence(aid)
            if e["status"] != "SUPERSEDED"]


def adjudicate(direct_vm, deployed, sender, aid, statuses):
    mock_panel(direct_vm, make_verdict(aid, statuses, evidence_examined=ids(deployed, aid)))
    direct_vm.sender = sender
    return deployed.request_adjudication(aid)


def refused(direct_vm, deployed, aid, sender, call, message):
    """Attempt `call` as `sender`; it must revert with `message` and move nothing."""
    before = ledger(deployed, aid)
    direct_vm.sender = sender
    with direct_vm.expect_revert(message):
        call()
    assert ledger(deployed, aid) == before, f"{message!r} attempt changed state"


@pytest.fixture
def accepted(direct_vm, deployed, direct_alice, submitted):
    """An ACCEPTED verdict (PARTIAL), appeal window open."""
    adjudicate(direct_vm, deployed, direct_alice, submitted, PARTIAL)
    assert deployed.get_agreement(submitted)["status"] == "ACCEPTED"
    return submitted


@pytest.fixture
def undetermined(direct_vm, deployed, direct_alice, submitted):
    adjudicate(direct_vm, deployed, direct_alice, submitted, UNDECIDED)
    assert deployed.get_agreement(submitted)["status"] == "UNDETERMINED"
    return submitted


@pytest.fixture
def outsider(deployed, direct_accounts, direct_alice, direct_bob, direct_charlie):
    """An account with no relationship to anything — not party, not attacker."""
    known = {bytes(x) if isinstance(x, (bytes, bytearray)) else str(x)
             for x in (direct_alice, direct_bob, direct_charlie)}
    for acct in reversed(direct_accounts):
        key = bytes(acct) if isinstance(acct, (bytes, bytearray)) else str(acct)
        if key not in known:
            return acct
    raise AssertionError("no unrelated account available")


# ═══ Test 1 — the public clock attack ═══════════════════════════════════════

def test_1_public_clock_advance_is_unavailable(
    direct_vm, deployed, direct_charlie, accepted
):
    before = ledger(deployed, accepted)
    direct_vm.sender = direct_charlie
    for name in ("tick", "advance_time", "advance_clock", "set_time",
                 "set_protocol_time", "set_deadline", "force_expire", "force_finalize"):
        with direct_vm.expect_revert():
            getattr(deployed, name)()
    assert ledger(deployed, accepted) == before
    info = deployed.get_protocol_info()
    assert "current_tick" not in info
    assert info["time_source"] == "GenLayer transaction datetime (Unix seconds)"


# ═══ Test 2 — a caller-supplied future timestamp ════════════════════════════

def test_2_no_lifecycle_method_accepts_a_timestamp(
    direct_vm, deployed, direct_alice, direct_bob, accepted
):
    """A far-future time handed to every deadline-gated method is refused —
    none of them has anywhere to put it. Time comes from the transaction."""
    far_future = 4_102_444_800            # 2100-01-01
    for sender, method in ((direct_alice, "finalize"), (direct_alice, "expire_agreement"),
                           (direct_alice, "recover_escrow"), (direct_bob, "accept_agreement"),
                           (direct_alice, "settle")):
        refused(direct_vm, deployed, accepted, sender,
                lambda: getattr(deployed, method)(accepted, far_future), "")
    assert deployed.get_agreement(accepted)["status"] == "ACCEPTED"


# ═══ Tests 3 & 4 — acceptance ═══════════════════════════════════════════════

def test_3_premature_acceptance_expiry_is_refused(
    direct_vm, deployed, direct_alice, direct_bob, funded
):
    a = deployed.get_agreement(funded)
    assert a["funded_at"] == T0
    assert a["acceptance_deadline"] == T0 + ACCEPTANCE_WINDOW

    # the last second of the window
    warp_to(direct_vm, a["acceptance_deadline"])
    refused(direct_vm, deployed, funded, direct_alice,
            lambda: deployed.expire_agreement(funded), "acceptance deadline not reached")
    refused(direct_vm, deployed, funded, direct_alice,
            lambda: deployed.recover_escrow(funded), "illegal transition from FUNDED")
    assert deployed.get_agreement(funded)["escrow_available"] == ESCROW

    # and the provider can still accept in that same second
    direct_vm.sender = direct_bob
    deployed.accept_agreement(funded)
    assert deployed.get_agreement(funded)["status"] == "ACTIVE"


def test_4_legitimate_acceptance_expiry_and_refund(
    direct_vm, deployed, direct_alice, direct_bob, funded
):
    pass_deadline(direct_vm, deployed, funded, "acceptance_deadline")
    refused(direct_vm, deployed, funded, direct_bob,
            lambda: deployed.accept_agreement(funded), "acceptance deadline passed")

    direct_vm.sender = direct_alice
    deployed.expire_agreement(funded)
    assert deployed.get_agreement(funded)["status"] == "EXPIRED"
    deployed.recover_escrow(funded)
    a = deployed.get_agreement(funded)
    assert a["status"] == "REFUNDED"
    assert a["escrow_available"] == 0 and a["escrow_released"] == ESCROW


# ═══ Tests 5 & 6 — service ══════════════════════════════════════════════════

def test_5_premature_service_expiry_is_refused(
    direct_vm, deployed, direct_alice, direct_bob, active
):
    a = deployed.get_agreement(active)
    assert a["accepted_at"] == T0
    assert a["service_deadline"] == T0 + SERVICE_WINDOW

    warp_to(direct_vm, a["service_deadline"])
    for sender in (direct_alice, direct_bob):
        refused(direct_vm, deployed, active, sender,
                lambda: deployed.expire_agreement(active), "service deadline not reached")
    assert deployed.get_agreement(active)["status"] == "ACTIVE"
    assert deployed.get_agreement(active)["escrow_available"] == ESCROW


def test_6_legitimate_service_expiry_then_recovery_only_after_resolution(
    direct_vm, deployed, direct_alice, active
):
    pass_deadline(direct_vm, deployed, active, "service_deadline")
    direct_vm.sender = direct_alice
    deployed.expire_agreement(active)
    a = deployed.get_agreement(active)
    assert a["status"] == "EXPIRED"
    assert a["resolution_deadline"] == a["service_deadline"] + RESOLUTION_WINDOW

    # expired is not refundable yet — the resolution window still runs
    warp_to(direct_vm, a["resolution_deadline"])
    refused(direct_vm, deployed, active, direct_alice,
            lambda: deployed.recover_escrow(active), "resolution deadline not reached")

    pass_deadline(direct_vm, deployed, active, "resolution_deadline")
    deployed.recover_escrow(active)
    assert deployed.get_agreement(active)["status"] == "REFUNDED"
    assert deployed.get_agreement(active)["escrow_available"] == 0


# ═══ Test 7 — resolution ════════════════════════════════════════════════════

def test_7_premature_resolution_expiry_is_refused(
    direct_vm, deployed, direct_alice, direct_bob, undetermined
):
    a = deployed.get_agreement(undetermined)
    warp_to(direct_vm, a["resolution_deadline"])
    for sender in (direct_alice, direct_bob):
        refused(direct_vm, deployed, undetermined, sender,
                lambda: deployed.recover_escrow(undetermined), "resolution deadline not reached")
        refused(direct_vm, deployed, undetermined, sender,
                lambda: deployed.finalize(undetermined), "illegal transition from UNDETERMINED")
        refused(direct_vm, deployed, undetermined, sender,
                lambda: deployed.settle(undetermined), "illegal transition from UNDETERMINED")
    a = deployed.get_agreement(undetermined)
    assert a["status"] == "UNDETERMINED" and a["finalized_at"] == 0
    assert a["escrow_available"] == ESCROW


# ═══ Tests 8 & 9 — appeal and finality ══════════════════════════════════════

def test_8_premature_finalization_is_refused(
    direct_vm, deployed, direct_alice, direct_bob, accepted
):
    a = deployed.get_agreement(accepted)
    assert a["verdict_at"] == T0
    assert a["appeal_deadline"] == T0 + APPEAL_WINDOW

    warp_to(direct_vm, a["appeal_deadline"])          # the last second
    for sender in (direct_alice, direct_bob):
        refused(direct_vm, deployed, accepted, sender,
                lambda: deployed.finalize(accepted), "appeal window open until")
        refused(direct_vm, deployed, accepted, sender,
                lambda: deployed.settle(accepted), "illegal transition from ACCEPTED")
    a = deployed.get_agreement(accepted)
    assert a["status"] != "FINALIZED" and a["finalized_at"] == 0
    assert a["escrow_available"] == ESCROW

    # the appeal right is intact in that same second
    direct_vm.sender = direct_bob
    deployed.appeal(accepted, "the delivery commit is misread")
    assert deployed.get_agreement(accepted)["status"] == "APPEALED"


def test_9_legitimate_finalization_after_the_appeal_deadline(
    direct_vm, deployed, direct_alice, direct_bob, accepted
):
    deadline = pass_deadline(direct_vm, deployed, accepted, "appeal_deadline")
    refused(direct_vm, deployed, accepted, direct_bob,
            lambda: deployed.appeal(accepted, "too late"), "appeal window closed")

    direct_vm.sender = direct_alice
    deployed.finalize(accepted)
    a = deployed.get_agreement(accepted)
    assert a["status"] == "FINALIZED"
    assert a["finalized_at"] == deadline + 1 == tx_time(direct_vm)

    deployed.settle(accepted)
    s = deployed.get_settlement(accepted)
    assert s["provider_payout"] == ESCROW * 70 // 100
    assert s["client_refund"] == ESCROW - ESCROW * 70 // 100
    assert deployed.get_agreement(accepted)["escrow_available"] == 0


def test_9_a_new_verdict_opens_a_new_window_from_its_own_transaction(
    direct_vm, deployed, direct_alice, direct_bob, accepted
):
    """§16 — the only thing that rewrites the appeal deadline is a later
    verdict, and it anchors to that verdict's own datetime."""
    direct_vm.sender = direct_bob
    deployed.appeal(accepted, "re-hear")
    assert deployed.get_agreement(accepted)["appeal_deadline"] == 0

    retried_at = T0 + 5 * 3600
    warp_to(direct_vm, retried_at)
    adjudicate(direct_vm, deployed, direct_alice, accepted, PARTIAL)
    a = deployed.get_agreement(accepted)
    assert a["verdict_at"] == retried_at
    assert a["appeal_deadline"] == retried_at + APPEAL_WINDOW


# ═══ Test 10 — who calls does not change what time it is ════════════════════

def test_10_no_caller_controls_elapsed_time(
    direct_vm, deployed, direct_alice, direct_bob, direct_charlie, outsider, accepted
):
    """Four callers, every deadline-gated action, before the deadline. Then an
    attacker does what used to age every agreement — a burst of unrelated
    state-changing transactions — and the deadlines have not moved."""
    before = ledger(deployed, accepted)
    warp_to(direct_vm, before["appeal_deadline"])

    party_attempts = [
        (lambda: deployed.finalize(accepted), "appeal window open until"),
        (lambda: deployed.settle(accepted), "illegal transition from ACCEPTED"),
        (lambda: deployed.recover_escrow(accepted), "illegal transition from ACCEPTED"),
        (lambda: deployed.expire_agreement(accepted), "illegal transition from ACCEPTED"),
    ]
    for sender in (direct_alice, direct_bob):
        for call, message in party_attempts:
            refused(direct_vm, deployed, accepted, sender, call, message)
    for sender in (direct_charlie, outsider):
        for call, _ in party_attempts:
            refused(direct_vm, deployed, accepted, sender, call, "not a party to this agreement")

    # the old incidental attack: generate activity
    direct_vm.sender = direct_charlie
    for _ in range(25):
        deployed.create_agreement(str(direct_alice), "noise", REQUIREMENTS_JSON, 1, *WINDOWS)

    assert ledger(deployed, accepted) == before
    refused(direct_vm, deployed, accepted, direct_alice,
            lambda: deployed.finalize(accepted), "appeal window open until")


# ═══ Test 11 — every fund path, prematurely ═════════════════════════════════

def test_11_no_fund_path_opens_early_at_any_stage(
    direct_vm, deployed, direct_alice, direct_bob, direct_charlie
):
    """Walk one agreement through every held-escrow state. At each, try every
    time-gated path that moves or unlocks funds, from a party and from an
    attacker, one second before the relevant deadline."""
    direct_vm.sender = direct_alice
    aid = deployed.create_agreement(str(direct_bob), "svc", REQUIREMENTS_JSON, ESCROW, *WINDOWS)
    direct_vm.value = ESCROW
    deployed.fund_agreement(aid)
    direct_vm.value = 0

    def attack(stage, deadline_field):
        deadline = deployed.get_agreement(aid)[deadline_field]
        warp_to(direct_vm, deadline)
        for sender in (direct_alice, direct_bob, direct_charlie):
            for method in ("expire_agreement", "recover_escrow", "finalize", "settle"):
                before = ledger(deployed, aid)
                direct_vm.sender = sender
                with direct_vm.expect_revert():
                    getattr(deployed, method)(aid)
                assert ledger(deployed, aid) == before, f"{stage}: {method} moved state"
        a = deployed.get_agreement(aid)
        assert a["escrow_available"] == ESCROW and a["escrow_released"] == 0, stage
        assert a["finalized_at"] == 0, stage

    attack("FUNDED", "acceptance_deadline")
    direct_vm.sender = direct_bob
    deployed.accept_agreement(aid)
    attack("ACTIVE", "service_deadline")
    direct_vm.sender = direct_bob
    commit_canonical_evidence(deployed, aid)
    deployed.submit_deliverable(aid, "done")
    attack("SUBMITTED", "resolution_deadline")
    adjudicate(direct_vm, deployed, direct_alice, aid, PARTIAL)
    attack("ACCEPTED", "appeal_deadline")
    direct_vm.sender = direct_bob
    deployed.appeal(aid, "contest")
    attack("APPEALED", "resolution_deadline")


# ═══ Test 12 — deadlines cannot be rewritten ════════════════════════════════

def test_12_no_public_write_rewrites_an_established_deadline(
    direct_vm, deployed, direct_alice, direct_bob, direct_charlie, accepted
):
    """Every public write, from both parties and an attacker, with arguments
    that would reach its body. None may move a deadline, a window or the
    terms. (appeal and request_adjudication ARE the events that own the
    appeal window, and are exercised in test 9.)"""
    before = ledger(deployed, accepted)
    evidence_id = ids(deployed, accepted)[0]
    writes = [
        lambda: deployed.update_terms(accepted, "new", REQUIREMENTS_JSON, "", ""),
        lambda: deployed.fund_agreement(accepted),
        lambda: deployed.accept_agreement(accepted),
        lambda: deployed.submit_deliverable(accepted, "again"),
        lambda: deployed.expire_agreement(accepted),
        lambda: deployed.cancel_agreement(accepted),
        lambda: deployed.finalize(accepted),
        lambda: deployed.settle(accepted),
        lambda: deployed.recover_escrow(accepted),
        lambda: deployed.submit_evidence(accepted, "R1", "OTHER", "note", "x", "late note"),
        lambda: deployed.challenge_evidence(accepted, evidence_id, "contest the dataset"),
        lambda: deployed.create_agreement(str(direct_bob), "other", REQUIREMENTS_JSON,
                                          1, *WINDOWS),
    ]
    for sender in (direct_alice, direct_bob, direct_charlie):
        for write in writes:
            direct_vm.sender = sender
            try:
                write()
            except Exception:
                pass
            now = deployed.get_agreement(accepted)
            for field in (*DEADLINES, "acceptance_window_seconds", "service_window_seconds",
                          "resolution_window_seconds", "appeal_window_seconds", "terms_hash"):
                assert now[field] == before[field], f"{field} moved"
    assert deployed.get_agreement(accepted)["status"] == "ACCEPTED"


# ═══ Test 13 — the decision is a function of the transaction datetime ═══════

def test_13_contract_time_is_the_transaction_datetime_not_the_host_clock(
    direct_vm, deployed, direct_alice, direct_bob
):
    """The machine running this test is in 2026. The transaction says 2031.
    The contract records 2031, to the second."""
    in_2031 = 1_925_078_400                 # 2031-01-01T00:00:00Z
    warp_to(direct_vm, in_2031)
    direct_vm.sender = direct_alice
    aid = deployed.create_agreement(str(direct_bob), "svc", REQUIREMENTS_JSON, ESCROW, *WINDOWS)
    assert deployed.get_agreement(aid)["created_at"] == in_2031
    warp_to(direct_vm, in_2031 + 17)
    direct_vm.value = ESCROW
    deployed.fund_agreement(aid)
    direct_vm.value = 0
    a = deployed.get_agreement(aid)
    assert a["funded_at"] == in_2031 + 17
    assert a["acceptance_deadline"] == in_2031 + 17 + ACCEPTANCE_WINDOW


def test_13_same_transaction_datetime_same_decision(
    direct_vm, deployed, direct_alice, accepted
):
    """Re-executing at the same datetime reaches the same decision, on both
    sides of the boundary, byte for byte."""
    deadline = deployed.get_agreement(accepted)["appeal_deadline"]
    direct_vm.sender = direct_alice

    outcomes = []
    for _ in range(2):
        snap = direct_vm.snapshot()
        warp_to(direct_vm, deadline)
        try:
            deployed.finalize(accepted)
            early = "finalized"
        except Exception as e:
            early = str(e)
        warp_to(direct_vm, deadline + 1)
        deployed.finalize(accepted)
        outcomes.append((early, deployed.get_agreement(accepted)["finalized_at"]))
        direct_vm.revert(snap)

    assert outcomes[0] == outcomes[1]
    assert "appeal window open until" in outcomes[0][0]
    assert outcomes[0][1] == deadline + 1


def test_13_consensus_does_not_depend_on_time(
    direct_vm, deployed, direct_alice, accepted
):
    """Time is read deterministically OUTSIDE the nondeterministic block. A
    validator replaying the adjudication round a day later agrees with the
    leader: nothing inside the round reads the clock."""
    captured = direct_vm._captured_validators
    index = max(i for i, c in enumerate(captured)
                if isinstance(c[0], dict) and "verification" in c[0])
    mock_panel(direct_vm, make_verdict(accepted, PARTIAL,
                                       evidence_examined=ids(deployed, accepted)))
    assert direct_vm.run_validator(index=index) is True
    warp_to(direct_vm, T0 + 24 * 3600 + 1)
    assert direct_vm.run_validator(index=index) is True


# ═══ §22 — the lifecycle, before and after each deadline ════════════════════

def test_end_to_end_lifecycle_before_and_after_deadlines(
    direct_vm, deployed, direct_alice, direct_bob, direct_charlie
):
    direct_vm.sender = direct_alice
    aid = deployed.create_agreement(str(direct_bob), "Deliver the Q3 dataset.",
                                    REQUIREMENTS_JSON, ESCROW, *WINDOWS, "", "", 0,
                                    APPEAL_WINDOW)
    direct_vm.value = ESCROW
    deployed.fund_agreement(aid)
    direct_vm.value = 0

    warp_to(direct_vm, T0 + 1800)                       # half-way through acceptance
    direct_vm.sender = direct_bob
    deployed.accept_agreement(aid)
    a = deployed.get_agreement(aid)
    assert a["accepted_at"] == T0 + 1800
    assert a["service_deadline"] == T0 + 1800 + SERVICE_WINDOW

    warp_to(direct_vm, T0 + 3 * 24 * 3600)              # day 3: deliver
    commit_canonical_evidence(deployed, aid)
    deployed.submit_deliverable(aid, "delivered")
    assert deployed.get_agreement(aid)["delivered_late"] is False

    warp_to(direct_vm, T0 + 4 * 24 * 3600)              # day 4: adjudicate
    adjudicate(direct_vm, deployed, direct_alice, aid, PARTIAL)
    a = deployed.get_agreement(aid)
    assert a["status"] == "ACCEPTED"
    assert a["appeal_deadline"] == T0 + 4 * 24 * 3600 + APPEAL_WINDOW

    # BEFORE the appeal deadline — from anyone — nothing finalizes or pays
    warp_to(direct_vm, a["appeal_deadline"])
    for sender in (direct_alice, direct_bob, direct_charlie):
        direct_vm.sender = sender
        with direct_vm.expect_revert():
            deployed.finalize(aid)
        with direct_vm.expect_revert():
            deployed.settle(aid)
    assert deployed.get_agreement(aid)["escrow_available"] == ESCROW

    # AFTER it — the legitimate path runs
    pass_deadline(direct_vm, deployed, aid, "appeal_deadline")
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("not a party"):
        deployed.finalize(aid)
    direct_vm.sender = direct_alice
    deployed.finalize(aid)
    deployed.settle(aid)
    a = deployed.get_agreement(aid)
    s = deployed.get_settlement(aid)
    assert a["status"] == "SETTLED" and a["escrow_available"] == 0
    assert s["provider_payout"] == 70 * 10 ** 18 and s["client_refund"] == 30 * 10 ** 18
    assert s["settled_at"] == a["appeal_deadline"] + 1
