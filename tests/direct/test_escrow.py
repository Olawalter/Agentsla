"""TEST LAYER 2 — escrow custody, funding rules, accounting."""
from .conftest import ESCROW


def test_exact_funding_accepted(direct_vm, deployed, direct_alice, drafted):
    direct_vm.sender = direct_alice
    direct_vm.value = ESCROW
    deployed.fund_agreement(drafted)
    direct_vm.value = 0
    a = deployed.get_agreement(drafted)
    assert a["status"] == "FUNDED"
    assert a["escrow_deposited"] == ESCROW
    assert a["escrow_available"] == ESCROW
    assert a["terms_locked"] is True


def test_zero_funding_rejected(direct_vm, deployed, direct_alice, drafted):
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    with direct_vm.expect_revert("funding must be > 0"):
        deployed.fund_agreement(drafted)


def test_underfunding_rejected(direct_vm, deployed, direct_alice, drafted):
    direct_vm.sender = direct_alice
    direct_vm.value = ESCROW - 1
    with direct_vm.expect_revert("funding must equal payment amount"):
        deployed.fund_agreement(drafted)
    direct_vm.value = 0


def test_overfunding_rejected(direct_vm, deployed, direct_alice, drafted):
    direct_vm.sender = direct_alice
    direct_vm.value = ESCROW + 1
    with direct_vm.expect_revert("funding must equal payment amount"):
        deployed.fund_agreement(drafted)
    direct_vm.value = 0


def test_only_client_may_fund(direct_vm, deployed, direct_bob, drafted):
    direct_vm.sender = direct_bob
    direct_vm.value = ESCROW
    with direct_vm.expect_revert("client only"):
        deployed.fund_agreement(drafted)
    direct_vm.value = 0


def test_double_funding_rejected(direct_vm, deployed, direct_alice, funded):
    direct_vm.sender = direct_alice
    direct_vm.value = ESCROW
    with direct_vm.expect_revert("illegal transition from FUNDED"):
        deployed.fund_agreement(funded)
    direct_vm.value = 0


def test_escrow_ledger_separate_from_terms(direct_vm, deployed, funded):
    """payment_amount is a TERM; escrow_deposited is the MONEY. Settlement
    reads the latter."""
    esc = deployed.get_escrow(funded)
    assert esc["payment_amount_atto"] == ESCROW
    assert esc["deposited"] == ESCROW
    assert esc["released"] == 0
    assert esc["available"] == ESCROW
    assert esc["held"] is True


def test_cancel_refunds_client(direct_vm, deployed, direct_alice, funded):
    direct_vm.sender = direct_alice
    deployed.cancel_agreement(funded)
    a = deployed.get_agreement(funded)
    assert a["status"] == "CANCELLED"
    assert a["escrow_available"] == 0
    assert a["escrow_released"] == ESCROW


def test_cancel_after_accept_refused(direct_vm, deployed, direct_alice, active):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("illegal transition from ACTIVE"):
        deployed.cancel_agreement(active)


def test_recover_escrow_before_deadline_refused(
    direct_vm, deployed, direct_alice, active
):
    for _ in range(55):
        deployed.tick()
    direct_vm.sender = direct_alice
    deployed.expire_agreement(active)
    with direct_vm.expect_revert("resolution deadline not reached"):
        deployed.recover_escrow(active)


def test_recover_escrow_after_deadline(direct_vm, deployed, direct_alice, active):
    for _ in range(55):
        deployed.tick()
    direct_vm.sender = direct_alice
    deployed.expire_agreement(active)
    for _ in range(60):          # resolution_deadline_ticks = 100
        deployed.tick()
    deployed.recover_escrow(active)
    a = deployed.get_agreement(active)
    assert a["status"] == "REFUNDED"
    assert a["escrow_available"] == 0
