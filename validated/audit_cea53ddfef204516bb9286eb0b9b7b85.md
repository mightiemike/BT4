### Title
`handle_poison_microblock` slashes/pays out on any two same-signer microblock headers regardless of sequence match, not just true equivocations - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
The consensus-level state-transition function `handle_poison_microblock` (invoked from `process_transaction` for every `TransactionPayload::PoisonMicroblock`) only calls `check_microblock_header_signer`, which checks that `pkh1 == pkh2`, but never asserts `mblock_header_1.sequence == mblock_header_2.sequence`. This lets a `PoisonMicroblock(h1, h2)` transaction with two non-conflicting, different-sequence headers from the same honest miner be accepted as if it were a genuine equivocation.

### Finding Description
The equality that should be enforced before slashing is: *the two headers must share the same sequence number* (true equivocation, as defined and detected in `validate_parent_microblock_stream`, which only flags a fork when `prior_microblock.header.sequence == cur_microblock.header.sequence && prior_microblock.block_hash() != cur_microblock.block_hash()` [1](#0-0) ).

However, the consensus code path that actually processes a submitted `PoisonMicroblock` transaction is:
- `handle_poison_microblock` calls `check_microblock_header_signer(mblock_header_1, mblock_header_2)`, which recovers `pkh1` and `pkh2` and only errors if `pkh1 != pkh2` — it never compares `mblock_hdr_1.sequence` to `mblock_hdr_2.sequence` [2](#0-1) .
- `handle_poison_microblock` then proceeds directly to look up the pubkey-hash height, check maturity, and call `insert_microblock_poison(mblock_pubk_height, &sender_principal, mblock_header_1.sequence)`, all keyed purely off `pubkh` with no sequence-equality gate [3](#0-2) .

The only place in the codebase that enforces `microblock_header_1.sequence == microblock_header_2.sequence` (along with `prev_block` and `version` equality) is `will_admit_mempool_tx`, a **mempool relay-policy** check, not a consensus/state-transition check: [4](#0-3) . Mempool admission filters do not run during actual block validation/execution — a miner (an attacker who "can submit Nakamoto blocks and microblocks" per the threat model) can construct their own block, include a hand-crafted `PoisonMicroblock` transaction directly (bypassing `will_admit_mempool_tx` entirely), and have it processed by `process_transaction` -> `handle_poison_microblock` on every validating node.

Attacker's exact input: two headers `h1` (sequence 0, honestly produced) and `h2` (sequence 1, honestly produced, chained to `h1` via `prev_block`), both legitimately signed by the same honest miner's microblock key. This is simply a normal 2-microblock stream, not a fork. Submitted as `PoisonMicroblock(h1, h2)` inside a self-mined block, `check_microblock_header_signer` returns `pkh` (since `pkh1 == pkh2`, trivially true because it's the same honest signer), and the function proceeds to record a poison report / slashing entry against that honest miner's key, exactly as if a real equivocation had occurred.

Existing guards checked:
- `validate_parent_microblock_stream` and its sequence/fork detection only run during microblock *stream* loading/relay, not during `PoisonMicroblock` transaction execution — they do not gate `handle_poison_microblock`.
- `check_microblock_header_signer` is the *only* validation performed on the header pair inside the consensus path, and it does not check sequence.
- The mempool-level `will_admit_mempool_tx` check does enforce sequence equality, but a miner including their own transaction directly into a block they produce is not required to pass mempool admission — every other node's `process_transaction` still executes `handle_poison_microblock` on it since that function is a pure state-transition performed at block-apply time regardless of how the tx entered the block.

### Impact Explanation
An honest miner who has produced a normal, non-conflicting 2+ microblock stream can have their coinbase/mining reward slashed and reassigned to an attacker-controlled "reporter" address, purely because both headers were signed by the same key (which is always true for a legitimate stream). This is block-reward theft/mis-payment directed at an innocent party, reachable by any single unprivileged miner slot without needing majority stake — it only requires being able to get one transaction included in a block (even one's own block, self-mined). This matches "poison or reward mis-payment bounded to fees" (High) up to outright reward theft from an honest miner (Critical, if the maturation-window reward is actually redirected as a result of `insert_microblock_poison`).

### Likelihood Explanation
Preconditions: attacker needs only their own BTC-funded miner slot (or the ability to get a single transaction mined) and knowledge of a target honest miner's already-broadcast, legitimate two-(or-more)-sequence microblock stream (which is public information, trivially observable from the target's normal mining activity) and the target's report/reward window must still be within `MINER_REWARD_MATURITY` of the referenced pubkey-hash height. No majority stake, no signer collusion, and no compromise of the target's key is required — the attacker uses the target's own honestly-published headers verbatim. This is fully repeatable against every miner who has ever produced more than one microblock in a stream.

### Recommendation
Add an explicit `mblock_header_1.sequence == mblock_header_2.sequence` check (and ideally `prev_block`/`version` equality, mirroring `will_admit_mempool_tx`'s existing check at stackslib/src/chainstate/stacks/db/blocks.rs:6845-6850) directly inside `check_microblock_header_signer` or `handle_poison_microblock` in transactions.rs, before any pubkey-hash lookup or `insert_microblock_poison` call, so the consensus state-transition function itself — not just the mempool relay filter — rejects non-equivocating header pairs.

### Proof of Concept
Rust integration test plan (transactions.rs test module, alongside existing `process_poison_microblock_*` tests):
1. Generate `block_privk`/`block_pubkh`, register pubkey hash at height 1 via `insert_microblock_pubkey_hash`.
2. Build `mblock_1 = make_signed_microblock(&block_privk, ..., seq=0)` and `mblock_2 = make_signed_microblock(&block_privk, ..., seq=1, prev_block = mblock_1.block_hash())` — i.e., a normal, non-conflicting, chained 2-microblock stream from the same honest signer.
3. Assert as a precondition: `mblock_1.header.sequence != mblock_2.header.sequence` and that `validate_parent_microblock_stream` over `[mblock_1, mblock_2]` returns `None` for the poison payload (i.e., no true fork detected) — this establishes the two are NOT a genuine equivocation.
4. Construct `TransactionPayload::PoisonMicroblock(mblock_1.header.clone(), mblock_2.header.clone())`, sign with an attacker/reporter key, and call `StacksChainState::process_transaction` directly (simulating direct block inclusion, bypassing `will_admit_mempool_tx`).
5. Assert on both sides of the equality: expected (secure) behavior is `process_transaction` returns `Err` (rejecting mismatched-sequence pair); observed (vulnerable) behavior is that it returns `Ok`, and `StacksChainState::get_poison_microblock_report(&mut conn, 1)` returns `Some((reporter_addr, 0))` — proving an honest, non-forking miner was slashed/reported based solely on same-signer, different-sequence headers.

### Citations

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L3021-3037)
```rust
        for (j, cur_microblock) in signed_microblocks.iter().skip(1).enumerate() {
            if prior_microblock.header.sequence == cur_microblock.header.sequence
                && prior_microblock.block_hash() != cur_microblock.block_hash()
            {
                // deliberate microblock fork
                debug!(
                    "Deliberate microblock fork at sequence {}",
                    prior_microblock.header.sequence
                );
                return Some((
                    j, // j := `index in signed_microblocks of cur_microblock - 1`
                    Some(TransactionPayload::PoisonMicroblock(
                        prior_microblock.header.clone(),
                        cur_microblock.header.clone(),
                    )),
                ));
            }
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L6844-6867)
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
```

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
