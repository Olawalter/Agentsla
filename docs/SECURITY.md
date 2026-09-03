# Security

Every defence below is backed by a named test. The tests assert that
**money did not move**, not merely that a status string changed.

## Threat table

| # | Attack | Defence | Test |
|---|---|---|---|
| A | Frontend claims a verdict | No method anywhere accepts a verdict as an argument. The only route to a verdict is `request_adjudication`, which runs the nondet block. | `test_A_frontend_cannot_inject_a_verdict` |
| B | Terms changed after funding | `update_terms` is legal only in `DRAFT`; funding sets `terms_locked` and moves to `FUNDED`. Every subsequent method calls `_require_terms_intact`, which recomputes the hash and compares. | `test_B_terms_cannot_change_after_funding`, `test_B_reweighting_requirements_is_impossible_post_funding` |
| C | Committed evidence replaced | No mutating method exists. `supersede_evidence` is additive: the original keeps its hash and is marked `SUPERSEDED`. | `test_C_committed_evidence_cannot_be_edited` |
| D | Unauthorized settlement | `settle` and `finalize` both call `_require_party`. | `test_D_third_party_cannot_settle`, `test_D_third_party_cannot_finalize` |
| E | Double settlement | `settle` requires `FINALIZED`; the first call sets `SETTLED`. State is committed before any transfer. | `test_E_second_settlement_fails` |
| F | Settlement before finality | `settle` accepts only `FINALIZED`. `finalize` refuses until the appeal window elapses. | `test_F_accepted_cannot_settle`, `test_F_finalize_blocked_inside_appeal_window` |
| G | Settling an UNDETERMINED verdict | The state machine has no path from `UNDETERMINED` to `FINALIZED` or `SETTLED`, and `settle` re-checks the verdict outcome. | `test_G_undetermined_cannot_settle` |
| H | Model returns a payout field | `_normalize_verdict` returns a fixed key set; invented fields are dropped. `earned_weight` is computed by the contract from committed weights. | `test_H_llm_payout_field_is_ignored`, `test_payout_fields_are_stripped` |
| I | Cross-agreement evidence | `_resolve_evidence` enforces the binding on every access; after consensus, every cited evidence id is checked against this agreement's set. | `test_I_evidence_from_another_agreement_rejected`, `test_I_verdict_citing_foreign_evidence_rejected` |

## Authorization

Identity comes from `gl.message.sender_address` — the actual transaction
caller. No method accepts a caller-supplied address as an identity
claim.

| Role | Enforced by | Methods |
|---|---|---|
| client | `_require_client` | `fund_agreement`, `update_terms`, `cancel_agreement` |
| provider | `_require_provider` | `accept_agreement`, `submit_deliverable` |
| either party | `_require_party` | `submit_evidence`, `supersede_evidence`, `challenge_evidence`, `request_adjudication`, `appeal`, `finalize`, `settle`, `expire_agreement`, `recover_escrow` |
| anyone | — | `tick`, all views |

Additionally, only the **original submitter** may supersede their own
evidence.

## Terms manipulation

The commitment covers exactly the fields a verdict may depend on:

```
agreement_id · client · provider · service_description · requirements
payment_amount · penalty_bps · all three deadlines · appeal_window
evidence_rules · settlement_rules
```

`_require_terms_intact` recomputes and compares on `accept_agreement`,
`submit_deliverable`, `submit_evidence`, `request_adjudication`,
`finalize` and `settle`. Any drift turns into a refusal rather than a
wrong payout.

`settle()` adds a second, narrower check: the verdict's own
`terms_hash` must equal the agreement's. A verdict may only settle the
terms it actually judged.

## Deadline manipulation

The **contract** decides whether a deadline has passed, never the caller.
Deadlines are absolute tick values fixed at creation:

```python
acceptance_deadline_tick = now + acceptance_deadline_ticks
```

`create_agreement` enforces `0 < acceptance < service < resolution`, so
the ordering cannot be inverted to create a favourable window. Each gate
compares the live `current_tick` against the stored deadline.

The clock is a monotonic per-write counter, not a wall clock. `gl.message.datetime`
is not populated in every runtime this contract must work in, and a
deadline that silently reads zero is worse than one that is explicitly
abstract. Consequence: deadlines advance with protocol activity, and
`tick()` is public so any account can age one forward. This is a
documented limitation, not a hidden one — see README "Known limitations".

## Escrow safety

- **Custody is real.** `fund_agreement` is `@gl.public.write.payable`;
  the recorded amount is `gl.message.value`, the figure the chain
  actually moved.
- **Terms and money are separate fields.** `payment_amount_atto` is a
  term; `escrow_deposited_atto` is the money. Settlement reads the
  latter.
- **Exact funding only.** Under- and over-funding are both refused.
- **One emission channel.** All value leaves through `_send_gen`, which
  validates recipient and positive amount.
- **Zero before transfer**, on every path.
- **Bounded rounds.** `MAX_ADJUDICATION_ROUNDS = 5` stops an adversary
  pinning escrow by re-adjudicating forever.
- **Always an exit.** `recover_escrow` refunds the client from
  `EXPIRED` / `UNDETERMINED` / `APPEALED` once the resolution deadline
  passes, so an abandoned counterparty cannot lock funds permanently.

## Malformed verdict handling

`_normalize_verdict` rejects, with `[LLM_ERROR]`, every one of:
non-object output, wrong `agreement_id`, invalid outcome, unknown
requirement, duplicate requirement, **missing** requirement, invalid
status, non-boolean `deadline_met`, non-list `evidence_examined`, empty
`reasoning`, and internally incoherent outcome/status combinations.

On rejection `request_adjudication` restores the prior state, so a bad
round never leaves the agreement stuck in `ADJUDICATING` —
`test_wrong_agreement_id_rejected` asserts the rollback.

## Finality bypass

The only route to `SETTLED` is `FINALIZED`, and the only route to
`FINALIZED` is `finalize()` after the appeal window. Appealing moves the
agreement to `APPEALED`, from which neither `settle` nor `finalize` is
legal; the way back runs through a fresh adjudication round.

`test_FINALITY_appeal_prevents_stale_settlement` appeals an accepted
100/100 PASS, re-adjudicates to 70/100 PARTIAL, and asserts the
settlement used the **second** verdict and paid 70%.

## Reentrancy

The zero-before-transfer ordering is the defence. By the time
`_send_gen` is reached, `escrow_released` already equals
`escrow_deposited` and the status is terminal, so a re-entrant call
finds nothing to release and an illegal state transition.

## What is NOT defended

Stated plainly rather than left implicit:

- **Panel capture.** If a majority of validators are compromised, they
  can agree on a false verdict. That is GenLayer's trust model, not
  something this contract can fix. The appeal path exists so a bad round
  can be contested and re-run.
- **Evidence quality.** The contract commits to a hash; it cannot verify
  that a URL still serves those bytes. `AUTHORITATIVE_TYPES` and the
  prompt's rule 3 push the panel toward independently checkable
  artefacts, but a determined liar can submit a plausible fake.
- **Off-chain collusion.** Two parties agreeing privately to a different
  outcome than the contract computes is outside its scope.
