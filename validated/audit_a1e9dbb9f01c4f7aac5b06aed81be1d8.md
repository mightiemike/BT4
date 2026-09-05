### Title
`handle_poison_microblock` accepts a signature-malleated pair of headers as a "double-sign" of a miner who signed only once - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`StacksChainState::check_microblock_header_signer` (used by `handle_poison_microblock`) recovers the signer's public key from each header using `check_recover_pubkey`, which hashes the header with the signature field zeroed out and calls `recover_to_pubkey_without_validating_low_s`. Because the signing digest excludes the signature bytes and low-S canonicalization is not enforced, anyone who observes a single legitimately-signed microblock header can publicly derive a second, syntactically different (different signature bytes) but cryptographically valid header for the exact same content via ECDSA signature malleability (`s' = n - s`, flipped recovery id), and submit both as a `PoisonMicroblock` "double-sign" even though the miner produced only one microblock.

### Finding Description
The invariant the system is supposed to enforce is: *slash ⇔ the accused miner produced two distinct, differently-signed microblocks*. The actual check performed is much weaker.

`StacksMicroblockHeader::check_recover_pubkey` (`stacks-codec/src/transaction.rs`) computes the signing digest by re-serializing the header with the signature field zeroed (`self.serialize(&mut bytes, true)`), then calls: [1](#0-0) 

Note that the digest depends only on `version`, `sequence`, `prev_block`, `tx_merkle_root` — not on the signature itself — and recovery uses `recover_to_pubkey_without_validating_low_s`, i.e. it explicitly skips the canonical low-S check that is normally used to reject the malleable counterpart of a signature.

`handle_poison_microblock` in `stackslib/src/chainstate/stacks/db/transactions.rs` relies on `check_microblock_header_signer` purely to establish that both headers were signed "by the same key": [2](#0-1) [3](#0-2) 

The only structural anti-fraud checks applied before this point (during `TransactionPayload::PoisonMicroblock` consensus-deserialize and mempool admission) verify that the two headers are not byte-for-byte identical, and that they share `sequence` or `prev_block` (to "identify a fork") — but these checks compare the header structs (including the signature field), not the *signed message*: [4](#0-3) 

Because ECDSA signatures are malleable — given any valid `(r, s)` over a message, anyone can compute the equally-valid `(r, n-s)` (with the complementary recovery id) without knowing the private key — an attacker who observes one real, honestly-signed microblock header `H` with signature `sig` can construct `H'` = `H` with signature `sig'` (the malleated counterpart). `H` and `H'`:
- Are not byte-identical (different signature bytes) → passes the "headers must differ" check.
- Have identical `sequence`/`prev_block` → passes the "must identify a fork" check (since it only rejects when *both* sequence and prev_block differ).
- Both recover to the *same* public-key hash under `check_recover_pubkey`, because the recovery digest ignores the signature field and the check does not enforce canonical (low-S) signatures.

Thus `PoisonMicroblock(H, H')` is accepted by `handle_poison_microblock` as a valid double-signature, even though the miner signed only one message once. The miner is slashed for something they never did.

### Impact Explanation
This breaks the stated invariant "slash == a valid, unreported double-signature under the miner's key." A completely honest, non-equivocating miner who has never signed two conflicting microblocks can be reported and slashed by anyone who has merely observed one of their broadcast microblocks, purely via a public, key-independent signature transformation. This is theft/unjust slashing of a miner's stake/reward, matching the "poison or reward mis-payment" / "block-reward theft" impact category. It is trivially repeatable against any miner who has ever signed a microblock (attacker only needs to see one signed header on the wire) and requires no stake, no majority position, and no compromise of any key.

### Likelihood Explanation
- Precondition: attacker observes at least one microblock header signed by a target miner's microblock key (this is broadcast data, always observable).
- Attacker cost: zero BTC/stake — the malleated signature is a pure, deterministic elliptic-curve computation on public data (`s' = n - s`, flip recovery bit).
- No majority stake, no signer/majority role, no privileged access required — fully consistent with the "unprivileged attacker, minority resources" threat model.
- Repeatable against any miner as long as `MINER_REWARD_MATURITY` window guard (checked via `mblock_pubk_height`) has not elapsed, which is satisfied for any recently active miner.

### Recommendation
- Enforce canonical (low-S) signatures when recovering the public key in `check_recover_pubkey` / `check_microblock_header_signer`, i.e. use a recovery function that validates low-S and rejects the malleable counterpart (mirroring what is done elsewhere for transaction signatures).
- Additionally/alternatively, require that `handle_poison_microblock` reject header pairs whose signing digests are identical (i.e., where the only difference between the two headers is the signature encoding of the same message) — a genuine double-sign must be over two distinct digests (different `tx_merkle_root` and/or `prev_block`/`sequence` combination that represents genuinely different microblock content), not merely two different byte-encodings of a signature over the same digest.

### Proof of Concept
Rust integration test plan (in `stackslib/src/chainstate/stacks/db/transactions.rs` test module, alongside `process_poison_microblock_reward`):
1. Generate a real `block_privk` and sign one legitimate `StacksMicroblockHeader` `mblock_1` (fixed `version`, `sequence`, `prev_block`, `tx_merkle_root`), recording its recoverable signature `(r, s, v)`.
2. Programmatically compute the malleated signature `(r, n-s, 1-v)` (secp256k1 order `n`) without using `block_privk`, and build `mblock_2` = `mblock_1` with this malleated signature substituted, leaving all other fields (`version`, `sequence`, `prev_block`, `tx_merkle_root`) identical.
3. Assert `mblock_1 != mblock_2` (headers differ only in `signature`) and `mblock_1.header.check_recover_pubkey().unwrap() == mblock_2.header.check_recover_pubkey().unwrap()` (both recover to the miner's real pubkey hash) — this is the equality violated: **"two headers recovering to the miner's key" ≠ "two distinct microblocks signed by the miner."**
4. Register the miner's `block_pubkh` via `StacksChainState::insert_microblock_pubkey_hash`.
5. Build and process a `TransactionPayload::PoisonMicroblock(mblock_1.header, mblock_2.header)` transaction via `StacksChainState::process_transaction` as in `process_poison_microblock_reward`.
6. Assert that processing **succeeds** and a poison report/slash is recorded via `StacksChainState::get_poison_microblock_report`, despite the miner never having produced two distinct microblocks — demonstrating the unjust slash.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L722-753)
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

```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L6844-6861)
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
```
