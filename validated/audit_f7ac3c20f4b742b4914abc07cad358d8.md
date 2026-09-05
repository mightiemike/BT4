### Title
`check_microblock_header_signer` never verifies the two microblock headers actually differ, enabling reward theft via self-identical "poison" evidence - (File: `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Summary
`check_microblock_header_signer` (lines 686-713) only checks that `mblock_hdr_1.check_recover_pubkey()` equals `mblock_hdr_2.check_recover_pubkey()`; it never compares the headers' hashes/contents. Since recovering the same signer from two byte-identical headers is trivially true, an attacker can submit `PoisonMicroblock(header, header)` for any legitimately-mined microblock header they observed on the wire and have `handle_poison_microblock` treat it as valid equivocation evidence.

### Finding Description
The equality that should hold before a poison report is accepted is:

`valid_poison_evidence == (pkh1 == pkh2) AND (mblock_header_1.block_hash() != mblock_header_2.block_hash())`

i.e., proof of equivocation requires *both* the same signer *and* two distinct microblocks at the same sequence number. The actual code only enforces the first half: [1](#0-0) 

`check_microblock_header_signer` recovers `pkh1` and `pkh2` and rejects only when `pkh1 != pkh2`; there is no comparison of `mblock_hdr_1.block_hash()` vs `mblock_hdr_2.block_hash()`, nor any check that the two headers are non-identical. Because `check_recover_pubkey()` is a deterministic function of the header's own fields (including its own signature), passing the same header object twice always yields `pkh1 == pkh2`, satisfying the check with zero fork evidence.

`handle_poison_microblock` then proceeds using only `pubkh` derived from this check: [2](#0-1) 

It looks up `get_microblock_pubkey_hash_height(&pubkh)` — which will return `Some(height)` for any pubkey hash that was legitimately used to sign a real, previously-mined microblock — and checks only that the height is within `MINER_REWARD_MATURITY`. There is no re-derivation or comparison of the actual microblock header content between the two supplied headers beyond the pubkey-hash equality already discussed.

Following that, the report-insertion logic reads/writes the poison record purely based on `mblock_header_1.sequence` versus any prior report's `seq`: [3](#0-2) 

At no point does this logic require `mblock_header_1` and `mblock_header_2` to be distinct microblocks. An attacker who observes any real, already-broadcast microblock header (public information — no privileged access needed) can submit it as both arguments to the `PoisonMicroblock` payload. `check_microblock_header_signer` passes trivially, `get_microblock_pubkey_hash_height` succeeds because the pubkey hash was genuinely used, and `insert_microblock_poison` records the attacker as the reporter/beneficiary for that miner's slot — all without any actual double-signing having occurred.

At reward maturity, `find_mature_miner_rewards`/`calculate_miner_reward` redirect the coinbase reward for that block height to the reporter recorded via this bogus report, per the existing poison/commission mechanism, causing the legitimate miner to lose (or share/lose) their coinbase reward to an attacker who presented no actual fork evidence.

No other guard in the reachable path (`get_microblock_pubkey_hash_height`, the maturity-window check, or the sequence comparison in `handle_poison_microblock`) verifies header distinctness, so the divergence is not caught anywhere downstream.

### Impact Explanation
This allows an unprivileged attacker to redirect a legitimately-earned block/coinbase reward from the rightful miner to themselves by fabricating "poison" evidence out of a single real microblock header replayed twice. This is a reward mis-payment/theft bounded to the coinbase amount for the targeted block, repeatable for any mined microblock header observed within the `MINER_REWARD_MATURITY` window, and requires no majority stake, no signer key, and no admin/node privilege — only the ability to broadcast a standard transaction. This matches the "block-reward theft/mispayment" Critical/High category described in the rules.

### Likelihood Explanation
Preconditions are minimal: the attacker needs only to observe (from the public P2P network) any microblock header that was mined and is still within the `MINER_REWARD_MATURITY` window, and to submit a standard `PoisonMicroblock` transaction with that same header passed as both `mblock_header_1` and `mblock_header_2`. This costs only the standard transaction fee and requires no BTC spend, no leader-key registration, and no elevated stake — fully consistent with the unprivileged threat model. The attack is repeatable against every mature microblock header on the network.

### Recommendation
In `check_microblock_header_signer`, in addition to the existing `pkh1 != pkh2` check, explicitly require that the two headers are not identical evidence of a fork, e.g. assert `mblock_hdr_1.sequence == mblock_hdr_2.sequence` (already implied) **and** `mblock_hdr_1.block_hash() != mblock_hdr_2.block_hash()` (or compare the full serialized header bytes), rejecting the transaction with `InvalidStacksTransaction` if the two headers are equal. This restores the requirement that valid poison evidence be two genuinely distinct microblocks signed by the same key at the same sequence number.

### Proof of Concept
Rust integration test plan (chainstate test harness, e.g. in `stackslib/src/chainstate/stacks/tests/block_construction.rs` style):
1. Mine a tenure containing a legitimate microblock stream signed by miner key `K`, so that `get_microblock_pubkey_hash_height(Hash160::from_key(K))` returns `Some(height)` in chainstate.
2. Capture one real `StacksMicroblockHeader` `H` from that stream (with valid `signature`, `sequence`, `prev_block`, `tx_merkle_root`).
3. Construct `TransactionPayload::PoisonMicroblock(H.clone(), H.clone())` and wrap in a signed `StacksTransaction` from an unrelated attacker account.
4. Call `StacksChainState::process_transaction` on this transaction against the chainstate from step 1, while still within `MINER_REWARD_MATURITY` blocks of `height`.
5. Assert:
   - `process_transaction` returns `Ok(..)` (no error), i.e. `check_microblock_header_signer(H, H)` succeeds — first side of the broken equality (`pkh1 == pkh2` trivially true).
   - `env.global_context.database.get_microblock_poison_report(height)` now returns `Some((attacker_principal, H.sequence))` — a poison record was inserted despite `H.block_hash() == H.block_hash()` (zero distinct headers) — second side of the equality (no real fork) fails to be checked.
   - Advance chain state to reward maturity and assert `find_mature_miner_rewards`/`calculate_miner_reward` redirects the coinbase for `height` to `attacker_principal` instead of the original miner, confirming reward theft.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-803)
```rust
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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L805-830)
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
```
