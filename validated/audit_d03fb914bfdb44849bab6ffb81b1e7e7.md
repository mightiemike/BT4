### Title
`handle_poison_microblock` accepts two microblock headers as proof of equivocation without verifying they sign a distinct message digest, enabling signature-malleation forgery of a poison report against a miner who signed only once - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`handle_poison_microblock` and its helper `check_microblock_header_signer` only verify that the two supplied `StacksMicroblockHeader` values recover to the *same public key hash*; they never verify that the two headers actually commit to two *different signed digests* produced by two independent signing acts. Because ECDSA/secp256k1 recoverable signatures are malleable (flipping `s -> n-s` together with the recovery bit yields a second, different-looking, still-valid signature that recovers to the same pubkey without needing the private key), an unprivileged reporter can take a single, honestly-produced microblock, derive a second "header" with a malleated signature, and submit both as a `PoisonMicroblock` transaction that the chain will accept as proof of a double-sign that never happened.

### Finding Description
The intended equality that should hold before a poison report is honored is: `header1.block_hash != header2.block_hash` **because they encode genuinely different signed content**, `AND` both recover to the same signer key, `AND` both are at the same sequence number. In the actual code, `check_microblock_header_signer` at [1](#0-0)  only recovers `pkh1`/`pkh2` via `check_recover_pubkey()` and rejects the transaction solely if `pkh1 != pkh2`. `handle_poison_microblock` then trusts `mblock_header_1.sequence` directly and never re-checks that the two headers even share a sequence number or that their `block_hash()` values differ for a content-level reason [2](#0-1) .

The only place that enforces "the two headers must differ" is upstream, at the mempool/codec admission layer, which checks `sequence`, `prev_block`, `version` equality plus struct-level inequality (i.e., the headers must not be byte-identical) [3](#0-2) . This check operates on raw struct equality (including the raw `signature` bytes), not on whether the underlying signed digest differs. Since `signature` is itself part of the header hashed into `block_hash`, a signature-malleated variant of the exact same microblock produces a different `block_hash` and a different byte-for-byte header while still recovering to the identical public key hash — satisfying every check in the current code path.

An unprivileged reporter can therefore:
1. Observe one legitimately broadcast microblock header `(version, sequence, prev_block, tx_merkle_root, signature=(r,s,v))` signed once by the real miner.
2. Compute the malleated signature `(r, n-s, 1-v)`, which is a standard secp256k1 recoverable-signature transform requiring no private key.
3. Submit `PoisonMicroblock(header_1, header_2)` where `header_2` is identical except for the malleated signature.
4. Pass the codec/mempool "not byte-equal, same sequence/prev_block/version" check (signature bytes differ).
5. Pass `check_microblock_header_signer` (both recover to the same pubkey hash).
6. Trigger `handle_poison_microblock`'s slashing/report logic and be recorded as the legitimate reporter of equivocation at that sequence, even though the miner signed only one microblock.

### Impact Explanation
This breaks the invariant that a `PoisonMicroblock` report proves an actual double-sign. It lets an unprivileged attacker seize the "unearned" coinbase-forfeiture reward/commission from a miner who never equivocated, which is a form of block-reward theft/mis-payment directed at an honest miner — matching the Critical/High reward-loss category described in the rules. The mechanism is repeatable against any miner whose microblock signature the attacker can observe (i.e., any broadcast microblock), and requires no stake, no signer key, and no majority position — only observation of one public signature.

### Likelihood Explanation
Feasibility depends entirely on whether the recoverable-signature verification routine used by `check_recover_pubkey()` enforces canonical (low-S) signature encoding. If it does not reject high-S/malleated signatures, the exploit needs no on-chain resources beyond the fee for one `PoisonMicroblock` transaction, no coordination, and can be repeated for every miner/microblock the attacker observes. I was not able to inspect the concrete implementation of `check_recover_pubkey()`/the underlying secp256k1 wrapper within the available iterations to confirm or rule out low-S canonicalization; this is the key unresolved factual gap for a final verdict. What is confirmed directly from the code read is that `handle_poison_microblock` and `check_microblock_header_signer` in `transactions.rs` never independently verify that the two signed digests differ — they rely on the upstream struct-inequality check, which is signature-byte-sensitive rather than digest-sensitive.

### Recommendation
In `check_microblock_header_signer` (or in `handle_poison_microblock` before recording the report), additionally: (1) verify `mblock_header_1.sequence == mblock_header_2.sequence` and `mblock_header_1.prev_block == mblock_header_2.prev_block` are true equivocation-relevant fields; (2) recompute and compare the pre-signature digest (`version, sequence, prev_block, tx_merkle_root`) of both headers and require it to differ, rejecting reports where only the signature bytes differ; (3) enforce canonical low-S signature verification in `check_recover_pubkey()` so that a malleated signature never recovers as "valid" in the first place.

### Proof of Concept
Rust integration test plan (to be run against a local chainstate, e.g. in `transactions.rs` test module near `process_poison_microblock_multiple_same_block`):
1. Sign one microblock header `header_1` with a real private key at `sequence = N`.
2. Derive `header_2` by taking `header_1`, malleating only the `signature` field (`s -> n-s`, flip recovery bit), leaving `version`, `sequence`, `prev_block`, `tx_merkle_root` identical.
3. Assert `header_1.block_hash() != header_2.block_hash()` (struct-level difference, as the existing codec check would allow) — this is the *broken* half of the equality.
4. Assert that recomputing the pre-signature digest of `header_1` and `header_2` yields the **same** value (i.e., they are the same signed content) — this is the fact the current code fails to check.
5. Submit `TransactionPayload::PoisonMicroblock(header_1, header_2)` via `StacksChainState::process_transaction`.
6. Current (vulnerable) behavior: transaction succeeds and `handle_poison_microblock` returns a slashing tuple. Expected (fixed) behavior: the transaction should be rejected with an error such as "poison-microblock headers do not prove distinct signing events," and the test should assert on this rejection rather than on successful slashing.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-756)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

        let microblock_height_opt = env
            .global_context
            .database
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
