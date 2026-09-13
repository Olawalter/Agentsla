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
| I | Cross-agreement evidence | `_resolve_evidence` enforces the binding on every access; a round acquires only the agreement's own records; after consensus, every cited evidence id is checked against this agreement's set. | `test_I_evidence_from_another_agreement_rejected`, `test_I_verdict_citing_foreign_evidence_rejected`, `test_E_evidence_of_another_agreement_is_never_acquired_or_usable` |

## Evidence trust

The steward's rejection: adjudication never retrieved the committed
evidence, so validators judged descriptions, reference strings and
unverified hashes. The defences, each on the leader and on every
validator (see [EVIDENCE.md](EVIDENCE.md), [CONSENSUS.md](CONSENSUS.md)):

| Attack | Defence | Test |
|---|---|---|
| **Fake description** — "requirement completed" over an artifact that shows nothing of the kind | The description reaches the model only as `submitted_claim_UNTRUSTED`; the model reads the retrieved artifact. A requirement with no verified record is UNDETERMINED in code whatever the model says. | `test_A_description_reaches_the_model_only_as_an_untrusted_claim`, `test_A_description_without_a_verified_artifact_cannot_pass` |
| **Fake hash** — an identity that does not belong to what the reference serves | Every node fetches the reference and compares the identity of the bytes it received with the commitment, in `_verify_artifact`. Mismatch → `HASH_MISMATCH`, no artifact shown, requirement UNDETERMINED. | `test_B_wrong_hash_is_a_mismatch` |
| **Source substitution** — the source changes after commitment, or between rounds | Same comparison, performed afresh in every round; nothing verified earlier is reused. | `test_C_modified_artifact_is_a_mismatch`, `test_C_modified_commit_identity_is_a_mismatch`, `test_H_artifact_that_changes_after_a_verdict_is_reacquired_and_fails` |
| **Leader-only verification** — a leader reports PASS and "verified" | Validators fetch, verify and judge for themselves and compare fingerprints that include per-record `verified`. They never read the leader's verification rows. | `test_F_leader_claiming_pass_and_verified_is_refused`, `test_F_leader_verification_rows_are_never_read_by_the_validator`, `test_G_validator_disagrees_when_its_own_retrieval_differs`, `test_G_validator_refuses_a_verification_it_cannot_reproduce` |
| **Unavailable evidence becoming PASS** | 404 / 403 / 5xx / no response → `SOURCE_UNAVAILABLE`; empty, oversized or unparseable → `INVALID_ARTIFACT`; neither can decide a requirement. | `test_D_source_unavailable_never_passes`, `test_D_no_response_at_all_is_unavailable`, `test_invalid_artifacts_are_rejected` |
| **Unsupported evidence standing in for proof** | `BLOCKCHAIN_TX`, `SIGNED_MESSAGE`, `OTHER` are `UNSUPPORTED`: never fetched, never shown with content, never decide a requirement. | `test_I_unsupported_type_is_marked_and_capped` |
| **Nothing verifiable, settled anyway** | No verified record → no model call, every requirement UNDETERMINED, `settle` and `finalize` refused, escrow whole. | `test_J_nothing_verifiable_is_undetermined_without_consulting_a_model` |
| **Frontend or backend says "verified"** | There is no method, argument or storage field through which verification can be supplied. Verification exists only as the output of a node's own fetch inside the round. | by construction — `_verify_artifact` is the only producer of a status |
| **Silent reference change** — truncation or edit | References over 512 characters are refused, not clipped; no method mutates a stored reference or identity. | `test_long_reference_is_refused_not_truncated`, `test_C_committed_evidence_cannot_be_edited` |
| **Premature settlement** | Unchanged: only `FINALIZED` settles; UNDETERMINED cannot finalize. | `test_F_accepted_cannot_settle`, `test_G_undetermined_cannot_settle` |

Each of these defences was broken on purpose in the contract to confirm
the suite catches it: removing the evidence rule, trusting the submitted
hash, a validator agreeing without comparing, a validator that does not
fetch, dropping verification from the fingerprint, showing a mismatched
artifact to the model, treating an HTTP error page as content, consulting
the model with nothing verified, accepting a malformed identity,
acquiring superseded records, ignoring the JSON pointer, truncating a long
reference — 12 of 12 caught.

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
- **Authorship and truth of a verified artifact.** Verification proves the
  source served exactly the committed bytes. It does not prove who wrote
  them or that they are true: a provider can host a fabricated report and
  commit its hash, and it will verify. The panel then judges what that
  artifact shows — a self-hosted report is still a party's own document.
  A commit identity proves GitHub serves that commit; its author and date
  are unauthenticated git metadata.
- **Unstable sources.** A source that serves different bytes on every
  request cannot verify, by design. Such evidence must be committed as a
  stable artifact (for example a commit-pinned raw file), or as a JSON
  payload selected by pointer.
- **Unsupported types.** Chain transactions and signatures are recorded
  but cannot decide a requirement (see EVIDENCE.md for why).
- **Off-chain collusion.** Two parties agreeing privately to a different
  outcome than the contract computes is outside its scope.
