### Title
Poison-microblock slashing can be triggered on a single miner-signed microblock via an ECDSA signature-malleable duplicate header, without a genuine equivocation - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`check_microblock_header_signer` and `handle_poison_microblock` only verify that the two supplied `StacksMicroblockHeader`s recover to the same public-key hash; they never check that the header *content* (`prev_block`, `tx_merkle_root`, `version`) actually differs between the two headers. Combined with the fact that the only "are these headers actually the same" guard is a byte-for-byte struct equality check (which includes the signature bytes), an attacker who computes a signature-malleable variant of an already-published microblock header (same signed digest, different valid `MessageSignature` bytes/recovery id) can submit that pair as a "poison" and get the miner slashed even though the miner only ever produced one microblock.

### Finding Description
The claimed-broken equality is: `slashed == (mblock_header_1 and mblock_header_2 represent two distinct microblocks signed by the same key at the same sequence position)`.

`check_microblock_header_signer` (transactions.rs:686-713) recovers a pubkey hash from each header independently via `check_recover_pubkey` and only rejects when `pkh1 != pkh2` [1](#0-0) . It never compares `tx_merkle_root`, `prev_block`, or `version` between the two headers.

`handle_poison_microblock` (transactions.rs:722-856) uses only this pubkey check, then unconditionally treats the pair as proof of equivocation and inserts a poison report keyed on `mblock_header_1.sequence` [2](#0-1) [3](#0-2) . There is no field-level content check inside this Clarity-level handler at all.

The only "are the two headers actually different" gate in the whole pipeline is the mempool pre-check in `blocks.rs`, which checks `sequence`, `prev_block`, and `version` equality (required to prove a fork at the same position) and pubkey-hash equality, but does **not** check that `tx_merkle_root` differs and does not check that the signatures differ meaningfully: [4](#0-3) . The consensus-level "duplicate header" rejection is a full-struct byte-equality check ("microblock headers match"), which is demonstrated in the codec test to only fire when the entirety of both headers — including the 65-byte `signature` field — is byte-identical: [5](#0-4) .

Because ECDSA (secp256k1, recoverable form used for `MessageSignature`) is malleable — flipping `s -> n-s` together with the recovery id yields a second, distinct-byte signature that recovers to the identical public key over the identical signed digest — an attacker can take one honestly-published microblock header, derive its malleable twin (same `version`, `sequence`, `prev_block`, `tx_merkle_root`; different `signature` bytes), and submit `PoisonMicroblock(header, header_malleable)`:
1. Deserialization's full-struct equality check does not fire (signature bytes differ), so it is not rejected as "headers match".
2. The mempool content check passes (`sequence`, `prev_block`, `version` match, pubkeys match) since `tx_merkle_root` equality isn't checked at all and isn't disqualifying either way.
3. `check_microblock_header_signer` recovers the same pubkey hash for both (malleable-signature recovery preserves the pubkey), so it passes.
4. `handle_poison_microblock` proceeds to slash the miner and pay a commission to the "reporter", despite the miner never having produced two distinct microblocks.

None of `check_tenure_tx`, `verify_signer_signatures`, `validate_vrf_seed`, or the MARF hashing path intervenes here — this is purely a Clarity native-function code path invoked from a `TransactionPayload::PoisonMicroblock` transaction, and none of those guards apply to this payload type.

### Impact Explanation
This is a poison/reward mis-payment bounded to fees: an unprivileged attacker can force `handle_poison_microblock` to mark a miner's microblock public key hash as poisoned and redirect a portion of that miner's future coinbase/fees to the attacker's principal, without the miner ever having equivocated. This matches the "High" category ("a poison or reward mis-payment bounded to fees") from the rules — it is a wrongful slashing/mis-payment, not a chain split, and its blast radius is bounded to the targeted miner's coinbase-fraction per report.

### Likelihood Explanation
The attacker needs only: (1) visibility of one legitimately broadcast microblock header signed by the target miner (trivially obtainable from the P2P network), and (2) the ability to compute the standard secp256k1 signature-malleability transform and submit a `PoisonMicroblock` transaction with a normal fee — no stake, no majority position, no privileged role. This is repeatable against any miner as soon as they publish a microblock, and can be repeated across many miners, matching the "potentially escalating... if repeated across many miners at scale" note in the prompt's own scoped-impact statement.

### Recommendation
In `check_microblock_header_signer` (or in `handle_poison_microblock` before processing), explicitly require that the non-signature fields of the two headers differ in a way that constitutes a real fork — i.e., assert `mblock_hdr_1.sequence == mblock_hdr_2.sequence && mblock_hdr_1.prev_block == mblock_hdr_2.prev_block && mblock_hdr_1.tx_merkle_root != mblock_hdr_2.tx_merkle_root`, and reject the transaction as `InvalidStacksTransaction` otherwise. Additionally, enforce canonical low-S signatures on microblock headers (mirroring the `TransactionAuthVerificationMode::EnforceLowS` handling already used for transaction auth) so that a malleable resignature of the same digest cannot be constructed at all.

### Proof of Concept
Rust integration test plan (in `stackslib/src/chainstate/stacks/db/transactions.rs` test module, alongside the existing `test_process_poison_microblock` test using `make_signed_microblock`):
1. Generate a single valid microblock `mblock_1` signed by `block_privk` (as in the existing test at transactions.rs:5525-5528).
2. Construct `mblock_2` by cloning `mblock_1.header`, then transforming `signature` into its ECDSA-malleable twin (`s' = n - s`, flipped recovery id) so that `mblock_2.header.check_recover_pubkey()` still recovers the same pubkey hash but `mblock_2.header != mblock_1.header` only due to signature bytes, and `mblock_1.header.tx_merkle_root == mblock_2.header.tx_merkle_root`, `prev_block` and `sequence` equal.
3. Assert on both sides of the equality: (a) BEFORE fix — build `TransactionPayload::PoisonMicroblock(mblock_1.header, mblock_2.header)`, run it through `StacksChainState::process_transaction`, and observe it succeeds and returns a poison receipt (`report_opt` populated, `reporter` credited) — proving slashing occurred without a genuine two-distinct-microblock equivocation. (b) AFTER fix — assert the same call now returns `Err(Error::InvalidStacksTransaction(..))` because `tx_merkle_root` (or full content) is identical between the two headers, confirming the fix restores `slashed == genuine equivocation`.

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L690-712)
```rust
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
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-756)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

        let microblock_height_opt = env
            .global_context
            .database
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L821-855)
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

**File:** stacks-codec/src/transaction.rs (L3817-3828)
```rust
        // Deserializing two equal microblock headers must fail (they should differ
        // for a valid poison proof).
        let mut payload_bytes_equal = vec![TransactionPayloadID::PoisonMicroblock as u8];
        let equal_header = header_bytes([0x00, 0x34], 0, 2, 2);
        payload_bytes_equal.extend_from_slice(&equal_header);
        payload_bytes_equal.extend_from_slice(&equal_header);
        assert!(
            TransactionPayload::consensus_deserialize(&mut &payload_bytes_equal[..])
                .unwrap_err()
                .to_string()
                .contains("microblock headers match")
        );
```
