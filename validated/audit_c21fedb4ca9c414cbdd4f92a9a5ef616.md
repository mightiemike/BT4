### Title
Unconditional `remove_deposits` after block production drops failed deposits from the mempool without crediting cBTC - (`crates/sequencer/src/runner.rs`, `crates/sequencer/src/deposit_data_mempool.rs`)

### Summary
`produce_l2_block_inner` fetches a batch of pending deposits via `fetch_deposits`, but only deposits whose system transaction actually applies successfully in `process_sys_txs` end up in `txs_to_run`/the produced block. Despite this, `remove_deposits(&deposit_data)` at the end of `produce_l2_block_inner` is called with the *entire original* fetched list, not the filtered/successful subset, so a deposit whose Bridge system transaction reverted is still permanently purged from the deposit mempool as if it had succeeded.

### Finding Description
The binding that must hold: `deposit removed from DepositDataMempool == deposit's BridgeDeposit system tx was actually applied and committed on-chain (cBTC credited)`.

Trace:
1. `send_raw_deposit_transaction` (`crates/sequencer/src/rpc.rs:330-388`) simulates the deposit with `evm.get_call(... Some(BlockId::pending()) ...)` and, on success, calls `add_deposit_tx`, admitting the raw deposit blob into `DepositDataMempool`.
2. During block production, `produce_l2_block_inner` (`crates/sequencer/src/runner.rs:506-596`) calls `self.deposit_mempool.lock().fetch_deposits(...)` (line 517-520) to get `deposit_data` — this does not remove anything, it just reads.
3. `dry_run_transactions` → `produce_and_run_system_transactions` → `process_sys_txs` (`crates/sequencer/src/runner.rs:1617-1706`) builds one `BridgeDeposit` system tx per entry in `deposit_data` and applies each with `self.stf.apply_l2_block_txs`. If it fails with `L2BlockModuleCallError::EvmSystemTransactionNotSuccessful` (e.g. Bridge's internal witness/`moveTx` validation fails even though `calc_tx_id`/ABI decoding and the earlier `eth_call` simulation succeeded), the code explicitly reverts that tx's working set and `continue`s (`crates/sequencer/src/runner.rs:1682-1697`), **without pushing it into `all_txs`**. That failed deposit is silently excluded from the block that gets built (`txs_to_run`).
4. Back in `produce_l2_block_inner`, after the block is built and saved, line 636-638 does:
```rust
if !deposit_data.is_empty() {
    let removed_count = self.deposit_mempool.lock().remove_deposits(&deposit_data);
```
`deposit_data` here is still the *original, unfiltered* list fetched in step 2 — it was never filtered down to only the deposits that succeeded and made it into `txs_to_run`. There is no analogous `l1_fee_failed_txs`-style tracking of failed deposits (that mechanism only exists for ordinary EVM mempool transactions).
5. `remove_deposits` (`crates/sequencer/src/deposit_data_mempool.rs:79-109`) recomputes `calc_tx_id` for every deposit in this full list and removes it from both `accepted_deposit_txs` and `pending_deposits`, regardless of whether it was actually applied to the chain state.

Root cause: the code conflates "fetched for this block attempt" with "successfully applied in this block." Because `deposit_data` (the fetch result) rather than the filtered success subset is passed to `remove_deposits`, any deposit that fails Bridge's on-chain validation after passing the earlier `eth_call` admission check at `send_raw_deposit_transaction` is dropped from the mempool with no `Deposit` event emitted and no cBTC credited — and it is also removed from `pending_deposits`, so a user resubmission of the exact same bytes will again pass the (still-lenient) `eth_call` simulation, again fail on-chain, and again be silently dropped, looping forever for a deterministic mismatch between the `eth_call` simulation and the Bridge's actual system-tx validation.

No existing guard (`is_system_caller`, `CitreaTransactionValidator`, `Auth`, `verify_batch_proof_seq_comm_relation`, etc.) touches this because this is sequencer-local mempool bookkeeping, not part of the proven state transition; the state transition itself is safe (the revert correctly rolls back state), but the sequencer's off-chain deposit mempool falsely marks the deposit as handled.

### Impact Explanation
A real Bitcoin deposit whose corresponding blob is submitted via `citrea_sendRawDepositTransaction`, passes the lenient `eth_call` pending-block simulation, but fails Bridge's stricter on-chain system-tx validation, is permanently removed from the sequencer's deposit mempool without ever crediting cBTC on L2. This is a "funds permanently frozen" scenario — the depositor's BTC is locked/spent to the bridge multisig on L1 with no corresponding, mintable claim path left, since the sequencer will never re-attempt inclusion of the purged deposit. This matches the Critical impact category. It is repeatable for any deposit hitting this simulation/execution mismatch and is not limited to a single block; it recurs each time such a deposit blob is (re)submitted and picked up by `fetch_deposits`.

### Likelihood Explanation
Requires only `deposit_mempool_fetch_limit >= 1` (default sequencer operation) and a deposit blob that: (a) ABI-decodes for `calc_tx_id`, (b) passes the `eth_call` simulation against `BlockId::pending()`, and (c) fails the stricter validation performed inside the real `apply_l2_block_txs`/Bridge system-tx execution (e.g., malformed witness data, or any transient/racy divergence between the two evaluation points). No privileged role is needed — any user calling the public `citrea_sendRawDepositTransaction` RPC with such a blob triggers it. The cost is simply the BTC fee to produce/broadcast such a deposit transaction, or none at all if the mismatch can be reproduced purely with the ABI-encoded `moveTx` bytes (RPC-level, no need to mine anything, since deposit blobs are self-contained ABI-encoded data, not on-chain Bitcoin lookups performed here).

### Recommendation
Track per-deposit success in `process_sys_txs`/`produce_and_run_system_transactions` (mirroring the `l1_fee_failed_txs` pattern for EVM txs) and only pass the deposits that were actually included in `txs_to_run` (i.e., successfully applied) to `remove_deposits`. Deposits whose system tx reverted should remain in the mempool (or be moved to a distinct "invalid deposit" bucket with an explicit event/log) rather than being silently discarded.

### Proof of Concept
```rust
// crates/sequencer/src/deposit_data_mempool.rs (or a new integration test in crates/sequencer)
#[test]
fn test_remove_deposits_should_not_remove_failed_deposit() {
    // 1. Build two deposits: DEPOSIT_OK (fully valid) and DEPOSIT_BAD (decodes for calc_tx_id,
    //    passes a lenient `eth_call` simulation against BlockId::pending(), but is crafted so
    //    that Bridge's internal validation (malformed witness) fails when actually applied via
    //    `apply_l2_block_txs`, i.e. returns L2BlockModuleCallError::EvmSystemTransactionNotSuccessful).
    // 2. Add both to DepositDataMempool via add_deposit_tx.
    // 3. Simulate produce_l2_block_inner behavior directly:
    //    let deposit_data = mempool.fetch_deposits(2); // returns both
    //    let (all_txs, _working_set) = process_sys_txs(...); // only DEPOSIT_OK ends up in all_txs
    //    assert_eq!(all_txs.len(), 1);
    // 4. Reproduce the bug: call mempool.remove_deposits(&deposit_data) with the FULL fetched
    //    list (as runner.rs currently does), not just the successful subset.
    // 5. Assert the bug:
    assert_eq!(mempool.remove_deposits(&deposit_data), 2); // both removed
    // DEPOSIT_BAD was never applied (no Deposit event, no cBTC credited) yet it's gone from
    // both accepted_deposit_txs and pending_deposits.
    assert!(mempool.add_deposit_tx(DEPOSIT_BAD.clone()).unwrap()); // can be re-added (not "already pending")
    // but resubmission will again pass eth_call and again fail on-chain: infinite loop,
    // deposit never credited => binding
    //   "deposit removed from mempool == deposit applied on-chain and cBTC credited"
    // is broken (left side true, right side false).
}
```
Fix verification: after applying the recommended change, `remove_deposits` should be called only with the filtered `[DEPOSIT_OK]`, and the assertion `mempool.accepted_deposit_txs.contains(DEPOSIT_BAD) == true` after a failed block-production attempt should hold, allowing the deposit to be retried in a later block or explicitly surfaced as invalid.