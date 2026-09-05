### Title
`handle_poison_microblock` never checks `mblock_header_1.sequence == mblock_header_2.sequence`, allowing two honestly-produced sequential microblocks (same signer, different sequence) to be accepted as equivocation evidence - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`check_microblock_header_signer` (transactions.rs:686-713) only verifies that the two supplied `StacksMicroblockHeader`s recover to the same public key hash (`pkh1 == pkh2`); it never compares `sequence` or `block_hash` fields. `handle_poison_microblock` (transactions.rs:722-883) calls only this check before treating the pair as a valid double-sign, then records a poison report and derives a punished sequence directly from `mblock_header_1.sequence` with no cross-check against `mblock_header_2.sequence`.

### Finding Description
The claimed invariant is: *reward paid == exactly one valid double-signature by the miner at a SINGLE shared sequence number*. Tracing the code:

- `check_microblock_header_signer` (transactions.rs:686-713) recovers `pkh1` from `mblock_hdr_1` and `pkh2` from `mblock_hdr_2` and errors only `if pkh1 != pkh2`. No comparison of `.sequence` or `.block_hash()` is performed anywhere in this function.
- `handle_poison_microblock` (transactions.rs:722-883) calls this check at line 751-752, obtains `pubkh`, looks up `get_microblock_pubkey_hash_height(&pubkh)` (line 754-757), checks the maturity window (lines 770-803), and then unconditionally uses `mblock_header_1.sequence` (lines 821, 831, 833, 846, 853, 855) as the "point where the fork occurred," inserting/updating a `microblock_poison` report and returning a tuple crediting `reporter_principal` with `reported_seq`.

Nowhere in this path is `mblock_header_1.sequence == mblock_header_2.sequence` enforced, nor is `mblock_header_1.block_hash() != mblock_header_2.block_hash()` enforced (the latter would at least ensure the two headers are not identical). Any two microblock headers signed with the same microblock private key — including two perfectly ordinary, non-conflicting, sequential microblocks (e.g., sequence 5 and sequence 6) that the miner legitimately produced as part of one honest microblock stream — satisfy `pkh1 == pkh2` and are accepted as "poison" evidence. This is not a double-sign: a genuine equivocation requires two *distinct* microblocks at the *same* sequence number, both validly signed by the same key, which is what actually indicates the miner forked its own microblock stream.

Existing guards in the traced function do not close this gap: `get_microblock_pubkey_hash_height`/maturity check only gate on whether the key was seen recently, and `runtime_cost`/`add_memory` calls are unrelated cost-accounting, not correctness checks.

### Impact Explanation
An attacker (any unprivileged participant who observes an honest miner's normal, sequential microblock stream on the wire) can construct a `PoisonMicroblock` transaction from any two sequential, non-conflicting microblock headers of that miner and have it accepted by `handle_poison_microblock` as valid equivocation evidence. This results in a wrongful poison report being recorded against the honest miner's microblock public key hash at `mblock_pubk_height`, and a `reporter_principal`/`reported_seq` tuple returned that is subsequently used elsewhere in the reward path to slash/redirect the honest miner's commission. This is a reward mis-payment tied to a poison report — matching the rules' High-severity category ("a poison or reward mis-payment bounded to fees"). No chain split or state-root divergence results, since all honest nodes evaluate the same (broken) logic identically and agree on the (wrong) outcome; the damage is a wrongful transfer of commission away from an honest miner.

### Likelihood Explanation
- Preconditions: attacker needs visibility into any two sequential microblock headers signed by a target miner's microblock key — these are broadcast in the clear over the P2P network, so no privileged access is required.
- Cost: a single standard `PoisonMicroblock` transaction fee; no BTC spend, no stake, no signer key.
- Feasibility: trivial and fully repeatable against any miner who produces more than one microblock per tenure (the normal case), as long as `mblock_pubk_height` has not matured past `MINER_REWARD_MATURITY`.
- This requires only a minority/unprivileged position, consistent with the threat model in scope.

### Recommendation
In `check_microblock_header_signer` (or immediately in `handle_poison_microblock` before treating the pair as valid), require `mblock_header_1.sequence == mblock_header_2.sequence` and reject otherwise; additionally require the two headers to have distinct block hashes/signatures (`mblock_header_1.block_hash() != mblock_header_2.block_hash()`) so that identical or merely sequential headers cannot be mistaken for equivocation evidence.

### Proof of Concept
Rust integration test plan (chainstate test harness, e.g. alongside `stackslib/src/chainstate/stacks/tests/block_construction.rs`):
1. Generate a microblock signing keypair for a simulated miner.
2. Produce two valid `StacksMicroblockHeader`s, `hdr_seq5` and `hdr_seq6`, both signed by the same key, with `sequence = 5` and `sequence = 6` respectively, and distinct, non-conflicting `block_hash` values representing normal sequential microblocks (no fork).
3. Assert precondition: `hdr_seq5.sequence != hdr_seq6.sequence`.
4. Build a `TransactionPayload::PoisonMicroblock(hdr_seq5, hdr_seq6)` transaction from an arbitrary reporter account and invoke `StacksChainState::handle_poison_microblock` (or the full transaction-processing path) against a chainstate where the miner's pubkey hash height is registered and unmatured.
5. Assert that the call returns `Err(Error::InvalidStacksTransaction(..))` (expected fix behavior) — currently it returns `Ok(Value::Tuple(..))` with `sequence = 5`, demonstrating that two non-conflicting, honestly sequential headers are wrongly accepted as poison evidence.
6. Additionally assert no `microblock_poison` report is inserted into `env.global_context.database` for this pubkey hash when the fix is applied, versus the current behavior where `insert_microblock_poison` is called at line 828/850. [1](#0-0) [2](#0-1)

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L722-856)
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
