### Title
Poison-microblock consensus check never verifies the two headers actually conflict, allowing signature-malleability/duplicate-header forgery to slash a non-equivocating miner - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`check_microblock_header_signer` and `handle_poison_microblock` only verify that `mblock_header_1` and `mblock_header_2` recover to the same public-key hash; neither this function nor the mempool admission check (`will_admit_mempool_tx`) verifies that the two headers actually represent distinct, conflicting microblock content (e.g., differing `tx_merkle_root`). Because `check_recover_pubkey()` uses `recover_to_pubkey_without_validating_low_s`, an attacker who observes a single miner-signed microblock can derive a second, byte-distinct but mathematically-malleated signature for the *same* message that recovers to the identical pubkey hash, and submit it as "evidence" of equivocation that never occurred.

### Finding Description
The broken equality: **poison-microblock reward paid == a genuine, previously-unreported double-signature by the slashed miner**. The code only checks pkh1 == pkh2, not "header1 and header2 encode two distinct, conflicting microblocks."

Path:
1. `check_recover_pubkey()` [1](#0-0)  recovers a pubkey from an arbitrary `(r,s,v)` signature without enforcing canonical low-s form (`recover_to_pubkey_without_validating_low_s`). Given one valid signature `(r, s, v)` produced by the miner over digest `d`, the malleated pair `(r, n-s, 1-v)` is also a mathematically valid ECDSA signature over the same digest `d`, computable by anyone with no knowledge of the private key, and it recovers to the exact same public key / `Hash160`.
2. `check_microblock_header_signer` [2](#0-1)  only compares `pkh1 != pkh2` and returns the shared pubkey hash; it never compares the header contents (`sequence`, `prev_block`, `tx_merkle_root`) for genuine divergence, nor rejects `mblock_header_1 == mblock_header_2` (bit-identical) or signature-malleated duplicates.
3. `handle_poison_microblock` [3](#0-2)  proceeds directly from the signer check to `get_microblock_pubkey_hash_height`, maturity-window check, and `insert_microblock_poison`, with no additional equivocation-content check.
4. The mempool-level gate `will_admit_mempool_tx` [4](#0-3)  checks `sequence`, `prev_block`, `version` equality and `pkh1 == pkh2`, but likewise never checks `tx_merkle_root` divergence or header non-identity — so it does not filter out a duplicated/malleated single-microblock pair either.

Exploit flow: attacker observes the one microblock the miner actually published at sequence `n` (header `H`, signature `sig`). Attacker computes the malleated signature `sig' = (r, n-s, 1-v)` for the same digest, builds `H' = H` with `signature = sig'`. Both `H.check_recover_pubkey()` and `H'.check_recover_pubkey()` equal the miner's pubkey hash `pkh`. Attacker submits `PoisonMicroblock(H, H')`. All existing guards (mempool sequence/prev_block/version check, `check_microblock_header_signer`, maturity-window check) pass, even though no genuine second, conflicting microblock was ever produced by the miner.

### Impact Explanation
`insert_microblock_poison` records a slash/report against the miner's `pubkh` at the microblock's height with zero true equivocation. Downstream, this poison record is consumed by miner-reward maturation logic to redirect/forfeit the miner's coinbase and pay a commission to the false "reporter" — this is a reward-theft / unfair-slashing outcome affecting a specific miner's coinbase and payout to an arbitrary unprivileged reporter, matching the Critical "block-reward theft" category. It is repeatable against any observed single-microblock miner output and requires no majority stake — a lone attacker who merely observes one broadcast microblock can forge the second header offline.

### Likelihood Explanation
Preconditions are minimal and attacker-controllable: the attacker needs to see exactly one already-broadcast, validly signed microblock header from a miner (public information, no privileged access needed) and be able to broadcast a standard `PoisonMicroblock` transaction, both well within the "unprivileged reporter" threat model. No majority stake, no signer key, no node compromise is required — only a public secp256k1 signature-malleability computation, which is cheap and deterministic. This makes the attack fully feasible and repeatable across every miner that has ever signed at least one microblock still within the `MINER_REWARD_MATURITY` window.

### Recommendation
`check_microblock_header_signer` (or `handle_poison_microblock`) should additionally require that the two headers constitute genuine, non-identical conflicting evidence, e.g.:
- Reject if `mblock_header_1 == mblock_header_2` (including signature bytes) or if their signatures are canonicalized-equal (normalize `s` to low-s and recovery bit before comparing, so malleated duplicates collapse to "same signature").
- Require `mblock_header_1.tx_merkle_root != mblock_header_2.tx_merkle_root` (or another content field) while `sequence`/`prev_block` match, matching the genuine-fork construction used in `validate_parent_microblock_stream`.
- Enforce canonical low-s signature verification in `check_recover_pubkey` (reject non-canonical / malleated signature encodings) rather than `recover_to_pubkey_without_validating_low_s`.

### Proof of Concept
Rust integration test plan (in `stackslib/src/chainstate/stacks/db/transactions.rs` test module):
1. Generate `block_privk`, sign one legitimate `StacksMicroblockHeader` `H` (sequence `s`, fixed `prev_block`/`tx_merkle_root`) with `H.sign(&block_privk)`.
2. Extract `H.signature` as `(r, s_val, v)`; compute the malleated triplet `(r, n - s_val, 1 - v)` over secp256k1 order `n`; construct `H2 = H.clone()` with `signature` replaced by the malleated bytes.
3. Assert `H.check_recover_pubkey().unwrap() == H2.check_recover_pubkey().unwrap()` and assert `H.tx_merkle_root == H2.tx_merkle_root` and `H != H2` only in the `signature` field (i.e., no genuine second microblock exists).
4. Register `insert_microblock_pubkey_hash` for `Hash160::from_node_public_key(&pubkey)` at some height.
5. Build and sign a `TransactionPayload::PoisonMicroblock(H, H2)` from an unrelated reporter key; call `StacksChainState::process_transaction`.
6. Assert the transaction succeeds (no `InvalidStacksTransaction` error) and `StacksChainState::get_poison_microblock_report` returns `Some((reporter, s))`, proving a slash was recorded despite `H` and `H2` encoding the identical microblock content signed exactly once.

### Citations

**File:** stacks-codec/src/transaction.rs (L2598-2616)
```rust
    pub fn check_recover_pubkey(&self) -> Result<Hash160, AuthError> {
        let mut bytes = vec![];
        self.serialize(&mut bytes, true)
            .expect("BUG: failed to serialize to a vec");
        let digest = Sha512Trunc256Sum::from_data(&bytes[..]);

        let mut pubk = StacksPublicKey::recover_to_pubkey_without_validating_low_s(
            digest.as_bytes(),
            &self.signature,
        )
        .map_err(|_ve| {
            AuthError::VerifyingError(
                "Failed to verify signature: failed to recover public key".to_string(),
            )
        })?;

        pubk.set_compressed(true);
        Ok(Hash160::from_node_public_key(&pubk))
    }
```

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L722-856)
```rust
    pub fn handle_poison_microblock(
        env: &mut ExecutionState,
        invoke_ctx: &InvocationContext,
        mblock_header_1: &StacksMicroblockHeader,
        mblock_header_2: &StacksMicroblockHeader,
    ) -> Result<Value, Error> {
        let cost_before = env.global_context.cost_track.get_total();

        // encodes MARF reads for loading microblock height and current height, and loading and storing a
        // poison-microblock report
        runtime_cost(ClarityCostFunction::PoisonMicroblock, env, 0)
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

        let sender_principal = match &invoke_ctx.sender {
            Some(ref sender) => {
                if let PrincipalData::Standard(sender) = sender.clone() {
                    sender
                } else {
                    panic!(
                        "BUG: tried to handle poison microblock without a standard principal sender"
                    );
                }
            }
            None => {
                panic!("BUG: tried to handle poison microblock without a sender");
            }
        };

        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

        let microblock_height_opt = env
            .global_context
            .database
            .get_microblock_pubkey_hash_height(&pubkh)?;
        let current_height = env.global_context.database.get_current_block_height();

        // for the microblock public key hash we had to process
        env.add_memory(20)
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

        // for the block height we had to load
        env.add_memory(4)
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

        // was the referenced public key hash used anytime in the past
        // MINER_REWARD_MATURITY blocks?
        let mblock_pubk_height = match microblock_height_opt {
            None => {
                // public key has never been seen before
                let msg = format!(
                    "Invalid Stacks transaction: microblock public key hash {} never seen in this fork",
                    &pubkh
                );
                warn!("{}", &msg;
                      "microblock_pubkey_hash" => %pubkh
                );

                return Err(Error::InvalidStacksTransaction(msg, false));
            }
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
