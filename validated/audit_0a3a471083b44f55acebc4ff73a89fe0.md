### Title
Missing distinctness check in `PoisonMicroblock` allows slashing an honest miner with a duplicated microblock header - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`StacksChainState::check_microblock_header_signer` and `StacksChainState::handle_poison_microblock` only verify that both supplied microblock headers recover to the **same signer public key**; neither function verifies that `mblock_header_1` and `mblock_header_2` are actually distinct (different content/hash) at the claimed sequence. An attacker can submit the same, single legitimately-mined microblock header twice as both halves of a `TransactionPayload::PoisonMicroblock` and have it accepted as proof of equivocation.

### Finding Description
The claimed equality is: `poison reward paid == exactly one valid double-sign (two different headers, same sequence, same signer)`.

`check_microblock_header_signer` only checks:
```
pkh1 = mblock_hdr_1.check_recover_pubkey()
pkh2 = mblock_hdr_2.check_recover_pubkey()
if pkh1 != pkh2 { return Err(...) }
Ok(pkh1)
``` [1](#0-0) 

If `mblock_header_1` and `mblock_header_2` are byte-identical, `check_recover_pubkey()` on the identical bytes trivially returns the same public key hash for both — this is not evidence of equivocation, it's the same signature verified twice.

`handle_poison_microblock` then:
1. Calls `check_microblock_header_signer` (the only "is this valid" gate) at [2](#0-1) .
2. Looks up whether `pubkh` was ever used by a real miner (`get_microblock_pubkey_hash_height`), checks the maturity window, and — critically — never compares `mblock_header_1` against `mblock_header_2` for content/hash inequality, nor validates that they share the same sequence number by any independent means beyond trusting the caller's data [3](#0-2) .
3. It then unconditionally inserts a poison-microblock report crediting the sender with a commission of the "punished" miner's coinbase, based solely on `mblock_header_1.sequence` [4](#0-3) .

Because a real miner legitimately signs one microblock header per sequence with their microblock pubkey, and that header's signature is publicly recoverable from the header data itself, any observer can copy that single header and submit it twice as `(hdr, hdr.clone())`. `check_microblock_header_signer` will report `pkh1 == pkh2` (trivially true, since it's the same object), and nothing downstream rejects the report for lacking a genuine second, conflicting header. This breaks the intended equality: a poison report is recorded and a slash is applied even though no equivocation (two different signed streams at the same sequence) ever existed.

### Impact Explanation
This results in block-reward theft: an honest miner who mined exactly one microblock at a given sequence has their future coinbase maturation slashed via `get_microblock_poison_report` / `insert_microblock_poison`, and the attacker who filed the duplicate-header report becomes entitled to a commission from that stolen coinbase. This matches the "Critical: block-reward theft/double-payment/loss" impact category, since it moves reward from the legitimate miner to an unrelated reporter without any real fork or double-signing.

### Likelihood Explanation
The precondition is trivial and requires no privilege beyond broadcasting a transaction: the attacker only needs to observe any already-published, legitimately-signed microblock header (these are broadcast on the P2P network / included in blocks) and resubmit it as both `mblock_header_1` and `mblock_header_2` in a `PoisonMicroblock` transaction via `StacksChainState::process_transaction`. No majority stake, no signer key, and no additional BTC spend is required — this is fully within the described unprivileged attacker capability ("file poison reports"). It is repeatable against any miner's microblock pubkey hash that is still within the `MINER_REWARD_MATURITY` window.

### Recommendation
In `check_microblock_header_signer` (or immediately before it in `handle_poison_microblock`), add an explicit distinctness check: require `mblock_hdr_1.sequence == mblock_hdr_2.sequence` (same slot) AND that the two headers are NOT byte-identical / do not have the same block hash (e.g., compare `mblock_hdr_1.block_hash() != mblock_hdr_2.block_hash()` or the full serialized header), rejecting the transaction with `Error::InvalidStacksTransaction` if the headers are equal. Only after confirming both same-sequence and different-content should the shared-pubkey-hash check be treated as proof of equivocation.

### Proof of Concept
```rust
// stackslib/src/chainstate/stacks/db/transactions.rs (test module)
#[test]
fn test_poison_microblock_rejects_identical_headers() {
    // 1. Set up chainstate, mine one tenure with a real microblock stream signed by miner key K,
    //    so that get_microblock_pubkey_hash_height(pkh(K)) resolves to a real height.
    let hdr = /* the single, legitimately mined+signed StacksMicroblockHeader at sequence N */;

    // 2. Craft PoisonMicroblock(hdr.clone(), hdr.clone()) — byte-identical headers.
    let payload = TransactionPayload::PoisonMicroblock(hdr.clone(), hdr.clone());
    let tx = make_signed_tx(payload, attacker_key);

    // 3. Process the transaction against chainstate.
    let result = StacksChainState::process_transaction(&mut clarity_tx, &tx, false, ExecutionCost::max_value());

    // Equality under test:
    // LHS: a poison report exists for pkh(K) at height H (StacksChainState::get_poison_microblock_report / env.database.get_microblock_poison_report)
    // RHS: mblock_header_1 != mblock_header_2 (real equivocation)
    let report = get_poison_microblock_report(&mut clarity_tx, height_of_hdr);

    // Current (buggy) behavior: report.is_some() == true even though hdr1 == hdr2 (RHS is false).
    assert!(report.is_none(), "poison report must not be recorded when both headers are identical (no equivocation occurred)");
}
```
This test currently fails against the code shown (a report is inserted), demonstrating that the poison-report equality invariant is broken and an honest miner can be slashed without ever having equivocated.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-752)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L754-803)
```rust
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
