### Title
Poison-microblock proof accepted via ECDSA signature-malleated duplicate header, causing wrongful reward slashing - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`check_microblock_header_signer` and `handle_poison_microblock` only verify that both microblock headers recover to the same public-key hash; they never verify that the two headers actually commit to *different* content (e.g., different `tx_merkle_root`/`block_hash`) at the same sequence. Because ECDSA signatures are malleable (`s → n-s` with the recovery id flipped), an attacker can take any single validly-signed `StacksMicroblockHeader` from a miner, derive a second, bit-for-bit distinct header that differs only in signature bytes but recovers to the identical pubkey and commits to the identical microblock content, and submit that pair as a "poison" report to wrongfully slash the miner's coinbase and collect the reporter commission.

### Finding Description
The intended security property of the poison-microblock mechanism is:

`valid double-sign == two headers with DIFFERING block_hash / content commitments at the SAME sequence, both independently verifying under the miner's key`

The actual code only checks the "same key" half of this identity: [1](#0-0) 

`check_microblock_header_signer` recovers a pubkey hash from each header via `check_recover_pubkey()` and errors only if `pkh1 != pkh2`. It never compares `mblock_hdr_1`/`mblock_hdr_2` for content equality/inequality (no comparison of `tx_merkle_root`, `block_hash()`, or even full header equality).

`handle_poison_microblock` calls only this function and then proceeds directly to record a poison report and commission entitlement based on `mblock_header_1.sequence`: [2](#0-1) [3](#0-2) 

The only other place headers are compared is the mempool admission precheck, which checks that `sequence`, `prev_block`, and `version` are *equal* (to establish "same position in the stream") and that `pkh1 == pkh2`, but likewise never checks that the headers' content actually *differs* (no check on `tx_merkle_root` or `block_hash()` inequality): [4](#0-3) 

Because a Stacks microblock's signing hash (and its `block_hash()`) is computed over the header with the signature field held out, an ECDSA signature is malleable: given a valid `(r, s)` over that hash, `(r, n-s)` with the complementary recovery id is also a valid signature recovering to the *same* public key, over the *same* message (same `version`, `sequence`, `prev_block`, `tx_merkle_root`). This produces a `StacksMicroblockHeader` that is byte-for-byte distinct from the original (different `signature` field) yet commits to exactly the same microblock content and same signer — i.e., not an equivocation at all.

Exploit flow:
1. Attacker observes any legitimately published, validly-signed `StacksMicroblockHeader` from a target miner (public data, no private key needed).
2. Attacker computes the malleated signature variant `(r, n-s)` with flipped recovery id — a purely public-key operation, requiring no secret material.
3. Attacker submits `PoisonMicroblock(mblock_header_1, mblock_header_2)` where header_2 is the malleated variant.
4. The mempool precheck (`sequence`/`prev_block`/`version` equal, `pkh1==pkh2`) passes because these fields are identical by construction.
5. `check_microblock_header_signer` passes because `pkh1 == pkh2` (same signer, trivially, since it's the same underlying content/key).
6. `handle_poison_microblock` records the report and grants commission entitlement to the attacker, wrongly slashing the honest, non-equivocating miner.

### Impact Explanation
This is block-reward theft/loss: an honest miner who never double-signed a microblock has their coinbase reward wrongfully diverted to an attacker-controlled reporter address via the poison-microblock commission mechanism, with no genuine equivocation having occurred. This is repeatable against any miner for whom the attacker can observe at least one signed microblock header (which is the normal, public case for all miners), and requires no signer majority, no node compromise, and no private key access — only an unprivileged actor who can submit a transaction. This matches the "Critical: block-reward theft/double-payment/loss" category.

### Likelihood Explanation
Preconditions are minimal: the attacker needs (a) one publicly broadcast, validly-signed `StacksMicroblockHeader` from any miner and (b) the ability to submit a standard `PoisonMicroblock` transaction (any unprivileged account with enough balance to pay tx fee). ECDSA signature malleation (flipping `s` to `n-s` and the recovery id) is a purely public computation — no private key, no majority stake, no elevated role required. This is trivially feasible and repeatable against every miner that has ever produced a microblock.

### Recommendation
In `check_microblock_header_signer` (and/or `handle_poison_microblock`), in addition to checking `pkh1 == pkh2`, explicitly require that the two headers commit to genuinely different content at the same position — e.g., assert `mblock_hdr_1.block_hash() != mblock_hdr_2.block_hash()` (or equivalently compare `tx_merkle_root`) while still requiring identical `sequence`/`prev_block`/`version`. Apply the same content-inequality check in the mempool precheck (`stackslib/src/chainstate/stacks/db/blocks.rs`, the `PoisonMicroblock` match arm) so malleated-signature duplicates are rejected with `PoisonMicroblocksDoNotConflict` before ever reaching `handle_poison_microblock`.

### Proof of Concept
Rust test plan (integration test in `stackslib/src/chainstate/stacks/db/transactions.rs` test module):
1. Generate a miner keypair `block_privk`, sign a `StacksMicroblockHeader` (`mblock_1`) with fixed `sequence`, `prev_block`, `tx_merkle_root`, `version`.
2. Programmatically derive `mblock_2` as a signature-malleated variant of `mblock_1`: parse `mblock_1.signature` into `(r, s, recid)`, compute `s' = n - s`, `recid' = recid ^ 1`, and construct a new `MessageSignature` from `(r, s', recid')`, keeping every other field (`version`, `sequence`, `prev_block`, `tx_merkle_root`) identical to `mblock_1`.
3. Assert `mblock_1.block_hash() == mblock_2.block_hash()` and `mblock_1 != mblock_2` (bytes differ due to signature) — establishing the "same content, different signature bytes" precondition.
4. Build and sign a `TransactionPayload::PoisonMicroblock(mblock_1.header, mblock_2.header)` transaction from an attacker-controlled reporter key.
5. Call `StacksChainState::process_transaction` (which invokes `handle_poison_microblock`).
6. Assert that the call returns `Err(Error::InvalidStacksTransaction(..))` (fixed behavior) rather than `Ok(..)` with a poison report/commission recorded via `get_poison_microblock_report` (current, vulnerable behavior) — i.e., assert the equality `mblock_1.block_hash() == mblock_2.block_hash()` on both sides is used to reject the pair, instead of only checking recovered-pubkey equality.

### Citations

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L722-757)
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
