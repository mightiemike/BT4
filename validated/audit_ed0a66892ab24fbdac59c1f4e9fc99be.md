### Title
`handle_poison_microblock` slashes honest miners on non-conflicting same-key microblock header pairs - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`StacksChainState::handle_poison_microblock` and its helper `check_microblock_header_signer` only verify that `mblock_header_1` and `mblock_header_2` recover to the same public key hash; they never verify that the two headers actually conflict (e.g. same `sequence`/`prev_block` with differing content). Since every microblock in a tenure's stream is signed with the same ephemeral key, an attacker can pair any two genuinely sequential, non-conflicting microblock headers from an honest miner's real stream and have them processed as a valid "poison," wrongfully slashing that miner.

### Finding Description
The broken equality is: **"reward paid == exactly one valid, previously unreported double-signature"** vs. what the code actually enforces: **"reward paid == any two headers recoverable to the same pubkey."**

`check_microblock_header_signer` (transactions.rs:686-713) only compares `pkh1 != pkh2` after recovering each header's signer [1](#0-0) . `handle_poison_microblock` then reads only `mblock_header_1.sequence` to compare against the previously recorded `seq` and to store the report, never inspecting `mblock_header_2.sequence`, `prev_block`, or `tx_merkle_root` for divergence [2](#0-1) .

Because all microblocks published in one Stacks tenure share the same microblock signing key (committed once via `microblock_pubkey_hash` in the anchored block header), any two legitimately, sequentially published (non-forking) microblocks from an honest miner's public stream will trivially pass `check_microblock_header_signer`. There is no check anywhere in `handle_poison_microblock` requiring `mblock_header_1.sequence == mblock_header_2.sequence` and divergent `prev_block`/`tx_merkle_root`/signature (the actual definition of equivocation), nor a check that `prev_block` hashes collide from two different children.

Such a divergence/fork-conflict check does exist, but only at the **mempool admission** layer: `mempool_storage`'s payload check for `TransactionPayload::PoisonMicroblock` explicitly requires `sequence`, `prev_block`, and `version` to match between the two headers before accepting the tx into the mempool, returning `MemPoolRejection::PoisonMicroblocksDoNotConflict` otherwise [3](#0-2) . This is a mempool-ingress-only guard; it is not re-enforced inside `process_transaction_payload`/`handle_poison_microblock`, which is the actual consensus-critical execution path invoked when any block (including one self-mined by the attacker) is processed [4](#0-3) . Per the attacker model, the attacker may hold "a single miner slot," which is sufficient to assemble and mine a block containing a hand-crafted `PoisonMicroblock` transaction that never passes through mempool admission, bypassing the `PoisonMicroblocksDoNotConflict` check entirely.

Exploit flow:
1. Attacker observes two real, non-conflicting, sequential microblocks legitimately published by an honest miner during a tenure (e.g., sequence 5 and sequence 6, same key, distinct, non-forking `prev_block` chaining).
2. Attacker crafts a `PoisonMicroblock(header_5, header_6)` transaction with themselves as sender, and includes it directly in their own mined block (bypassing mempool checks).
3. Honest nodes validate and apply the block; `handle_poison_microblock` recovers matching pubkeys (trivially true) and records/pays a poison report for the honest miner's key, crediting the attacker a coinbase commission.

### Impact Explanation
This lets an unprivileged single-slot miner obtain a poison-microblock coinbase commission against an honest miner who never equivocated, i.e., a reward mis-payment/theft bounded to the target miner's coinbase commission. This matches the "High" impact tier defined in scope (poison or reward mis-payment bounded to fees). It does not cause a chain split, since all honest nodes apply the same (flawed) logic deterministically and agree on the resulting state — the harm is a wrongful, but consistently-applied, reward transfer.

### Likelihood Explanation
The attacker needs: (a) a single miner slot to mine one block (already permitted in the attacker model), and (b) visibility into any honest miner's own non-conflicting sequential microblock stream (which is publicly broadcast). No stolen keys, no majority stake, and no privileged role are required. This is repeatable against any miner who publishes microblocks, once per tenure/pubkey-height window (subject to the `MINER_REWARD_MATURITY` window check at transactions.rs:783-803).

### Recommendation
In `handle_poison_microblock` (or `check_microblock_header_signer`), enforce the same genuine-fork/conflict predicate used by mempool admission before treating the pair as poison evidence: require `mblock_header_1.sequence == mblock_header_2.sequence` and that the headers differ in content (`prev_block`, `tx_merkle_root`, or `signature`), or alternatively that they share `prev_block` while being distinct children — mirroring the logic in `blocks.rs`'s `MemPoolRejection::PoisonMicroblocksDoNotConflict` check — directly inside the consensus execution path, not only at mempool ingress.

### Proof of Concept
Rust integration test plan (chainstate-level, bypassing mempool):
1. Set up a chainstate; register an honest miner's microblock pubkey hash via `insert_microblock_pubkey_hash` at some height, mirroring existing test setup patterns [5](#0-4) .
2. Construct two microblocks signed by the same key that are genuinely sequential/non-conflicting: `sequence = 5, prev_block = H4` and `sequence = 6, prev_block = block_hash(mblock_5)` — i.e., a valid, non-forking two-block stream (not the `assert!(mblock_1 != mblock_2)` same-sequence-fork pattern used in existing tests).
3. Build a `TransactionPayload::PoisonMicroblock(header_5, header_6)` transaction and call `StacksChainState::process_transaction` directly (as is done in the existing `handle_poison_microblock` tests), bypassing `mempool_storage`'s admission check entirely.
4. Assert on both sides of the equality:
   - Expected (secure) side: `process_transaction` should return `Err(Error::InvalidStacksTransaction(..))` because the headers do not identify a real fork.
   - Actual (current) side: assert that `process_transaction` succeeds and `get_poison_microblock_report` returns a report crediting the attacker's `reporter_principal`, proving the miner was wrongfully slashed despite no equivocation.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L1389-1413)
```rust
            TransactionPayload::PoisonMicroblock(ref mblock_header_1, ref mblock_header_2) => {
                // post-conditions are not allowed for this variant, since they're non-sensical.
                // Their presence in this variant makes the transaction invalid.
                if !tx.post_conditions.is_empty() {
                    let msg = "Invalid Stacks transaction: PoisonMicroblock transactions do not support post-conditions".to_string();
                    info!("{}", &msg);

                    return Err(Error::InvalidStacksTransaction(msg, false));
                }

                let cost_before = clarity_tx.cost_so_far();
                let res = clarity_tx.run_poison_microblock(
                    &origin_account.principal,
                    mblock_header_1,
                    mblock_header_2,
                )?;
                let mut cost = clarity_tx.cost_so_far();
                cost.sub(&cost_before)
                    .expect("BUG: running poison microblock tx has negative cost");

                let receipt =
                    StacksTransactionReceipt::from_poison_microblock(tx.clone(), res, cost);

                Ok(receipt)
            }
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L5509-5528)
```rust
        for (dbi, burn_db) in ALL_BURN_DBS.iter().enumerate() {
            let mut conn = chainstate.block_begin(
                *burn_db,
                &FIRST_BURNCHAIN_CONSENSUS_HASH,
                &FIRST_STACKS_BLOCK_HASH,
                &ConsensusHash([(dbi + 1) as u8; 20]),
                &BlockHeaderHash([(dbi + 1) as u8; 32]),
            );

            StacksChainState::insert_microblock_pubkey_hash(&mut conn, 1, &block_pubkh).unwrap();

            let height_opt =
                StacksChainState::has_microblock_pubkey_hash(&mut conn, &block_pubkh).unwrap();
            assert_eq!(height_opt.unwrap(), 1);

            // make poison
            let mblock_1 =
                make_signed_microblock(&block_privk, &privk, BlockHeaderHash([0x11; 32]), 123);
            let mblock_2 =
                make_signed_microblock(&block_privk, &privk, BlockHeaderHash([0x11; 32]), 123);
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L6844-6868)
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
            }
```
