### Title
`tx_burnt_amount` underflow panic from stale `price_deficit` vs. drifted `gas_burn_price` in receipt gas accounting - (File: runtime/runtime/src/lib.rs)

### Summary
The ECG bug is a class of "two independently-derived quantities are assumed to stay in a fixed relationship (`A >= B`), but a discrete state change (multiplier/price update) can invert that relationship, causing an unchecked subtraction to fail." In `Runtime::process_action_receipt`, `tx_burnt_amount` is computed as `gas_burn_price * gas_burnt` minus `price_deficit`, where `gas_burn_price` is derived from the *current block's* gas price at execution time while `price_deficit` was computed relative to the *receipt's* purchase-time gas price. The code carries only a comment-level invariant ("`price_deficit` is strictly less than `gas_burn_price * gas_burnt`") rather than a checked/error-returning subtraction, and unwraps directly: [1](#0-0) 

### Finding Description
`gas_burn_price` is explicitly defined to be `min(gas_purchase_price, apply_state.gas_price)` — i.e., it tracks whichever of the two prices (purchase-time vs. current block) is lower, exactly analogous to how `creditMultiplier` in the ECG report re-prices a previously fixed quantity based on a later, independently-updated global parameter: [2](#0-1) 

`gas_price` itself is a per-block, load-driven, network-wide value that moves up or down every block based on aggregate gas utilization (`compute_next_gas_price_checked`), and a receipt can sit delayed/buffered across many blocks (congestion, bandwidth scheduling) before it is finally executed, so the gap between `gas_purchase_price` (fixed when the receipt was created) and `apply_state.gas_price` (at execution) can grow arbitrarily over that delay window: [3](#0-2) 

`price_deficit` (and its counterpart `price_surplus`) is computed inside `refund_unspent_gas_and_deposits` by comparing the burn price against the purchase price at refund time, and is added into the running `stats.balance.gas_deficit_amount` for the whole chunk: [4](#0-3) 

The final accounting step then performs an unchecked `checked_sub(...).unwrap()` between `gas_burn_price * gas_burnt` (computed fresh for *this* receipt using the *current* block price) and `gas_refund_result.price_deficit` (computed for the *same* receipt but during its own internal refund logic, which itself depends on the receipt's own purchase price vs. burn price relationship). Because these are two related-but-independently-computed quantities that both depend on the same volatile, block-level `gas_price` input at different points and under different rounding/clamping paths (min/max, deposit-cost interactions, `AccountCostIncrease` gating, NEP-536 penalty clamping), an attacker who controls the shape of a receipt (attached gas, deposit, receiver contract behavior causing failure/success asymmetries) combined with naturally occurring gas-price volatility across the delay window of a congested/buffered receipt can attempt to force `price_deficit` above `gas_burn_price * gas_burnt` for that specific receipt.

This mirrors the ECG root cause precisely: `redeemableCredit` (computed from a fixed minted amount times the *current* `creditMultiplier`) can exceed `targetTotalSupply()` (a separately-tracked, rebasing quantity) once the multiplier moves and a user takes an action (burn) that shifts the balance — here, `price_deficit` (computed from a per-receipt refund path sensitive to price drift) can exceed the freshly-recomputed `gas_burn_price * gas_burnt` for that receipt once the block gas price has moved between purchase and execution and the receipt's specific gas/deposit shape is chosen to maximize the imbalance.

### Impact Explanation
Unlike the Solidity original, where the failure mode is a reverting view/estimation call that merely blocks new borrows, the analogous nearcore failure is a `.unwrap()` panic inside `Runtime::process_action_receipt`, which executes during chunk application on the hot consensus path. A panic here does not just fail one transaction — it aborts the chunk-application thread of every honest validator/RPC node processing that chunk with the offending receipt, which is a transaction-triggered liveness halt (chain stall) rather than a mere transaction rejection. This is strictly more severe than the original Medium-severity ECG finding.

### Likelihood Explanation
This requires: (1) a receipt that sits delayed/buffered across enough blocks for the network gas price to move materially relative to its purchase price (achievable by deliberately congesting the target shard, which is a normal, permissionless capability), and (2) crafting the receipt's attached gas/deposit/failure characteristics to maximize the divergence between the fresh `gas_burn_price * gas_burnt` term and the `price_deficit` produced by the receipt's own refund path. I was not able to fully trace the exact bounds enforced inside `refund_unspent_gas_and_deposits` (`lib.rs:1166` per the spec) within the available tool budget, so I cannot confirm whether existing clamping (e.g., the `min(purchase, block)` comment at line 1040-1041, or NEP-536 penalty clamps) already forecloses this specific underflow in all cases, or whether it is only an assumed-but-unverified invariant as the comment implies ("should always be ≤ ... otherwise ... might underflow").

### Recommendation
Replace the bare `.checked_sub(gas_refund_result.price_deficit).unwrap()` at `lib.rs:1094-1095` with an explicit bounds check/clamp (e.g., `saturating_sub`, or an error path returning `RuntimeError::StorageError(StorageInconsistentState(..))` non-fatally) so that any unexpected drift between `gas_burn_price * gas_burnt` and `price_deficit` cannot panic mid-chunk-application. Additionally, add a fuzz/property test that varies block gas price across the lifetime of a delayed/buffered receipt (spanning many blocks of congestion) combined with edge-case attached gas/deposit amounts, to positively verify the "`price_deficit` strictly less than `gas_burn_price * gas_burnt`" invariant the current code only asserts in a comment.

### Proof of Concept
I could not construct or run a concrete Rust reproduction within the available tool budget (no code execution/terminal access), and I was unable to fully read `refund_unspent_gas_and_deposits`'s exact arithmetic (only its high-level description via the derived spec doc) to confirm a concrete input triggering the underflow. This should be validated by a Devin agent with repository and terminal access, focused on: `runtime/runtime/src/lib.rs` function `refund_unspent_gas_and_deposits` (~line 1166) and its interaction with `process_action_receipt`'s `tx_burnt_amount` computation (line 1092-1095), specifically searching for an attainable combination of `gas_purchase_price`, `apply_state.gas_price` (post multi-block drift), attached gas, and deposit/failure state that makes `price_deficit > gas_burn_price * gas_burnt` for a single receipt.

### Citations

**File:** runtime/runtime/src/lib.rs (L1033-1045)
```rust
        // The price at which the gas attached to this receipt was purchased.
        let gas_purchase_price = action_receipt.gas_price();

        // The price at which gas was burnt while applying this receipt. Can be different from the price at
        // which the gas was purchased.
        let gas_burn_price =
            if ProtocolFeature::AccountCostIncrease.enabled(apply_state.current_protocol_version) {
                // should always be <= gas_purchase_price, otherwise receiver_reward might underflow
                // or mint new tokens.
                std::cmp::min(gas_purchase_price, apply_state.gas_price)
            } else {
                apply_state.gas_price
            };
```

**File:** runtime/runtime/src/lib.rs (L1092-1095)
```rust
        // `price_deficit` is strictly less than `gas_burn_price * gas_burnt`.
        let mut tx_burnt_amount = safe_gas_to_balance(gas_burn_price, gas_burnt)?
            .checked_sub(gas_refund_result.price_deficit)
            .unwrap();
```

**File:** protocol-model/spec/economics.md (L43-49)
```markdown
### 4. Gas-price adjustment
The next block's gas price is a load-feedback controller (`core/primitives/src/block.rs:440` — `compute_next_gas_price_checked`):
- Formula (`block.rs:417`): `next_gas_price = gas_price * (1 + (gas_used/gas_limit − 1/2) * adjustment_rate)`. Implemented as the exact integer ratio `numerator/denominator` at `block.rs:460`. When utilization is exactly 50% the price is unchanged; above 50% it rises, below it falls.
- If the block was skipped (`gas_limit == 0`) the price is unchanged (`block.rs:449`).
- The result is clamped to `[min_gas_price, max_gas_price]` (`block.rs:471`).
- `min_gas_price` is `MIN_GAS_PRICE_NEP_92_FIX` (`100_000_000` yN) for chains whose genesis is `PROD_GENESIS_PROTOCOL_VERSION`, else the genesis value (`chain/chain/src/types.rs:199`, const at `core/primitives/src/version.rs:29`). `max_gas_price` is `min(genesis_max_gas_price, min_gas_price * 20)` (`types.rs:207`, `MAX_GAS_MULTIPLIER = 20` at `types.rs:191`).
- The chain applies this over one block via `compute_next_gas_price_checked` (called at `block.rs:182`); under Spice it folds over certified results (`compute_gas_price_from_certified_results_checked`, `block.rs:479`).
```

**File:** protocol-model/spec/economics.md (L59-67)
```markdown
### 6. Gas burning, rewards, and refunds during receipt execution
In `Runtime::apply_action_receipt` (`runtime/runtime/src/lib.rs`), after actions run:
1. **Gas burn price** — with `AccountCostIncrease` enabled, gas is burnt at `min(gas_purchase_price, apply_state.gas_price)`; before it, at `apply_state.gas_price` (`lib.rs:920`).
2. **Refunds** — `refund_unspent_gas_and_deposits` (`lib.rs:1166`) refunds unspent gas and, on failure, the full deposit. `gross_gas_refund` = prepaid gas + prepaid exec gas − gas actually used/burnt (`lib.rs:1186`). It then:
   - Computes the NEP-536 penalty `gas_penalty_for_gas_refund(gross)` = `min(gross, max(gross * gas_refund_penalty, min_gas_refund_penalty))` (`core/parameters/src/cost.rs:683`) at `gas_burn_price` (post-`AccountCostIncrease`) or `gas_purchase_price` (before) (`lib.rs:1201`). The unused-gas refund is issued at `gas_purchase_price` minus this penalty (`lib.rs:1210`).
   - Records `price_deficit` if the burn price rose above purchase price, else `price_surplus` (`lib.rs:1220`). With `AccountCostIncrease`, the surplus is refunded to the signer (`burned_gas_refund`, `lib.rs:1235`); before, it was retained as burnt.
   - If a new account was created (post-`AccountCostIncrease`), an extra `create_account_charge` = `min(desired = account_creation_charge − already-burned, burned_gas_refund)` is subtracted from the refund (`lib.rs:1243`).
3. **tx_burnt_amount** — burnt tokens for the receipt = `gas_burn_price * gas_burnt − price_deficit` (`lib.rs:975`), plus `refund_penalty`, `create_account_charge`, and `result.tokens_burnt`; pre-`AccountCostIncrease` also plus `price_surplus` (`lib.rs:978`). System/refund receipts burn 0 gas (`lib.rs:972`).
4. **Contract reward** — of the gas burnt *for function calls*, a `burnt_gas_reward` fraction (mainnet `3/10`) is paid to the receiver account and *subtracted* from `tx_burnt_amount` (`lib.rs:990`). Reward is priced at `gas_burn_price` post-`AccountCostIncrease`, else `gas_purchase_price` (`lib.rs:998`). If the receiver account no longer exists, the whole amount stays burnt (goes to validators via §3). The remaining `tx_burnt_amount` accumulates into `stats.balance.tx_burnt_amount` (`lib.rs:1023`) and ultimately reduces total supply as `balance_burnt`.
```
