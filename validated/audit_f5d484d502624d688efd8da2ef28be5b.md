I found a clear analog in the bandwidth scheduler's `try_forward` default-limit handling.

### Title
Missing per-shard `OutgoingLimit` entry defaults to unlimited gas instead of no grant, allowing receipt admission bypass ([File: runtime/runtime/src/congestion_control.rs])

### Summary
The reported bug is a "sentinel value collides with a legitimate value" class of bug: `WrappedVault` used `start == 0` as a proxy for "no active reward schedule," but `0` is also a legitimate start time, so real schedules were silently skipped. The nearcore analog is in `ReceiptSink::try_forward` (`runtime/runtime/src/congestion_control.rs:397-441`), which uses a *missing map entry* as a sentinel for "no limit configured" and defaults it to `Gas::MAX` combined with `size: 0`, rather than distinguishing "shard genuinely has zero granted bandwidth this chunk" from "shard is simply absent from `apply_state.congestion_info`."

### Finding Description
`ReceiptSink::new` (`runtime/runtime/src/congestion_control.rs:85-142`) builds `outgoing_limit: HashMap<ShardId, OutgoingLimit>` only for shards present in `apply_state.congestion_info`. In `try_forward` (`:403-441`), when forwarding a buffered/new receipt to a shard, the code does:
```rust
let default_gas_limit = Gas::MAX;
let default_size_limit = 0;
let default_outgoing_limit = OutgoingLimit { gas: default_gas_limit, size: default_size_limit };
let forward_limit = outgoing_limit.entry(shard).or_insert(default_outgoing_limit);
``` [1](#0-0) 

The comment at `:429-433` explicitly rationalizes this as intentional ("if we cannot know a limit, treating it as literally 'no limit' is the safest approach"), and the `size: 0` default is separately justified at `:436-437` ("a shard is not allowed to send any receipts if it doesn't have a grant"). This mirrors the reported pattern exactly: a *missing/zero* signal (no map entry) is treated identically to a benign default (`Gas::MAX`) rather than being distinguished from "target shard is fully congested / has no outgoing gas allowance for us," which is precisely the state that `CongestionControl::outgoing_gas_limit` (`core/primitives/src/congestion_info.rs:80-93`) is designed to compute per sender/receiver pair.

Under `ReceiptSinkV2::new` this only happens for shards outside `apply_state.congestion_info` (resharding edge cases per the comment), so its blast radius today is narrow. However, the code path is directly reachable by any user's cross-shard receipt: every `Transfer`, `FunctionCall`, etc. that produces a cross-shard receipt goes through `try_forward`, and any shard-layout/resharding transition, or any bug/edge-case that omits a shard from `apply_state.congestion_info` for one block, causes that shard's admission gas limit to jump from the intended "red light" (`Gas::ZERO` when fully congested per `congestion_info.rs:83-89`) to `Gas::MAX` — i.e., the congestion-control backpressure invariant documented in the spec ("a fully congested shard grants `Gas::ZERO` to all senders except its `allowed_shard`") is bypassed for that shard on that block, defeating the "Bounded incoming work" invariant.

### Impact Explanation
If the default-fallback case is ever hit for a shard that is actually fully congested (deterministically the same on every honest node, since `apply_state.congestion_info` is built identically everywhere from chunk headers), all nodes agree, so there's no state-root divergence — but the fully-congested shard's delayed-receipt queue can grow unboundedly for a block, bypassing the throttle that congestion control exists to enforce. This is a gas/backpressure-bypass class issue rather than direct fund theft, but it undermines a documented safety invariant ("Bounded incoming work") that other components (bandwidth scheduler `is_link_allowed`) rely on to avoid deadlock/liveness assumptions.

### Likelihood Explanation
Today the `outgoing_limit` map is populated from `apply_state.congestion_info`, which should normally contain every shard in the current epoch's shard layout, so the default-fallback branch is intended only as a defensive fallback for resharding edge cases (per the inline comment) — it is not routinely hit in the base case. Exploitability therefore depends on finding/inducing a state where a legitimately congested shard is transiently missing from `apply_state.congestion_info` (e.g., during a shard split/resharding boundary), which is a narrower trigger condition than the original report's "start=0 is a normal, common input."

### Recommendation
Do not default a missing `OutgoingLimit` entry to `Gas::MAX`; instead, explicitly distinguish "shard absent because it doesn't exist / pre-dates this chunk's layout" (safe to treat as unlimited) from "shard is present in the epoch's shard layout but missing from `congestion_info`" (should conservatively default to `Gas::ZERO`, mirroring the fully-congested red-light default), analogous to how the report recommended checking the actual rate/flag (`rewardsInterval_.rate == 0`) instead of an overloaded `start == 0` sentinel.

### Proof of Concept
Not independently reproduced against a live cluster; the analysis is based on static code review of `runtime/runtime/src/congestion_control.rs:397-441` and `core/primitives/src/congestion_info.rs:80-93`, cross-referenced with the spec description of the "Bounded incoming work" invariant in `protocol-model/spec/cross-shard-congestion.md:352-360`. [2](#0-1)

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L429-441)
```rust
        // Default case set to `Gas::MAX`: If no outgoing limit was defined for the receiving
        // shard, this usually just means the feature is not enabled. Or, it
        // could be a special case during resharding events. Or even a bug. In
        // any case, if we cannot know a limit, treating it as literally "no
        // limit" is the safest approach to ensure availability.
        let default_gas_limit = Gas::MAX;

        // Since bandwidth scheduler, a shard is not allowed to send any receipts if it doesn't have a grant.
        let default_size_limit = 0;

        let default_outgoing_limit =
            OutgoingLimit { gas: default_gas_limit, size: default_size_limit };
        let forward_limit = outgoing_limit.entry(shard).or_insert(default_outgoing_limit);
```

**File:** protocol-model/spec/cross-shard-congestion.md (L352-360)
```markdown
## Invariants & failure modes

- **Bounded incoming work**: a fully congested shard grants `Gas::ZERO` to all
  senders except its `allowed_shard` (`congestion_info.rs:83`), and the bandwidth
  scheduler forbids all links into it except the allowed one (`scheduler.rs:535`);
  together these stop unbounded delayed-queue growth while guaranteeing one sender can
  always make progress. Asserted by the `test_missed_chunks_finalize`
  (`congestion_info.rs:814`) and the `test_*_congestion` tests
  (`:769` missed-chunks, `:621` memory, `:670` incoming, `:723` outgoing).
```
