### Title
Off-by-one in `handle_poison_microblock`'s maturity check lets a poison report be written after the coinbase for that height already matured, permanently orphaning the report - (File: `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Summary
`handle_poison_microblock` rejects a poison-microblock report only when `height + MINER_REWARD_MATURITY < current_height`, which is a strict inequality that still admits a report submitted in the very block where `current_height == height + MINER_REWARD_MATURITY`. `find_mature_miner_rewards` computes `reward_height = tip_stacks_height - MINER_REWARD_MATURITY` and reads `get_poison_microblock_report(reward_height)` exactly once, as part of `setup_block`'s pre-transaction accounting for the new tip. Because the equivocating miner's coinbase matures and is credited before that same block's own transactions (including a late-arriving but still "valid" poison tx) are executed, `insert_microblock_poison` can write a report for `reward_height` that is stored in the Clarity/MARF state but is never again consulted by any future `find_mature_miner_rewards` call.

### Finding Description
The intended equality is: `reward_paid(miner_at_height_h) == full_coinbase` **iff** `get_poison_microblock_report(h)` was `None` at the moment `h` matured (i.e., no valid double-sign proof existed in time). The code breaks this equality at the exact boundary `h + MINER_REWARD_MATURITY == current_height`:

- `check_microblock_header_signer` validates the two microblock headers are signed by the same key, and the height gate is applied at [1](#0-0) , using a strict `<` comparison that only rejects once `current_height` has moved *past* the boundary, not *at* it.
- `find_mature_miner_rewards` computes `reward_height = tip_stacks_height - MINER_REWARD_MATURITY` and looks up the poison report a single time for that height, per [2](#0-1)  and [3](#0-2) .
- This maturation/crediting for the tip currently being appended happens as part of the block-setup accounting for that tip (before that block's own transactions, including a poison tx targeting the very same `reward_height`, are processed by `handle_poison_microblock`). At the instant `current_height == h + MINER_REWARD_MATURITY`, the `<` check in the poison-tx handler does not reject the tx, so `insert_microblock_poison(h, ...)` succeeds and stores the report — but the same block's maturation step has already run `get_poison_microblock_report(h)` and found nothing, paying the equivocating miner the full coinbase via `calculate_miner_reward` with `poison_reporter_opt = None` at [4](#0-3) .
- No future call to `find_mature_miner_rewards` will ever query height `h` again, since `reward_height` strictly advances by one with each subsequent tip. The stored report at `h` becomes permanently unreachable dead state.
- Nothing in `check_tenure_tx`, `verify_signer_signatures`, VRF/static validators, or `common_validate_against_burnchain` guards this path — the maturity gate is purely local to `handle_poison_microblock`'s height arithmetic.

### Impact Explanation
The equivocating miner keeps a coinbase reward (and its associated fees) that should have been diverted (subject to `POISON_MICROBLOCK_COMMISSION_FRACTION`) to the honest reporter, and the remainder that should have been burned is instead paid out — a reward mispayment bounded to the coinbase/fees of the single block at height `h`. This is deterministic: every honest node processing the same transactions in the same order reaches the identical outcome, so there is no chain split or state-root divergence — all nodes agree that the miner keeps the funds and that the poison report is stored-but-orphaned. The scoped impact is therefore reward loss/miner-escapes-slashing for one block, not a network-wide invalid-block acceptance or fork.

### Likelihood Explanation
Exploitation requires only that the poison-microblock transaction land in the exact block where `current_height == h + MINER_REWARD_MATURITY`, which is entirely within the control of whoever crafts/broadcasts the tx and any unprivileged miner willing to include it at that height (no majority stake, no signer collusion, and no privileged role needed — any participant able to submit transactions and observe chain height can time this). It is a narrow, single-block timing window per double-sign event, so it is not "free" to hit on every equivocation, but it is a reliable, repeatable technique any minority miner/colluding reporter can engineer deliberately (e.g., withhold the poison evidence until the boundary block is reached before broadcasting).

### Recommendation
Change the maturity comparison in `handle_poison_microblock` from strict `<` to `<=` (i.e., reject once `height + MINER_REWARD_MATURITY <= current_height`), so that a public key hash cannot be poisoned in or after the exact block where its reward matures. Alternatively, have `find_mature_miner_rewards`/`setup_block` process the current block's own poison-microblock transactions before performing the maturity payout so that a same-block report is honored.

### Proof of Concept
1. Set up a two-miner regtest/integration harness (using the existing chainstate test utilities in `stackslib/src/chainstate/stacks/tests/`), configure a short `MINER_REWARD_MATURITY` for testability.
2. Have miner A produce two conflicting microblocks at the same sequence number, signed with the same key, at Stacks height `h` (a genuine equivocation).
3. Allow the chain to advance until `current_height == h + MINER_REWARD_MATURITY` (the maturation block for height `h`), and confirm via test hooks that `find_mature_miner_rewards` already ran for `reward_height = h` with `get_poison_microblock_report(h) == None`, crediting miner A the full coinbase.
4. In that same block, submit a valid `PoisonMicroblock` transaction (built from the two conflicting headers) targeting the pubkey hash at height `h`; assert it is accepted (`handle_poison_microblock` does not error) and `insert_microblock_poison(h, reporter, seq)` succeeds.
5. Assert both sides of the equality: (a) miner A's account balance already reflects the full, unpunished coinbase for height `h` (no commission deducted, no burn applied) and (b) `get_poison_microblock_report(h)` now returns `Some((reporter, seq))` — proving a valid report exists that will never again be read by any subsequent `find_mature_miner_rewards` call, and no retroactive clawback/commission payment ever occurs for the reporter.

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L783-800)
```rust
            Some(height) => {
                if height
                    .checked_add(
                        u32::try_from(MINER_REWARD_MATURITY).expect("FATAL: maturity > 2^32"),
                    )
                    .expect("BUG: too many blocks")
                    < current_height
                {
                    let msg = format!(
                        "Invalid Stacks transaction: microblock public key hash from height {} has matured relative to current height {}",
                        height, current_height
                    );
                    warn!("{}", &msg;
                          "microblock_pubkey_hash" => %pubkh
                    );

                    return Err(Error::InvalidStacksTransaction(msg, false));
                }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L993-998)
```rust
        if tip_stacks_height <= MINER_REWARD_MATURITY {
            // no mature rewards exist
            return Ok(None);
        }

        let reward_height = tip_stacks_height - MINER_REWARD_MATURITY;
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L1029-1052)
```rust
        let poison_recipient_opt =
            StacksChainState::get_poison_microblock_report(clarity_tx, reward_height)?
                .map(|(reporter, _)| reporter);

        if let Some(ref _poison_reporter) = poison_recipient_opt.as_ref() {
            test_debug!(
                "Poison-microblock reporter {} at height {}",
                &_poison_reporter.to_string(),
                reward_height
            );
        } else {
            test_debug!("No poison-microblock report at height {}", reward_height);
        }

        // calculate miner reward
        let (parent_miner_reward, miner_reward) = StacksChainState::calculate_miner_reward(
            mainnet,
            parent_evaluated_epoch.epoch_id,
            &miner,
            &miner,
            &users,
            &parent_miner,
            poison_recipient_opt.as_ref(),
        );
```
