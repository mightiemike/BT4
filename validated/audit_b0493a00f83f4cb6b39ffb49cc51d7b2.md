### Title
Trailing malformed `signer_signature` entry causes `verify_signer_signatures` to reject an already-sufficiently-signed block - ([File: stackslib/src/chainstate/nakamoto/mod.rs])

### Summary
`NakamotoBlockHeader::verify_signer_signatures` iterates over every entry in `signer_signature` and aborts the whole function with `ChainstateError::InvalidStacksBlock` as soon as any single signature fails to recover a public key, even if the cumulative weight of the signatures processed before that point already met or exceeded the approval threshold. Because individual signer signatures only sign the header hash and do not commit to the contents or length of the `signer_signature` vector itself, an attacker relaying the block can append one bogus/unrecoverable signature (e.g. `MessageSignature::empty()`) to an otherwise validly-signed block and cause every node that validates that copy to reject it.

### Finding Description
The broken equality is: "a block whose legitimate, distinct signer weight already reaches the approval threshold is accepted, independent of any additional/trailing garbage bytes appended to `signer_signature`." Concretely:

- The per-signature loop at [1](#0-0)  calls `Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s` for every entry in `self.signer_signature` and immediately propagates any recovery failure via `map_err(...)?`.
- Threshold and weight accounting happen only *after* the loop finishes, at [2](#0-1) : `total_weight_signed` is accumulated inside the loop, and the `< threshold` check is performed only once the loop has iterated (successfully) over the entire vector.
- There is no early-exit once `total_weight_signed >= threshold`; the function must process the entire signature list before it can decide the block is valid.
- Because `signer_signature_hash`/`block_hash` (used to build `signer_signature`'s message and the block identity) do not cover the `signer_signature` field itself, any party relaying the block — not just the original signers — can append arbitrary bytes to `signer_signature` without invalidating the legitimate signatures already present, and without changing the block's `block_id`.

Attacker's exact input: take a block that already carries enough valid, correctly-ordered signer signatures to clear `compute_voting_weight_threshold`, and append one more entry equal to `MessageSignature::empty()` (or any byte string that is not a valid recoverable ECDSA signature over the header's sighash) to the end of `signer_signature`.

Exploit flow: attacker rebroadcasts this modified `NakamotoBlock` to peers. Any node that calls `verify_signer_signatures` on this modified copy hits the failing recovery at the appended entry and returns `Err(...)` from the `?` operator before reaching the threshold check, so the block is rejected as `InvalidStacksBlock` even though the legitimate signatures inside it already satisfied consensus.

Existing guards do not prevent this: `check_tenure_tx`/block acceptance paths call `verify_signer_signatures` as an opaque validity check and have no separate mechanism verifying that "sufficient signatures already accumulated" should short-circuit malformed trailing entries; the ordering check (`last_index`) and duplicate-signature handling only operate on entries that successfully recover a key, so they do nothing to protect against an unrecoverable entry aborting the whole function.

### Impact Explanation
Any node/relayer that only sees (or that an attacker specifically feeds) the tampered copy of an otherwise validly-signed Nakamoto block will reject that block outright, while nodes that receive the untampered original will accept it and advance their tip. This produces a temporary tip disagreement across the network — nodes fed the poisoned copy stall at the parent tip until they obtain a byte-identical, untampered copy of the block, while other nodes progress. This matches the "High" severity category (minority-triggerable, temporary tip disagreement) described in scope, since a single unprivileged relayer/attacker can trigger it against any node they can feed blocks to, with no signer-majority or privileged role required.

### Likelihood Explanation
Preconditions: a Nakamoto block whose signer signatures already legitimately clear the 70% weight threshold must exist (this happens on every ordinary tenure/block). The attacker only needs the ability to relay/rebroadcast blocks to peers (an unprivileged capability explicitly in scope) and to append one byte blob to the `signer_signature` vector before forwarding it — no BTC spend, no signer key, no majority stake required. This is trivially repeatable for every block the attacker can intercept or race to broadcast first to a target node.

### Recommendation
Change `verify_signer_signatures` so that a single malformed/unrecoverable signature does not abort the entire check when sufficient legitimate weight has already been accumulated: either (a) reject the block outright if *any* entry in `signer_signature` fails to recover to a valid, expected-signer public key (i.e., treat malformed signatures as fatal for the whole block, which is arguably the safer semantic and should be explicitly documented/tested), or (b) if trailing/extra unrecognized entries are meant to be tolerated, skip non-recovering entries via `continue` instead of `?`, and only fail if total weight never reaches threshold. Given the existing duplicate/ordering checks assume a well-formed vector, option (a) combined with an explicit test asserting current behavior is likely the intended semantics — but this should be confirmed against the signer/miner code that constructs `signer_signature`, since if malformed trailing entries are never expected to be producible by an honest miner, the fix should instead ensure a single node's rejection cannot diverge from other nodes' view of the same canonical block bytes (e.g., by having block-relay code canonicalize the signature vector before validation, or by validating any received block only after confirming byte-for-byte reproducibility against the miner's original announcement).

### Proof of Concept
Rust integration test outline in `stackslib/src/chainstate/nakamoto/mod.rs` tests module:
1. Build a `RewardSet` with N signers and known weights such that a subset S of signers' weight already exceeds `compute_voting_weight_threshold(total_weight)`.
2. Construct a `NakamotoBlockHeader`, compute `signer_signature_hash()`, and have each signer in S sign it in increasing `signer_index` order, populating `header.signer_signature`.
3. Assert `header.verify_signer_signatures(&reward_set, epoch_id)` returns `Ok(w)` with `w >= threshold` (baseline: equality holds).
4. Append `MessageSignature::empty()` to `header.signer_signature`.
5. Call `header.verify_signer_signatures(&reward_set, epoch_id)` again and assert it now returns `Err(ChainstateError::InvalidStacksBlock(_))`, demonstrating that a block whose legitimate signer weight already met the threshold is rejected solely due to one trailing malformed signature appended after validation-sufficiency was already reached — breaking the claimed equality.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1133-1143)
```rust
        for signature in self.signer_signature.iter() {
            let public_key = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                message.bits(),
                signature,
            )
            .map_err(|_| {
                ChainstateError::InvalidStacksBlock(format!(
                    "Unable to recover public key from signature {}",
                    signature.to_hex()
                ))
            })?;
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1175-1189)
```rust
            total_weight_signed = total_weight_signed
                .checked_add(signer.weight)
                .expect("FATAL: overflow while computing signer set threshold");
        }

        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }

        return Ok(total_weight_signed);
```
