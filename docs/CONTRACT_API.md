# Contract API

25 public methods: 15 writes, 10 views.

**Time.** Every time condition below is judged against the GenLayer
**transaction datetime** of the call (`now`, Unix seconds) — identical on
every validator, chosen by no caller. No method accepts a time, a
timestamp or a deadline as an argument, and none advances a clock.

---

## Writes

### `create_agreement(provider, service_description, requirements_json, payment_amount_atto, acceptance_window_seconds, service_window_seconds, resolution_window_seconds, evidence_rules="", settlement_rules="", penalty_bps=0, appeal_window_seconds=86400) -> str`

**Purpose** Open a DRAFT agreement and return its id.
**Caller** Anyone; becomes the client.
**State** — (creates new).
**Authorization** None.
**Side effects** Writes the agreement with `created_at = now`, appends to the registry, computes the initial terms hash. The four windows are durations (terms); no deadline exists yet.
**Returns** `"SLA-000001"` style id.
**Fails when** provider address invalid; provider == client; empty description; requirements not valid JSON / not a list / empty / over 32 items; any requirement missing an id or description; duplicate id; weight outside 1..100; weights not summing to exactly 100; payment ≤ 0; penalty_bps outside 0..10000; an acceptance, service or resolution window outside 60..31622400 seconds; an appeal window outside 600..31622400 seconds.

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
**Side effects** Records `gl.message.value` as escrow, sets `terms_locked`, recomputes the hash; `funded_at = now`, `acceptance_deadline = now + acceptance_window_seconds`.
**Fails when** not the client; not DRAFT; `value` ≤ 0; `value` ≠ `payment_amount_atto`.

### `accept_agreement(agreement_id)`

**Purpose** Provider takes the job.
**Caller** **Designated provider only.**
**State** FUNDED → ACTIVE.
**Side effects** `accepted_at = now`; `service_deadline = now + service_window_seconds`; `resolution_deadline = service_deadline + resolution_window_seconds`.
**Fails when** caller is not the designated provider; not FUNDED; terms hash drifted; `now > acceptance_deadline`.

### `submit_evidence(agreement_id, requirement_id, evidence_type, source_reference, content_hash, description="") -> str`

**Purpose** Commit an evidence record against one requirement: a pointer (`source_reference`) and the identity the artifact must have (`content_hash`). Nothing is fetched here — every node fetches and verifies it during adjudication. `description` is the submitter's claim and is shown to the panel as untrusted.
**Caller** Either party.
**State** ACTIVE, SUBMITTED, ACCEPTED, APPEALED or UNDETERMINED.
**Commitment shape** by type (see EVIDENCE.md): byte types `https://…` + `sha256:<64 hex>` of the exact bytes; `JSON`/`API_RESULT` `https://…[#/pointer]` + `sha256:<64 hex>` of the canonical JSON payload; `GITHUB_COMMIT` `https://github.com/<o>/<r>/commit/<40 hex>` + `git:<same sha>`; `BLOCKCHAIN_TX`/`SIGNED_MESSAGE`/`OTHER` any reference and identity — recorded as UNSUPPORTED.
**Side effects** Appends the record (identity stored lowercase); writes the owner and index entries.
**Returns** `"SLA-000001-E0001"` style id.
**Fails when** not a party; wrong state; unknown `requirement_id`; `evidence_type` outside the vocabulary; empty identity or over 128 characters; reference over 512 characters (refused, never truncated); for a fetchable type, a reference or identity its acquisition method could never verify; evidence limit (128) reached.

### `supersede_evidence(agreement_id, evidence_id, source_reference, content_hash, description="") -> str`

**Purpose** Explicitly replace an earlier record with a new one.
**Caller** The **original submitter** only.
**Side effects** Marks the old record SUPERSEDED (hash untouched); appends a new record with `version + 1`.
**Returns** The new evidence id.
**Fails when** unknown evidence; evidence belongs to another agreement; caller is not the original submitter; already superseded; the new reference or identity breaks the commitment rules for the record's type.

### `challenge_evidence(agreement_id, evidence_id, reason)`

**Purpose** Flag a record as contested without deleting it.
**Caller** Either party.
**Side effects** Sets status CHALLENGED, appends a note to the description.
**Fails when** unknown evidence; cross-agreement; empty reason.

### `submit_deliverable(agreement_id, note="")`

**Purpose** Provider declares delivery. **This is a claim, not evidence** — it advances the state machine and nothing else.
**Caller** Provider.
**State** ACTIVE → SUBMITTED.
**Side effects** `delivered_at = now`. Delivery after `service_deadline` is allowed and incurs the agreed penalty at settlement.
**Fails when** not the provider; not ACTIVE; terms drifted; **no evidence attached yet**.

### `request_adjudication(agreement_id) -> int`

**Purpose** Run a GenLayer adjudication round. The core nondeterministic operation.
**Caller** Either party.
**State** SUBMITTED, APPEALED or UNDETERMINED → ADJUDICATING → ACCEPTED or UNDETERMINED.
**Side effects** Runs `gl.vm.run_nondet_unsafe`, in which the leader and every validator fetch each non-superseded committed reference, verify the retrieved bytes against the committed identity, judge only verified artifacts, and apply the evidence rule (a requirement without a verified record is UNDETERMINED). If nothing verifies, no model is called. On success writes a new Verdict — contract-derived `earned_weight`, per-record verification rows, evidence commitment hash — increments the round, sets `verdict_at = now`, and either opens an appeal window — `appeal_deadline = now + appeal_window_seconds` (ACCEPTED) — or parks the agreement (UNDETERMINED).
**Returns** The new verdict id.
**Fails when** not a party; wrong state; terms drifted; round limit (5) reached; no evidence; the panel's verdict fails normalisation; the verdict cites evidence from another agreement; the verification rows do not describe exactly the committed records or a decided requirement lacks a verified record. **On any failure the prior state is restored.** A source that cannot be read is not a failure — it is a verification status.

### `appeal(agreement_id, reason)`

**Purpose** Contest an accepted verdict inside the appeal window.
**Caller** Either party.
**State** ACCEPTED → APPEALED.
**Side effects** Clears `appeal_deadline`; the next verdict opens a new window from its own datetime.
**Fails when** not a party; not ACCEPTED; empty reason; `now > appeal_deadline`.

### `finalize(agreement_id)`

**Purpose** Close the appeal window: ACCEPTED → FINALIZED. **The finality gate.**
**Caller** Either party.
**State** ACCEPTED → FINALIZED.
**Side effects** `finalized_at = now`.
**Fails when** not a party; not ACCEPTED; terms drifted; `now ≤ appeal_deadline`.

### `settle(agreement_id)`

**Purpose** Release escrow per the finalized verdict.
**Caller** Either party.
**State** FINALIZED → SETTLED.
**Side effects** Computes the split — the penalty applies iff `delivered_at > service_deadline` — zeroes the ledger, writes a Settlement, **then** emits transfers. No time condition beyond FINALIZED.
**Fails when** not a party; **not FINALIZED**; terms drifted; no verdict; verdict's terms hash ≠ agreement's; verdict outcome UNDETERMINED; escrow ≤ 0; arithmetic fails to balance.

### `cancel_agreement(agreement_id)`

**Purpose** Abandon before the provider accepts; refund the client.
**Caller** Client.
**State** DRAFT or FUNDED → CANCELLED.

### `expire_agreement(agreement_id)`

**Purpose** Mark an undelivered agreement expired.
**Caller** Either party.
**State** FUNDED or ACTIVE → EXPIRED.
**Fails when** not a party; wrong state; FUNDED and `now ≤ acceptance_deadline`; ACTIVE and `now ≤ service_deadline`.

### `recover_escrow(agreement_id)`

**Purpose** Escape hatch — refund the client from a terminally stuck agreement.
**Caller** Either party.
**State** EXPIRED, UNDETERMINED or APPEALED → REFUNDED.
**Fails when** not a party; wrong state; `now ≤ resolution_deadline` (or, for an agreement never accepted, `now ≤ acceptance_deadline`); no escrow.

There is no `tick()`: versions up to 1.1.0 had a public clock any account could advance; it was removed (see SECURITY.md, "Time and deadlines").

---

## Views

| Method | Returns |
|---|---|
| `get_protocol_info()` | version, agreement count, `time_source`, window bounds and default appeal window, `weight_total`, max rounds, the evidence-type and authoritative-type vocabularies, `acquisition_by_type`, `verification_statuses` |
| `get_agreement(id)` | full agreement record: terms hash, lock flag, escrow triple, the four windows, `created_at` `funded_at` `accepted_at` `delivered_at` `verdict_at` `finalized_at` `updated_at`, the four deadlines, `delivered_late`, status, round, latest verdict id |
| `get_requirements(id)` | the committed requirement array |
| `get_evidence(id)` | all evidence records with derived `authoritative` and `acquisition` fields |
| `list_verdicts(id)` | verdict ids, oldest first |
| `get_verdict(id, verdict_id)` | outcome, per-requirement statuses, `deadline_met`, examined evidence, reasoning, contract-derived `earned_weight`, terms hash, raw panel JSON (`{}` when no model was consulted), `evidence_verification` rows, `evidence_commitment_hash` |
| `get_escrow(id)` | payment amount, deposited, released, available, `held` flag |
| `get_settlement(id)` | payout, refund, penalty, escrow before/after, earned/total weight, status, `settled_at` |
| `get_state(id)` | compact lifecycle probe: status, escrow, round, the four deadlines, `finalized_at`, `can_settle` — what a keeper or UI polls |
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
  ⟨appeal window: real time, from the verdict's datetime⟩
finalize                  either       → FINALIZED
settle                    either       → SETTLED
```
