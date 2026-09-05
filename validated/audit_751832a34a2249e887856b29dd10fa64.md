### Title
Missing equivocation check in `handle_poison_microblock` allows slashing a miner with a single, non-forked microblock - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`handle_poison_microblock` (called from `process_transaction_payload` for `TransactionPayload::PoisonMicroblock`) never checks that the two submitted `StacksMicroblockHeader`s actually differ or conflict. It only calls `check_microblock_header_signer`, which recovers the pubkey hash from each header independently and requires `pkh1 == pkh2`. Since recovery only depends on the header's own signature, two byte-identical headers trivially satisfy this check, so an attacker can submit a real, single, non-forked microblock header twice and still get the report accepted and rewarded as if an equivocation had occurred.

### Finding Description
The claimed equality is: *"a recorded poison-microblock slash == an actual equivocation (two conflicting microblocks signed by the same key at the same or diverging sequence)."*

`check_microblock_header_signer` at [1](#0-0)  only recovers `pkh1` and `pkh2` from each header's own `check_recover_pubkey()` and requires `pkh1 == pkh2`. It performs no comparison of the two headers' content (sequence, `prev_block`, `tx_merkle_root`, `block_hash`) beyond that.

`handle_poison_microblock` at [2](#0-1)  then:
1. Calls `check_microblock_header_signer(mblock_header_1, mblock_header_2)` to get `pubkh`.
2. Looks up `pubkh` via `get_microblock_pubkey_hash_height` — this only requires that the key was *ever* used to author a microblock in this fork (a real, legitimate miner key), not that a fork actually happened.
3. Checks maturity window (`MINER_REWARD_MATURITY`).
4. Unconditionally records a poison report keyed by `mblock_header_1.sequence`, crediting `sender_principal` as the reporter — with **no check that `mblock_header_1 != mblock_header_2`**, and no check that they represent conflicting content at the same sequence position.

Because a miner's legitimately broadcast microblock headers are public, any unprivileged attacker can take one real header `h` from an honest miner's microblock stream and submit `TransactionPayload::PoisonMicroblock(h, h.clone())`. `check_microblock_header_signer` trivially returns `Ok(pkh)` since both recover to the identical key (it's the same bytes/signature), and `handle_poison_microblock` proceeds to register a poison report for a miner who committed no equivocation.

The maturity/height checks (`microblock_height_opt`, `current_height` comparison) and `check_microblock_header_signer` are the only guards in this path; none of them validates that the two headers are distinct or conflicting. The miner-side fork-detection logic in `validate_parent_microblock_stream` (which the miner's own node uses to *construct* legitimate poison payloads, see [3](#0-2) ) is irrelevant here — an externally-submitted transaction goes straight to `process_transaction_payload` → `run_poison_microblock` → `handle_poison_microblock` at [4](#0-3) , bypassing any node-side fork-detection.

### Impact Explanation
This causes the miner's coinbase to be diverted: at maturation, `find_mature_miner_rewards` looks up `get_poison_microblock_report` for the reward height and, if present, redirects the `POISON_MICROBLOCK_COMMISSION_FRACTION` portion of the coinbase to the recorded reporter instead of the honest miner, as seen in [5](#0-4) . Since no actual fork occurred, this is a reward-mis-payment: funds that should go to an honest miner are redirected to an attacker who merely replayed a public header. Per the given severity taxonomy this class of bug ("poison or reward mis-payment") is a High-severity issue (not Critical, since it does not cause a chain split, invalid/rejected block, or non-reproducible state root — all nodes process the same transaction deterministically and agree on the resulting state).

### Likelihood Explanation
The attacker needs no special privilege: they only need to observe one publicly broadcast microblock header from any miner (a normal unprivileged network participant can do this), then submit a `PoisonMicroblock` transaction with that same header duplicated, before the coinbase matures (`MINER_REWARD_MATURITY` blocks). No BTC stake, no majority position, no other party's key is required. This is repeatable against any miner who publishes microblocks and is a minority/unprivileged-triggerable condition.

### Recommendation
In `handle_poison_microblock` (and/or `check_microblock_header_signer`), require that `mblock_header_1 != mblock_header_2` and, more importantly, that they represent an actual conflict — e.g., same `sequence` with different `block_hash`/`tx_merkle_root`, or a mismatched `prev_block` chain — signed by the same key, before recording any poison report.

### Proof of Concept
Rust integration test plan (extending existing tests in `stackslib/src/chainstate/stacks/db/transactions.rs`, e.g. near `process_poison_microblock` tests):
1. Register a real microblock pubkey hash for a miner key at some height `H` via `StacksChainState::insert_microblock_pubkey_hash`.
2. Construct a single signed `StacksMicroblockHeader` `mblock` (one legitimate, non-conflicting microblock).
3. Build `TransactionPayload::PoisonMicroblock(mblock.header.clone(), mblock.header.clone())`, sign with an attacker/reporter key, and process via `StacksChainState::process_transaction`.
4. Assert `process_transaction` returns `Ok(..)` (equality LHS: "slash recorded").
5. Assert `StacksChainState::get_poison_microblock_report(&mut conn, H)` returns `Some((reporter_addr, mblock.header.sequence))` (equality RHS should require a genuine second, conflicting header — but here only one microblock ever existed).
6. This demonstrates LHS ("slash recorded/reward diverted") ≠ RHS ("genuine equivocation occurred"), confirming the missing-check vulnerability.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L1389-1404)
```rust
            TransactionPayload::PoisonMicroblock(ref mblock_header_1, ref mblock_header_2) => {
                // post-conditions are not allowed for this variant, since they're non-sensical.
                // Their presence in this variant makes the transaction invalid.
                if !tx.post_conditions.is_empty() {
                    let msg = "Invalid Stacks transaction: PoisonMicroblock transactions do not support post-conditions".to_string();
                    info!("{}", &msg);

                    return Err(Error::InvalidStacksTransaction(msg, false));
                }

                let cost_before = clarity_tx.cost_so_far();
                let res = clarity_tx.run_poison_microblock(
                    &origin_account.principal,
                    mblock_header_1,
                    mblock_header_2,
                )?;
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L8731-8806)
```rust
        // deliberate miner fork
        {
            let mut broken_microblocks = microblocks.clone();
            let mut forked_microblocks = vec![];

            let mut new_child_block_header = child_block_header.clone();
            let mut conflicting_microblock = microblocks[0].clone();

            for i in 0..broken_microblocks.len() {
                broken_microblocks[i].header.signature = MessageSignature([0u8; 65]);
                broken_microblocks[i].sign(&privk).unwrap();
                if i + 1 < broken_microblocks.len() {
                    broken_microblocks[i + 1].header.prev_block =
                        broken_microblocks[i].block_hash();
                }

                forked_microblocks.push(broken_microblocks[i].clone());
                if i == num_mblocks / 2 {
                    conflicting_microblock = broken_microblocks[i].clone();

                    let extra_tx = {
                        let auth = TransactionAuth::from_p2pkh(&privk).unwrap();
                        let tx_smart_contract = StacksTransaction::new(
                            TransactionVersion::Testnet,
                            auth.clone(),
                            TransactionPayload::new_smart_contract(
                                "name-contract",
                                &format!("conflicting smart contract {i}"),
                                None,
                            )
                            .unwrap(),
                        );
                        let mut tx_signer = StacksTransactionSigner::new(&tx_smart_contract);
                        tx_signer.sign_origin(&privk).unwrap();
                        tx_signer.get_tx().unwrap()
                    };

                    conflicting_microblock.txs.push(extra_tx);

                    let txid_vecs: Vec<_> = conflicting_microblock
                        .txs
                        .iter()
                        .map(|tx| tx.txid().as_bytes().to_vec())
                        .collect();

                    let merkle_tree = MerkleTree::<Sha512Trunc256Sum>::new(&txid_vecs);

                    conflicting_microblock.header.tx_merkle_root = merkle_tree.root();

                    conflicting_microblock.sign(&privk).unwrap();
                    forked_microblocks.push(conflicting_microblock.clone());
                }
            }

            let l = broken_microblocks.len();
            new_child_block_header.parent_microblock = broken_microblocks[l - 1].block_hash();

            let res = StacksChainState::validate_parent_microblock_stream(
                &block.header,
                &child_block_header,
                &forked_microblocks,
                true,
            );
            assert!(res.is_some());

            let (cutoff, poison_opt) = res.unwrap();
            assert_eq!(cutoff, num_mblocks / 2);
            assert!(poison_opt.is_some());

            let poison = poison_opt.unwrap();
            let TransactionPayload::PoisonMicroblock(h1, h2) = poison else {
                panic!("Unexpected poison type");
            };
            assert_eq!(h2, forked_microblocks[num_mblocks / 2].header);
            assert_eq!(h1, conflicting_microblock.header);
        }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L1027-1052)
```rust
        // was this block penalized for mining a forked microblock stream?
        // If so, find the principal that detected the poison, and reward them instead.
        let poison_recipient_opt =
            StacksChainState::get_poison_microblock_report(clarity_tx, reward_height)?
                .map(|(reporter, _)| reporter);

        if let Some(ref _poison_reporter) = poison_recipient_opt.as_ref() {
            test_debug!(
                "Poison-microblock reporter {} at height {}",
                &_poison_reporter.to_string(),
                reward_height
            );
        } else {
            test_debug!("No poison-microblock report at height {}", reward_height);
        }

        // calculate miner reward
        let (parent_miner_reward, miner_reward) = StacksChainState::calculate_miner_reward(
            mainnet,
            parent_evaluated_epoch.epoch_id,
            &miner,
            &miner,
            &users,
            &parent_miner,
            poison_recipient_opt.as_ref(),
        );
```
