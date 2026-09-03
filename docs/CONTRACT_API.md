# Contract API

26 public methods: 16 writes, 10 views.

---

## Writes

### `create_agreement(provider, service_description, requirements_json, payment_amount_atto, acceptance_deadline_ticks, service_deadline_ticks, resolution_deadline_ticks, evidence_rules="", settlement_rules="", penalty_bps=0, appeal_window_ticks=3) -> str`

**Purpose** Open a DRAFT agreement and return its id.
**Caller** Anyone; becomes the client.
**State** — (creates new).
**Authorization** None.
**Side effects** Writes the agreement, appends to the registry, computes the initial terms hash.
**Returns** `"SLA-000001"` style id.
**Fails when** provider address invalid; provider == client; empty description; requirements not valid JSON / not a list / empty / over 32 items; any requirement missing an id or description; duplicate id; weight outside 1..100; weights not summing to exactly 100; payment ≤ 0; penalty_bps outside 0..10000; deadlines not satisfying `0 < acceptance < service < resolution`; negative appeal window.

### `update_terms(agreement_id, service_description, requirements_json, evidence_rules, settlement_rules) -> str`

**Purpose** Amend a DRAFT agreement before money is involved.
**Caller** Client.
**State** DRAFT, and `terms_locked` false.
**Side effects** Rewrites terms and recomputes the hash.
**Returns** The new terms hash.
**Fails when** not the client; not DRAFT; terms locked; requirement validation fails.

### `fund_agreement(agreement_id)` — payable

**Purpose** Deposit the exact payment amount; lock the terms.
**Caller** Client.
**State** DRAFT → FUNDED.
**Side effects** Records `gl.message.value` as escrow, sets `terms_locked`, recomputes the hash.
**Fails when** not the client; not DRAFT; `value` ≤ 0; `value` ≠ `payment_amount_atto`.

### `accept_agreement(agreement_id)`

**Purpose** Provider takes the job.
**Caller** **Designated provider only.**
**State** FUNDED → ACTIVE.
**Fails when** caller is not the designated provider; not FUNDED; terms hash drifted; past the acceptance deadline.

### `submit_evidence(agreement_id, requirement_id, evidence_type, source_reference, content_hash, description="") -> str`

**Purpose** Commit an evidence record against one requirement.
**Caller** Either party.
**State** ACTIVE, SUBMITTED, ACCEPTED, APPEALED or UNDETERMINED.
**Side effects** Appends the record; writes the owner and index entries.
**Returns** `"SLA-000001-E0001"` style id.
**Fails when** not a party; wrong state; unknown `requirement_id`; unsupported `evidence_type`; empty `content_hash`; evidence limit (128) reached.

### `supersede_evidence(agreement_id, evidence_id, source_reference, content_hash, description="") -> str`

**Purpose** Explicitly replace an earlier record with a new one.
**Caller** The **original submitter** only.
**Side effects** Marks the old record SUPERSEDED (hash untouched); appends a new record with `version + 1`.
**Returns** The new evidence id.
**Fails when** unknown evidence; evidence belongs to another agreement; caller is not the original submitter; already superseded; empty hash.

### `challenge_evidence(agreement_id, evidence_id, reason)`

**Purpose** Flag a record as contested without deleting it.
**Caller** Either party.
**Side effects** Sets status CHALLENGED, appends a note to the description.
**Fails when** unknown evidence; cross-agreement; empty reason.

### `submit_deliverable(agreement_id, note="")`

**Purpose** Provider declares delivery. **This is a claim, not evidence** — it advances the state machine and nothing else.
**Caller** Provider.
**State** ACTIVE → SUBMITTED.
**Fails when** not the provider; not ACTIVE; terms drifted; **no evidence attached yet**.

### `request_adjudication(agreement_id) -> int`

**Purpose** Run a GenLayer adjudication round. The core nondeterministic operation.
**Caller** Either party.
**State** SUBMITTED, APPEALED or UNDETERMINED → ADJUDICATING → ACCEPTED or UNDETERMINED.
**Side effects** Runs `gl.vm.run_nondet_unsafe`; on success writes a new Verdict (with contract-derived `earned_weight`), increments the round, and either opens an appeal window (ACCEPTED) or parks the agreement (UNDETERMINED).
**Returns** The new verdict id.
**Fails when** not a party; wrong state; terms drifted; round limit (5) reached; no evidence; the panel's verdict fails normalisation; the verdict cites evidence from another agreement. **On any failure the prior state is restored.**

### `appeal(agreement_id, reason)`

**Purpose** Contest an accepted verdict inside the appeal window.
**Caller** Either party.
**State** ACCEPTED → APPEALED.
**Fails when** not a party; not ACCEPTED; empty reason; window already closed.

### `finalize(agreement_id)`

**Purpose** Close the appeal window: ACCEPTED → FINALIZED. **The finality gate.**
**Caller** Either party.
**State** ACCEPTED → FINALIZED.
**Fails when** not a party; not ACCEPTED; terms drifted; `current_tick < appeal_deadline_tick`.

### `settle(agreement_id)`

**Purpose** Release escrow per the finalized verdict.
**Caller** Either party.
**State** FINALIZED → SETTLED.
**Side effects** Computes the split, zeroes the ledger, writes a Settlement, **then** emits transfers.
**Fails when** not a party; **not FINALIZED**; terms drifted; no verdict; verdict's terms hash ≠ agreement's; verdict outcome UNDETERMINED; escrow ≤ 0; arithmetic fails to balance.

### `cancel_agreement(agreement_id)`

**Purpose** Abandon before the provider accepts; refund the client.
**Caller** Client.
**State** DRAFT or FUNDED → CANCELLED.

### `expire_agreement(agreement_id)`

**Purpose** Mark an undelivered agreement expired.
**Caller** Either party.
**State** FUNDED or ACTIVE → EXPIRED.
**Fails when** the service deadline has not passed.

### `recover_escrow(agreement_id)`

**Purpose** Escape hatch — refund the client from a terminally stuck agreement.
**Caller** Either party.
**State** EXPIRED, UNDETERMINED or APPEALED → REFUNDED.
**Fails when** wrong state; resolution deadline not reached; no escrow.

### `tick() -> int`

**Purpose** Advance the protocol clock by one.
**Caller** Anyone.
**Returns** The new tick.

---

## Views

| Method | Returns |
|---|---|
| `get_protocol_info()` | version, agreement count, current tick, `weight_total`, default appeal window, max rounds, the evidence-type and authoritative-type vocabularies |
| `get_agreement(id)` | full agreement record including terms hash, lock flag, escrow triple, all deadlines, status, round, latest verdict id |
| `get_requirements(id)` | the committed requirement array |
| `get_evidence(id)` | all evidence records with a derived `authoritative` flag |
| `list_verdicts(id)` | verdict ids, oldest first |
| `get_verdict(id, verdict_id)` | outcome, per-requirement statuses, `deadline_met`, examined evidence, reasoning, contract-derived `earned_weight`, terms hash, raw panel JSON |
| `get_escrow(id)` | payment amount, deposited, released, available, `held` flag |
| `get_settlement(id)` | payout, refund, penalty, escrow before/after, earned/total weight, status, tick |
| `get_state(id)` | compact lifecycle probe including `can_settle` — what a keeper or UI polls |
| `list_agreements(offset, limit)` | paged registry |

All views are read-only and callable by anyone.

---

## Typical sequence

```
create_agreement          client
fund_agreement            client       (payable, exact amount; terms lock)
accept_agreement          provider     (designated wallet only)
submit_evidence × N       provider
submit_deliverable        provider
request_adjudication      either       → ACCEPTED (or UNDETERMINED)
  ⟨appeal window⟩
finalize                  either       → FINALIZED
settle                    either       → SETTLED
```
