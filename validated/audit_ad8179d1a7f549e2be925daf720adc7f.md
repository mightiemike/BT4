### Title
`handle_poison_microblock` slashes a miner using two non-conflicting, honestly-signed microblock headers - (File: `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Summary
`StacksChainState::handle_poison_microblock` in `stackslib/src/chainstate/stacks/db/transactions.rs` only checks that both submitted `StacksMicroblockHeader`s recover to the same signer pubkey hash via `check_microblock_header_signer`, and that the pubkey hash is within the `MINER_REWARD_MATURITY` window. It never checks that the two headers actually conflict (same `sequence` and same `prev_block`, i.e. a genuine fork/double-sign). The only place this "do these headers actually conflict" check exists is `StacksChainState::will_admit_mempool_tx` in `stackslib/src/chainstate/stacks/db/blocks.rs`, which is a soft mempool-admission filter, not an on-chain validity check enforced during block processing.

### Finding Description
The invariant the question is probing is: **slash == a valid, unreported double-signature under the miner's key**, i.e. two headers `(h1, h2)` should only trigger a poison/slash if `h1.sequence == h2.sequence`, `h1.prev_block == h2.prev_block`, `h1 != h2`, and both are signed by the same microblock key — a genuine equivocation.

Tracing `handle_poison_microblock` [1](#0-0) , the only cross-header check performed is `check_microblock_header_signer`, which merely recovers each header's pubkey and asserts they're equal: [2](#0-1) . There is no assertion that `mblock_header_1.sequence == mblock_header_2.sequence`, that `prev_block` fields match, or that the headers actually differ in content — the properties that make two microblock headers a genuine equivocation rather than two legitimate, sequential, non-conflicting microblocks from the same honest miner.

By contrast, the mempool admission path does enforce this: `will_admit_mempool_tx` explicitly rejects a `PoisonMicroblock` payload when `sequence`, `prev_block`, or `version` differ between the two headers [3](#0-2) . This is only a client-side mempool filter (`MemPoolRejection::PoisonMicroblocksDoNotConflict`), not a consensus-critical check re-verified when a block containing the transaction is actually processed. No equivalent check was found in `stackslib/src/chainstate/stacks/block.rs`, `stackslib/src/chainstate/stacks/mod.rs`, or `stackslib/src/chainstate/stacks/transaction.rs`, and `handle_poison_microblock` itself (the function invoked from `process_transaction_payload` when the block is actually applied to chainstate) does not repeat the mempool's conflict check.

**Attacker's exact input**: Any unprivileged party observes two real, honestly-produced, sequential microblocks from a target miner's microblock stream (e.g. seq=5 and seq=6, both correctly signed and chained, no fork whatsoever). The attacker crafts a `TransactionPayload::PoisonMicroblock(header_seq5, header_seq6)` transaction, signs it with their own key as `sender`, and either submits it directly to a miner who includes it in a block without honest mempool admission (e.g. a colluding/attacker-controlled miner, or a future/alternate code path that bypasses `will_admit_mempool_tx`), or the attacker is themselves the miner and mines the transaction into their own block. Because block-processing's `handle_poison_microblock` does not re-check `sequence`/`prev_block` equality, the transaction is accepted, `insert_microblock_poison` is called, and the honest miner's microblock pubkey hash is marked "poisoned" with the attacker registered as reporter, entitling the attacker to the poison-microblock commission fraction of that miner's coinbase — despite there being no actual double-sign.

### Impact Explanation
This breaks the reward-payment invariant: an honest, non-equivocating miner has their coinbase commission diverted to an attacker who fabricated a "poison" report from two non-conflicting headers. This is a reward mis-payment/theft bounded to the poison-microblock commission fraction of a miner's coinbase, paid to an attacker who did not observe or cause any real equivocation. Per the question's severity mapping this is a bounded reward mis-payment ("High" tier: poison or reward mis-payment bounded to fees/commission) — it steals coinbase commission from a specific honest miner to an attacker-controlled address, and is repeatable against any miner whose microblock stream the attacker can observe (which is any miner, since microblocks are broadcast).

### Likelihood Explanation
Preconditions: the attacker needs (1) to observe two real, sequential microblock headers produced by any active miner (public information from the P2P network), and (2) a way to get the crafted `PoisonMicroblock` transaction included in a block without going through the honest `will_admit_mempool_tx` gate — e.g., by being a miner themselves (a single miner slot, consistent with "attacker controls their own block-commit/leader-key" in the threat model) who directly includes the transaction when building their own block, since a miner assembling its own block does not necessarily re-run `will_admit_mempool_tx` on transactions it authors/includes locally. This requires no majority stake, no signer collusion, and no compromise of the target miner's key — only observation of public microblock broadcasts and control of a single miner slot or a cooperating relay path. This is fully within the declared unprivileged attacker capabilities.

### Recommendation
Add the same equivocation checks from `will_admit_mempool_tx` directly into `StacksChainState::handle_poison_microblock` (or a shared validation helper it calls) before accepting the report: require `mblock_header_1.sequence == mblock_header_2.sequence`, `mblock_header_1.prev_block == mblock_header_2.prev_block`, `mblock_header_1.version == mblock_header_2.version`, and `mblock_header_1 != mblock_header_2` (i.e., differing signature/content at the identical sequence/prev_block position), in addition to the existing same-signer check. Reject the transaction with `Error::InvalidStacksTransaction` if these do not hold, mirroring `MemPoolRejection::PoisonMicroblocksDoNotConflict`.

### Proof of Concept
Rust integration test plan (in `stackslib/src/chainstate/stacks/db/transactions.rs` test module, alongside `process_poison_microblock_same_block`):
1. Set up chainstate as in `process_poison_microblock_same_block` [4](#0-3) , inserting `block_pubkh` via `insert_microblock_pubkey_hash`.
2. Build two **non-conflicting** microblocks signed by `block_privk`: `mblock_1` with `sequence = 5`, `prev_block = X`; `mblock_2` with `sequence = 6`, `prev_block = mblock_1.block_hash()` (a valid, non-forked, contiguous chain — assert `mblock_1.header.sequence != mblock_2.header.sequence` and `mblock_1.header.prev_block != mblock_2.header.prev_block`).
3. Construct and sign a `TransactionPayload::PoisonMicroblock(mblock_1.header, mblock_2.header)` from an attacker/reporter key, bypassing `will_admit_mempool_tx` (call `StacksChainState::process_transaction` directly, as the existing tests do, not `mem_pool.miner_submit`).
4. Assert **before**: `StacksChainState::get_poison_microblock_report(&mut conn, block_height)` returns `None` (equality: no slash exists for this honest key).
5. Call `StacksChainState::process_transaction(...)` and assert it succeeds (**this is the bug** — it should return `Err(Error::InvalidStacksTransaction(..))` because the headers do not conflict).
6. Assert **after**: `get_poison_microblock_report` now returns `Some((reporter_addr, 5))`, proving an unjust slash was recorded for `block_pubkh` despite `mblock_1`/`mblock_2` forming a valid, non-equivocating, contiguous microblock chain — violating "slash == valid, unreported double-signature."

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L722-756)
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
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L5478-5522)
```rust
    fn process_poison_microblock_same_block() {
        let privk = StacksPrivateKey::from_hex(
            "6d430bb91222408e7706c9001cfaeb91b08c2be6d5ac95779ab52c6b431950e001",
        )
        .unwrap();
        let auth = TransactionAuth::from_p2pkh(&privk).unwrap();
        let addr = auth.origin().address_testnet();

        let balances = vec![(addr.clone(), 1000000000)];

        let mut chainstate = TestChainstateBuilder::new_testnet(function_name!())
            .with_balances(balances)
            .build();

        let block_privk = StacksPrivateKey::from_hex(
            "2f90f1b148207a110aa58d1b998510407420d7a8065d4fdfc0bbe22c5d9f1c6a01",
        )
        .unwrap();

        let block_pubkh =
            Hash160::from_node_public_key(&StacksPublicKey::from_private(&block_privk));

        let reporter_privk = StacksPrivateKey::from_hex(
            "e606e944014b2a9788d0e3c8defaf6bc44b1e3ab881aaba32faa6e32002b7e1f01",
        )
        .unwrap();
        let reporter_addr = TransactionAuth::from_p2pkh(&reporter_privk)
            .unwrap()
            .origin()
            .address_testnet();

        for (dbi, burn_db) in ALL_BURN_DBS.iter().enumerate() {
            let mut conn = chainstate.block_begin(
                *burn_db,
                &FIRST_BURNCHAIN_CONSENSUS_HASH,
                &FIRST_STACKS_BLOCK_HASH,
                &ConsensusHash([(dbi + 1) as u8; 20]),
                &BlockHeaderHash([(dbi + 1) as u8; 32]),
            );

            StacksChainState::insert_microblock_pubkey_hash(&mut conn, 1, &block_pubkh).unwrap();

            let height_opt =
                StacksChainState::has_microblock_pubkey_hash(&mut conn, &block_pubkh).unwrap();
            assert_eq!(height_opt.unwrap(), 1);
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
