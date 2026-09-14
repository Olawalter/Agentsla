# Reply to the steward — non-manipulable protocol clock

> Please replace or constrain the publicly caller-advanceable protocol clock
> so untrusted accounts cannot manufacture elapsed time for acceptance,
> service, resolution, or appeal deadlines. Those deadlines govern expiry,
> finality, and escrow recovery, so they need a non-manipulable
> time/progression source. Submit matching corrected repository and Explorer
> source so the lifecycle and fund-safety path can be verified.

The clock was replaced, not constrained: it no longer exists. The fix is in
commit `685864f`, and the evidence is in `6486324`.

**Corrected deployment:** [`0x0a93b5b3C7C9F35c49853F12E7851345b76Ae829`](https://explorer-studio.genlayer.com/address/0x0a93b5b3C7C9F35c49853F12E7851345b76Ae829)
on StudioNet. It is byte-identical to `contracts/agentsla_core.py` at
`685864f`, and to the source the Explorer displays.

---

## 1. What was wrong

`current_tick` was one global counter, and two things advanced it:

- **Directly.** `tick()` was a public write with no caller check.
- **Incidentally.** Every state-changing call ran `_tick()`. An attacker did
  not need `tick()` at all; creating unrelated agreements aged every other
  agreement in the contract.

All four deadlines were measured in that counter, so any account could:

| Manufacture time to… | Effect |
|---|---|
| lapse the acceptance window | the provider can no longer accept |
| pass the service deadline | `expire_agreement` mid-service, then escrow recovery |
| close the appeal window | `finalize` before the losing party can appeal, then `settle` |
| pass the resolution deadline | `recover_escrow` from UNDETERMINED / APPEALED / EXPIRED |

The previous README listed this as a "known limitation". It was a
fund-safety defect.

## 2. What replaced it

There is no clock in storage. Time is the GenLayer **transaction datetime**:

```python
def _now() -> int:
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp())
```

GenVM wires the standard-library clock to the transaction's datetime, so
every validator re-executing the transaction reads the same instant. I
checked this against the pinned runner itself: its
`genlayer/_internal/msg.py` carries `datetime` on `gl.message_raw`. The
official transaction-context documentation says the same. `_now()` takes no
argument, writes nothing, and calls no web service or model. It is read only
in deterministic code, never inside the consensus round.

The windows are **terms**. Each is a duration agreed at creation, bounded
(60 s to 366 days; the appeal window at least 600 s), and frozen into
`terms_hash` at funding. Each **deadline** is derived by the contract from
the datetime of the transaction that opens its window:

```
fund_agreement        acceptance_deadline = funding tx time    + acceptance window
accept_agreement      service_deadline    = acceptance tx time + service window
                      resolution_deadline = service_deadline   + resolution window
request_adjudication  appeal_deadline     = verdict tx time    + appeal window
```

No method accepts a time, a timestamp or a deadline. `tick()` is gone. No
method rewrites a deadline except the event that owns it: an appeal clears
the appeal window, and the next verdict opens a new one from its own
datetime.

One related change. The late-delivery penalty used to depend on the
panel's `deadline_met`, which amounted to a model deciding whether time had
elapsed. Lateness is now `delivered_at > service_deadline`: two
transaction datetimes the contract recorded itself. `deadline_met` stays on
the verdict as the panel's reading of the evidence, and no amount depends
on it.

## 3. Lifecycle and fund-safety map

| Transition | Caller | Time condition (`now` = transaction datetime) | Funds |
|---|---|---|---|
| `accept_agreement` FUNDED→ACTIVE | provider | `now ≤ acceptance_deadline` | — |
| `expire_agreement` FUNDED→EXPIRED | party | `now > acceptance_deadline` | — |
| `expire_agreement` ACTIVE→EXPIRED | party | `now > service_deadline` | — |
| `appeal` ACCEPTED→APPEALED | party | `now ≤ appeal_deadline` | — |
| `finalize` ACCEPTED→FINALIZED | party | `now > appeal_deadline` | — |
| `settle` FINALIZED→SETTLED | party | none beyond FINALIZED; penalty iff `delivered_at > service_deadline` | provider / client |
| `recover_escrow` →REFUNDED | party | `now > resolution_deadline` (`acceptance_deadline` if never accepted) | client |
| `cancel_agreement` →CANCELLED | client | none; only before acceptance | client |

## 4. Tests

Transaction time is moved only by gltest's `direct_vm.warp()`. That is test
infrastructure, and the contract has no equivalent. Every premature
attempt asserts that state, escrow and finality did not move.

| # | Test (`tests/direct/test_clock.py`) | Result |
|---|---|---|
| 1 | `tick` / `advance_time` / `set_time` / `set_deadline` / `force_*` from an attacker | unavailable, nothing moved |
| 2 | far-future timestamp passed to every deadline-gated method; a timestamp passed as a window | refused |
| 3 | acceptance expiry in the deadline's last second | refused; the provider can still accept in that second |
| 4 | acceptance expiry after the deadline | late accept refused, expiry and refund succeed |
| 5 | service expiry in the last second, by either party | refused |
| 6 | service expiry after the deadline | succeeds; recovery refused until the resolution deadline, then succeeds |
| 7 | recovery / finalize / settle before the resolution deadline, from UNDETERMINED | refused, escrow whole |
| 8 | finalize / settle in the appeal deadline's last second | refused; appeal still allowed in that second |
| 9 | finalize after the deadline | succeeds, `finalized_at` = transaction time, settles 70/30; a later verdict opens a new window from its own datetime |
| 10 | two parties, an attacker and an unrelated account attempt every gated action early; the attacker then sends 25 unrelated transactions | all refused; no deadline moved |
| 11 | every time-gated fund path, from a party and an attacker, at FUNDED, ACTIVE, SUBMITTED, ACCEPTED and APPEALED | refused at every stage, escrow whole |
| 12 | every public write from both parties and an attacker | no deadline, window or terms hash changed |
| 13 | contract time on a 2026 host with a 2031 transaction; same datetime replayed; validator replay a day later | records 2031 to the second; identical decisions; validator still agrees |

Also covered: a lifecycle test before and after each deadline; windows out of
bounds; the penalty applied for a late delivery transaction even when the
panel says on time, and not applied for an on-time one even when the panel
says late.

```
genvm-lint check          passes — 25 methods (10 view, 15 write)
pytest tests/direct       155 passed
mutation check            12/12 caught
```

The 12 deliberate breakages were: `tick()` restored, host `time.time()`
instead of transaction time, finalize allowed in the last second, the
appeal / acceptance / service / resolution gates removed, the penalty decided
by the model, the appeal or service window anchored to creation, window
bounds removed, and delivery time not recorded.

## 5. Proven live on StudioNet

`tests/integration/test_end_to_end.py`: **3 passed in 24m52s**. The suite
advanced no clock. It reached each deadline by waiting for real time to
pass. Full record: `docs/live-run.json`.

**Contract time is the transaction's time.** For every lifecycle field set in
the run, the stored value equals the network's own `created_timestamp` for the
transaction that set it:

| Field | Contract | Transaction | Difference |
|---|---|---|---|
| SLA-000004 `funded_at` | 1789398926 | `0x7649757e…` | 0 s |
| SLA-000005 `funded_at` / `accepted_at` / `delivered_at` | 1789399368 / 1789399382 / 1789399440 | `0x8c309ae2…` / `0xb1f1a463…` / `0x87b5b1f6…` | 0 s |
| SLA-000005 `verdict_at` / `finalized_at` | 1789399453 / 1789400157 | `0xbc0d0ffe…` / `0xd2ec5b93…` | 0 s |
| SLA-000006 `funded_at` | 1789400240 | `0xcb5bfd80…` | 0 s |

**SLA-000004: the acceptance window, attacked and then really lapsing**
(deadline 1789399226)

| Step | Tx | Result |
|---|---|---|
| client `expire_agreement` | `0x977a0d6717f76b9df7acc0b121ed2a97e6c3628276c3413dace3c1483d80049c` | **refused**: `acceptance deadline not reached (1789399226, transaction time 1789398939)` |
| attacker `tick()` | `0x0bb88e3ebf47346740766aa31cdec923621cf9d4789d1bc1b5af867f57e44d7c` | **ERROR**: no such method |
| attacker `expire_agreement`, `recover_escrow` | `0xee3b0a5d…`, `0xe4311f44…` | **refused**: `not a party to this agreement` |
| provider `accept_agreement`, after real time passed | `0xb0ef7854be118f4f8b5f32b818ae115f84510c64fcbd14a297726df17ecaa4b7` | **refused**: `acceptance deadline passed at 1789399226 (transaction time 1789399277)` |
| client `expire_agreement` → `recover_escrow` | `0xec94ddd2…` → `0x3e4740f7…` | REFUNDED; client balance +0.1 GEN |

**SLA-000005: verified evidence, real appeal window** (deadline 1789400053)

| Step | Tx | Result |
|---|---|---|
| `request_adjudication` | `0xbc0d0ffe01962d811ee2624d4f7c1a9db1baa3741a2f03714901c96a4e5a2401` | PARTIAL: R1 PASS, R2 PASS, R3 FAIL |
| client `settle` | `0x34e8256e…` | **refused**: `illegal transition from ACCEPTED` |
| client `finalize` | `0x0ef52adeea2f7509269c284896eec7535fd8da4629fb22a077a6dc7dd275d9d9` | **refused**: `appeal window open until 1789400053 (transaction time 1789399571)` |
| attacker `finalize`; attacker `tick()` | `0xb094df94…`; `0xe8954c93…` | refused; ERROR (no such method) |
| client `finalize`, after real time passed | `0xd2ec5b9386ff41109a6696caebfb2721fe5779be6f5e465e21c8dc043fd4f1b2` | FINALIZED at 1789400157 |
| client `settle` | `0x6cae9e742b00cc6ecae4039d74c31861fe10cda58b5587d62bdd9abfd36f8b66` | provider 0.07 GEN, client 0.03 GEN, penalty 0, escrow 0; provider balance +0.07 GEN, contract −0.1 GEN |

**SLA-000006: nothing verifiable; recovery not early** (resolution deadline 1789407452)

| Step | Tx | Result |
|---|---|---|
| `request_adjudication` | `0x419d6e8855ec6dc96ef55e3cb82c00cadb3fad2bb290035641841800283696b8` | UNDETERMINED, no model consulted |
| client `settle`, `finalize` | `0x581e7e53…`, `0x3bc5dcb2…` | **refused**: `illegal transition from UNDETERMINED` |
| client `recover_escrow` | `0xc2fc12db8a9d7f85b212ac02908b9be090f8d99fb5d295398e45d54bd444a73e` | **refused**: `resolution deadline not reached (1789407452, transaction time 1789400356)` |
| attacker `recover_escrow` | `0x572398e2…` | **refused**: `not a party to this agreement` |

SLA-000001 to 000003 on the same contract come from a first run. That run's
test helper counted only a contract rollback as a refusal. The attacker's
`tick()` failed execution instead, because the method does not exist, so the
helper stopped two scenarios before their deadlines. The contract behaved
identically in both runs, and the helper was fixed before the run above.

## 6. Repository = deployment = Explorer

| | |
|---|---|
| Contract | `0x0a93b5b3C7C9F35c49853F12E7851345b76Ae829` |
| Deploy tx | `0x6b3685bf09021099dd99ba66baf630679adc6b17d7817481825227fb85f89d58` |
| Source commit | `685864f` (unchanged at `6486324`) |
| sha256 | `2b0084d6f4a37208f581574ffd61d2f624cfd6f6865d4c40a2041c4079958a49`, 99 335 bytes |

Three sources hash to that one digest:

- the code GenLayer stores for the address (`gen_getContractCode`);
- `contracts/agentsla_core.py` at `685864f`;
- the source shown on the Explorer's *Contract → Code* tab.

That stored code contains `_now()`, and contains neither `tick(` nor
`current_tick`. Repeat the chain-to-repository check with:

```bash
python scripts/verify_deployment.py 0x0a93b5b3C7C9F35c49853F12E7851345b76Ae829 685864f
# MATCH - the deployment is byte-identical to the repository source
```

The earlier deployment `0x6a03Baf33dC24fBC0a7C1ceC47C511A740CddED9` still
has the `tick()` clock. The same script reports it as DIFFER. It should not
be used.

## 7. What is not claimed

- **Not exercised live, covered by direct tests:**
  - recovery *after* a resolution deadline (live, early recovery was
    refused, and a real refund went through on the acceptance-expiry path;
    waiting out a resolution deadline live takes over two hours);
  - an appeal filed inside the window, followed by re-adjudication.
- **Transaction time is the transaction's.** GenLayer pins a datetime to the
  transaction, so a call submitted just before a deadline and executed just
  after is judged as before it. That is what makes the decision identical on
  every validator. It is also why the appeal window has a 600-second floor.
- **Calendar dates written into requirements** are still read by the panel
  from verified evidence; that is how R3 fails. The protocol's own deadlines,
  and the penalty, are decided in code.
