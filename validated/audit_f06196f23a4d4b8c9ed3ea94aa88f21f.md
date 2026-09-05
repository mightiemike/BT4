### Title
`handle_poison_microblock` never checks that the two headers actually conflict (same sequence/prev_block) — only that they share a signer — allowing slashing of an honest miner who reuses a microblock signing key across non-conflicting streams - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`StacksChainState::handle_poison_microblock` (called from `process_transaction_payload` during real block processing) only verifies `pkh1==pkh2` via `check_microblock_header_signer`/`check_recover_pubkey`, and that the recorded pubkey-hash height is unmatured. It never checks that the two `StacksMicroblockHeader`s share the same `sequence`, `prev_block`, or `version` — the actual proof of equivocation. That "do they conflict" check (`PoisonMicroblocksDoNotConflict`) exists only in the mempool-admission path `will_admit_mempool_tx`, not in the consensus-level transaction processor.

### Finding Description
The claimed equality is: `reported_pubkh(header_1) == reported_pubkh(header_2) == actual_two_distinct_signed_microblocks_by_the_same_miner_at_the_same_fork_point`.

`check_microblock_header_signer` (stackslib/src/chainstate/stacks/db/transactions.rs:686-713) only computes:
```
pkh1 = mblock_hdr_1.check_recover_pubkey()
pkh2 = mblock_hdr_2.check_recover_pubkey()
if pkh1 != pkh2 { return Err(...) }
``` [1](#0-0) 

`handle_poison_microblock` (transactions.rs:722-803) then only checks `pubkh` was previously recorded via `get_microblock_pubkey_hash_height` and is within `MINER_REWARD_MATURITY`. It never checks `mblock_header_1.sequence == mblock_header_2.sequence`, `prev_block` equality, or that the headers actually differ from a genuine fork point. [2](#0-1) 

By contrast, the *mempool admission* code path (`will_admit_mempool_tx`) does enforce this, but only as a relay-time filter, not as a consensus rule:
```rust
if microblock_header_1.sequence != microblock_header_2.sequence
    || microblock_header_1.prev_block != microblock_header_2.prev_block
    || microblock_header_1.version != microblock_header_2.version
{
    return Err(MemPoolRejection::PoisonMicroblocksDoNotConflict);
}
``` [3](#0-2) 

Because this "do they conflict" check is absent from `process_transaction_payload`/`handle_poison_microblock`, any transaction that reaches block processing directly (i.e., placed into a block by a miner, bypassing the relay mempool's admission rules) is accepted by every honest node as valid, since block processing is the sole consensus-authoritative code path. An attacker who is themselves a miner (permitted by the threat model: "submit Nakamoto blocks and microblocks") can build a block containing a crafted `PoisonMicroblock` transaction whose two headers are both **real, validly-signed** headers by the same private key — but pulled from two entirely different, non-conflicting microblock streams/tenures (this can occur if a miner ever reuses the same microblock signing key across two tenures — nothing in `insert_microblock_pubkey_hash`/`get_microblock_pubkey_hash_height` prevents key reuse, since it is keyed purely by pubkey hash, not by tenure or fork). Because `check_recover_pubkey` performs real ECDSA recovery, the attacker cannot forge a signature for arbitrary content; they must find two authentic headers signed by the victim's key. If the victim ever reused a microblock key, `pkh1==pkh2` holds even though the headers never conflicted (different `prev_block`/`sequence`), breaking the intended equivalence between "same signer" and "provable equivocation."

Existing guards do not prevent this: `check_tenure_tx`, `verify_signer_signatures`, VRF/static validators, and `common_validate_against_burnchain` are unrelated to this Stacks-transaction-level check; the maturity window only limits *when* the slashing can occur, not *whether* the headers actually conflict.

### Impact Explanation
This is block-reward theft: the honest miner's coinbase for the block associated with `mblock_pubk_height` is slashed, and the (attacker-controlled or colluding) reporter is paid the commission via `insert_microblock_poison`/reward distribution, despite no genuine double-signing having occurred. Because block processing is the network's sole consensus authority (not the mempool filter), every honest node that processes the malicious block will accept the poison record identically — this is a network-wide, reproducible mis-payment/loss of the honest miner's matured reward, matching the "block-reward theft/double-payment/loss" Critical category.

### Likelihood Explanation
The attack requires: (1) attacker control of a miner slot able to place a crafted transaction directly into a block bypassing mempool relay rules (permitted under the stated threat model), and (2) the existence of two authentic, validly-signed `StacksMicroblockHeader`s from the *same* private key that do not actually conflict — i.e., a real miner having reused a microblock signing key across two non-forking tenures/streams. Precondition (2) is not itself enforced-against anywhere in the codebase (no uniqueness check on microblock pubkey hash across blocks), so it is a plausible operational condition rather than a cryptographic break. No majority stake or signer collusion is required — only the attacker's own miner-slot block-construction capability.

### Recommendation
Add the missing conflict check directly inside `check_microblock_header_signer` or `handle_poison_microblock` (the consensus-critical path), mirroring `will_admit_mempool_tx`'s `PoisonMicroblocksDoNotConflict` logic: require `mblock_header_1.sequence == mblock_header_2.sequence`, `mblock_header_1.prev_block == mblock_header_2.prev_block`, `mblock_header_1.version == mblock_header_2.version`, and `mblock_header_1 != mblock_header_2` before accepting the pubkey-hash match as proof of equivocation.

### Proof of Concept
Rust integration test plan in stackslib/src/chainstate/stacks/db/transactions.rs:
1. Using `make_signed_microblock`, create `header_A` for tenure/stream A with `prev_block = X`, `sequence = 5`, signed with `block_privk`.
2. Create `header_B` for a different, non-conflicting tenure/stream B with `prev_block = Y` (`Y != X`), `sequence = 7`, signed with the **same** `block_privk` (simulating key reuse).
3. Record `insert_microblock_pubkey_hash` for `block_pubkh` at some height `H`.
4. Build `TransactionPayload::PoisonMicroblock(header_A, header_B)`, sign with reporter key, and call `StacksChainState::process_transaction` directly (bypassing `will_admit_mempool_tx`).
5. Assert: `header_A.sequence != header_B.sequence` and `header_A.prev_block != header_B.prev_block` (i.e., these are NOT a genuine fork) — the "left side" of the intended equality.
6. Assert `StacksChainState::process_transaction` succeeds (no `PoisonMicroblocksDoNotConflict`-style error is raised) and `get_poison_microblock_report(&mut conn, H)` returns `(reporter_addr, header_A.sequence)` — proving the forged report is accepted by the state-transition function despite the headers never having been part of the same equivocating stream.

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

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L6844-6861)
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
```
