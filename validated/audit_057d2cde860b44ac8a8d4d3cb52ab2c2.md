### Title
Poison-microblock reward theft via ECDSA signature malleability - two headers with identical content but malleated signatures both recover to the same honest miner's pubkey hash without any real equivocation ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`StacksChainState::check_microblock_header_signer` (called from `handle_poison_microblock`) only verifies that the two supplied `StacksMicroblockHeader`s recover to the same `Hash160` via `check_recover_pubkey`, which itself calls `recover_to_pubkey_without_validating_low_s` — a signature-malleable, non-low-S-enforcing recovery. An attacker who observes a single, genuinely-signed microblock header from a miner can mathematically derive a second, differently-signed but content-identical header (same `tx_merkle_root`, `sequence`, `prev_block`, `version`) whose signature is the standard ECDSA malleated variant `(r, n-s, 1-recid)`. Both headers recover to the same pubkey hash, satisfying every check in the code path, even though the miner never signed two conflicting microblocks.

### Finding Description
The claimed equality is: `poison_microblock reward paid == exactly one valid double-signature by the slashed miner verified under that miner's key` (i.e., the miner must have signed two *distinct, conflicting* microblocks at the same `(prev_block, sequence)`).

The actual code path:
- `check_microblock_header_signer` [1](#0-0)  only recovers `pkh1`/`pkh2` independently via `check_recover_pubkey` and asserts `pkh1 == pkh2`. It never checks that the headers are semantically distinct (e.g., different `tx_merkle_root`), nor does it re-derive the "conflict" beyond what the mempool pre-check already loosely validated.
- `check_recover_pubkey` [2](#0-1)  uses `recover_to_pubkey_without_validating_low_s`, which explicitly skips the low-S canonicalization check [3](#0-2) . For any valid ECDSA signature `(r, s, recid)` over a message `m`, the malleated signature `(r, n-s, 1-recid)` is also a valid recoverable signature for the *same* message `m` and recovers to the *same* public key — this requires no private key, only public arithmetic on an already-observed signature.
- The only other pre-check, in the mempool rejection logic, requires `sequence`, `prev_block`, and `version` to match, and requires the recovered pubkey hashes to match — but never requires the two headers to differ in content (e.g. `tx_merkle_root`) [4](#0-3) .
- `handle_poison_microblock` then checks only that the pubkey hash was live within `MINER_REWARD_MATURITY` and unconditionally calls `insert_microblock_poison` [5](#0-4) , recording the report with no verification that an actual fork/equivocation occurred.

Exploit flow: attacker observes one legitimately broadcast, signed `StacksMicroblockHeader` from a miner whose `microblock_pubkey_hash` is still within the maturity window. Attacker computes the malleated signature variant of that header's signature (public math only), producing `header_2` that is byte-different from `header_1` (so it is not rejected as a literal duplicate) but has an identical `tx_merkle_root`/`sequence`/`prev_block`/`version`, and recovers to the identical pubkey hash. Attacker submits `TransactionPayload::PoisonMicroblock(header_1, header_2)`. All checks pass, and `get_poison_microblock_report`/`insert_microblock_poison` record a poison report against the miner even though the miner signed only one microblock at that sequence — no genuine fork ever existed.

Existing guards do not catch this: `check_tenure_tx`, `validate_vrf_seed`, and MARF hashing are irrelevant to this path; the maturation window (`MINER_REWARD_MATURITY`) only limits *when* the report can be filed, not *whether* it represents a real equivocation; no code anywhere in `handle_poison_microblock` or the mempool pre-check enforces low-S signatures or content divergence between the two headers.

### Impact Explanation
When the report matures, `find_mature_miner_rewards` looks up `get_poison_microblock_report` and, if present, redirects the miner's coinbase commission to the reporter via `calculate_miner_reward` [6](#0-5) , with the remainder burned. This is a direct block-reward theft/loss from an honest, non-equivocating miner's coinbase, repeatable against any miner whose signed microblock header the attacker can observe, requiring only public information — no stake, no majority position, no other party's key.

### Likelihood Explanation
Preconditions: a `microblock_pubkey_hash` entry alive within `MINER_REWARD_MATURITY` blocks (normal operating condition for any active miner using microblocks), and the attacker needs to have seen exactly one broadcast, validly signed microblock header from that miner (trivially available since all microblocks/headers are broadcast on the P2P network). No BTC spend, no stake, no privileged role, no majority signers required — this is exploitable by any unprivileged participant who can submit a transaction, matching the "unprivileged reporter" threat model exactly.

### Recommendation
- Enforce low-S canonical signatures in `check_recover_pubkey`/`StacksMicroblockHeader::verify` paths used for poison-microblock validation (use `recover_to_pubkey_possibly_with_low_s_verification` with `verify_low_s = true`, or explicitly reject high-S signatures for poison evidence).
- Additionally, require that `mblock_header_1` and `mblock_header_2` differ in `tx_merkle_root` (or otherwise represent genuinely conflicting content) before accepting a `PoisonMicroblock` transaction, both in the mempool pre-check (`blocks.rs`) and in `check_microblock_header_signer`/`handle_poison_microblock`.

### Proof of Concept
Rust integration test plan:
1. Generate a miner keypair and register its pubkey hash via `insert_microblock_pubkey_hash` at some height H, within `MINER_REWARD_MATURITY` of current height.
2. Construct one genuine `StacksMicroblockHeader` (`header_1`) with a given `tx_merkle_root`, sign it with the miner's key to obtain `(r, s, recid)`.
3. Compute the malleated signature `(r, n-s, 1-recid)` purely from public values (no private key) to build `header_2`, identical to `header_1` in `version`/`sequence`/`prev_block`/`tx_merkle_root`, differing only in `signature` bytes.
4. Assert `header_1.check_recover_pubkey().unwrap() == header_2.check_recover_pubkey().unwrap()` (equality side 1: signature-check passes).
5. Assert there was no genuine second microblock ever produced/signed by the miner for that `(prev_block, sequence)` (equality side 2: no real equivocation).
6. Submit `TransactionPayload::PoisonMicroblock(header_1, header_2)` from an unprivileged reporter account via `StacksChainState::process_transaction`.
7. Assert `StacksChainState::get_poison_microblock_report(&mut conn, H)` returns `Some((reporter_addr, sequence))`, demonstrating a poison report was recorded despite the broken equality — the miner is slashed for a fabricated "double-signature" that was in fact a single genuine signature and its public malleation.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-856)
```rust
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

**File:** stacks-common/src/util/secp256k1/native.rs (L207-239)
```rust
    fn recover_to_pubkey_possibly_with_low_s_verification(
        msg: &[u8],
        sig: &MessageSignature,
        verify_low_s: bool,
    ) -> Result<Secp256k1PublicKey, &'static str> {
        _secp256k1.with(|ctx| {
            let msg = LibSecp256k1Message::from_slice(msg).map_err(|_e| {
                "Invalid message: failed to decode data hash: must be a 32-byte hash"
            })?;

            let secp256k1_sig = sig
                .to_secp256k1_recoverable()
                .ok_or("Invalid signature: failed to decode recoverable signature")?;

            if verify_low_s {
                let secp256k1_sig_standard = secp256k1_sig.to_standard();
                let mut secp256k1_sig_low_s = secp256k1_sig_standard;
                secp256k1_sig_low_s.normalize_s();
                if secp256k1_sig_low_s != secp256k1_sig_standard {
                    return Err("Invalid signature: high-S");
                }
            }

            let recovered_pubkey = ctx
                .recover_ecdsa(&msg, &secp256k1_sig)
                .map_err(|_e| "Invalid signature: failed to recover public key")?;

            Ok(Secp256k1PublicKey {
                key: recovered_pubkey,
                compressed: true,
            })
        })
    }
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

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L869-895)
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
