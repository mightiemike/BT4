Based on tracing the exact code path claimed in the question, the central claims do not hold up:

**Binding claimed:** individual `eth_call` success (via `send_raw_deposit_transaction`) == guaranteed success when batched with other deposits in `produce_and_run_system_transactions`.

**Trace:**
1. `send_raw_deposit_transaction` calls `evm.get_call` with `gas: Some(SYSTEM_TX_GAS_LIMIT)` on the pending block state — this is a per-tx gas-limit simulation, not block-cumulative. [1](#0-0) 
2. At production time, `produce_and_run_system_transactions` batches `populate_deposit_system_events` with light-client events and calls `process_sys_txs`. [2](#0-1) 
3. Crucially, `process_sys_txs` applies **each system tx individually** via `self.stf.apply_l2_block_txs(l2_block_info, &txs, ...)` with `txs = vec![signed_tx]` (a single-element vec), inside its own checkpoint — not as one giant batched call.
<invoke name="grep_search">
<parameter name="pattern">nothing</parameter>
</invoke>

### Citations

**File:** crates/sequencer/src/rpc.rs (L339-357)
```rust
        let dep_tx = self
            .context
            .deposit_mempool
            .lock()
            .make_deposit_tx_from_data(deposit.clone().into());

        let start = std::time::Instant::now();
        let tx_res = evm.get_call(
            dep_tx,
            Some(BlockId::pending()),
            None,
            None,
            &mut working_set,
            &self.context.ledger,
        );
        let deposit_tx_call_duration = Instant::now()
            .saturating_duration_since(start)
            .as_secs_f64();
        SM.deposit_tx_call_duration.record(deposit_tx_call_duration);
```

**File:** crates/sequencer/src/runner.rs (L1596-1607)
```rust
        let deposit_events = populate_deposit_system_events(deposit_data);

        system_events.extend(deposit_events);

        self.process_sys_txs(
            l2_block_info,
            working_set_to_discard,
            nonce,
            evm,
            system_events,
        )
    }
```
