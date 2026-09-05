### Title
Poison-microblock report accepted without proof of an actual fork, enabling theft of an honest miner's coinbase reward - (File: `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Summary
`StacksChainState::handle_poison_microblock`, the function that actually applies a `PoisonMicroblock` transaction to chain state, only verifies that the two supplied microblock headers were signed by the same key via `check_microblock_header_signer`. It never checks that the two headers actually conflict (same sequence number with differing content, or a duplicate/forked parent link), which is the only thing that would prove a miner equivocated. Any two microblocks from a miner's normal, non-forked stream are signed by the same key and can be submitted as a "poison pair," causing the honest miner's coinbase to be slashed and a large fraction paid to an unrelated "reporter."

### Finding Description
The claimed equality that should hold is: *a `PoisonMicroblock` report is accepted only if `mblock_header_1` and `mblock_header_2` are proof of equivocation* (i.e., same `sequence` with different `tx_merkle_root`/hash, or same `prev_block` with different bodies — the actual fork conditions checked in `StacksChainState::validate_parent_microblock_stream`, e.g. duplicate-parent detection at `stackslib/src/chainstate/stacks/db/blocks.rs:2992-3011` and same-sequence-divergence detection at `blocks.rs:3018-3037`).

The on-chain validity check in `handle_poison_microblock` breaks that equality: [1](#0-0) 
`check_microblock_header_signer` only recovers each header's public key and asserts `pkh1 == pkh2`. It performs **no comparison of `sequence`, `prev_block`, or content** between the two headers. [2](#0-1) 
`handle_poison_microblock` calls only this signer check, then proceeds straight to recording the report using `mblock_header_1.sequence` as the "fork point," with no further validation that the pair actually conflicts: [3](#0-2) 

The equivalent, stricter check — that the two headers share the same `sequence`, `prev_block`, and `version` (i.e., are genuinely a fork pair) — exists **only** in the mempool admission path `will_admit_mempool_tx`, which is not re-invoked when a block containing the transaction is processed/validated: [4](#0-3) 

Because a miner (even one holding only a single leader slot) constructs their own block's transactions directly rather than going through `will_admit_mempool_tx`, and other nodes validate the block by calling `process_transaction_payload` → `handle_poison_microblock` (which lacks the fork-conflict check), any node accepts a `PoisonMicroblock(header_1, header_2)` transaction where `header_1` is the honest miner's real seq-0 microblock and `header_2` is any later, legitimately-published microblock (`seq = k`) from the same, non-forked stream. Both share the same signing key trivially (they are from the same honest stream), so `check_microblock_header_signer` succeeds, `get_microblock_pubkey_hash_height` finds the real anchoring height, the maturity check passes if within `MINER_REWARD_MATURITY`, and `insert_microblock_poison` records `seq=0` as the "fork point" even though no fork occurred.

`check_tenure_tx`, `verify_signer_signatures`, `validate_vrf_seed`, and MARF hashing are irrelevant to this path and do not defend against it, since this is purely a Clarity/transaction-processing semantic gap in `handle_poison_microblock`, not a sortition, VRF, or MARF-structural issue.

### Impact Explanation
`find_mature_miner_rewards` reads the (bogus) poison report and redirects the coinbase reward: the honest miner receives nothing, the attacker-controlled "reporter" principal is paid `POISON_MICROBLOCK_COMMISSION_FRACTION` (5%) of the coinbase, and the remaining 95% is destroyed: [5](#0-4) [6](#0-5) 

This is block-reward theft/loss: an honest miner's earned coinbase is permanently destroyed/misdirected to an unrelated attacker, with no actual protocol violation by the miner. It requires only a single unprivileged participant able to get a crafted transaction included in a block (their own mined block, or relayed if any downstream miner fails to re-check the fork condition before inclusion). This matches the Critical category ("block-reward theft/double-payment/loss").

### Likelihood Explanation
Preconditions are exactly as stated: a normal miner tenure with a microblock stream of length > 1, no actual fork needed. The attacker needs only to observe two publicly gossiped microblock headers (`seq=0` and `seq=k`) from the target miner's own stream — no signing key, no majority stake, no node-operator privilege. To have the transaction actually applied, the attacker needs it included in some validated block; a single miner slot (which the attacker is permitted to hold per the rules) is sufficient to include the crafted `PoisonMicroblock` transaction directly in their own mined block, bypassing the mempool's stricter `will_admit_mempool_tx` filter, since block validation on other nodes calls `handle_poison_microblock` directly without repeating that filter. This is fully repeatable against any miner during any tenure, at negligible attacker cost (transaction fee only).

### Recommendation
In `handle_poison_microblock` (or in `check_microblock_header_signer`), before accepting the report, enforce the same fork-legitimacy conditions used in `validate_parent_microblock_stream`/`will_admit_mempool_tx`: require `mblock_header_1.sequence == mblock_header_2.sequence` and either differing `tx_merkle_root`/signature with equal `prev_block`, or equal `sequence` with conflicting `prev_block` linkage — i.e., cryptographic proof that the same signer produced two distinct, conflicting microblocks at a common point in the stream, not merely two arbitrary microblocks signed by the same key.

### Proof of Concept
Rust integration test plan (chainstate-level, single-node harness):
1. Set up a `TestChainstateBuilder` chain, mine an anchored block with `mblock_privk` and a legitimate microblock stream of length ≥ 3 (`seq = 0, 1, 2`), all correctly signed and non-forked (`prev_block` chained correctly, distinct `tx_merkle_root`s).
2. Confirm the microblocks are accepted normally (`validate_parent_microblock_stream` returns `(len, None)` — no poison detected), establishing baseline: `equality_before`: honest miner's future coinbase == full scheduled `coinbase` (no poison report exists for `mblock_pubk_height`).
3. Construct a `TransactionPayload::PoisonMicroblock(microblocks[0].header.clone(), microblocks[2].header.clone())` — i.e., real `seq=0` and real `seq=2` headers, both signed by `mblock_privk`, from the legitimate (non-forked) stream.
4. Call `StacksChainState::process_transaction` directly with this crafted transaction (bypassing `will_admit_mempool_tx`), simulating inclusion in a block.
5. Assert it succeeds (`.unwrap()`) and `get_poison_microblock_report(&mut conn, mblock_pubk_height)` returns `Some((attacker_reporter_addr, 0))` — i.e., `equality_after` is broken: a poison record now exists despite no fork.
6. Advance chain height past `MINER_REWARD_MATURITY`, call `find_mature_miner_rewards`, and assert the honest miner's `MinerReward.coinbase == 0` while the attacker/reporter address balance increases by `coinbase * POISON_MICROBLOCK_COMMISSION_FRACTION / 100`, proving reward theft from an honest, non-forking miner.

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L684-713)
```rust
    /// Given two microblock headers, were they signed by the same key?
    /// Return the pubkey hash if so; return Err otherwise
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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-757)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

        let microblock_height_opt = env
            .global_context
            .database
            .get_microblock_pubkey_hash_height(&pubkh)?;
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L805-856)
```rust
        // add punishment / commission record, if one does not already exist at lower sequence
        let (reporter_principal, reported_seq) = if let Some((reporter, seq)) = env
            .global_context
            .database
            .get_microblock_poison_report(mblock_pubk_height)?
        {
            // account for report loaded
            env.add_memory(u64::from(TypeSignature::PrincipalType.size().map_err(
                |_| Error::Expects("Failed to get size of PrincipalType".into()),
            )?))
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

            // u128 sequence
            env.add_memory(16)
                .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

            if mblock_header_1.sequence < seq {
                // this sender reports a point lower in the stream where a fork occurred, and is now
                // entitled to a commission of the punished miner's coinbase
                debug!("Sender {} reports a better poison-miroblock record (at {}) for key {} at height {} than {} (at {})", &sender_principal, mblock_header_1.sequence, &pubkh, mblock_pubk_height, &reporter, seq;
                    "sender" => %sender_principal,
                    "microblock_pubkey_hash" => %pubkh
                );
                env.global_context.database.insert_microblock_poison(
                    mblock_pubk_height,
                    &sender_principal,
                    mblock_header_1.sequence,
                )?;
                (sender_principal, mblock_header_1.sequence)
            } else {
                // someone else beat the sender to this report
                debug!("Sender {} reports an equal or worse poison-microblock record (at {}, but already have one for {}); dropping...", &sender_principal, mblock_header_1.sequence, seq;
                    "sender" => %sender_principal,
                    "microblock_pubkey_hash" => %pubkh
                );
                (reporter, seq)
            }
        } else {
            // first-ever report of a fork
            debug!(
                "Sender {} reports a poison-microblock record at seq {} for key {} at height {}",
                &sender_principal, mblock_header_1.sequence, &pubkh, &mblock_pubk_height;
                "sender" => %sender_principal,
                "microblock_pubkey_hash" => %pubkh
            );
            env.global_context.database.insert_microblock_poison(
                mblock_pubk_height,
                &sender_principal,
                mblock_header_1.sequence,
            )?;
            (sender_principal, mblock_header_1.sequence)
        };
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L6844-6867)
```rust
            TransactionPayload::PoisonMicroblock(microblock_header_1, microblock_header_2) => {
                if microblock_header_1.sequence != microblock_header_2.sequence
                    || microblock_header_1.prev_block != microblock_header_2.prev_block
                    || microblock_header_1.version != microblock_header_2.version
                {
                    return Err(MemPoolRejection::PoisonMicroblocksDoNotConflict);
                }

                let microblock_pkh_1 = microblock_header_1
                    .check_recover_pubkey()
                    .map_err(|_e| MemPoolRejection::InvalidMicroblocks)?;
                let microblock_pkh_2 = microblock_header_2
                    .check_recover_pubkey()
                    .map_err(|_e| MemPoolRejection::InvalidMicroblocks)?;

                if microblock_pkh_1 != microblock_pkh_2 {
                    return Err(MemPoolRejection::PoisonMicroblocksDoNotConflict);
                }

                if !has_microblock_pubkey {
                    return Err(MemPoolRejection::NoAnchorBlockWithPubkeyHash(
                        microblock_pkh_1,
                    ));
                }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L868-895)
```rust

        // process poison -- someone can steal a fraction of the total coinbase if they can present
        // evidence that the miner forked the microblock stream.  The remainder of the coinbase is
        // destroyed if this happens.
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
                } else {
                    // users that helped a miner that reported a poison-microblock get nothing
                    (
                        StacksAddress::burn_address(mainnet),
                        StacksAddress::burn_address(mainnet).to_account_principal(),
                        0,
                        false,
                    )
                }
```

**File:** stackslib/src/chainstate/stacks/db/mod.rs (L938-939)
```rust
// fraction (out of 100) of the coinbase a user will receive for reporting a microblock stream fork
pub const POISON_MICROBLOCK_COMMISSION_FRACTION: u128 = 5;
```
