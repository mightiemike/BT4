### Title
Absence of a Fee-Proportional Minimum for L1-Handler Transactions Enables Cheap Compute-Exhaustion via Dust L1→L2 Messages - (File: crates/blockifier/src/transaction/l1_handler_transaction.rs)

### Summary
L1-handler transactions are admitted and executed by the sequencer based only on the check that `paid_fee_on_l1 != 0`, with no minimum tied to the actual L2 resources they are allowed to consume. Since `l1_handler_max_amount_bounds` (a fixed, versioned-constant cap, e.g. `l2_gas: 100000000` in `0_14_1`/`0_14_2`/`0_14_3`) is independent of the fee paid, an attacker can send arbitrarily cheap (dust) `sendMessageToL2` calls from L1 and have the sequencer execute up to that fixed compute budget on L2, essentially for free.

### Finding Description
`L1HandlerTransaction::execute_raw` only requires `self.paid_fee_on_l1 != Fee(0)` post-execution; it never scales the allowed execution budget (`l1_handler_max_amount_bounds`) to the amount actually paid on L1: [1](#0-0) 

The sequencer-side code paths that construct and admit these transactions treat `paid_fee_on_l1` as an essentially unchecked/placeholder value — multiple call sites even hardcode `Fee(1)`: [2](#0-1) [3](#0-2) 

The maximum resource budget that any L1 message can consume, regardless of the (dust) fee paid, is fixed by versioned constants: [4](#0-3) 

Once scraped, the L1 handler transaction is unconditionally added to the proposable index and is included in blocks by `TransactionManager::get_txs`, with no minimum-value/dust filtering at any layer between L1 scraping and blockifier execution: [5](#0-4) 

Every L1 message causes real Cairo execution work on the sequencer (contract call, storage reads/writes, etc.) up to the fixed `l1_handler_max_amount_bounds`, whether or not the fee paid on L1 covers any meaningful fraction of that cost. This mirrors the reported bug class exactly: an attacker can flood a system with tiny ("dust") transactions because no minimum amount is enforced relative to the resources consumed, wasting compute/gas and degrading throughput for legitimate users. On Starknet, this is compounded by the fact that the cost incurred by the attacker (L1 gas for `sendMessageToL2`, plus a trivially small `msg.value`) is decoupled from the cost the L2 sequencer actually bears (up to 100,000,000 L2 gas of Cairo execution per message under current constants).

### Impact Explanation
An unprivileged L1 account can force the sequencer to spend Starknet L2 execution resources (steps, builtins, storage I/O) that are not compensated in proportion to the L2 resources consumed. At scale, repeated dust L1→L2 messages targeting computation-heavy `#[l1_handler]` entry points let an attacker consume block space/execution budget cheaply, crowding out legitimate transactions and degrading the sequencer's capacity to include and confirm real user transactions — a network congestion / reduced-throughput impact consistent with a Medium-severity DoS-class issue. It does not directly cause fund loss or wrong state, but it does allow a low-cost actor to impose an execution-cost/compensation asymmetry on the network.

### Likelihood Explanation
Likelihood is high in the sense that anyone with a funded L1 account can trigger it: the only requirement is calling the L1 core contract's `sendMessageToL2` with a non-zero, arbitrarily small value (the sequencer code already treats `Fee(1)` as sufficient in several places, and the on-chain check only verifies `paid_fee_on_l1 != Fee(0)`). The primary cost to the attacker is L1 gas for the `sendMessageToL2` call itself, which is comparatively cheap relative to the L2 compute it can trigger (up to the fixed `l1_handler_max_amount_bounds`). The `max_l1_handler_txs_per_block` config value bounds how many such transactions can be included per block, which somewhat limits (but does not eliminate) the achievable impact per block; sustained attacks across many blocks could still meaningfully degrade throughput for L1-handler-dependent dApps and consume sequencer compute over time.

### Recommendation
Introduce a minimum, fee-proportional bound for L1-handler transactions instead of a flat non-zero check:
- Scale the allowed `l1_handler_max_amount_bounds` (or otherwise cap the granted execution budget) as a function of `paid_fee_on_l1`, so dust payments only unlock a proportionally small amount of L2 compute.
- Alternatively/additionally, enforce an explicit minimum `paid_fee_on_l1` threshold (e.g., pegged to a fraction of a USD-equivalent or to the L2 gas price) before a scraped L1 event is accepted into `TransactionManager`'s proposable set, rejecting/deferring transactions that fall below it.
- Consider per-source-address or global rate limiting for L1-handler transactions in `apollo_l1_events`/`TransactionManager` to reduce the effectiveness of dust flooding even for transactions that individually pass the fee check.

### Proof of Concept
1. On L1, call the Starknet core contract's `sendMessageToL2` targeting an expensive `#[l1_handler]` entry point on some L2 contract, sending the minimum non-zero `value` (e.g., 1 wei), as done in test helpers: [6](#0-5) 
2. The scraper converts this into an `ExecutableL1HandlerTransaction` with `paid_fee_on_l1` set from the L1 `value` (dust amount), and it is unconditionally added to `TransactionManager`'s proposable index once the cooldown passes: [5](#0-4) 
3. When proposed/validated, `execute_raw` runs the full entry point up to `l1_handler_max_amount_bounds` (e.g., 100,000,000 L2 gas) and only rejects the transaction *after* execution if `paid_fee_on_l1 == Fee(0)`; any non-zero dust amount passes: [1](#0-0) 
4. Repeating this from L1 with many dust messages (cost bounded by L1 gas only) forces the sequencer to repeatedly execute compute-heavy Cairo logic for negligible L2-side payment, up to `max_l1_handler_txs_per_block` per block, degrading available capacity for legitimate transactions.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L92-113)
```rust
                // Enforce resource bounds.
                let fee_check_report = FeeCheckReport::check_all_gas_amounts_within_bounds(
                    &l1_handler_bounds,
                    &receipt.gas,
                );
                match fee_check_report {
                    Ok(()) => {
                        // Post-execution check passed, commit the execution.
                        execution_state.commit();
                        // TODO(Arni): Consider removing this check. It is covered by the starknet
                        // core contract.
                        let paid_fee = self.paid_fee_on_l1;
                        // For now, assert only that any amount of fee was paid.
                        // The error message still indicates the required fee.
                        if paid_fee == Fee(0) {
                            return Err(TransactionExecutionError::TransactionFeeError(Box::new(
                                TransactionFeeError::InsufficientFee {
                                    paid_fee,
                                    actual_fee: receipt.fee,
                                },
                            )));
                        }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L473-483)
```rust
    fn convert_consensus_l1_handler_to_internal_l1_handler(
        &self,
        tx: transaction::L1HandlerTransaction,
    ) -> TransactionConverterResult<executable_transaction::L1HandlerTransaction> {
        Ok(executable_transaction::L1HandlerTransaction::create(
            tx,
            &self.chain_id,
            // TODO(Gilad): Change this once we put real value in paid_fee_on_l1.
            Fee(1),
        )?)
    }
```

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L421-429)
```rust
        starknet_api::transaction::Transaction::Invoke(value) => {
            Ok(ExecutableTransactionInput::Invoke(value, false))
        }
        starknet_api::transaction::Transaction::L1Handler(value) => {
            // todo(yair): This is a temporary solution until we have a better way to get the l1
            // fee.
            let paid_fee_on_l1 = Fee(1);
            Ok(ExecutableTransactionInput::L1Handler(value, paid_fee_on_l1, false))
        }
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_1.json (L184-189)
```json
        "l1_handler_version": 0,
        "l1_handler_max_amount_bounds": {
            "l1_gas": 40000,
            "l1_data_gas": 20000,
            "l2_gas": 100000000
        },
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L71-113)
```rust
    // TODO(Arni): use created_at_block_timestamp in addition to scrape_timestamp.
    pub fn get_txs(&mut self, n_txs: usize, now: u64) -> Vec<L1HandlerTransaction> {
        // Oldest        Now.sub(timelock)     Newest       Now
        //  |<---  passed  --->|                 |           |
        //  |<--- cooldown --->|                 |           |
        // t-------------------------------------------------->
        let cutoff = now.saturating_sub(self.config.l1_handler_proposal_cooldown_seconds.as_secs());
        let past_cooldown_txs = self.proposable_index.range(..cutoff);

        // Linear scan, but we expect this to be a small number of transactions (< 10 roughly).
        let unstaged_tx_hashes: Vec<_> = past_cooldown_txs
            .flat_map(|(_timestamp, tx_hashes)| tx_hashes.iter())
            .skip_while(|&&tx_hash| self.is_staged(tx_hash))
            .take(n_txs)
            .copied()
            .collect();

        for &tx_hash in unstaged_tx_hashes.iter() {
            let record = self.records.get(&tx_hash).expect("transaction should exist");
            assert_eq!(
                record.state,
                TransactionState::Pending,
                "Transaction {tx_hash} has state {:?}. Only Pending transactions should be in the \
                 proposable index.",
                record.state
            );
        }

        let mut txs = Vec::with_capacity(n_txs);
        let current_staging_epoch = self.current_staging_epoch; // borrow-checker constraint.
        for tx_hash in unstaged_tx_hashes {
            let newly_staged =
                self.with_record(tx_hash, |record| record.try_mark_staged(current_staging_epoch));
            assert_eq!(
                newly_staged,
                Some(true),
                "Inconsistent storage state: indexed l1 handler {tx_hash} is not in storage or \
                 wasn't marked as staged."
            );

            txs.push(self.records[&tx_hash].get_unchecked().clone());
        }
        txs
```

**File:** crates/apollo_base_layer_tests/src/anvil_base_layer.rs (L284-308)
```rust
pub async fn send_message_to_l2(
    starknet_core_contract: &StarknetL1Contract,
    l1_handler: &L1HandlerTransaction,
) -> TransactionReceipt {
    const PAID_FEE_ON_L1: U256 = U256::from_be_slice(b"paid"); // Arbitrary value.

    let l2_contract_address = l1_handler.contract_address.0.key().to_hex_string().parse().unwrap();
    let l2_entry_point = l1_handler.entry_point_selector.0.to_hex_string().parse().unwrap();

    // The calldata of an L1 handler transaction consists of the L1 sender address followed by
    // the transaction payload. We remove the sender address to extract the message
    // payload.
    let payload =
        l1_handler.calldata.0[1..].iter().map(|x| x.to_hex_string().parse().unwrap()).collect();
    let msg = starknet_core_contract.sendMessageToL2(l2_contract_address, l2_entry_point, payload);

    msg
        // Sets a non-zero fee to be paid on L1.
        .value(PAID_FEE_ON_L1)
        // Sends the transaction to the Starknet L1 contract. For debugging purposes, replace
        // `.send()` with `.call_raw()` to retrieve detailed error messages from L1.
        .send().await.expect("Transaction submission to Starknet L1 contract failed.")
        // Waits until the transaction is received on L1 and then fetches its receipt.
        .get_receipt().await.expect("Transaction was not received on L1 or receipt retrieval failed.")
}
```
