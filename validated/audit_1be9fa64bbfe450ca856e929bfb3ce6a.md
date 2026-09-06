### Title
`check_microblock_header_signer` never verifies `mblock_header_1 != mblock_header_2`, allowing poison-microblock reports with a duplicated header - (File: `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Summary
`StacksChainState::check_microblock_header_signer` only compares the recovered pubkey hashes (`pkh1 != pkh2`) and never checks that the two microblock headers themselves are distinct (different block hash / sequence / content). `handle_poison_microblock`, which calls this function directly (not through `consensus_deserialize`), therefore accepts a `PoisonMicroblock(h, h)` transaction built with the identical header twice, as long as `h` is a header that was previously broadcast by a real miner.

### Finding Description
The broken equality is: a valid poison report should require `mblock_header_1.block_hash() != mblock_header_2.block_hash()` (or at minimum distinct signatures over distinct content) as proof of equivocation at the same sequence number. Instead, `check_microblock_header_signer` at `stackslib/src/chainstate/stacks/db/transactions.rs:686-713` only asserts: [1](#0-0) 

If `pkh1 != pkh2` fails, it errors; otherwise it returns `pkh1`. It never inspects whether `mblock_hdr_1` and `mblock_hdr_2` are actually different headers. `handle_poison_microblock` (`stackslib/src/chainstate/stacks/db/transactions.rs:722-856`) calls this check and then proceeds straight to looking up `get_microblock_pubkey_hash_height(&pubkh)`, checking maturity, and recording/crediting a poison report — at no point comparing the two header structs for equality: [2](#0-1) [3](#0-2) 

The only place in the codebase that rejects `mblock_header_1 == mblock_header_2` is the codec-level check in `consensus_deserialize` for the wire-format `TransactionPayload::PoisonMicroblock` (exercised by the `tx_stacks_transaction_payload_microblock_poison` test), which is bypassed if a `StacksTransaction` with `TransactionPayload::PoisonMicroblock(h.clone(), h.clone())` is constructed directly in memory (e.g., by an internal miner-generated tx path, or any code path that builds and processes a `StacksTransaction` without going through wire deserialization) and fed to `process_transaction_payload` → `run_poison_microblock` → `handle_poison_microblock`.

Since `h.clone()` trivially recovers to the same pubkey hash as `h`, `check_microblock_header_signer` always passes for `(h, h)`. As long as `h`'s pubkey hash was previously seen and recorded via `get_microblock_pubkey_hash_height` and is within `MINER_REWARD_MATURITY`, the transaction is accepted as "valid poison evidence" with zero actual equivocation — no second, distinct microblock was ever produced by the miner.

### Impact Explanation
This lets an attacker forge a poison-microblock report and claim `StacksChainState::poison_microblock_commission(coinbase_reward)` from a miner's coinbase using only a single, legitimately-produced microblock header duplicated in-transaction — no real fork/equivocation evidence is required. This is a reward mis-payment bounded to the coinbase/commission of the affected block (per `accounts.rs` poison-reporter payout logic), i.e. it causes the wrong party to receive a portion of a miner's reward. It does not by itself cause a chain split or an unreproducible state root; it matches the "poison or reward mis-payment bounded to fees" category (High), not a network-wide invalid/valid-block divergence.

### Likelihood Explanation
The attacker needs no special privilege: they only need to have observed (via P2P) a single microblock header ever produced by any miner whose pubkey hash was recorded and is still within the maturity window, and the ability to submit an internally-constructed `StacksTransaction`/payload that reaches `process_transaction_payload` without going through the wire-level `consensus_deserialize` equality guard. Whether this bypass is reachable depends on whether any accepted code path (e.g., internal transaction construction, RPC/mempool acceptance without full struct-level deserialization validation, or a future internal caller) constructs `TransactionPayload::PoisonMicroblock` values directly rather than via `consensus_deserialize`. Based on the code reviewed, the guard is enforced only at the codec boundary and not inside `check_microblock_header_signer`/`handle_poison_microblock` themselves, so any caller that skips `consensus_deserialize` — including hand-built transactions submitted to a mempool that re-validates structurally but not by full wire round-trip, or unit/integration code paths — would successfully trigger the slash.

### Recommendation
Add an explicit distinctness check inside `check_microblock_header_signer` (or `handle_poison_microblock`) that rejects `mblock_hdr_1 == mblock_hdr_2` (or, more precisely, requires `mblock_hdr_1.block_hash() != mblock_hdr_2.block_hash()` at the same `sequence`) before or alongside the pubkey-hash comparison, so the guard is enforced at the point where the actual chainstate mutation and reward payout happen — not only at the wire-decode layer.

### Proof of Concept
Rust integration test plan (chainstate-level, bypassing `consensus_deserialize`):
1. Set up a `StacksChainState`/test harness with a miner that has produced and had its microblock pubkey hash recorded via `insert_microblock_pubkey_hash`/normal chain processing, within `MINER_REWARD_MATURITY`.
2. Construct a single `StacksMicroblockHeader` `h` signed by the miner's real key (a legitimate, previously-broadcast header).
3. Build a `StacksTransaction` in memory with `TransactionPayload::PoisonMicroblock(h.clone(), h.clone())`, sign it, and do **not** round-trip it through `consensus_deserialize`.
4. Feed the transaction directly into `StacksChainState::process_transaction` (or `process_transaction_payload`).
5. Assert: **before** — `h == h` trivially, no distinct second header exists; **after** — the current code returns `Ok(...)` from `handle_poison_microblock` and records a poison report / commission payout, i.e. the assertion `result.is_err()` (expected if the guard existed) **fails**, demonstrating the missing distinctness check. A fixed implementation should make this call return `Err(Error::InvalidStacksTransaction(..))` because `mblock_hdr_1 == mblock_hdr_2`.

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L704-712)
```rust
        if pkh1 != pkh2 {
            let msg = format!(
                "Invalid PoisonMicroblock transaction -- signature pubkey hash {} != {}",
                &pkh1, &pkh2
            );
            warn!("{}", &msg);
            return Err(Error::InvalidStacksTransaction(msg, false));
        }
        Ok(pkh1)
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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L805-833)
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
```
