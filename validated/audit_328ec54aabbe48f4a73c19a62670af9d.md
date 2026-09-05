### Title
`handle_poison_microblock` accepts a duplicated (non-conflicting) microblock header pair as valid slash proof - (File: `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Summary
`StacksChainState::handle_poison_microblock` (invoked from `ClarityTransactionConnection::run_poison_microblock` during `process_transaction_payload`) only validates a `PoisonMicroblock` transaction by checking that both supplied headers recover to the *same* public key hash via `check_microblock_header_signer`. It never checks that the two headers actually differ, or that they share the same `sequence`/`prev_block`/`version` — the properties that define a genuine equivocation (double-sign). The stricter equality/difference check that exists in `stackslib/src/chainstate/stacks/db/blocks.rs` (`TransactionPayload::PoisonMicroblock` branch, lines 6844-6867) is only exercised by the mempool admission path (`will_admit_mempool_tx`), not by actual block/transaction processing.

### Finding Description
The invariant the system is supposed to enforce is:
```
slash(pubkey) == valid_and_unreported_double_signature_under(pubkey)
```
i.e. a slash record should only be created when two headers with the *same sequence number and same prev_block* (identifying a genuine fork point) are signed with the *same key* but have *different content* (different `tx_merkle_root`, hence different signatures over different data).

`handle_poison_microblock` (`stackslib/src/chainstate/stacks/db/transactions.rs:722-803`) implements this by calling:
```rust
let pubkh = StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;
```
`check_microblock_header_signer` (lines 686-713) does exactly one check: that `check_recover_pubkey()` on each header yields the same `Hash160`. It performs **no** comparison of `sequence`, `prev_block`, `version`, or overall header equality between the two headers.

Consequently, an attacker can take a single, legitimately broadcast, correctly-signed `StacksMicroblockHeader` from any honest miner (this is public data, observed on the network) and submit a `PoisonMicroblock` transaction where `mblock_header_1 == mblock_header_2` (byte-for-byte identical). Both headers trivially recover to the same pubkey hash (they are the same signed object), so `check_microblock_header_signer` succeeds even though no double-signature/fork ever occurred.

The equal-header/differing-header validation that *would* catch this (`microblock_header_1.sequence != microblock_header_2.sequence || ... != ... || ...` and the pubkey-hash-equal check culminating in `MemPoolRejection::PoisonMicroblocksDoNotConflict`) lives in `stackslib/src/chainstate/stacks/db/blocks.rs:6844-6867`, inside the function that computes whether the mempool will admit a transaction (`will_admit_mempool_tx`/`check_transaction_payload` type logic). This function is a **mempool gatekeeper**, not part of the on-chain state-transition function. An attacker who wins a single sortition slot (minority stake, no majority or Sybil resource needed) assembles their own block directly and is not required to submit the poison transaction through the mempool RPC path — they place the transaction straight into their own block's transaction list. `process_transaction_payload` (`stackslib/src/chainstate/stacks/db/transactions.rs:892` onward) dispatches `TransactionPayload::PoisonMicroblock` straight to `clarity_tx.run_poison_microblock(...)` → `handle_poison_microblock`, with no re-application of the mempool's stricter conflict check.

### Impact Explanation
This lets an unprivileged, minority-stake miner (or anyone who can get a transaction included in a block they mine) forge a punishment/slash record against an honest, **non-equivocating** miner whose microblock public key was merely observed on the network. Per the code's own comments ("account for a commission of the punished miner's coinbase"), a successful poison report entitles the reporter to a commission drawn from the targeted miner's matured coinbase reward. This is block-reward theft/mis-payment directed at an innocent party — matching the High/Critical "poison or reward mis-payment... theft" impact category. It is repeatable against any miner whose signed microblock header the attacker can observe (which is virtually all miners producing microblocks), and only requires the attacker to win one sortition (minority stake) to get their crafted transaction mined.

### Likelihood Explanation
Preconditions: the attacker needs (a) any previously broadcast, validly-signed `StacksMicroblockHeader` produced by a target miner (trivially obtainable — it's on the wire), and (b) the ability to get a transaction into a block, which for epoch-2.x miners just requires winning a single sortition with minority stake (as permitted by the threat model), or, in principle, direct block assembly rights. No majority stake, no signer collusion, and no private key of the victim is needed — the attacker duplicates the victim's own valid signature rather than forging a new one. This is a low-cost, low-skill, fully repeatable attack limited only by "one sortition slot."

### Recommendation
In `handle_poison_microblock` / `check_microblock_header_signer` (`stackslib/src/chainstate/stacks/db/transactions.rs`), enforce the same conflict-detection invariant that is currently only applied at mempool admission time:
- Reject if `mblock_header_1 == mblock_header_2` (or more precisely, require `sequence` and `prev_block` and `version` equal *and* the headers not be identical / must produce different `block_hash()`).
- Only after establishing that the headers identify a genuine fork (same sequence/prev_block, different content) should `check_microblock_header_signer` be applied to confirm same-key authorship.
This check must exist in the actual state-transition path (`process_transaction_payload`/`handle_poison_microblock`), not merely in the mempool's `will_admit_mempool_tx` gatekeeping, since miners can bypass the mempool entirely when assembling their own blocks.

### Proof of Concept
Rust integration test plan (extending existing tests in `stackslib/src/chainstate/stacks/db/transactions.rs`, e.g. near `process_poison_microblock_multiple_same_block`):
1. Generate `block_privk`/`block_pubkh` and insert `block_pubkh` via `StacksChainState::insert_microblock_pubkey_hash` at some height H (simulating an honest miner's microblock key being recorded).
2. Construct a single, validly-signed `StacksMicroblockHeader` (`mblock_1`) using `block_privk`, calling `.sign()`.
3. Clone it into `mblock_2` such that `mblock_1 == mblock_2` (identical fields/signature) — i.e. do **not** create a second, differently-signed header.
4. Build and sign a `TransactionPayload::PoisonMicroblock(mblock_1.header.clone(), mblock_2.header.clone())` transaction from an arbitrary reporter key, bypassing `will_admit_mempool_tx` entirely (call `StacksChainState::process_transaction` directly, as done in existing tests).
5. Assert on both sides of the invariant:
   - BEFORE: `StacksChainState::get_poison_microblock_report(&mut conn, H)` returns `None` (no slash record exists; equality "slash == valid double-sign" trivially holds vacuously).
   - Execute the transaction.
   - AFTER: assert `StacksChainState::process_transaction(...)` returns `Ok(...)` (i.e., is accepted) **and** `get_poison_microblock_report(&mut conn, H)` now returns `Some((reporter_addr, seq))` — demonstrating a slash record was created from two headers that are byte-identical (`mblock_1 == mblock_2`), i.e. NOT a valid double-signature. This breaks the equality `slash == valid double-sign`, and the reporter has an on-chain credential entitling them to the victim's coinbase commission despite the victim never having equivocated. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L1389-1408)
```rust
            TransactionPayload::PoisonMicroblock(ref mblock_header_1, ref mblock_header_2) => {
                // post-conditions are not allowed for this variant, since they're non-sensical.
                // Their presence in this variant makes the transaction invalid.
                if !tx.post_conditions.is_empty() {
                    let msg = "Invalid Stacks transaction: PoisonMicroblock transactions do not support post-conditions".to_string();
                    info!("{}", &msg);

                    return Err(Error::InvalidStacksTransaction(msg, false));
                }

                let cost_before = clarity_tx.cost_so_far();
                let res = clarity_tx.run_poison_microblock(
                    &origin_account.principal,
                    mblock_header_1,
                    mblock_header_2,
                )?;
                let mut cost = clarity_tx.cost_so_far();
                cost.sub(&cost_before)
                    .expect("BUG: running poison microblock tx has negative cost");

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

**File:** stackslib/src/clarity_vm/clarity.rs (L2511-2536)
```rust
    /// Evaluate a poison-microblock transaction
    pub fn run_poison_microblock(
        &mut self,
        sender: &PrincipalData,
        mblock_header_1: &StacksMicroblockHeader,
        mblock_header_2: &StacksMicroblockHeader,
    ) -> Result<Value, ClarityError> {
        self.with_abort_callback(
            |vm_env| {
                vm_env
                    .execute_in_env(sender.clone(), None, None, |exec_state, invoke_ctx| {
                        exec_state.run_as_transaction(invoke_ctx, |exec_state, invoke_ctx| {
                            StacksChainState::handle_poison_microblock(
                                exec_state,
                                invoke_ctx,
                                mblock_header_1,
                                mblock_header_2,
                            )
                        })
                    })
                    .map_err(ClarityError::from)
            },
            |_, _| None,
        )
        .map(|(value, ..)| value)
    }
```

**File:** stacks-codec/src/transaction.rs (L2598-2617)
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
