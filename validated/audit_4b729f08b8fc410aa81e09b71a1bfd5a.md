### Title
`handle_poison_microblock` slashes an honest miner when the attacker submits two byte-identical microblock headers instead of a real equivocation - (File: `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Finding Description
The intended equality that must hold before a miner is slashed is: *"two distinct microblocks exist at the same sequence number, both with different hashes, both validly signed by the same key"*. The code that is supposed to enforce this, `StacksChainState::check_microblock_header_signer`, only recovers the public key hash from each header and compares the two pubkey hashes: [1](#0-0) 

It never checks that `mblock_hdr_1` and `mblock_hdr_2` are actually different microblocks (e.g. different block hash / different signature bytes) at the same sequence number. If an attacker submits a `PoisonMicroblock` transaction where `mblock_header_1` is byte-for-byte identical to `mblock_header_2` (a replay of the single, honestly-broadcast microblock header), `check_recover_pubkey()` on both headers trivially yields the same `pkh1 == pkh2`, and the function returns `Ok(pkh1)` — exactly as it would for a genuine equivocation.

`handle_poison_microblock` then proceeds unconditionally: it looks up `mblock_pubk_height` for that key, checks the maturity window, and — finding no prior report — calls `insert_microblock_poison` to record a poison report keyed on `mblock_header_1.sequence`: [2](#0-1) 

This poison record subsequently causes the miner's coinbase to be slashed and the `poison_microblock_commission` to be paid to the reporter when rewards mature, even though the targeted miner never signed two conflicting headers at the same sequence — they signed exactly one honest microblock, which was merely replayed by the attacker.

### Impact Explanation
This is block-reward theft from a non-equivocating miner: the honest miner's coinbase is slashed and the attacker is paid a commission for a "poison" that never occurred. Any single unprivileged party who can observe one broadcast microblock header can trigger this by submitting a `PoisonMicroblock` transaction with `mblock_header_1 == mblock_header_2`. This matches the "Critical: block-reward theft/double-payment/loss" category, since a legitimate miner permanently loses reward while an attacker profits, and this is repeatable against any/every miner whose microblock header the attacker can observe (which is trivial, since headers are broadcast).

### Likelihood Explanation
The only precondition is observing one broadcast, honestly-signed microblock header — no forking, no majority stake, no signer collusion, and no BTC cost beyond the fee for submitting the `PoisonMicroblock` transaction itself. Any unprivileged network participant satisfies this trivially and can repeat it against every miner's microblocks.

### Recommendation
In `check_microblock_header_signer` (or immediately before/after it in `handle_poison_microblock`), explicitly reject the transaction if `mblock_hdr_1` and `mblock_hdr_2` are identical — e.g., require `mblock_hdr_1.sequence == mblock_hdr_2.sequence` (already implicit) AND `mblock_hdr_1.block_hash() != mblock_hdr_2.block_hash()` (or compare full serialized headers/signatures) before treating the submission as proof of equivocation. Only proceed to `insert_microblock_poison` if the two headers genuinely conflict.

### Proof of Concept
Add an integration/unit test in `stackslib/src/chainstate/stacks/db/transactions.rs` (or the existing poison-microblock test module) that:
1. Constructs a single valid, signed `StacksMicroblockHeader` (`hdr`) for a known miner key.
2. Builds a `PoisonMicroblock` payload/transaction with `mblock_header_1 = hdr.clone()` and `mblock_header_2 = hdr.clone()` (byte-identical serialization).
3. Sets up chain state so the miner's pubkey hash height is recorded and within the maturity window (mirroring existing poison-microblock tests).
4. Calls `StacksChainState::handle_poison_microblock` (or processes the tx through `process_transaction_payload`) with these two identical headers.
5. Asserts the call returns `Err(Error::InvalidStacksTransaction(..))` and that no `insert_microblock_poison` record is created — currently, the call instead succeeds and returns `Ok(Value::Tuple(..))`, and a poison record is inserted, demonstrating the vulnerability.

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
