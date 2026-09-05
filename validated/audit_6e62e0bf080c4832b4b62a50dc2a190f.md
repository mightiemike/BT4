### Title
ECDSA signature malleability in `check_recover_pubkey` allows framing an honest miner with a self-malleated PoisonMicroblock, with no real equivocation - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`StacksChainState::check_microblock_header_signer` only verifies that both microblock headers recover to the same pubkey hash via `check_recover_pubkey`, which calls `recover_to_pubkey_without_validating_low_s` (no low-S/canonical-signature enforcement). Because the signed digest excludes the signature field, an attacker can derive a mathematically valid alternate signature `(r, n-s, flipped-recid)` for the *same* header content from a single observed, legitimately-broadcast microblock, producing a second `StacksMicroblockHeader` that is byte-identical except for its `MessageSignature`, yet recovers to the identical pubkey hash.

### Finding Description
The intended equality for a valid `PoisonMicroblock` transaction is: *"the reported headers represent two genuinely different microblocks (different content) independently signed by the same miner key."* This equality is broken.

- `StacksMicroblockHeader::sign` computes the digest over the header serialized with an empty signature field (`stacks-codec/src/transaction.rs:2570-2583`, `2585-2596`), so the digest depends only on `version/sequence/prev_block/tx_merkle_root`, never on the signature bytes. [1](#0-0) 
- `check_recover_pubkey` recovers the pubkey using `recover_to_pubkey_without_validating_low_s`, explicitly *not* enforcing canonical (low-S) signatures. [2](#0-1) 
- `check_microblock_header_signer` merely checks `pkh1 == pkh2`. [3](#0-2) 
- The on-chain execution path `handle_poison_microblock` calls only `check_microblock_header_signer`, then looks up when `pubkh` was registered and checks the `MINER_REWARD_MATURITY` window — it never checks that `mblock_header_1` and `mblock_header_2` differ in `tx_merkle_root`, or that they represent an actual competing fork (no `block_hash()` inequality check, no distinct-content check). [4](#0-3) 
- The mempool admission gate (`will_admit_mempool_tx`) is similarly permissive: it only checks `sequence`, `prev_block`, and `version` equality plus `pkh1 == pkh2` — it never requires the two headers' `tx_merkle_root` (or overall content) to differ. [5](#0-4) 

Exploit flow: an attacker observes a single legitimately-broadcast microblock header `H` (version, sequence, prev_block, tx_merkle_root, sig1) signed by the miner's key. Using standard secp256k1 malleability, the attacker computes `sig2 = (r, n-s mod n)` with the complementary recovery id — a purely public-key-arithmetic operation requiring no private key. The attacker sets `mblock_header_2 = H` with `signature = sig2`. Both `check_recover_pubkey(mblock_header_1)` and `check_recover_pubkey(mblock_header_2)` recover the identical `Hash160`, so `check_microblock_header_signer` returns success as if the miner had signed two different microblocks, even though only one microblock content was ever produced.

Existing guards do not stop this: `check_tenure_tx`/`verify_signer_signatures`/`validate_vrf_seed` are unrelated to this Clarity-level poison-report path; the sequence/prev_block/version equality checks in the mempool gate are trivially satisfied because `mblock_header_2` is a copy of `mblock_header_1`'s content; and `check_recover_pubkey`'s use of the non-low-S recovery function means the malleated signature is accepted as "valid" rather than rejected as non-canonical.

### Impact Explanation
An unprivileged attacker (no private key, no majority stake, only needs to observe one broadcast microblock) can submit a `PoisonMicroblock` transaction that `handle_poison_microblock` will accept as proof the miner double-signed. This records a poison report against the miner's registered `microblock_pubkey_hash`, entitling the reporter to "a commission of the punished miner's coinbase" per the code's own comment, and slashing/forfeiting the honest miner's reward for that tenure despite no actual equivocation having occurred. This is a false-positive slashing of an honest miner's coinbase/reward, matching the High-severity category of a poison/reward mis-payment. [6](#0-5) 

### Likelihood Explanation
Preconditions are minimal: the attacker needs only to observe one broadcast, validly-signed microblock header from any miner whose `microblock_pubkey_hash` is registered and still within the `MINER_REWARD_MATURITY` window; no BTC spend, no stake, and no privileged role are required — only the ability to submit a standard transaction (which any account can do). The exploit is a pure elliptic-curve arithmetic operation on public data and is repeatable against any miner's microblocks at will.

### Recommendation
Enforce canonical low-S signatures in `check_recover_pubkey` (use `recover_to_pubkey` with low-S validation, rejecting malleated/high-S signatures), and additionally require that the two `PoisonMicroblock` headers differ in substantive content (e.g., `tx_merkle_root` or overall `block_hash()`), both in `StacksChainState::check_microblock_header_signer`/`handle_poison_microblock` and in the mempool admission check in `blocks.rs`, so that identical-content headers with merely malleated signatures cannot be treated as proof of equivocation.

### Proof of Concept
Rust integration test outline (in `stackslib/src/chainstate/stacks/db/transactions.rs` test module):
1. Generate a miner keypair, sign a `StacksMicroblockHeader` `mblock_header_1` normally (`.sign(&privk)`), capturing `sig1 = (r, s, recid)`.
2. Parse `sig1` into its secp256k1 `r, s, recid` components; compute `s2 = n - s mod n` and `recid2 = 1 - recid`; construct `sig2` bytes as `MessageSignature`.
3. Clone `mblock_header_1` into `mblock_header_2`, replacing only the `signature` field with `sig2`. Assert `mblock_header_1.signature != mblock_header_2.signature` (different `MessageSignature`) while `mblock_header_1.version/sequence/prev_block/tx_merkle_root == mblock_header_2`'s respectively.
4. Call `StacksChainState::check_microblock_header_signer(&mblock_header_1, &mblock_header_2)` and assert it returns `Ok(pkh)` equal to `Hash160::from_node_public_key(&pubkey_of(privk))` — i.e. `pkh1 == pkh2` despite no genuine double-signing.
5. Extend to a full block-processing test analogous to `process_poison_microblock` (transactions.rs ~5509) using this malleated pair as the `PoisonMicroblock` payload, and assert `StacksChainState::process_transaction` succeeds and records a poison report for the honest miner's `pubkh`, demonstrating the false-positive slashing.

### Citations

**File:** stacks-codec/src/transaction.rs (L2570-2583)
```rust
    pub fn sign(&mut self, privk: &StacksPrivateKey) -> Result<(), AuthError> {
        self.signature = MessageSignature::empty();
        let mut bytes = vec![];
        self.consensus_serialize(&mut bytes)
            .expect("BUG: failed to serialize to a vec");

        let digest = Sha512Trunc256Sum::from_data(&bytes[..]);
        let sig = privk
            .sign(digest.as_bytes())
            .map_err(|se| AuthError::SigningError(se.to_string()))?;

        self.signature = sig;
        Ok(())
    }
```

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L722-803)
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
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L820-833)
```rust

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
