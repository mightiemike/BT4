### Title
`handle_poison_microblock` slashes miners based on a pubkey-hash match alone, without verifying the two headers actually equivocate (same `sequence` + same `prev_block`) - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`check_microblock_header_signer` (the only header-consistency check invoked by `handle_poison_microblock`) verifies only that both microblock headers recover to the same public-key hash; it never checks that `sequence`, `prev_block`, or `version` match. The stronger consistency check (`PoisonMicroblocksDoNotConflict`, requiring equal `sequence`/`prev_block`/`version`) only exists in the mempool admission gate `will_admit_mempool_tx`, not in the consensus-level execution path that actually slashes a miner.

### Finding Description
The intended equality for a valid poison-microblock proof is: *two headers form a genuine fork of the same stream* — i.e. `h1.sequence == h2.sequence && h1.prev_block == h2.prev_block && h1 != h2`, both signed by the anchor block's registered microblock key.

`check_microblock_header_signer` only enforces:
```
pkh1 = h1.check_recover_pubkey()
pkh2 = h2.check_recover_pubkey()
pkh1 == pkh2
``` [1](#0-0) 

`handle_poison_microblock`, which runs the actual state transition (slashing/commission bookkeeping), calls only `check_microblock_header_signer` and never separately checks `sequence`, `prev_block`, or `version` equality between `mblock_header_1` and `mblock_header_2`: [2](#0-1) 

The stronger check that would reject two unrelated headers exists **only** in the mempool admission path `will_admit_mempool_tx`: [3](#0-2) 

That mempool check is a pre-relay gate, not part of consensus execution. A miner assembling their own block does not have to route their own transaction through the mempool's `will_admit_mempool_tx`; and even if relayed generally, this gate is advisory (nodes are not required to re-validate this constraint before consensus applies the payload). Since `handle_poison_microblock` is the function actually invoked by Clarity/consensus processing of the `PoisonMicroblock` payload (via `stacksk_vm/clarity.rs`), the only enforced invariant at the point that funds/commissions are actually moved is "same signer pubkey hash" — nothing about the two headers actually representing a fork of one stream.

Because microblock signing keys are derived deterministically by the miner's `Keychain` and can legitimately repeat across different (non-adjacent) tenures (as seen in `stacks-node/src/tests/mempool.rs` key-derivation-by-index pattern), an honest miner can reuse the same microblock pubkey-hash in two unrelated tenures without ever equivocating. An attacker can then take one header from tenure A (sequence N) and one header from a completely unrelated tenure B (also happens to have some header at sequence N), both signed with the reused key but with different `prev_block` values, and submit them as `PoisonMicroblock(h1, h2)`. `check_microblock_header_signer` accepts it because `pkh1 == pkh2`, and `handle_poison_microblock` proceeds to record a poison report against the pubkey-hash's registered block height, entitling the attacker/reporter to the punished miner's coinbase commission — despite no actual equivocation having occurred.

### Impact Explanation
This allows an unprivileged attacker to trigger the poison-microblock slashing/commission-redirection logic against an honest miner who never equivocated, redirecting that miner's future coinbase reward to the false reporter. This is a reward-theft / reward-misdirection bug bounded to a single miner's coinbase, matching the "reward mis-payment" impact category. It does not by itself cause a chain split (all honest nodes execute the same faulty logic identically and agree on the wrong outcome), so its severity is bounded by the definitions given — a consensus-consistent but incorrect reward redirection.

### Likelihood Explanation
Preconditions: the attacker needs to find (or wait for) two anchored blocks in different tenures whose microblock-signing keys collide in pubkey-hash (this can happen by design if the keychain schedule reuses microblock key indices across tenures, or via any other mechanism producing a genuine, non-malicious hash collision/reuse) and to find matching `sequence` numbers, which is easy since sequences start at 0 and are small integers, making collision across independent streams likely. No majority stake, no privileged role, and no signer key is required — a single unprivileged participant who can submit a transaction (either into their own mined block or via any relay path that doesn't re-run `will_admit_mempool_tx`) suffices.

### Recommendation
Move the header-consistency checks currently only present in `will_admit_mempool_tx` (`sequence` equality, `prev_block` equality, `version` equality, and header inequality) into `check_microblock_header_signer` or `handle_poison_microblock` itself, so that consensus-level execution enforces the same invariant as mempool admission. Reject any `PoisonMicroblock` payload whose two headers do not actually represent a genuine fork point.

### Proof of Concept
1. In a chainstate test harness (as in `stackslib/src/chainstate/stacks/db/transactions.rs` `process_poison_microblock_*` tests), register the same microblock pubkey hash at two different heights/tenures via `insert_microblock_pubkey_hash`.
2. Construct `mblock_1` at `sequence = N`, `prev_block = A` (from tenure 1's stream) and `mblock_2` at `sequence = N`, `prev_block = B != A` (from tenure 2's unrelated stream), both signed with the same private key.
3. Submit `TransactionPayload::PoisonMicroblock(mblock_1.header, mblock_2.header)` directly through `StacksChainState::process_transaction` (bypassing `will_admit_mempool_tx`).
4. Assert: `process_transaction` succeeds and a poison/commission report is recorded (current behavior) — this is the bug. A fixed implementation should instead return `Error::InvalidStacksTransaction` because `mblock_1.header.prev_block != mblock_2.header.prev_block`.
Reference existing tests `process_poison_microblock_same_block` and `process_poison_microblock_invalid_transaction` for harness setup patterns. [4](#0-3)

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L722-757)
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
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L5478-5560)
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

            // make poison
            let mblock_1 =
                make_signed_microblock(&block_privk, &privk, BlockHeaderHash([0x11; 32]), 123);
            let mblock_2 =
                make_signed_microblock(&block_privk, &privk, BlockHeaderHash([0x11; 32]), 123);
            assert!(mblock_1 != mblock_2);

            // report poison (in the same block)
            let mut tx_poison_microblock = StacksTransaction::new(
                TransactionVersion::Testnet,
                TransactionAuth::from_p2pkh(&reporter_privk).unwrap(),
                TransactionPayload::PoisonMicroblock(
                    mblock_1.header.clone(),
                    mblock_2.header.clone(),
                ),
            );

            tx_poison_microblock.chain_id = 0x80000000;
            tx_poison_microblock.set_tx_fee(0);

            let mut signer = StacksTransactionSigner::new(&tx_poison_microblock);
            signer.sign_origin(&reporter_privk).unwrap();
            let signed_tx_poison_microblock = signer.get_tx().unwrap();

            // process it!
            let (fee, receipt) = StacksChainState::process_transaction(
                &mut conn,
                &signed_tx_poison_microblock,
                false,
                None,
            )
            .unwrap();

            // there must be a poison record for this microblock, from the reporter, for the microblock
            // sequence.
            let report_opt = StacksChainState::get_poison_microblock_report(&mut conn, 1).unwrap();
            assert_eq!(report_opt.unwrap(), (reporter_addr.clone(), 123));
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
