# AgentSLA Core

> A reusable GenLayer Intelligent Contract primitive for evidence-based
> service agreements: lock an agreement and real GEN on chain, commit
> verifiable evidence, let validator consensus decide whether the
> requirements were met, wait for finality, and settle deterministically.

The contract is the artifact. There is no frontend, and the project
loses nothing without one.

---

## The problem

A deterministic contract enforces this perfectly:

```python
if timestamp > deadline:
    transfer()
```

It cannot establish this at all:

> Did the provider actually satisfy this natural-language requirement,
> given the evidence submitted?

That question decides most real agreements — agent-to-agent work, API
SLAs, data delivery, bounties, milestone work, procurement. Today it is
answered by a human, a platform, or nobody.

## The design

AgentSLA Core splits the two jobs and gives each to the layer that can
actually do it.

```
      GenLayer validator consensus              Deterministic contract code
      decides FACTS                             decides MONEY

      per-requirement PASS / FAIL /             earned_weight × escrow ÷ 100
        UNDETERMINED                            penalty if deadline missed
      deadline_met                              client_refund = escrow − payout
      which evidence was examined               escrow zeroing, the transfer
```

The adjudicating model is **never allowed to name an amount**. It
returns a structured verdict over the committed requirement set; the
contract multiplies the earned weight by the real escrow balance. A
verdict carrying `provider_payout: 999999999999` changes nothing,
because no code path reads such a field. That is not a policy — it is a
property of `_normalize_verdict`, which returns a fixed key set.

## Why GenLayer

Everything except the judgment could run on any chain. The judgment
cannot: it needs a model to read unstructured evidence, and it needs
more than one independent party to agree on what that evidence shows.

GenLayer gives both. `request_adjudication` runs
`gl.vm.run_nondet_unsafe`, where the leader **and every validator**
independently evaluate the same committed prompt and compare decision
fingerprints. Agreement means several nodes reading the same evidence
reached the same factual conclusions — not that one node's JSON parsed.

## Lifecycle

```
DRAFT ──fund──► FUNDED ──accept──► ACTIVE ──deliver──► SUBMITTED
                                                            │
                                              request_adjudication
                                                            │
                                        ┌───────────────────┴──────────────┐
                                        ▼                                  ▼
                                  UNDETERMINED                        ACCEPTED
                                  escrow frozen                    appeal window
                                        │                                  │
                     more evidence ─────┤                    ┌─────────────┴────────┐
                     re-adjudicate      │                 appeal                finalize
                                        │                    │                     │
                     recover_escrow ────┘                 APPEALED            FINALIZED
                             │                               │                     │
                             ▼                    re-adjudicate                 settle
                         REFUNDED                                                  │
                                                                                   ▼
                                                                               SETTLED
```

`ACCEPTED` is **not** `FINALIZED`. A verdict accepted by consensus is
not yet spendable; it becomes spendable only after the window in which a
party could contest it has actually elapsed. Appealing moves the
agreement out of `ACCEPTED` entirely, so a stale accepted verdict can
never trigger settlement.

## Evidence

Four things this contract keeps apart:

```
CLAIM                 "I completed the work."   — submit_deliverable()
EVIDENCE              a dataset, a commit, an API response
EVIDENCE COMMITMENT   the on-chain record: id, requirement binding,
                      content hash, submitter, tick, version, status
AUTHORITATIVE SOURCE  evidence a third party can independently re-derive
```

Content hashes are written once and never mutated. Replacement is
additive: `supersede_evidence` marks the original `SUPERSEDED` — hash
intact, still readable — and appends a new record with an incremented
version. There is no `update_evidence` or `delete_evidence` anywhere;
a test asserts their absence from the deployed schema.

Types in `AUTHORITATIVE_TYPES` (commits, chain transactions, API
results, signed messages, datasets, URLs) surface an `authoritative:
true` flag to the panel, which is told to weight them above prose. See
[docs/EVIDENCE.md](docs/EVIDENCE.md).

## Consensus

The consensus-critical projection is:

```
agreement_id · outcome · per-requirement statuses ·
deadline_met · evidence_examined
```

`reasoning` is deliberately excluded. Two honest validators reading
identical evidence reach the same verdict and do **not** write the same
paragraph; demanding identical prose would make consensus fail for a
reason unrelated to correctness. The reasoning is stored verbatim for
audit — it simply does not participate in agreement.

Requirement results and evidence ids are sorted and deduplicated before
comparison, so ordering never breaks a round. Only substance does. See
[docs/CONSENSUS.md](docs/CONSENSUS.md).

## Settlement

```
earned_weight   = Σ weight[r] for every requirement marked PASS
provider_gross  = escrow × earned_weight ÷ 100        (integer floor)
penalty         = provider_gross × penalty_bps ÷ 10000  only if deadline missed
provider_net    = max(0, provider_gross − penalty)
client_refund   = escrow − provider_net
```

Integer arithmetic throughout; no float touches a weight or an amount.
Rounding remainders go to the **client**, because `client_refund` is
computed by subtraction — the party owed a refund is never short by a
rounding artefact, and the provider cannot gain from one.

Worked: 100 GEN escrow, R1 (40) PASS, R2 (30) PASS, R3 (30) FAIL →
provider **70 GEN**, client **30 GEN**, escrow **0**.
See [docs/SETTLEMENT.md](docs/SETTLEMENT.md).

## Escrow

Real custody, not an accounting variable. `fund_agreement` is
`@gl.public.write.payable` and records `gl.message.value` — the figure
the chain actually moved, never a caller-supplied argument. Under- and
over-funding are both refused.

All value leaves through one audited helper, `_send_gen`, on three
paths only: `settle`, `cancel_agreement`, `recover_escrow`. Every one
follows the same order:

```
read ledger → validate → compute → zero ledger → persist → emit transfer
```

## Reuse

Nothing in the contract knows about any industry. A consumer supplies
the requirements, weights, deadlines and rules; the contract hashes them
into a commitment and adjudicates against that frozen version.

**Agent work**
```json
[{"requirement_id":"R1","description":"Produce the requested analysis as a written report","weight":60},
 {"requirement_id":"R2","description":"Cite at least five primary sources","weight":40}]
```

**API SLA**
```json
[{"requirement_id":"R1","description":"p99 latency stayed under 250ms for the billing period","weight":50},
 {"requirement_id":"R2","description":"Uptime was at least 99.9%","weight":50}]
```

**Data delivery**
```json
[{"requirement_id":"R1","description":"Deliver the requested dataset","weight":40},
 {"requirement_id":"R2","description":"Dataset meets the agreed quality bar","weight":30},
 {"requirement_id":"R3","description":"Delivery before the deadline","weight":30}]
```

Same contract, no modification. Weights must be integers summing to
exactly 100.

## Quick start

```bash
pip install -r requirements.txt

genvm-lint check contracts/agentsla_core.py     # lint
pytest tests/direct/ -v                         # 102 tests, ~20s
```

Deploy:

```bash
genlayer network set studionet
genlayer deploy --contract contracts/agentsla_core.py
genlayer schema <address>
```

Drive a full lifecycle against a deployment:

```bash
AGENTSLA_CLIENT_KEY=0x… AGENTSLA_PROVIDER_KEY=0x… \
  node scripts/drive_e2e.mjs <address>

  node scripts/drive_e2e.mjs <address> --undetermined   # the UNDETERMINED path
```

## Complete example

```python
# client creates
aid = create_agreement(
    provider          = "0xA012…0995",
    service_description = "Deliver a cleaned Q3 2026 market dataset with a quality report.",
    requirements_json = json.dumps([
        {"requirement_id": "R1", "description": "Deliver the requested dataset",        "weight": 40},
        {"requirement_id": "R2", "description": "Dataset meets the agreed quality bar", "weight": 30},
        {"requirement_id": "R3", "description": "Delivery before the deadline",         "weight": 30},
    ]),
    payment_amount_atto        = 100 * 10**18,
    acceptance_deadline_ticks  = 10,
    service_deadline_ticks     = 30,
    resolution_deadline_ticks  = 200,
)

fund_agreement(aid)            # client, payable, exactly 100 GEN — terms LOCK here
accept_agreement(aid)          # provider — designated wallet only

submit_evidence(aid, "R1", "DATASET",       "https://…parquet", "sha256:8f14…")
submit_evidence(aid, "R2", "API_RESULT",    "https://…/reports/8821", "sha256:c4ca…")
submit_evidence(aid, "R3", "GITHUB_COMMIT", "https://…/commit/9fe1c2b", "sha256:e3b0…")
submit_deliverable(aid)        # a CLAIM — advances state, proves nothing

request_adjudication(aid)      # LIVE panel → PARTIAL, R3 FAIL, earned_weight 70
                               # → ACCEPTED, appeal window opens

settle(aid)                    # REFUSED — ACCEPTED is not FINALIZED
tick(); tick(); tick(); tick()
finalize(aid)                  # → FINALIZED
settle(aid)                    # provider 70 GEN, client 30 GEN, escrow 0, SETTLED
```

## Proven live on StudioNet

The canonical scenario, driven end to end against a real deployment with
a real validator panel. No mocks anywhere in this run.

- Contract: [`0x3Ff03C3313889C1e6B147F5cC6D4E7102e663a29`](https://genlayer-explorer.vercel.app/address/0x3Ff03C3313889C1e6B147F5cC6D4E7102e663a29)
- Deploy tx: `0xd84117340809563241783a3990b9d93612d86c6e76ee430cf047fc473845718d`
- Runner: `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`
- Reproduce: `node scripts/drive_e2e.mjs 0x3Ff03C3313889C1e6B147F5cC6D4E7102e663a29`

| Step | Tx |
|---|---|
| `create_agreement` | `0x0eb7c27950f096841b8f70c501fee0834d36feb3f5dee70ff87cee1c3af13b9b` |
| `fund_agreement` (0.1 GEN, terms lock) | `0x99109df258e07dde70db20eda689bdffb768b1783f1d14588ac410750ceb2e3d` |
| `accept_agreement` (designated provider) | `0x5501588c23413c14d35150836cf7ebbdb7c4a8dcce98172914848001b2b60f6d` |
| `submit_evidence` ×3 | `0xddb7572813…`, `0xcce506fdc8…`, `0xb0c940c0f3…` |
| `submit_deliverable` | `0x012517770a4b24a6b3fcc299d8063c0eab8e0088e7d90c271cd19719de8eb622` |
| **`request_adjudication`** — live panel | `0x3dc2d5ba643896183adf9ddeb51e468825327ba9e37adcc702f8b6de71a99307` |
| `settle` **while ACCEPTED** → REVERTED | `0x892217d92c3afa6a5ea00206ddc84383d12ebb3ae3f9f26011f47b66f51e0414` |
| `finalize` | `0xb7a34f095c2787bb0091458c159bd57a468570085f9720f38057c055ba45c381` |
| `settle` | `0x232b6301931563f3d48504ff53c8ff1db7ce22175978746b59cf518344c5a43c` |

The panel returned exactly the scenario's expected verdict:

```
outcome        PARTIAL
R1  PASS       R2  PASS       R3  FAIL
deadline_met   false
examined       [E0001, E0002, E0003]
earned_weight  70/100          ← derived by the CONTRACT, not the model
```

Its own reasoning, recorded on chain:

> *"R1 PASS: authoritative dataset evidence SLA-000001-E0001 provides a
> downloadable file reference and content hash, matching the requirement.
> R2 PASS: authoritative API result SLA-000001-E0002 states field
> completeness is …"*

Note it reached for the `authoritative` flag unprompted — that is rule 3
of the adjudication prompt doing its job.

**The finality gate fired.** `settle()` was called while the agreement
was merely `ACCEPTED` and the transaction **reverted**; only after four
ticks and `finalize()` did it succeed.

Final on-chain state (`get_settlement`):

```
status            SETTLED
verdict_id        1            outcome PARTIAL
earned_weight     70 / 100
escrow_before     100 000 000 000 000 000 atto   (0.1 GEN)
provider_payout    70 000 000 000 000 000 atto   (70%)
client_refund      30 000 000 000 000 000 atto   (30%)
penalty                                      0
escrow_after                                 0
```

`70000000000000000 + 30000000000000000 = 100000000000000000` — balances
exactly.

> One earlier round on a prior deployment returned `MAJORITY_DISAGREE`.
> The cause was `evidence_examined`: which records count as "examined"
> is a judgement call, so validators cited different subsets and the
> fingerprint comparison failed for a reason unrelated to the verdict.
> The fix was to make the field **mechanical** rather than to loosen the
> consensus rule — the prompt now instructs the panel to list every
> evidence id it was given, and enumerates them explicitly. The field
> stays consensus-critical; it just stopped being a matter of opinion.

## Documentation

| | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | layers, state machine, storage shape, where value can leave |
| [CONSENSUS.md](docs/CONSENSUS.md) | the nondet operation, decision fingerprint, why prose is excluded |
| [EVIDENCE.md](docs/EVIDENCE.md) | claim vs evidence vs commitment vs authoritative source |
| [SETTLEMENT.md](docs/SETTLEMENT.md) | the formula, rounding, invariants, the three exits |
| [SECURITY.md](docs/SECURITY.md) | attacks A–I, each mapped to its defence and test |
| [CONTRACT_API.md](docs/CONTRACT_API.md) | every method: purpose, caller, state, failures |
| [TESTING.md](docs/TESTING.md) | the five layers and how to run them |

## Testing

```
genvm-lint check     passes — 26 methods (10 view, 16 write)
pytest tests/direct  102 passed
```

Five layers — state, escrow, evidence, adjudication, equivalence — plus
adversarial attacks A through I and the three required scenarios (E2E,
UNDETERMINED, finality). Every adversarial test asserts that **money did
not move**, not merely that a status changed.

## Known limitations

- **The clock is a tick counter, not a wall clock.**
  `gl.message.datetime` is not populated in every runtime this contract
  must work in, and a deadline that silently reads zero is worse than
  one that is explicitly abstract. Deadlines are absolute tick values;
  `tick()` is public so any account can age one forward. Consequence:
  deadlines advance with protocol activity rather than elapsed time.
- **Direct mode runs the leader only.** Validator agreement is proven in
  `test_equivalence.py`, which drives the normaliser and fingerprint
  directly — those functions *are* the equivalence rule — and on a live
  network with a real panel.
- **Panel capture is out of scope.** A compromised validator majority
  can agree on a false verdict; that is GenLayer's trust model. The
  appeal path exists so a bad round can be contested and re-run.
- **Evidence quality is not verifiable on chain.** The contract commits
  to a hash; it cannot check that a URL still serves those bytes.
  `AUTHORITATIVE_TYPES` and the prompt push the panel toward
  independently checkable artefacts, but a determined liar can submit a
  plausible fake.
- **No frontend.** Deliberate — the primitive is the deliverable.
