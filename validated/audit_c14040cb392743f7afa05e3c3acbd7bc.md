### Title
Poison-microblock report accepts a signature-malleated duplicate of a single honest microblock header, allowing false-equivocation slashing of an honest miner - (File: stacks-codec/src/transaction.rs, stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`StacksMicroblockHeader::check_recover_pubkey` recovers the signer's public key using `recover_to_pubkey_without_validating_low_s`, which deliberately skips the low-S canonical-signature check. Combined with the fact that the poison-microblock deserializer and `check_microblock_header_signer` never verify that the two headers commit to different content (only that the raw structs are byte-unequal and share a sequence number or parent), an attacker can take one honestly-signed microblock header, flip its ECDSA signature to the mathematically equivalent high-S/opposite-recid form (pure public-key arithmetic, no private key needed), and submit both as a `PoisonMicroblock` proof. The chain will treat this as proof that the miner double-signed, slash the miner's coinbase, and pay a commission to the attacker even though only one microblock was ever produced.

### Finding Description
The broken equality: "reward paid == exactly one valid, previously-unreported double-signature by the slashed miner" is not enforced; the code only checks that the two `StacksMicroblockHeader` values are byte-unequal and recover to the same pubkey-hash, not that they represent two distinct signed contents (i.e. different `tx_merkle_root`).

- `TransactionPayload::consensus_deserialize` for `PoisonMicroblock` only rejects the payload if the two headers are *byte-identical* (`h1 == h2`) or if they disagree on *both* `sequence` and `prev_block`. It never inspects `tx_merkle_root`: [1](#0-0) 

- `StacksMicroblockHeader::check_recover_pubkey` recovers the signer key via `recover_to_pubkey_without_validating_low_s`, explicitly skipping the low-S canonical check, and the digest it recovers against excludes the signature field entirely (serialized with `empty_sig = true`): [2](#0-1) 

- `Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s` / `recover_to_pubkey_possibly_with_low_s_verification(..., false)` performs no low-S normalization check before recovery, unlike the ordinary `verify` path which does enforce low-S: [3](#0-2) [4](#0-3) 

- `StacksChainState::check_microblock_header_signer` only compares the recovered `Hash160` values, never comparing `tx_merkle_root` or any other content field between the two headers: [5](#0-4) 

- `handle_poison_microblock` trusts this check, looks up `mblock_pubk_height` via `get_microblock_pubkey_hash_height`, and unconditionally records a poison report (paying commission at maturity) as long as pubkey hashes match and the report is better (lower sequence) than any existing one: [6](#0-5) 

Exploit flow: attacker observes one broadcast, honestly-signed `StacksMicroblockHeader` `h_real` with signature `(r, s, recid)` over digest `d = H(version, sequence, prev_block, tx_merkle_root)`. Standard ECDSA/secp256k1 recoverable-signature malleability lets anyone compute `s' = n - s` and `recid' = recid XOR 1` without the private key; `(r, s', recid')` is a valid signature over the same digest `d` that recovers to the identical public key. The attacker sets `h_fake = h_real` with `signature = (r, s', recid')`. Now:
- `h_fake != h_real` (signature bytes differ) → passes the "must differ" check.
- `h_fake.sequence == h_real.sequence` → passes the fork-shape check (only rejects when both sequence and prev_block differ).
- `check_recover_pubkey(h_real) == check_recover_pubkey(h_fake) == P` → passes `check_microblock_header_signer`.

No real second microblock, no genuine equivocation, and no private key access by the attacker were required. This bypasses every existing guard: the codec-level `h1 == h2` check (defeated because signature bytes differ), the pubkey-hash-match check (defeated because malleability preserves the recovered key), and the maturity-window check (irrelevant, since it only bounds *when* a report is accepted, not whether it's a genuine equivocation).

### Impact Explanation
This causes reward mis-payment: the honestly-behaving miner's coinbase is slashed (`poison_microblock_commission`) at maturity, part is paid to the attacker as reporter, and the remainder is burned, even though the miner signed only one microblock. This is deterministic and identically reproducible on every node (every node runs the same `handle_poison_microblock` logic against the same transaction), so it is not a fork/consensus divergence — it is a **network-wide, reproducible reward theft/loss** against a specific, targeted honest miner. This matches the Critical category "block-reward theft/double-payment/loss."

### Likelihood Explanation
- No privileged role required: any address that can afford a small transaction fee can submit the `PoisonMicroblock` transaction.
- Precondition is only that the miner has produced and broadcast one microblock (`h_real`) under a pubkey-hash `P` registered via `insert_microblock_pubkey_hash_height`/`get_microblock_pubkey_hash_height`, which is normal, expected behavior of any active miner.
- No knowledge of the miner's private key is needed — only public information (the broadcast header/signature) and pure elliptic-curve arithmetic (`s' = n - s`).
- Repeatable against every miner that has ever produced a microblock and not yet had a poison report filed for that key, within the `MINER_REWARD_MATURITY` window.
- Cost is a single Stacks transaction fee; no BTC spend, no stake, no signer majority needed.

### Recommendation
- In the `PoisonMicroblock` codec/consensus check, require that the two headers not merely differ in raw bytes but commit to genuinely different signed content (e.g., require `tx_merkle_root` to differ when `sequence` and `prev_block` match, or more directly require `check_recover_pubkey` to be computed and compared to expect two distinct signing events, not just byte-different structs).
- Have `check_recover_pubkey` / `check_microblock_header_signer` reject signatures unless normalized to low-S (use `recover_to_pubkey` instead of `recover_to_pubkey_without_validating_low_s` for poison-microblock verification), removing the malleability window entirely.
- Additionally normalize/canonicalize signatures before the "headers differ" equality check so that malleated duplicates of the same signature collapse to the same canonical representation and are rejected as identical.

### Proof of Concept
Rust integration test plan (in `stackslib/src/chainstate/stacks/db/transactions.rs` test module, alongside existing `process_poison_microblock_*` tests):
1. Generate `block_privk`/`block_pubkh`, register it via `StacksChainState::insert_microblock_pubkey_hash` at height H (as in `process_poison_microblock` test, e.g. see the existing test harness at [7](#0-6)  ).
2. Sign exactly one `StacksMicroblockHeader` `h_real` with `block_privk` (call `.sign()`), and record its `MessageSignature (r, s, recid)`.
3. Compute `s' = SECP256K1_ORDER - s`, `recid' = recid ^ 1`, construct `h_fake` identical to `h_real` except `signature = (r, s', recid')` (this can bypass any private key; pure curve arithmetic on `s`).
4. Assert `h_fake != h_real` (equality on struct) and assert `h_fake.check_recover_pubkey().unwrap() == h_real.check_recover_pubkey().unwrap() == block_pubkh`.
5. Build `TransactionPayload::PoisonMicroblock(h_real.clone(), h_fake.clone())`, serialize/deserialize through `StacksTransaction::consensus_deserialize` to confirm it is accepted (not rejected by the "microblock headers match" / "do not identify a fork" checks).
6. Call `StacksChainState::process_transaction` with this tx.
7. Assert `StacksChainState::get_poison_microblock_report(&mut conn, H).unwrap()` returns `Some((reporter_addr, h_real.sequence))`, proving a slash/commission was recorded despite only one microblock (`h_real`) ever having existed — i.e., the equality "one recorded poison ⇒ one real equivocation" is broken.

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

**File:** stacks-codec/src/transaction.rs (L2905-2924)
```rust
            TransactionPayloadID::PoisonMicroblock => {
                let h1: StacksMicroblockHeader = read_next(fd)?;
                let h2: StacksMicroblockHeader = read_next(fd)?;

                // must differ in some field
                if h1 == h2 {
                    return Err(codec_error::DeserializeError(
                        "Failed to parse transaction -- microblock headers match".to_string(),
                    ));
                }

                // must have the same sequence number or same block parent
                if h1.sequence != h2.sequence && h1.prev_block != h2.prev_block {
                    return Err(codec_error::DeserializeError(
                        "Failed to parse transaction -- microblock headers do not identify a fork"
                            .to_string(),
                    ));
                }

                TransactionPayload::PoisonMicroblock(h1, h2)
```

**File:** stacks-common/src/util/secp256k1/native.rs (L197-205)
```rust
    /// Recover message and signature to public key (will be compressed), while
    /// skipping validation that the signature is normalized to low-S. You shouldn't
    /// use this in new code.
    pub fn recover_to_pubkey_without_validating_low_s(
        msg: &[u8],
        sig: &MessageSignature,
    ) -> Result<Secp256k1PublicKey, &'static str> {
        Self::recover_to_pubkey_possibly_with_low_s_verification(msg, sig, false)
    }
```

**File:** stacks-common/src/util/secp256k1/native.rs (L282-290)
```rust
            // libsecp256k1 doesn't ensure that the S is low,
            // we have to do it ourselves
            let secp256k1_sig_standard = secp256k1_sig.to_standard();

            let mut secp256k1_sig_low_s = secp256k1_sig_standard;
            secp256k1_sig_low_s.normalize_s();
            if secp256k1_sig_low_s != secp256k1_sig_standard {
                return Err("Invalid signature: high-S");
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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L5509-5529)
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
            assert!(mblock_1 != mblock_2);
```
