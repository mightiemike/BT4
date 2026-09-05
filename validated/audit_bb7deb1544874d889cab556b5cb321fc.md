### Title
Poison-microblock report commission is front-runnable, letting an attacker steal the whistleblower reward - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`handle_poison_microblock()` credits the poison-microblock commission to whichever `sender_principal` first submits a valid `PoisonMicroblock` transaction containing a matching pair of conflicting microblock headers, and pays that address (not necessarily the party who actually discovered the fork) via `calculate_miner_reward()`. Because the fork-proof (`mblock_header_1`, `mblock_header_2`) is public data that becomes visible in a broadcast/pending transaction, an attacker monitoring the mempool can copy the same proof, wrap it in their own `PoisonMicroblock` transaction, and get it mined first (e.g. with a higher fee), claiming the reward that was meant for the original discoverer — the same "front-running steals an unprivileged, anyone-callable reward" pattern described in the external report.

### Finding Description
`handle_poison_microblock` in [1](#0-0)  takes the transaction's `sender_principal` — whoever signed the enclosing `PoisonMicroblock` transaction — as the candidate reporter, with no requirement that this sender be the original discoverer of the microblock fork; anyone who can present a valid pair of conflicting, equally-signed microblock headers qualifies.

The report is only overwritten by a later sender if that later report proves a strictly earlier fork point: [2](#0-1) 
When there is no existing report yet (`None` branch), any first-arriving valid `PoisonMicroblock` transaction — regardless of who authored the underlying proof — is recorded as the reporter: [3](#0-2) 

This recorded reporter is later paid a fraction of the punished miner's coinbase in `calculate_miner_reward`: [4](#0-3) 
via the `poison_microblock_commission` calculation: [5](#0-4) 

The equality this breaks: "the principal that discovers and first broadcasts the fork proof" should equal "the principal that is recorded and paid as `reporter`." An attacker who front-runs the original reporter's transaction (by observing the plaintext `mblock_header_1`/`mblock_header_2` in the pending transaction, and re-submitting them under their own signature with a higher fee/priority) breaks this equality: the attacker becomes `sender_principal` in `handle_poison_microblock`, is stored via `insert_microblock_poison`, and is the one paid the coinbase commission, while the legitimate discoverer's identical (or higher-sequence) report is silently dropped by the `mblock_header_1.sequence < seq` check (equal sequence does not override).

This exactly mirrors the report's bug class: a permissionless, anyone-can-call function pays out a reward to whichever caller lands on-chain first, and the proof/data needed to claim it is visible to front-runners before it is finalized.

### Impact Explanation
This is a minority-triggerable, unprivileged reward mis-payment bounded to the poison-microblock commission (a fraction of one coinbase reward) — matching the "poison or reward mis-payment bounded to fees" High-severity category. It does not cause a chain split, does not corrupt consensus state, and does not require any majority or admin/validator key: a single attacker who watches the mempool and outbids the original reporter's fee (or gets included first in the same block) can redirect the whistleblower reward to themselves. The miner who forked the microblock stream is still correctly slashed; only the recipient of the commission is misdirected.

### Likelihood Explanation
Likelihood is moderate: it requires an attacker to actively monitor the mempool for `PoisonMicroblock` transactions and race to include an equivalent transaction first (via nonce/fee manipulation or by working with the block-producing miner). This is analogous to standard MEV/front-running risk already accepted as a known class of issue in the external report, and it is a plausible, cheap, purely mempool-observable attack with no special privileges needed.

### Recommendation
Consider one or more of:
- Require the poison-microblock proof to commit to a nonce/salt bound to the original reporter (e.g., a commit-reveal scheme) so that copying the visible proof does not let a front-runner claim the same report.
- Allow multiple simultaneous reporters for the same fork to share the commission proportionally, rather than an exclusive strictly-first/strictly-lower-sequence winner-take-all model.
- Document this as an accepted trust-minimized incentive design if the low, bounded value of the poison-microblock commission is deemed not worth mitigating (similar to the client's "Acknowledged" stance in the original report), but explicitly disclose the front-running risk to users/whistleblowers.

### Proof of Concept
1. Reporter `R` observes miner `M` produced two conflicting, equally-signed microblocks (fork at sequence `s`). `R` constructs and signs a `PoisonMicroblock` transaction `Tx_R` with `mblock_header_1`/`mblock_header_2` and broadcasts it to the mempool.
2. Attacker `A` observes `Tx_R` in the mempool (the microblock headers are transmitted in plaintext, not encrypted or committed) and constructs their own `PoisonMicroblock` transaction `Tx_A` containing the identical `mblock_header_1`/`mblock_header_2`, signed by `A`, with a higher fee (or otherwise arranges for `Tx_A` to be processed before `Tx_R`, e.g. same block, earlier tx index).
3. When the block containing both transactions is processed, `handle_poison_microblock` for `Tx_A` runs first: no report exists yet, so `insert_microblock_poison` records `(A, s)` per [3](#0-2) .
4. `handle_poison_microblock` for `Tx_R` then runs: since `mblock_header_1.sequence (s) < seq (s)` is false, `R`'s transaction takes the `else` branch — "someone else beat the sender to this report" — and `A` remains the recorded reporter per [6](#0-5) .
5. When rewards mature, `get_poison_microblock_report` returns `A` as the reporter, and `calculate_miner_reward` pays `A` (not `R`) the `poison_microblock_commission` of `M`'s coinbase, per [4](#0-3) .

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L722-748)
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
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L805-841)
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
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L842-856)
```rust
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

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L794-797)
```rust
    /// What's the commission for reporting a poison microblock stream?
    fn poison_microblock_commission(coinbase: u128) -> u128 {
        (coinbase * POISON_MICROBLOCK_COMMISSION_FRACTION) / 100
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L869-895)
```rust
        // process poison -- someone can steal a fraction of the total coinbase if they can present
        // evidence that the miner forked the microblock stream.  The remainder of the coinbase is
        // destroyed if this happens.
        let (child_address, child_recipient, coinbase_reward, punished) =
            if let Some(reporter_address) = poison_reporter_opt {
                if participant.miner {
                    // the poison-reporter, not the miner, gets a (fraction of the) reward
                    debug!(
                        "{:?} will recieve poison-microblock commission {}",
                        &reporter_address.to_string(),
                        StacksChainState::poison_microblock_commission(coinbase_reward)
                    );
                    (
                        reporter_address.clone(),
                        reporter_address.to_account_principal(),
                        StacksChainState::poison_microblock_commission(coinbase_reward),
                        true,
                    )
                } else {
                    // users that helped a miner that reported a poison-microblock get nothing
                    (
                        StacksAddress::burn_address(mainnet),
                        StacksAddress::burn_address(mainnet).to_account_principal(),
                        0,
                        false,
                    )
                }
```
