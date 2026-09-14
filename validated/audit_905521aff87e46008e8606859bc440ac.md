Found it: `distribute_remaining_bandwidth`'s `EndpointInfo::link_proposition` performs an **unguarded division by zero**, unlike its sibling function `average_link_bandwidth` in the same `impl` block, which explicitly checks for `links_num == 0` before dividing. [1](#0-0) 

### Title
Unguarded division-by-zero panic in `EndpointInfo::link_proposition` during per-chunk bandwidth scheduling - (File: runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs)

### Summary
`distribute_remaining_bandwidth` computes `granted_bandwidth` for every sender/receiver shard-link pair via `sender_info.link_proposition()` and `receiver_info.link_proposition()`. `link_proposition` divides `bandwidth_left / links_num` with no zero-check, whereas the adjacent `average_link_bandwidth` method explicitly guards against `links_num == 0`. This inconsistency is a direct code smell matching the report's bug class ("a few places in the code divide without checking the denominator").

### Finding Description
`distribute_remaining_bandwidth` (`runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs:22-87`) builds `sender_infos`/`receiver_infos` maps of `EndpointInfo { links_num, bandwidth_left }`, counting `links_num` from `is_link_allowed` for each shard [2](#0-1) . It then iterates `senders_by_avg_link_bandwidth` and for each allowed `(sender, receiver)` link calls:
```
if sender_info.links_num == 0 || receiver_info.links_num == 0 { break; }
let sender_proposition = sender_info.link_proposition();
let receiver_proposition = receiver_info.link_proposition();
``` [3](#0-2) 

The `break` guard checks `links_num == 0` *before* calling `link_proposition`, which appears to be the mitigation for the very same bug class described in the report — but this protection depends entirely on the surrounding call-site logic remembering to check first. `link_proposition` itself, unlike `average_link_bandwidth`, has no internal `if self.links_num == 0 { return 0 }` guard, making it a latent trap for any future caller (e.g. new callers added during protocol changes, refactors of the bandwidth scheduler, or future NEP work extending the scheduler) that forgets the precondition — exactly the report's warning that "this code could be reused in other circumstances later."

`links_num` is decremented every time a grant occurs (`sender_info.links_num -= 1;` / `receiver_info.links_num -= 1;`) [4](#0-3) , so it is a mutable, per-iteration value whose invariant ("never called at zero") is enforced only by the caller loop's `break`, not by the function itself.

### Impact Explanation
`run_bandwidth_scheduler`, which calls into this code, executes once per chunk on every validator applying that chunk, deterministically, as part of the state transition (`bandwidth_scheduler/mod.rs:44`, called from `lib.rs:1767` per the cross-shard-congestion spec). A panic here during `Runtime::apply` would abort chunk application — a transaction/receipt-triggered halt of chunk processing on that shard, which is exactly the kind of transaction-triggered-halt impact this scan is asked to flag. Because the code today happens to avoid calling `link_proposition` when `links_num == 0` via the `break` in the enclosing loop, the practical exploitability under the *current* call graph is low; the primary risk is architectural: a division that should be as bulletproof as its sibling `average_link_bandwidth()` (which self-guards) is not, and any code path that reaches `link_proposition()` without first re-verifying `links_num != 0` is an unrecoverable panic (integer division by zero panics in Rust, not a "safe" trap).

### Likelihood Explanation
Low likelihood under the current single call site (the `break` before the two `link_proposition()` calls currently prevents divide-by-zero), but the function is `pub(crate)`-callable within the module and has no defensive check of its own, unlike `average_link_bandwidth`. Any future refactor of `schedule_bandwidth`'s stages, or reordering of the zero-check relative to the two `link_proposition()` calls, silently reintroduces the exact same class of bug the external report describes, with no compiler or test-suite protection since `link_proposition` carries no doc-comment precondition contract and no assertion.

### Recommendation
Add the same `if self.links_num == 0 { return 0; }` guard to `link_proposition` that already exists in `average_link_bandwidth`, so both accessors are internally safe regardless of caller discipline:
```rust
fn link_proposition(&self) -> Bandwidth {
    if self.links_num == 0 {
        return 0;
    }
    self.bandwidth_left / self.links_num
}
```
This removes the reliance on caller-side ordering of checks and matches the recommendation in the source report ("handle the cases where the denominator could be zero appropriately").

### Proof of Concept
Not independently exploitable today because `distribute_remaining_bandwidth`'s loop always checks `links_num == 0` immediately before invoking `link_proposition()` [5](#0-4) . A concrete PoC would require demonstrating a call to `EndpointInfo::link_proposition()` with `links_num == 0`, which is not currently reachable through any exposed path in this file; this is a code-hygiene/defense-in-depth finding rather than a demonstrated live crash today.

### Citations

**File:** runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs (L41-48)
```rust
    for sender in shard_layout.shard_indexes() {
        for receiver in shard_layout.shard_indexes() {
            if *is_link_allowed.get(&ShardLink::new(sender, receiver)).unwrap_or(&false) {
                sender_infos.get_mut(&sender).unwrap().links_num += 1;
                receiver_infos.get_mut(&receiver).unwrap().links_num += 1;
            }
        }
    }
```

**File:** runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs (L60-76)
```rust
    for sender in senders_by_avg_link_bandwidth {
        let sender_info = sender_infos.get_mut(&sender).unwrap();
        for &receiver in &receivers_by_avg_link_bandwidth {
            if !*is_link_allowed.get(&ShardLink::new(sender, receiver)).unwrap_or(&false) {
                continue;
            }

            let receiver_info = receiver_infos.get_mut(&receiver).unwrap();

            if sender_info.links_num == 0 || receiver_info.links_num == 0 {
                break;
            }

            let sender_proposition = sender_info.link_proposition();
            let receiver_proposition = receiver_info.link_proposition();
            let granted_bandwidth = std::cmp::min(sender_proposition, receiver_proposition);
            bandwidth_grants.insert(ShardLink::new(sender, receiver), granted_bandwidth);
```

**File:** runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs (L78-82)
```rust
            sender_info.bandwidth_left -= granted_bandwidth;
            sender_info.links_num -= 1;

            receiver_info.bandwidth_left -= granted_bandwidth;
            receiver_info.links_num -= 1;
```

**File:** runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs (L97-111)
```rust
impl EndpointInfo {
    /// How much can be sent on every link on average
    fn average_link_bandwidth(&self) -> Bandwidth {
        if self.links_num == 0 {
            return 0;
        }
        self.bandwidth_left / self.links_num
    }

    /// Propose amount of bandwidth to grant on the next link.
    /// Both sides of the link propose something and the minimum of the two is granted on the link.
    fn link_proposition(&self) -> Bandwidth {
        self.bandwidth_left / self.links_num
    }
}
```
