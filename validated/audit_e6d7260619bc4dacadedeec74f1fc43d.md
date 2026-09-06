Based on my investigation, this is the key finding: `check_proposal_accepts_high_s_miner_sign` test explicitly documents that miner signatures with negated-S (ECDSA malleability) are **intentionally accepted** by design, and `miner_signature_hash_inner`/`signer_signature_hash_inner` in `stackslib/src/chainstate/nakamoto/mod.rs` commit to the raw `miner_signature` bytes as part of the `signer_signature_hash`, which in turn determines `block_hash()`/`block_id()`.

### Title
ECDSA miner-signature malleability allows two distinct block IDs for an identical, signer-approved block - (File: `stackslib/src/chainstate/nakamoto/mod.rs`)

### Summary
`NakamotoBlockHeader::block_hash()`/`block_id()` are derived from `signer_signature_hash_inner()`, which commits to the raw bytes of `miner_signature` [1](#0-0) . Miner-signature verification (`check_miner_signature`/`recover_miner_pk`) uses `recover_to_pubkey_without_validating_low_s`, which explicitly skips the high-S check that regular signature verification performs [2](#0-1) [3](#0-2) . Since ECDSA signatures are malleable (`s' = n - s` with flipped recovery-id parity recovers to the same public key), a miner can produce two different, both-valid `miner_signature` byte-strings for the same block content. This yields two different `signer_signature_hash` values and thus two different `block_id()`s for what is semantically the identical block (same txs, same state root, same tenure). The repo's own test helper `malleablize_signature` documents this precisely: "flipping the miner signature changes a block's id without changing its execution" [4](#0-3) , and the signer-side unit test `check_proposal_accepts_high_s_miner_sign` explicitly asserts that a high-S (malleated) miner signature is accepted by signer proposal validation [5](#0-4) .

### Finding Description
`signer_signature_hash_inner` includes `write_next(fd, &self.miner_signature)` in the preimage that signers sign over, and `block_hash()` reuses this same hash [6](#0-5) . Because `check_miner_signature` only checks that the recovered public key hash matches the expected miner (`recover_miner_pubkh` → `recover_to_pubkey_without_validating_low_s`) [7](#0-6) , both the low-S and the malleated high-S signature pass validation. The equality that should hold — "one committed block content ⇒ one `block_id`" — is broken: the miner (an unprivileged party who already legitimately won the sortition) can independently produce a second, equally-valid `miner_signature` for the exact same transactions/state root, changing `block_hash()`/`block_id()` without changing execution semantics.

This directly parallels the tiny-secp256k1 bug class: verification logic that fails to canonicalize/reject a non-unique signature encoding, letting a value pass `verify()`-equivalent checks that should be considered equivalent/rejected, breaking an invariant callers rely on (unique ID ↔ content).

### Impact Explanation
If the miner broadcasts/uses the two malleated variants inconsistently (e.g., proposes block A with signature variant 1 to get signer approval, but a different downstream path — such as a node validating an alternate propagation, or a later re-derivation depending on stored/re-signed headers — ends up with variant 2), two distinct `StacksBlockId`s exist for what should be one canonical block. Any code that indexes/keys state (staging DB, `parent_block_id` references, MARF/state root pinning, tenure lookup by block ID) by `block_id()` could disagree between nodes on which ID is "the" block for that tenure step, producing a **temporary tip disagreement** between honest nodes that saw different signature encodings, or an inability for a node to reproduce the expected block ID from re-signed content. This matches the "High" tier: a minority-triggerable validation divergence causing temporary tip disagreement, bounded to the miner who controls signature generation (an already-privileged-for-this-tenure but otherwise unprivileged actor — no majority collusion required).

### Likelihood Explanation
Likelihood is moderate: ECDSA signature malleability is trivial to compute (as shown by the repo's own `malleablize_signature` helper), and `recover_to_pubkey_without_validating_low_s` is used precisely because miner high-S signatures are intentionally allowed for compatibility (per the code comment on `check_proposal_accepts_high_s_miner_sign`) [8](#0-7) . The main constraint is that the miner must actually get two variants processed/stored by different nodes to realize the tip-disagreement impact, which requires some propagation-path divergence rather than pure signer-set collusion.

### Recommendation
Normalize (`normalize_s`) the miner signature before it is included in the header preimage/hash computation, or exclude `miner_signature` from the `signer_signature_hash`/`block_hash` preimage and instead bind the block ID to a canonical, malleability-free digest (e.g., a hash of the tx merkle root + state root + consensus hash without any signature bytes). Alternatively, enforce low-S canonicalization on `miner_signature` at block-acceptance time (as is already done for `secp256k1_verify`'s public API and for transaction signatures) rather than deliberately using `recover_to_pubkey_without_validating_low_s`.

### Proof of Concept
1. Miner builds a `NakamotoBlockHeader` with given txs/state root, calls `sign_miner(privk)` to get `miner_signature = sig`, computing `block_id_1 = header.block_id()`.
2. Using the repo's own `malleablize_signature(&sig)` helper [9](#0-8) , compute `sig' = malleablize_signature(&sig)`.
3. Set `header.miner_signature = sig'`; `check_miner_signature` still succeeds because `recover_to_pubkey_without_validating_low_s` recovers the same public key for `sig'` [2](#0-1) .
4. `header.block_id()` now differs (`block_id_2 != block_id_1`) because `signer_signature_hash_inner` commits to the signature bytes [10](#0-9) , despite identical transactions, state root, and consensus hash — demonstrating two valid, distinct IDs for one semantic block.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1026-1045)
```rust
    /// Inner calculation of the message digest for stackers to sign.
    /// This includes all fields _except_ the stacker signature.
    fn signer_signature_hash_inner(&self) -> Result<Sha512Trunc256Sum, CodecError> {
        let mut hasher = Sha512_256::new();
        let fd = &mut hasher;
        write_next(fd, &self.version)?;
        write_next(fd, &self.chain_length)?;
        write_next(fd, &self.burn_spent)?;
        write_next(fd, &self.consensus_hash)?;
        write_next(fd, &self.parent_block_id)?;
        write_next(fd, &self.tx_merkle_root)?;
        write_next(fd, &self.state_index_root)?;
        write_next(fd, &self.timestamp)?;
        write_next(fd, &self.miner_signature)?;
        write_next(fd, &self.pox_treatment)?;
        if Self::version_includes_problematic_txs(self.version) {
            write_next(fd, &self.problematic_txs)?;
        }
        Ok(Sha512Trunc256Sum::from_hasher(hasher))
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1047-1056)
```rust
    pub fn recover_miner_pk(&self) -> Option<StacksPublicKey> {
        let signed_hash = self.miner_signature_hash();
        let recovered_pk = StacksPublicKey::recover_to_pubkey_without_validating_low_s(
            signed_hash.bits(),
            &self.miner_signature,
        )
        .ok()?;

        Some(recovered_pk)
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1058-1069)
```rust
    pub fn block_hash(&self) -> BlockHeaderHash {
        // same as sighash -- we don't commit to signatures
        BlockHeaderHash(
            self.signer_signature_hash_inner()
                .expect("BUG: failed to serialize block header hash struct")
                .0,
        )
    }

    pub fn block_id(&self) -> StacksBlockId {
        StacksBlockId::new(&self.consensus_hash, &self.block_hash())
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1718-1758)
```rust
    /// Get the miner's public key hash160 from this signature
    pub(crate) fn recover_miner_pubkh(&self) -> Result<Hash160, ChainstateError> {
        let recovered_miner_pubk = self.header.recover_miner_pk().ok_or_else(|| {
            warn!(
                "Nakamoto Stacks block downloaded with unrecoverable miner public key";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id()
            );
            return ChainstateError::InvalidStacksBlock("Unrecoverable miner public key".into());
        })?;

        let recovered_miner_hash160 = Hash160::from_node_public_key(&recovered_miner_pubk);
        Ok(recovered_miner_hash160)
    }

    /// Verify the miner signature over this block.
    /// If this is a shadow block, then this is always Ok(())
    pub(crate) fn check_miner_signature(
        &self,
        miner_pubkey_hash160: &Hash160,
    ) -> Result<(), ChainstateError> {
        if self.is_shadow_block() {
            return Ok(());
        }

        let recovered_miner_hash160 = self.recover_miner_pubkh()?;
        if &recovered_miner_hash160 != miner_pubkey_hash160 {
            warn!(
                "Nakamoto Stacks block signature mismatch: {recovered_miner_hash160} != {miner_pubkey_hash160} from leader-key";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id()
            );
            return Err(ChainstateError::InvalidStacksBlock(
                "Invalid miner signature".into(),
            ));
        }

        Ok(())
    }
```

**File:** stacks-common/src/util/secp256k1/native.rs (L197-228)
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
```

**File:** stackslib/src/chainstate/nakamoto/tests/node.rs (L273-299)
```rust
/// Produce the other valid ECDSA encoding of a recoverable signature: the
/// high-S/low-S complement `s' = n - s`, with the recovery id's parity bit
/// flipped. It recovers to the same public key, so the signature remains valid,
/// but its bytes (and any hash committing to them) change. Used to construct
/// malleablized blocks: flipping the miner signature changes a block's id
/// without changing its execution.
fn malleablize_signature(sig: &MessageSignature) -> MessageSignature {
    // secp256k1 group order `n`, big-endian.
    const SECP256K1_ORDER_BE: [u8; 32] = [
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFE, 0xBA, 0xAE, 0xDC, 0xE6, 0xAF, 0x48, 0xA0, 0x3B, 0xBF, 0xD2, 0x5E, 0x8C, 0xD0, 0x36,
        0x41, 0x41,
    ];
    // MessageSignature layout: [recovery_id (1)][r (32)][s (32)].
    let mut bytes = sig.0;
    // s' = n - s, as a 32-byte big-endian subtraction.
    let mut borrow = 0i16;
    for i in (0..32).rev() {
        let diff = SECP256K1_ORDER_BE[i] as i16 - bytes[33 + i] as i16 - borrow;
        let (val, next_borrow) = if diff < 0 { (diff + 256, 1) } else { (diff, 0) };
        bytes[33 + i] = val as u8;
        borrow = next_borrow;
    }
    // Negating s flips the parity of R, so flip the recovery id's low bit.
    bytes[0] ^= 0x01;
    MessageSignature(bytes)
}
```

**File:** stacks-signer/src/chainstate/tests/v2.rs (L238-255)
```rust
#[test]
/// We no longer accept *transaction* signatures with high S (because they cause
/// txid malleability), but in *miner* signatures they're still allowed.
fn check_proposal_accepts_high_s_miner_sign() {
    let (stacks_client, mut signer_db, miner_sk, mut block, current_sortition, _, sortitions_view) =
        setup_test_environment(function_name!());
    block.header.consensus_hash = current_sortition.data.consensus_hash;

    block.header.miner_signature = miner_sk
        .sign(block.header.miner_signature_hash().as_bytes())
        .unwrap()
        .with_negated_s();
    assert_eq!(
        sortitions_view.check_proposal(&stacks_client, &mut signer_db, &block),
        Ok(()),
        "should validate"
    );
}
```
