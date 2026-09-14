# Settlement

## The formula

```
escrow          = escrow_deposited − escrow_released      (real custody)
earned_weight   = Σ weight[r] for every requirement the panel marked PASS
provider_gross  = escrow × earned_weight ÷ 100            (integer floor)
penalty         = provider_gross × penalty_bps ÷ 10000    only if delivered_at > service_deadline
provider_net    = max(0, provider_gross − penalty)
client_refund   = escrow − provider_net
```

Every input is either a committed term or the real escrow balance. None
comes from model output.

## Worked example — the canonical scenario

```
escrow          100 GEN
R1  weight 40   PASS
R2  weight 30   PASS
R3  weight 30   FAIL
penalty_bps      0

earned_weight   = 40 + 30          = 70
provider_gross  = 100 × 70 ÷ 100   = 70 GEN
penalty         = 0
provider_net    = 70 GEN
client_refund   = 100 − 70         = 30 GEN
```

Provider receives **70 GEN**, client receives **30 GEN**, escrow reaches
**0**, status becomes **SETTLED**.

## Where earned_weight comes from

Not from the model. `request_adjudication` computes it after consensus:

```python
weights = {r["requirement_id"]: int(r["weight"]) for r in committed_requirements}
earned = 0
for r in norm["requirements"]:          # the panel's per-requirement statuses
    if r["status"] == "PASS":
        earned += weights[r["requirement_id"]]
```

The panel supplies **facts** (which requirements passed). The contract
supplies **weights** (from the frozen terms) and does the addition. A
verdict carrying `earned_weight: 100` or `provider_payout: 999999999999`
changes nothing — `_normalize_verdict` returns a fixed key set and those
fields are dropped before storage.

## Integer arithmetic only

No float touches a weight or an amount, anywhere. Weights are integers
validated to sum to exactly 100. Amounts are `u256` atto values. All
division is Python floor division on integers.

### Rounding

`escrow × earned ÷ 100` floors, so a remainder of up to 99 atto can
arise. It goes to the **client**, because `client_refund` is computed by
subtraction:

```python
client_refund = escrow - provider_net
```

That direction is deliberate. The party owed a refund should never be
short by a rounding artefact, and the provider cannot gain from one. The
identity holds exactly, always:

```
provider_net + client_refund == escrow
```

`test_rounding_remainder_goes_to_client` uses a deliberately indivisible
escrow (1000000000000000007 atto) and asserts the sum is exact.

## Invariants, asserted not assumed

`_compute_settlement` raises rather than returning a bad split:

```python
if provider_net + client_refund != escrow:  raise   # must balance exactly
if provider_net > escrow or client_refund > escrow:  raise   # no overpayment
if earned < 0 or earned > 100:  raise               # weight in range
```

And `settle()` adds:

```python
if v.terms_hash != a.terms_hash:      raise   # verdict judged different terms
if v.outcome == "UNDETERMINED":       raise   # unresolved cannot pay
if escrow_before <= 0:                raise   # nothing to release
```

## Ordering — zero before transfer

```
1. read escrow ledger
2. validate state, authorization, verdict binding
3. compute the split
4. zero the ledger        escrow_released += escrow
5. persist                Settlement record written, status → SETTLED
6. ONLY THEN emit         _send_gen(provider), _send_gen(client)
```

State is committed before any value moves. A second `settle()` call
finds status `SETTLED` and is refused by `_require_state` before it can
reach step 6 — `test_E_second_settlement_fails` asserts escrow stays 0
after the failed retry.

## Partial performance

Weighted requirements are what make partial performance expressible.
With `R1=50, R2=30, R3=20`:

| Statuses | earned | provider | client |
|---|---|---|---|
| all PASS | 100 | 100% | 0% |
| R1,R2 PASS · R3 FAIL | 80 | 80% | 20% |
| R1 PASS · rest FAIL | 50 | 50% | 50% |
| all FAIL | 0 | 0% | 100% |

The contract does not special-case any of these; they all fall out of
the same formula.

## Penalties

Optional, `penalty_bps`, locked with the terms at funding. Applied
deterministically and **only when delivery was late** — when the
provider's `submit_deliverable` transaction datetime is after the
service deadline the contract derived from the acceptance transaction:

```
penalty = provider_gross × penalty_bps ÷ 10000
```

Both the fact and the consequence are the contract's. Whether delivery
was late is two datetimes it recorded itself, not a question for the
panel; the rate was agreed before the work started. The panel's
`deadline_met` stays on the verdict as its reading of the evidence and
moves no amount. `test_penalty_applies_when_delivery_transaction_was_late`
and `test_no_penalty_for_on_time_delivery_whatever_the_panel_says` pin both
directions.

## The three exits

| Path | Legal from | Recipient |
|---|---|---|
| `settle` | FINALIZED, verdict not UNDETERMINED | provider + client, per weights |
| `cancel_agreement` | DRAFT, FUNDED | client (full) |
| `recover_escrow` | EXPIRED, UNDETERMINED, APPEALED — transaction datetime past the resolution deadline (acceptance deadline if never accepted) | client (full) |

All three route through `_send_gen`, the single audited emission
channel, and all three follow the same zero-before-transfer ordering.

## Finality is required

`settle()` accepts **only** `FINALIZED`. `ACCEPTED` is not enough:

```
request_adjudication  →  ACCEPTED     (appeal window open, can_settle false)
   ⟨real time passes⟩ →   (appeal_deadline = verdict datetime + window)
      finalize()      →  FINALIZED    (can_settle true)
      settle()        →  SETTLED
```

`test_F_accepted_cannot_settle` and `test_FINALITY_accepted_is_not_finalized`
both assert the refusal and that escrow is untouched by the attempt.

## UNDETERMINED never pays

An `UNDETERMINED` verdict parks the agreement in the `UNDETERMINED`
state. From there:

- `settle()` — illegal
- `finalize()` — illegal
- `appeal()` — illegal (there is no decided verdict to contest)
- `request_adjudication()` — **legal**, after more evidence
- `recover_escrow()` — legal once a transaction's datetime is past the
  resolution deadline

Escrow stays whole throughout. `test_G_undetermined_cannot_settle` and
`test_UNDETERMINED_protects_escrow_then_retries` cover both the refusal
and the retry path that resolves it.
