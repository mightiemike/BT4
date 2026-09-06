### Title
`MinerReward::is_child()`/`is_parent()` classification collide when a punished child's coinbase truncates to zero, causing a deterministic assert panic in `insert_matured_child_miner_reward` - ([File: stackslib/src/chainstate/stacks/db/accounts.rs])

### Summary
`MinerReward::is_child()` is defined as `coinbase > 0 && tx_fees_streamed_produced == 0`, and `is_parent()` as `coinbase == 0`. Because the miner-side reward built in `calculate_miner_reward` always hardcodes `tx_fees_streamed_produced: 0` [1](#0-0) , once `coinbase_reward` is driven to exactly `0` (e.g. via poison-microblock commission integer truncation) the resulting "child" reward now satisfies `is_parent()` instead of `is_child()`, breaking the assertion in `insert_matured_child_miner_reward` [2](#0-1) .

### Finding Description
The broken equality is: `insert_matured_child_miner_reward(child_reward)` must always be called with a reward for which `is_child() == true` whenever `calculate_miner_reward` is invoked with `punished = true`. The code path is:

1. `calculate_miner_reward` computes `coinbase_reward = participant.coinbase * this_burn_total / burn_total` [3](#0-2) .
2. When a poison report exists and the participant is the miner, the reward is replaced with `poison_microblock_commission(coinbase_reward)` = `(coinbase_reward * POISON_MICROBLOCK_COMMISSION_FRACTION) / 100`, an integer division that truncates to `0` for any sufficiently small `coinbase_reward` [4](#0-3) [5](#0-4) .
3. The resulting `miner_reward` struct always has `tx_fees_streamed_produced: 0` regardless of punishment [1](#0-0) .
4. `is_child()` becomes `false` (since `coinbase == 0`), while `is_parent()` becomes `true`, colliding with the classification of the actual `parent_miner_reward` row (also `coinbase == 0`, `is_parent() == true`) [6](#0-5) .
5. `insert_matured_child_miner_reward` unconditionally asserts `child_reward.is_child()` [2](#0-1) , which now panics.

This panic is fully deterministic: it depends only on on-chain data (the coinbase schedule, burn amounts, and the poison-microblock report), all of which are agreed upon by consensus before reward maturation runs. Therefore every honest node computing matured rewards for that tenure at the maturity height will hit the identical panic — not a divergence between node A and node B, but a total network halt: no node can advance past the block whose maturity triggers this computation, which is functionally equivalent to a valid block being rejected/unprocessable network-wide.

No existing guard prevents this: `calculate_miner_reward` has no floor/lower-bound check ensuring `coinbase_reward` stays nonzero when punished, and `is_child()`/`is_parent()` were never designed to be mutually exclusive under a zero-coinbase edge case.

### Impact Explanation
If reached, this crashes/halts block processing across the entire network at the reward-maturity height (not a fork between nodes, since it's deterministic), matching the Critical category of "a valid block rejected network-wide" / permanent processing freeze, since no node can safely proceed past that maturity computation. All future reward maturation, and thus deterministic chain progress, is blocked network-wide until intervention (upgrade/config change), which functionally freezes the chain.

### Likelihood Explanation
Requires: (1) a Nakamoto tenure where the miner's proportional coinbase share (`participant.coinbase * this_burn_total / burn_total`) is small enough that `poison_microblock_commission()`'s `/100` integer division truncates it to `0` (trivially satisfiable with small enough shares, e.g. near-halving coinbase amounts or diluted burn shares among many participants), and (2) a valid `PoisonMicroblockReport` filed against that miner by any unprivileged reporter. Both are attacker-controllable with minimal cost — no majority stake, no privileged role, just submitting a legitimate poison report against a miner whose burn share is already small. This is repeatable on any future tenure meeting the truncation condition.

### Recommendation
Redefine `MinerReward::is_child()`/`is_parent()` to not rely solely on `coinbase == 0` as a proxy for "is parent." Instead, tag reward rows explicitly (e.g. a `role: Parent | Child` enum field) set at construction time in `calculate_miner_reward`, rather than inferring the role post-hoc from field values that can legitimately collide (zero coinbase for a punished/diluted child). Alternatively, ensure `insert_matured_child_miner_reward`/`insert_matured_child_user_reward` receive an explicit `is_child` flag from the caller instead of re-deriving it from mutable numeric fields.

### Proof of Concept
Rust integration test plan (in `stackslib/src/chainstate/stacks/db/accounts.rs` test module or a Nakamoto integration test harness):
1. Construct a `MinerPaymentSchedule` for a miner with a small `coinbase` and burn distribution such that `coinbase_reward = participant.coinbase * this_burn_total / burn_total` is small (e.g., 1–19 with `POISON_MICROBLOCK_COMMISSION_FRACTION` = 5).
2. Call `StacksChainState::calculate_miner_reward(..., poison_reporter_opt = Some(&reporter_addr))` and assert on both sides of the equality:
   - Before fix: `miner_reward.is_child() == false` and `miner_reward.is_parent() == true` (bug reproduced), and calling `insert_matured_child_miner_reward` on this row panics.
   - After fix: assert `insert_matured_child_miner_reward` completes without panicking, and `get_matured_miner_payment` correctly reconstructs the merged reward via `try_add_parent` without ambiguity, for both the punished-to-zero case and the normal case.
3. Wrap this in a full tenure-processing test that produces a coinbase-eligible block, adds a poison-microblock report against the child miner, and drives block processing through `find_mature_miner_rewards` to confirm no panic occurs and reward maturation is deterministic across two independently-processing chainstate instances.

### Citations

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L162-168)
```rust
    pub fn is_child(&self) -> bool {
        self.coinbase > 0 && self.tx_fees_streamed_produced == 0
    }

    pub fn is_parent(&self) -> bool {
        self.coinbase == 0
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L600-603)
```rust
        assert!(
            child_reward.is_child(),
            "FATAL: tried to insert a non-child reward as the child reward"
        );
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L795-797)
```rust
    fn poison_microblock_commission(coinbase: u128) -> u128 {
        (coinbase * POISON_MICROBLOCK_COMMISSION_FRACTION) / 100
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L863-867)
```rust
        let coinbase_reward = participant
            .coinbase
            .checked_mul(this_burn_total)
            .expect("FATAL: STX coinbase reward overflow")
            / burn_total;
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L872-886)
```rust
        let (child_address, child_recipient, coinbase_reward, punished) =
            if let Some(reporter_address) = poison_reporter_opt {
                if participant.miner {
                    // the poison-reporter, not the miner, gets a (fraction of the) reward
                    debug!(
                        "{:?} will recieve poison-microblock commission {}",
                        &reporter_address.to_string(),
                        StacksChainState::poison_microblock_commission(coinbase_reward)
                    );
                    (
                        reporter_address.clone(),
                        reporter_address.to_account_principal(),
                        StacksChainState::poison_microblock_commission(coinbase_reward),
                        true,
                    )
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L969-977)
```rust
        let miner_reward = MinerReward {
            address: child_address,
            recipient: child_recipient,
            coinbase: coinbase_reward,
            tx_fees_anchored,
            tx_fees_streamed_produced: 0,
            tx_fees_streamed_confirmed,
            vtxindex: participant.vtxindex,
        };
```
