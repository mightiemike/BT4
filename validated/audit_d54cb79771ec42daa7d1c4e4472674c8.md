This confirms the mechanics — `reject_tx_congestion_threshold` defaults to 0.5 [1](#0-0) , and `action_receipt_congestion_gas` counts the full **prepaid** (attached) gas of a receipt, not the gas actually burnt [2](#0-1) . I have enough to write the final analog.

### Title
Cheap prepaid-gas flooding triggers shard-wide `shard_accepts_transactions` congestion rejection, halting all transaction admission to a shard - (File: `core/primitives/src/congestion_info.rs`)

### Summary
NEAR's congestion control gates whether a shard accepts new transactions on `incoming_congestion = delayed_receipts_gas / max_congestion_incoming_gas` [3](#0-2) . The gas value fed into this metric is the receipt's **declared/prepaid** gas (`action_receipt_congestion_gas`: prepaid exec fees + new-receipt fee + prepaid send fees + attached function-call gas), not the gas the receipt actually burns during execution [4](#0-3) . An attacker can therefore attach the maximum allowed prepaid gas (`max_total_prepaid_gas` = 300 Tgas per transaction) to receipts that call a cheap/no-op method, causing the unused portion to be refunded post-execution while still occupying the full 300 Tgas of "congestion" budget while the receipt sits in the delayed queue. This is structurally the same class of bug as the OptimismPortal `ResourceMetering` report: a metered, per-block/per-chunk resource ceiling that gates access for *all* users can be exhausted by a single actor at a cost far below the nominal value of the resource consumed.

### Finding Description
- `CongestionControl::shard_accepts_transactions` rejects **all** new transactions addressed to a shard once `congestion_level >= reject_tx_congestion_threshold` (default 0.5) [5](#0-4)  and default `reject_tx_congestion_threshold: 0.5` [1](#0-0) .
- This check is invoked for every transaction whose receiver lives on the target shard, in `congestion_control_accepts_transaction`, and gates whether that transaction is included in a chunk at all [6](#0-5) .
- `incoming_congestion` is `delayed_receipts_gas / max_congestion_incoming_gas`, with `max_congestion_incoming_gas` defaulting to 20 PGas [7](#0-6) . So only ~10 PGas of *delayed_receipts_gas* backlog is needed to hit the 0.5 threshold and shut off new transaction admission to that shard for every account, every sender.
- Crucially, `delayed_receipts_gas` is populated from `compute_receipt_congestion_gas`, which for action receipts sums `prepaid_exec_gas` + `prepaid_send_cost` + attached function-call gas — the full *declared* budget, independent of how much gas is actually consumed by execution [4](#0-3) .
- A transaction may attach up to `max_total_prepaid_gas` (300 Tgas, mainnet) to a function call [8](#0-7) ; unused attached gas is refunded to the signer only *after* the receipt finishes executing [9](#0-8) , i.e., only once it leaves the delayed queue. While queued/delayed, the full 300 Tgas counts toward `delayed_receipts_gas` even if the target call is designed to burn only a few Tgas (e.g., a trivial method or a call to an account with no contract).
- Consequently, an attacker with a modest, mostly-refundable balance can generate a stream of function-call receipts targeting accounts on a victim shard, each declaring 300 Tgas but burning near-zero real gas, faster than the shard's per-chunk compute budget can drain them. This inflates `delayed_receipts_gas` past the reject threshold and makes `shard_accepts_transactions` return `No` for the whole shard, blocking every other user's transactions to that shard.

### Impact Explanation
This is a shard-wide, transaction-triggered halt of new-transaction admission — any account hosted on the targeted shard becomes unreachable for new transactions (RPC submissions are rejected/ignored at admission), affecting all unrelated users, not just the attacker's counterparty. This matches the "transaction-triggered halt" acceptance criterion: a single unprivileged transaction signer can deny service to an entire shard using disproportionately cheap, largely-refunded gas, echoing the OptimismPortal ResourceMetering report where a small real cost (43k L1 gas) blocks a much larger declared resource (8M L2 gas) for everyone.

### Likelihood Explanation
Reaching this condition requires sustained submission of many max-attached-gas, cheap-execution transactions faster than the target shard's compute budget can retire them from the delayed queue — feasible for a moderately funded attacker since only actually-burnt gas (base + minimal exec fees) is a true cost; the bulk of the declared gas is refunded. It requires no validator, network, or protocol privilege — only submitting standard `FunctionCall` transactions via RPC.

### Recommendation
Decouple the congestion-gas accounting from the raw attached/prepaid gas for function calls; consider counting only a bounded, non-refundable minimum (e.g., base send/exec fees plus a smaller capped fraction of attached gas), or dynamically re-price/discount delayed-queue occupancy proportional to actual measured burn on similar past calls, so an attacker cannot cheaply inflate `delayed_receipts_gas`/`incoming_congestion` with gas that will ultimately be refunded. Alternatively, tighten `max_total_prepaid_gas` relative to `max_congestion_incoming_gas`/chunk gas limits so a realistic attacker cost floor is enforced before congestion-based rejection engages, and/or rate-limit per-signer/per-account contribution to a shard's `delayed_receipts_gas`.

### Proof of Concept
1. Deploy or target an existing account on shard S with a method that finishes in a few Tgas (or target an account with no contract, which fails cheaply).
2. Submit a continuous stream of `FunctionCall` transactions, each attaching `max_total_prepaid_gas` (300 Tgas), addressed to accounts on shard S, from one or more signer accounts (balance is largely refunded after each receipt executes).
3. Submit receipts faster than shard S's per-chunk compute budget can process the delayed backlog, so `delayed_receipts_gas` accumulates via `compute_receipt_congestion_gas`/`action_receipt_congestion_gas` [2](#0-1) .
4. Once `incoming_congestion >= 0.5` (i.e., `delayed_receipts_gas >= 10` PGas with default config), `shard_accepts_transactions` returns `No` [10](#0-9) , and `congestion_control_accepts_transaction` causes every subsequent transaction addressed to shard S (from any other user) to be rejected at chunk-preparation/admission time [6](#0-5) , denying service to all legitimate users of that shard until the backlog drains.

### Citations

**File:** core/parameters/res/runtime_configs/68.yaml (L12-16)
```yaml
# 20 PGAS
max_congestion_incoming_gas: { 
  old : 9_223_372_036_854_775_807,
  new : 20_000_000_000_000_000,
}
```

**File:** core/parameters/res/runtime_configs/68.yaml (L60-64)
```yaml
# 0.5
reject_tx_congestion_threshold: { 
  old : { numerator: 1, denominator: 1 },
  new : { numerator: 50, denominator: 100 }
}
```

**File:** runtime/runtime/src/congestion_control.rs (L678-730)
```rust
pub(crate) fn compute_receipt_congestion_gas(
    receipt: &Receipt,
    config: &RuntimeConfig,
) -> Result<Gas, IntegerOverflowError> {
    match receipt.versioned_receipt() {
        VersionedReceiptEnum::Action(action_receipt) => {
            // account for gas guaranteed to be used for executing the receipts
            action_receipt_congestion_gas(receipt, config, action_receipt.into())
        }
        VersionedReceiptEnum::Data(_data_receipt) => {
            // Data receipts themselves don't cost gas to execute, their cost is
            // burnt at creation. What we should count, is the gas of the
            // postponed action receipt. But looking that up would require
            // reading the postponed receipt from the trie.
            // Thus, the congestion control MVP does not account for data
            // receipts or postponed receipts.
            Ok(Gas::ZERO)
        }
        VersionedReceiptEnum::PromiseYield(_) => {
            // The congestion control MVP does not account for yielding a
            // promise. Yielded promises are confined to a single account, hence
            // they never cross the shard boundaries. This makes it irrelevant
            // for the congestion MVP, which only counts gas in the outgoing
            // buffers and delayed receipts queue.
            Ok(Gas::ZERO)
        }
        VersionedReceiptEnum::PromiseResume(_) => {
            // The congestion control MVP does not account for resuming a promise.
            // Unlike `PromiseYield`, it is possible that a promise-resume ends
            // up in the delayed receipts queue.
            // But similar to a data receipt, it would be difficult to find the cost
            // of it without expensive state lookups.
            Ok(Gas::ZERO)
        }
        VersionedReceiptEnum::GlobalContractDistribution(_) => Ok(Gas::ZERO),
    }
}

fn action_receipt_congestion_gas(
    receipt: &Receipt,
    config: &RuntimeConfig,
    action_receipt: VersionedActionReceipt,
) -> Result<Gas, IntegerOverflowError> {
    let prepaid_exec_gas =
        total_prepaid_exec_fees(config, &action_receipt.actions(), receipt.receiver_id())?
            .gas
            .checked_add(config.fees.fee(ActionCosts::new_action_receipt).exec_fee().gas)
            .ok_or(IntegerOverflowError)?;
    // account for gas guaranteed to be used for creating new receipts
    let prepaid_send_cost = total_prepaid_send_fees(config, &action_receipt.actions())?;
    let prepaid_gas = prepaid_exec_gas.checked_add_result(prepaid_send_cost.gas)?;

    // account for gas potentially used for dynamic execution
```

**File:** core/primitives/src/congestion_info.rs (L56-58)
```rust
    fn incoming_congestion(&self) -> f64 {
        self.info.incoming_congestion(&self.config)
    }
```

**File:** core/primitives/src/congestion_info.rs (L119-151)
```rust
    /// Whether we can accept new transaction with the receiver set to this shard.
    ///
    /// If the shard doesn't accept new transaction, provide the reason for
    /// extra debugging information.
    pub fn shard_accepts_transactions(&self) -> ShardAcceptsTransactions {
        let incoming_congestion = self.incoming_congestion();
        let outgoing_congestion = self.outgoing_congestion();
        let memory_congestion = self.memory_congestion();
        let missed_chunks_congestion = self.missed_chunks_congestion();

        let congestion_level = incoming_congestion
            .max(outgoing_congestion)
            .max(memory_congestion)
            .max(missed_chunks_congestion);

        // Convert to NotNan here, if not possible, the max above is already meaningless.
        let congestion_level =
            NotNan::new(congestion_level).unwrap_or_else(|_| NotNan::new(1.0).unwrap());
        if *congestion_level < self.config.reject_tx_congestion_threshold {
            return ShardAcceptsTransactions::Yes;
        }

        let reason = if missed_chunks_congestion >= *congestion_level {
            RejectTransactionReason::MissedChunks { missed_chunks: self.missed_chunks_count }
        } else if incoming_congestion >= *congestion_level {
            RejectTransactionReason::IncomingCongestion { congestion_level }
        } else if outgoing_congestion >= *congestion_level {
            RejectTransactionReason::OutgoingCongestion { congestion_level }
        } else {
            RejectTransactionReason::MemoryCongestion { congestion_level }
        };
        ShardAcceptsTransactions::No(reason)
    }
```

**File:** chain/chain/src/runtime/mod.rs (L1773-1797)
```rust
/// Returns true if the transaction passes the congestion control checks. The
/// transaction will be accepted if the receiving shard is not congested or its
/// congestion level is below the threshold.
fn congestion_control_accepts_transaction(
    epoch_manager: &dyn EpochManagerAdapter,
    runtime_config: &RuntimeConfig,
    epoch_id: &EpochId,
    prev_block: &PrepareTransactionsBlockContext,
    validated_tx: &ValidatedTransaction,
) -> Result<bool, Error> {
    let receiver_id = validated_tx.receiver_id();
    let receiving_shard = account_id_to_shard_id(epoch_manager, receiver_id, &epoch_id)?;
    let congestion_info = prev_block.congestion_info.get(&receiving_shard);
    let Some(congestion_info) = congestion_info else {
        return Ok(true);
    };

    let congestion_control = CongestionControl::new(
        runtime_config.congestion_control_config,
        congestion_info.congestion_info,
        congestion_info.missed_chunks_count,
    );
    let shard_accepts_transactions = congestion_control.shard_accepts_transactions();
    Ok(shard_accepts_transactions.is_yes())
}
```

**File:** docs/architecture/how/gas.md (L121-133)
```markdown
prevent the current function call from executing to the end.

The gas attached to a function can be at most `max_total_prepaid_gas`, which is
300 Tgas since the mainnet launch. Note that this limit is per
`SignedTransaction`, not per function call. In other words, batched function
calls share this limit.

There is also a limit to how much single call can burn, `max_gas_burnt`, which
used to be 200 Tgas but has been increased to 300 Tgas in protocol version 52.
(Note: When attaching gas to an outgoing function call, this is not counted as
gas burnt.) However, given a call can never burn more than was attached anyway,
this second limit is obsolete with the current configuration where the two limits
are equal.
```

**File:** docs/architecture/how/gas.md (L159-171)
```markdown
How much contracts receive from execution depends on two things.

1. How much gas is burnt on the function call execution itself. That is, only
   the gas taken from the `attached_gas` of a function call is considered for
   contract rewards. The base fees paid for creating the receipt, including the
   `action_function_call` fee, are burnt 100%.
2. The remainder of the burnt gas is multiplied by the runtime configuration
   parameter
   [`burnt_gas_reward`](../../../core/parameters/res/runtime_configs/parameters.snap#L5C5-L5C5)
   which currently is at 30%.

During receipt execution, nearcore code tracks the `gas_burnt_for_function_call`
separately from other gas burning to enable this contract reward calculations.
```
