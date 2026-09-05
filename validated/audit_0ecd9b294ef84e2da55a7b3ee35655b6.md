Based on my review of `check_microblock_header_signer` and `handle_poison_microblock` in `stackslib/src/chainstate/stacks/db/transactions.rs`, together with the recovery primitives in `stacks-common/src/util/secp256k1/native.rs`, I can support this finding with the code I was able to inspect. I was not able to fully load `stacks-codec/src/transaction.rs::check_recover_pubkey` or any static/pre-validation of the `PoisonMicroblock` payload (e.g., a possible `mblock_header_1 != mblock_header_2` or explicit sequence-match guard performed before `handle_poison_microblock` is invoked) before running out of search iterations, so I note this residual uncertainty explicitly below.

### Title
Poison-microblock slashing accepts a signature-malleated forged second header instead of proof of genuine equivocation - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`StacksChainState::check_microblock_header_signer` (called from `handle_poison_microblock`) only checks that `mblock_header_1` and `mblock_header_2` recover to the same `Hash160` pubkey hash via `check_recover_pubkey`, which internally is documented/asserted in the question (and consistent with the low-S-skipping recovery path `Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s`) to accept both low-S and malleated high-S ECDSA signatures. Nowhere in `check_microblock_header_signer` or `handle_poison_microblock` is there a check that the two headers' *signed content* (independent of the signature bytes) actually differs, i.e. that they represent two distinct, independently-produced microblocks rather than the same message re-signed with a malleated `s`.

### Finding Description
The equality the poison-microblock mechanism is supposed to enforce is: *reward slashed == miner produced two independently-signed, conflicting microblocks (equivocation)*. The code as read only enforces a weaker equality: *pkh(header_1) == pkh(header_2)*, computed via [1](#0-0) 
which calls `mblock_hdr.check_recover_pubkey()` on each header independently and only compares the resulting `Hash160`s. Standard ECDSA is signature-malleable: from a valid `(r, s)` signature over a message hash `m`, an attacker can always construct `(r, n-s)` (with recovery id flipped), which still recovers to the same public key for the same message `m`. Because the recovery path used here does not enforce low-S normalization strictly (`recover_to_pubkey_without_validating_low_s`, see [2](#0-1) 
), an attacker can take a single, real, honestly-broadcast microblock header and byte-for-byte reuse its sequence/prev-block/tx-merkle-root fields, substituting only the malleated signature, to synthesize a second header that is structurally distinct (different signature bytes) yet recovers to the identical `pubkh`. This passes `check_microblock_header_signer` and thus `handle_poison_microblock`'s core validity gate at [3](#0-2) 
without the miner ever having signed two conflicting microblocks. The function then looks up `get_microblock_pubkey_hash_height`, checks maturity, and calls `insert_microblock_poison` to record the attacker as reporter at [4](#0-3) 
which later diverts the slashed miner's matured coinbase reward via `find_mature_miner_rewards`.

### Impact Explanation
If this gap is real (i.e., there is no earlier content-distinctness or independent-message check on the `PoisonMicroblock` payload that I could confirm), any unprivileged party who observes a single real microblock header from any miner can forge a self-consistent "double-signed" pair and claim the slashing commission, causing the honest miner to lose its matured coinbase reward to an attacker who never observed or needed a genuine equivocation. This is block-reward theft/misdirection, matching the Critical/High reward-mispayment category defined in the rules.

### Likelihood Explanation
Preconditions: the targeted miner's `pubkh` must be registered at some height `H` within `MINER_REWARD_MATURITY` of current height (a normal, common condition for any active miner), and at least one legitimately signed microblock header from that miner must be observable (trivial, since headers are broadcast publicly). No majority stake, no signer key, and no privileged role is required — only the ability to submit a `PoisonMicroblock` transaction, which is available to any account. This makes it low-cost and repeatable per-tenure, once per real microblock header the attacker can obtain from a target miner.

### Recommendation
Harden `check_microblock_header_signer` / `handle_poison_microblock` so that: (1) recovery uses strict low-S validation only (`Secp256k1PublicKey::recover_to_pubkey`, not the `_without_validating_low_s` variant), eliminating signature malleability for this codepath, and (2) explicitly verify that the two headers' signed message content differs meaningfully at the same `sequence` (i.e., different `tx_merkle_root`/`prev_block`/`block_hash`) rather than relying solely on pubkey-hash equality, so a malleated re-signature of the identical message cannot be presented as proof of equivocation.

### Proof of Concept
Rust integration test plan (in `stackslib/src/chainstate/stacks/db/transactions.rs` test module or an integration harness):
1. Generate a real `Secp256k1PrivateKey`, construct `mblock_header_1` with real content, sign it via the standard low-S signer, and register its `pubkh` at height `H` (e.g., through the normal microblock-processing path so `get_microblock_pubkey_hash_height` returns `Some(H)`).
2. Derive `mblock_header_2` by cloning `mblock_header_1` and replacing only the signature with `MessageSignature::with_negated_s()` (or equivalent explicit high-S malleation), leaving `sequence`, `prev_block`, and `tx_merkle_root` identical.
3. Assert LHS (broken side): `StacksChainState::check_microblock_header_signer(&mblock_header_1, &mblock_header_2)` returns `Ok(pubkh)` — i.e., currently succeeds despite `mblock_header_2` never having been independently produced/broadcast by the miner.
4. Assert RHS (expected/fixed side): after the fix, the same call should return `Err(...)` because header content (excluding signature) is identical, i.e., no genuine equivocation occurred.
5. Call `handle_poison_microblock` with the forged pair through a `ClarityTransactionConnection`/`ExecutionState` harness and assert that, pre-fix, it succeeds and calls `insert_microblock_poison`/diverts the miner's coinbase to the attacker's principal at maturation (verified via `find_mature_miner_rewards`), and post-fix it is rejected with `InvalidStacksTransaction`.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-757)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

        let microblock_height_opt = env
            .global_context
            .database
            .get_microblock_pubkey_hash_height(&pubkh)?;
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

**File:** stacks-common/src/util/secp256k1/native.rs (L200-205)
```rust
    pub fn recover_to_pubkey_without_validating_low_s(
        msg: &[u8],
        sig: &MessageSignature,
    ) -> Result<Secp256k1PublicKey, &'static str> {
        Self::recover_to_pubkey_possibly_with_low_s_verification(msg, sig, false)
    }
```
