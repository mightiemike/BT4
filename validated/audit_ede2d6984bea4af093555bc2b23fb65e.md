## Analysis

I found a valid analog: `transaction_pool_strict_nonce_ttl_blocks`, a fixed and comparatively short block-height TTL that causes valid, sequentially-ordered pending transactions to be permanently discarded from the transaction pool during ordinary chunk-preparation, mirroring the Buffer `MAX_WAIT_TIME` issue where a short deadline silently cancels legitimately-queued work.

### Title
Short `strict_nonce_ttl` window silently discards valid strict-nonce queued transactions from the pool - (File: `chain/chain/src/runtime/mod.rs`)

### Summary
NEAR's `NonceMode::Strict` transactions (and self-signed state-init/bootstrap transactions, which are forced into strict semantics regardless of what they declare) must be applied in exact nonce order. When a later-nonce transaction is peeked before its predecessor has landed, `prepare_transactions_extra` treats it as "gapped" and keeps it in the pool only while `validate_tx_ttl` returns true; the TTL is `strict_nonce_ttl_check`, computed from `prev_block_height.saturating_sub(base_header.height()) <= strict_nonce_ttl`, with `strict_nonce_ttl` defaulting to `transaction_pool_strict_nonce_ttl_blocks = 64` blocks. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
Once a gapped strict-nonce transaction's `block_hash` age exceeds this 64-block TTL, `prepare_transactions_extra` calls `transaction_group_iter.next()` and `continue`s without ever placing the transaction in `prepared_transactions` or `skipped_transactions`, i.e. it is popped out of the group iterator and never reintroduced to the pool — a silent, permanent eviction. [4](#0-3) 

This is architecturally identical to the Buffer `resolveQueuedTrades` bug: a short, fixed wait-window (`MAX_WAIT_TIME` = 1 minute vs. `strict_nonce_ttl` = 64 blocks) is used to decide whether a legitimately-queued, not-yet-processed item should be cancelled/discarded rather than honored, and the window is short enough that ordinary throughput conditions (crowded chunks, gas/size limits repeatedly evicting the group before the predecessor transaction gets included, `MAX_TXS_PER_GROUP_PER_VISIT` capping how many times a group is even visited per chunk) can exhaust it even though nothing is wrong with the transaction itself. Unlike the normal `transaction_validity_period` check (which is evaluated against the tx's own `block_hash` and is the intended global expiry), this TTL applies to a chain of *dependent* transactions: an later-nonce transaction can be timed out purely because the transaction(s) in front of it in the strict sequence have not yet been included, independent of whether the later transaction is itself still within its protocol validity period.

The test suite explicitly documents that this eviction is destructive and unconditional: `test_strict_nonce_gap_ttl_eviction` shows that when the TTL check fails, "all gapped txs from both signers are evicted" and the pool size drops to 0, with the comment "Nothing else on the same uninitialized account gets that treatment: the account's nonce cannot authorize any other transaction, so holding one would only keep junk around until its TTL expired" acknowledging the discard-on-timeout design, but this reasoning does not hold for ordinary, funded, sequential strict-nonce senders whose earlier transaction is merely delayed by normal chunk congestion rather than being genuinely unresolvable. [5](#0-4) 

### Impact Explanation
Any unprivileged transaction submitter using `NonceMode::Strict` (or self-signed state-init/bootstrap flows, which are always treated as strict) who submits a batch of sequentially-nonced transactions can have the entire tail of the batch permanently and silently dropped from the pool if the chunk producer is unable to include the head-of-sequence transaction within `strict_nonce_ttl_check`'s ~64-block window — a window that is dwarfed by ordinary block-production variance, gas/size-limited chunk selection, or transient congestion. Because eviction happens without any error surfaced to the caller (the RPC call for the original submission already returned success/broadcast-acknowledged), the sender has no signal that dependent transactions were discarded and must independently detect the failure and resubmit, which for state-init/bootstrap flows can affect account creation flows relying on strict ordering. This matches the reported bug class: a short wait-time silently cancels legitimately queued protocol work during normal throughput pressure, degrading service availability for a class of users (strict-nonce senders) without their consent or knowledge.

### Likelihood Explanation
This does not require any malicious actor, validator collusion, or network attack — it is triggered purely by an ordinary user submitting more than one strict-nonce transaction at once during periods where the pool/chunk is busy (e.g., `PrepareTransactionsLimit::Gas`/`Size`/`Time` repeatedly stops the head transaction from being included across a stretch of >64 blocks, or `MAX_TXS_PER_GROUP_PER_VISIT` limits how often the group is revisited). Given that block time is roughly 1 second, 64 blocks is on the order of one minute, a plausible delay under sustained network load — directly analogous to the reported 1-minute `MAX_WAIT_TIME` being too short under congestion.

### Recommendation
Reconsider whether gapped strict-nonce transactions should ever be unconditionally discarded rather than simply left in the pool (bounded by the existing `transaction_pool_size_limit`) or returned with an explicit rejection reason the RPC caller can observe; if a TTL-based eviction is retained, increase `transaction_pool_strict_nonce_ttl_blocks` materially above the normal chunk-selection contention window, or surface eviction to the submitter via `PrepareTransactionsLimit`/response metadata rather than silently dropping the transaction from `transaction_group_iter` without reintroduction.

### Proof of Concept
1. Submit transaction A with `NonceMode::Strict` nonce N to account X, and simultaneously submit transaction B (nonce N+1) and C (nonce N+2) from the same signer.
2. Ensure chunk selection is saturated for >64 blocks before A is included (e.g., flood the pool with other higher-priority/funded transactions to repeatedly hit `PrepareTransactionsLimit::Gas`/`Size`/`Time` before A's group is drained), as exercised by `test_prepare_transactions_flood_respects_time_limit_and_fairness`. [6](#0-5) 
3. Once `prev_block_height.saturating_sub(base_header.height()) > strict_nonce_ttl` (64 blocks) for B and C's `block_hash`, `validate_tx_ttl` returns false and `prepare_transactions_extra` calls `.next(); continue;` on them, per `test_strict_nonce_gap_ttl_eviction`'s "TTL=0" branch, permanently removing B and C from the pool. [7](#0-6) 
4. Even after A finally lands, B and C are gone from the pool and must be resubmitted by the user, who received no explicit failure notice for them.

### Citations

**File:** chain/chain/src/runtime/mod.rs (L1020-1033)
```rust
                // Nonce gap check: if the tx requires sequential nonces and
                // there is a gap, leave it in the pool for a future block
                // rather than popping and discarding it.
                let current_nonce =
                    gap_check_nonce(&state_update.trie_update.trie, &signer_overlay, tx_peek)?;
                if let Some(current_nonce) = current_nonce
                    && tx_peek.nonce().nonce() > current_nonce.saturating_add(1)
                {
                    if !validate_tx_ttl(tx_peek.to_signed_tx()) {
                        transaction_group_iter.next();
                        continue;
                    }
                    break;
                }
```

**File:** chain/chain/src/store/mod.rs (L523-537)
```rust
    /// Builds a closure that checks whether a gapped strict-nonce transaction's
    /// block_hash is still within the TTL window relative to `prev_block_height`.
    pub fn strict_nonce_ttl_check(
        &self,
        prev_block_height: BlockHeight,
        strict_nonce_ttl: BlockHeightDelta,
    ) -> impl Fn(&SignedTransaction) -> bool + Send + 'static {
        let chain_store = self.clone();
        move |tx: &SignedTransaction| -> bool {
            let Ok(base_header) = chain_store.get_block_header(&tx.transaction.block_hash()) else {
                return false;
            };
            prev_block_height.saturating_sub(base_header.height()) <= strict_nonce_ttl
        }
    }
```

**File:** core/chain-configs/src/client_config.rs (L600-602)
```rust
pub fn default_transaction_pool_strict_nonce_ttl_blocks() -> BlockHeightDelta {
    64
}
```

**File:** chain/chain/src/runtime/tests.rs (L2081-2082)
```rust
#[test]
fn test_prepare_transactions_flood_respects_time_limit_and_fairness() {
```

**File:** chain/chain/src/runtime/tests.rs (L2785-2855)
```rust
/// Gapped strict-nonce transactions are evicted when their block_hash is older
/// than the TTL, but kept when still within range. Multiple expired txs from
/// the same signer group and across different signers are all evicted.
#[test]
fn test_strict_nonce_gap_ttl_eviction() {
    let (env, chain, _) = get_test_env_with_chain_and_pool();
    let prev_hash = env.head.prev_block_hash;
    // env.head.height == 1, prev_hash is genesis (height 0).

    const TEST_SEED: RngSeed = [3; 32];
    let mut pool = TransactionPool::new(TEST_SEED, None, "");

    // Insert 3 gapped txs from test1 (nonces 100, 101, 102) and 2 from test2 (nonces 200, 201).
    // All have ak_nonce=0 so all are gapped.
    let signer1 = InMemorySigner::test_signer(&"test1".parse::<AccountId>().unwrap());
    let signer2 = InMemorySigner::test_signer(&"test2".parse::<AccountId>().unwrap());
    for nonce in [100, 101, 102] {
        let tx = SignedTransaction::from_actions_v1_strict(
            TransactionNonce::from_nonce(nonce),
            "test1".parse().unwrap(),
            "test2".parse().unwrap(),
            &signer1,
            vec![Action::Transfer(TransferAction { deposit: Balance::from_yoctonear(1) })],
            prev_hash,
        );
        pool.insert_transaction(ValidatedTransaction::new_for_test(tx));
    }
    for nonce in [200, 201] {
        let tx = SignedTransaction::from_actions_v1_strict(
            TransactionNonce::from_nonce(nonce),
            "test2".parse().unwrap(),
            "test1".parse().unwrap(),
            &signer2,
            vec![Action::Transfer(TransferAction { deposit: Balance::from_yoctonear(1) })],
            prev_hash,
        );
        pool.insert_transaction(ValidatedTransaction::new_for_test(tx));
    }
    assert_eq!(pool.len(), 5);

    // TTL=1: 1 <= 0 + 1 holds, all gapped txs stay in the pool.
    let ttl_valid = chain.chain_store().strict_nonce_ttl_check(env.head.height, 1);
    let (prepared, skipped) = prepare_transactions_extra(
        &env,
        &chain,
        &mut PoolIteratorWrapper::new(&mut pool),
        HashSet::new(),
        &ttl_valid,
        &mut PendingTxCheckResult::always_admit(),
        None,
    )
    .unwrap();
    assert!(prepared.transactions.is_empty());
    assert!(skipped.0.is_empty());
    assert_eq!(pool.len(), 5, "all gapped txs should be kept when TTL is sufficient");

    // TTL=0: 1 <= 0 + 0 does not hold, all gapped txs from both signers are evicted.
    let ttl_expired = chain.chain_store().strict_nonce_ttl_check(env.head.height, 0);
    let (prepared, skipped) = prepare_transactions_extra(
        &env,
        &chain,
        &mut PoolIteratorWrapper::new(&mut pool),
        HashSet::new(),
        &ttl_expired,
        &mut PendingTxCheckResult::always_admit(),
        None,
    )
    .unwrap();
    assert!(prepared.transactions.is_empty());
    assert!(skipped.0.is_empty());
    assert_eq!(pool.len(), 0, "all gapped txs should be evicted when TTL expired");
```
