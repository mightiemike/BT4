### Title
Poison-microblock height resolution binds only to `pubkh`, allowing a reused microblock signing key to mis-attribute the slashed coinbase to the wrong tenure - ([File: stackslib/src/chainstate/stacks/db/transactions.rs::handle_poison_microblock])

### Summary
`handle_poison_microblock` resolves the height to slash purely from `get_microblock_pubkey_hash_height(&pubkh)` [1](#0-0) , with no check that the two supplied microblock headers actually belong to the tenure whose sortition registered that pubkey hash at that height. `check_microblock_header_signer` only verifies both headers recover to the *same* public key [2](#0-1) ; it performs no comparison of `prev_block`, tenure, or anchored-block context between the two headers.

### Finding Description
The claimed equality is: *the height/tenure whose coinbase gets slashed by a `PoisonMicroblock` report == the height/tenure of the sortition/anchored block that actually authorized the microblock stream containing the two equivocating headers.*

`handle_poison_microblock` computes `mblock_pubk_height` solely as `get_microblock_pubkey_hash_height(&pubkh)` [3](#0-2) , and this value alone determines which height's coinbase gets flagged via `insert_microblock_poison(mblock_pubk_height, ...)` [4](#0-3) , later consumed by `find_mature_miner_rewards`/`calculate_miner_reward` to redirect/destroy that specific height's coinbase [5](#0-4) . The pubkey-hash-to-height mapping is a flat key-value association keyed only by `pubkh` (see call sites `insert_microblock_pubkey_hash(&mut conn, height, &pubkh)` in test helpers, e.g. [6](#0-5) ), with no compound key on tenure/consensus-hash/anchored-block-id. If the same microblock private key is legitimately re-registered by the same miner in a later, unrelated tenure (a second, non-adjacent sortition win), the stored mapping for that `pubkh` is overwritten to the later height. A `PoisonMicroblock` transaction combining two headers that both recover to that key — regardless of which tenure's stream they actually came from, since `check_microblock_header_signer` does not check `prev_block`/tenure linkage — will then resolve `mblock_pubk_height` to the *latest* registration height, not the height of the tenure that actually produced the two given headers.

Existing guards do not catch this: `check_microblock_header_signer` only checks signer identity [2](#0-1) ; the maturity window check only bounds staleness relative to `current_height`, not correctness of tenure attribution [7](#0-6) ; there is no `check_tenure_tx`/VRF/sortition binding visible in this function tying `pubkh` to a specific consensus hash or anchored block.

### Impact Explanation
If exploitable, this misdirects the poison slash/commission to the wrong block height's coinbase — a reward mis-payment, potentially penalizing an unrelated (later) tenure's miner rather than the tenure that actually produced conflicting headers, or allowing self-inflicted poisoning to be timed against a different, unrelated payout. This is bounded to coinbase reward redirection at one height per report and does not itself cause a chain split or MARF root divergence, since all nodes execute the same deterministic lookup and would agree on the (wrong) height.

### Likelihood Explanation
I was not able to fully verify two important preconditions with the available tools: (1) the exact overwrite semantics of the underlying pubkey-hash→height store (`insert_microblock_pubkey_hash`/`get_microblock_pubkey_hash_height` in `clarity_db.rs`, whose full body was not retrievable from the index), and (2) whether anchored-block validation elsewhere in the codebase enforces uniqueness of a microblock public key hash across the whole fork before it is ever inserted (which would fully block this key-reuse scenario at block-acceptance time rather than at poison-report time). Both are plausible existing guards that I could not confirm or rule out given index coverage limits.

### Recommendation
Given the verification gap, I cannot assert this as a confirmed exploitable bug. A background Devin session with full repository access should:
1. Read the complete implementation of `insert_microblock_pubkey_hash` and `get_microblock_pubkey_hash_height` in `clarity/src/vm/database/clarity_db.rs`.
2. Check anchored-block validation code (e.g. in `stackslib/src/chainstate/stacks/block.rs` or `db/blocks.rs`) for any uniqueness enforcement on `microblock_pubkey_hash` across a fork before an anchored block is accepted.
3. If no such guard exists, confirm whether `handle_poison_microblock` should additionally validate that both headers' `prev_block` (or the tenure/consensus hash that registered `pubkh`) match, before resolving `mblock_pubk_height`.

### Proof of Concept
Not constructed — pending confirmation of the underlying storage/uniqueness semantics described above. A concrete integration test would need to: register the same microblock private key in two disjoint tenures (two separate sortition wins), submit a `PoisonMicroblock` tx pairing headers exclusively from tenure 1, and assert whether the resulting `get_poison_microblock_report` height equals tenure 1's height or tenure 2's (latest-registration) height.

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L686-713)
```rust
    fn check_microblock_header_signer(
        mblock_hdr_1: &StacksMicroblockHeader,
        mblock_hdr_2: &StacksMicroblockHeader,
    ) -> Result<Hash160, Error> {
        let pkh1 = mblock_hdr_1.check_recover_pubkey().map_err(|e| {
            Error::InvalidStacksTransaction(
                format!("Failed to recover public key: {:?}", &e),
                false,
            )
        })?;

        let pkh2 = mblock_hdr_2.check_recover_pubkey().map_err(|e| {
            Error::InvalidStacksTransaction(
                format!("Failed to recover public key: {:?}", &e),
                false,
            )
        })?;

        if pkh1 != pkh2 {
            let msg = format!(
                "Invalid PoisonMicroblock transaction -- signature pubkey hash {} != {}",
                &pkh1, &pkh2
            );
            warn!("{}", &msg);
            return Err(Error::InvalidStacksTransaction(msg, false));
        }
        Ok(pkh1)
    }
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-758)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

        let microblock_height_opt = env
            .global_context
            .database
            .get_microblock_pubkey_hash_height(&pubkh)?;
        let current_height = env.global_context.database.get_current_block_height();
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L783-803)
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
                height
            }
        };
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L828-832)
```rust
                env.global_context.database.insert_microblock_poison(
                    mblock_pubk_height,
                    &sender_principal,
                    mblock_header_1.sequence,
                )?;
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L5518-5522)
```rust
            StacksChainState::insert_microblock_pubkey_hash(&mut conn, 1, &block_pubkh).unwrap();

            let height_opt =
                StacksChainState::has_microblock_pubkey_hash(&mut conn, &block_pubkh).unwrap();
            assert_eq!(height_opt.unwrap(), 1);
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L1027-1052)
```rust
        // was this block penalized for mining a forked microblock stream?
        // If so, find the principal that detected the poison, and reward them instead.
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
