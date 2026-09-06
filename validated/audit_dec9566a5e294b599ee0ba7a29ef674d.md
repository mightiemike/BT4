### Title
`handle_poison_microblock` accepts any two same-signer microblock headers regardless of sequence, allowing false poison reports against honest miners - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`StacksChainState::check_microblock_header_signer` (line 686) only verifies that `mblock_hdr_1` and `mblock_hdr_2` were signed by the same key; it never checks that the two headers share the same sequence number or otherwise prove equivocation. Consequently `handle_poison_microblock` (line 722) will accept a `PoisonMicroblock` transaction built from two headers taken from the *same, non-forked* microblock stream (e.g. seq=5 and seq=9), which is exactly what an honest miner produces.

### Finding Description
The intended equality for a valid poison report is: *two headers signed by the same key that commit to conflicting content at the same sequence number* (true equivocation/fork). The actual check performed is only:
```
stackslib/src/chainstate/stacks/db/transactions.rs:686-713
fn check_microblock_header_signer(...) -> Result<Hash160, Error> {
    let pkh1 = mblock_hdr_1.check_recover_pubkey()...
    let pkh2 = mblock_hdr_2.check_recover_pubkey()...
    if pkh1 != pkh2 { return Err(...) }
    Ok(pkh1)
}
``` [1](#0-0) 

No comparison of `mblock_header_1.sequence` vs `mblock_header_2.sequence`, and no check that the headers are actually different objects with conflicting parent/prev-block linkage, is performed anywhere in `handle_poison_microblock`: [2](#0-1) 

The only subsequent logic that touches `.sequence` is the "lower sequence wins" bookkeeping used to determine which reporter gets credit — it assumes any accepted pair is already a valid fork proof and merely picks the earliest sequence number for commission purposes:
```
stackslib/src/chainstate/stacks/db/transactions.rs:821-833
if mblock_header_1.sequence < seq { ... insert_microblock_poison(...) }
``` [3](#0-2) 

Because two headers from a normal, non-forked stream produced by the same miner key trivially satisfy `pkh1 == pkh2`, any attacker can submit `PoisonMicroblock(header[i], header[j])` for `i != j` from the legitimate stream and have it accepted as a valid poison report, even though no equivocation occurred.

### Impact Explanation
This lets any unprivileged party file a false poison report against an honest miner who produced a normal (non-forked) microblock stream of length ≥2. The consequence is a wrongful slashing / reward mis-payment: the honest miner's coinbase for that block height can be redirected to the false reporter once `MINER_REWARD_MATURITY` accounting is applied, and the poisoned public key hash is marked slashed in state for that height. This is a reward-mispayment bug bounded to a single miner's block reward — matching the "poison or reward mis-payment" High-severity category in scope, since it does not itself cause a chain split but does cause funds to be paid to the wrong party.

### Likelihood Explanation
No special privilege or stake is required. The attacker only needs to observe two already-broadcast microblock headers from an honest miner's normal stream (public information available to anyone via microblocks) and submit a single `PoisonMicroblock` transaction referencing them. This is trivially repeatable against every honest miner that ever emits more than one microblock at different sequence numbers, which is the common case for microblock-based mining. The precondition is simply availability of any two headers from a stream — no forking, no majority stake, no privileged role.

### Recommendation
`check_microblock_header_signer` (or a caller-side check in `handle_poison_microblock`) must additionally require that `mblock_header_1.sequence == mblock_header_2.sequence` and that the two headers are distinct (different block hash / signature) at that shared sequence, i.e. it must validate an actual equivocation rather than merely matching the signer key. Reject the transaction with `InvalidStacksTransaction` if the sequence numbers differ.

### Proof of Concept
1. In an integration test (e.g. extending `stackslib/src/chainstate/stacks/tests/block_construction.rs`), have a single miner key produce a valid, non-forked microblock stream of length 3 (`mblock[0..3]`), each with strictly increasing `sequence` and each correctly chained via `prev_block`.
2. Construct `PoisonMicroblock(mblock[0].header, mblock[2].header)` — two headers from the same honest stream at different sequences (0 and 2), signed by the same key, with no conflicting fork.
3. Feed this transaction through `StacksChainState::handle_poison_microblock`.
4. Assert on both sides of the intended equality:
   - Expected: `handle_poison_microblock` returns `Err(Error::InvalidStacksTransaction(...))` because there is no equivocation at a common sequence.
   - Actual (bug): the call returns `Ok(Value::Tuple(...))`, recording a poison report and crediting the reporter — demonstrating that TENURE_REWARD/slashing bookkeeping is triggered for a non-forked, honest microblock stream.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-857)
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
