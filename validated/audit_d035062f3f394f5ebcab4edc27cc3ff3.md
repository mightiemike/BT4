### Title
`handle_poison_microblock` never verifies that `sender_principal` differs from the address controlling `pubkh`, allowing the equivocating miner to self-report and capture the poison commission - (File: `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Summary
`StacksChainState::handle_poison_microblock` extracts the reporting `sender_principal` from the transaction's origin and the offending `pubkh` from the two conflicting microblock headers, but it never checks that `sender_principal` is distinct from (or unrelated to) the account controlling the microblock signing key `pubkh`. This lets the equivocating miner submit the `PoisonMicroblock` transaction against themselves and be recorded as the "reporter," redirecting the whistleblower commission to their own principal.

### Finding Description
The broken equality: the protocol intends `reward_paid_to(reporter) == commission_fraction * coinbase` **where `reporter != cheating_miner`**, i.e. the punished miner should never be the same principal that collects the commission for reporting their own equivocation. The code as written enforces neither side of the distinctness condition.

Tracing the path:
- `sender_principal` is taken directly from `invoke_ctx.sender`, i.e. whatever principal authored/signed the `PoisonMicroblock` transaction, with no cross-check against the equivocating key: [1](#0-0) 
- `pubkh` is recovered purely from the two conflicting microblock header signatures, and is only checked for equality between the two headers, never compared to `sender_principal`: [2](#0-1) [3](#0-2) 
- The report/commission bookkeeping (`insert_microblock_poison`, `get_microblock_poison_report`) stores whichever `sender_principal` submitted the best (lowest) sequence report as `reporter_principal`, with no filter excluding the party that controls `pubkh`: [4](#0-3) 

Because there is no cryptographic or semantic requirement linking `sender_principal` (the tx origin, an arbitrary standard principal that just needs to sign/pay the poison tx fee) to `pubkh` (the microblock signing key), the equivocating miner can simply author and submit the `PoisonMicroblock` transaction from their own STX address, immediately after (or instead of) an honest third party doing so. Existing guards (`check_microblock_header_signer`, the maturity-window check against `MINER_REWARD_MATURITY`, and the "already reported at lower sequence" ordering) all validate the *microblock fork* evidence and *timing*, not the *identity relationship* between reporter and cheater, so none of them close this gap.

### Impact Explanation
This is a reward mis-payment bounded to the poison-microblock commission fraction, not a chain split or double-spend: the miner who equivocated still forfeits the non-commission portion of their coinbase, but they recapture the commission share that was meant to be an incentive payment to an honest, independent whistleblower. This breaks the intended asymmetric-incentive design of the poison-microblock mechanism (a cheater should never be able to profit from, or reduce their loss via, reporting themselves), matching the "High" severity category: reward mis-payment bounded to a fee/commission amount, with no effect on consensus, sortition, or chain state agreement between nodes.

### Likelihood Explanation
This requires no privileged role, no majority stake, and no coordination: the same single miner who signs two conflicting microblocks with `pubkh` (already an action requiring only their own miner slot) can construct and broadcast the `PoisonMicroblock` transaction from any of their own standard principals before an honest third party notices and reports it. It is fully repeatable on every equivocation event, and cheaper than losing the entire commission to an outside reporter, so a rational equivocating miner is incentivized to always self-report.

### Recommendation
In `handle_poison_microblock`, derive the P2PKH/P2WPKH address(es) corresponding to `pubkh` and explicitly reject (or refuse to credit commission for) reports where `sender_principal` matches that address, or more generally track the miner identity associated with the tenure/block that produced the poisoned microblocks and disallow that miner (or any principal they control that is verifiable on-chain, e.g. the miner's registered reward address) from being recorded as `reporter_principal`.

### Proof of Concept
Rust integration test plan (chainstate test harness, e.g. extending `stackslib/src/chainstate/stacks/tests/block_construction.rs` poison-microblock tests):
1. Set up a Nakamoto/neon test chainstate with miner `M` holding keypair `k_mine` (microblock signing key) and a separate standard principal `M_addr` derived from `k_mine`'s STX key (the miner's own on-chain identity).
2. Have `M` produce two conflicting `StacksMicroblockHeader`s signed with `k_mine`, both referencing the same `pubkh`.
3. Construct a `PoisonMicroblock` transaction whose `sender_principal` equals `M_addr` (the same principal as the equivocating miner), signed and broadcast by `M`.
4. Mine this transaction into a block and process it through `handle_poison_microblock`.
5. Assert on both sides of the equality:
   - Before: `reporter_principal` recorded by `get_microblock_poison_report` should require `reporter_principal != miner_principal_for(pubkh)`.
   - After processing: assert `reporter_principal == M_addr` is accepted by current code (demonstrating the missing check), and assert that the subsequent commission payout in `stackslib/src/chainstate/stacks/db/accounts.rs` credits `M_addr` (the cheater) rather than being rejected or forced to a distinct reporter.
6. A passing/expected-fixed behavior would have step 3 return `Err(Error::InvalidStacksTransaction(...))` when `sender_principal` resolves to the same identity as `pubkh`'s controller.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L735-748)
```rust
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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-752)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;
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
