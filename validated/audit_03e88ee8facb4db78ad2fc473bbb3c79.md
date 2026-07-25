### Title
Cheap Chunk Stuffing via Large SIR `FunctionCall` Transactions: `send_sir` Per-Byte Gas Cost Mismatch with `combined_transactions_size_limit` — (`chain/chain/src/runtime/mod.rs`, `core/parameters/res/runtime_configs/69.yaml`)

---

### Summary

Chunk production in nearcore enforces two independent limits: a gas limit (`max_tx_gas` = 500 TGas) and a byte-size limit (`combined_transactions_size_limit` = 4 MiB). A `FunctionCall` transaction where `signer_id == receiver_id` (SIR — "signer is receiver") pays only `send_sir` per-byte gas (2,235,934 gas/byte), which was never raised when protocol version 69 raised `send_not_sir` to 47,683,715 gas/byte. Two max-size (1.5 MiB) SIR function-call transactions fill ~3 MiB of the 4 MiB size budget while consuming only ~16.5 TGas — 3.3% of the 500 TGas gas limit. The chunk is size-limited, not gas-limited, so the gas price formula sees `gasUsed ≪ gasLimit` and drives the gas price toward its minimum floor. Legitimate transactions are excluded from the chunk at a cost ~27× lower than filling the gas limit with normal transactions.

---

### Finding Description

**Dual-limit chunk packing in `prepare_transactions_extra`**

The chunk producer iterates the transaction pool and stops when either limit is hit:

```rust
// chain/chain/src/runtime/mod.rs
let transactions_gas_limit = chunk_tx_gas_limit(runtime_config, &prev_block, shard_id);
let size_limit = runtime_config.witness_config.combined_transactions_size_limit as u64;

'add_txs_loop: while let Some(transaction_group_iter) = transaction_groups.next() {
    if total_gas_burnt >= transactions_gas_limit { break; }   // 500 TGas
    if total_size >= size_limit { break; }                    // 4 MiB
    // ...
    if total_size.saturating_add(tx_peek.size_for_limits(protocol_version)) > size_limit {
        break 'add_txs_loop;
    }
    // validate, charge, include tx
    total_gas_burnt = total_gas_burnt.checked_add(result.gas_burnt).unwrap();
    total_size += validated_tx.size_for_limits(protocol_version);
``` [1](#0-0) 

**The asymmetric per-byte gas cost introduced in protocol version 69**

Protocol version 69 raised `send_not_sir` per-byte costs for `FunctionCall` to 47,683,715 gas/byte to price cross-shard bandwidth. The `send_sir` cost was left unchanged at 2,235,934 gas/byte:

```yaml
# core/parameters/res/runtime_configs/69.yaml
action_function_call_per_byte:
  old: { send_sir: 2_235_934, send_not_sir: 2_235_934, execution: 2_235_934 }
  new: { send_sir: 2_235_934, send_not_sir: 47_683_715, execution: 2_235_934 }
``` [2](#0-1) 

The current mainnet config confirms this asymmetry:

```json
"function_call_cost_per_byte": {
  "send_sir":     2235934,
  "send_not_sir": 47683715,
  "execution":    2235934
}
``` [3](#0-2) 

**Gas cost arithmetic for a max-size SIR function call**

With `max_transaction_size` = 1,572,864 bytes and `function_call_cost_per_byte.send_sir` = 2,235,934 gas/byte, `execution` = 2,235,934 gas/byte:

| Component | Gas |
|---|---|
| `action_receipt_creation` (send_sir + exec) | 216,119,000,000 |
| `function_call_cost` base (send_sir + exec) | 980,000,000,000 |
| Per-byte (1,572,864 × 4,471,868) | ~7,033,000,000,000 |
| Minimum prepaid gas | 1 |
| **Total** | **~8.23 TGas** |

Two such transactions fill ~3 MiB of the 4 MiB size budget and consume ~16.5 TGas — **3.3% of the 500 TGas `max_tx_gas` limit**. [4](#0-3) [5](#0-4) 

**Gas price formula**

The block-level gas price adjusts based on `gasUsed / gasLimit`:

```
next_gas_price = gas_price * (1 + (gasUsed/gasLimit - 0.5) * adjustment_rate)
``` [6](#0-5) 

When the chunk is size-limited at 3.3% gas utilization, the formula drives the gas price toward `min_gas_price` every block.

---

### Impact Explanation

1. **Chunk stuffing (non-network-level DoS):** Two 1.5 MiB SIR `FunctionCall` transactions fill the chunk's size budget, excluding all other users' transactions from that chunk. This is a per-shard, per-chunk exclusion reachable by any unprivileged user with a funded account.

2. **Gas price manipulation:** With `gasUsed ≈ 3.3% × gasLimit`, the gas price formula decreases the gas price every block. Sustained attack drives the gas price to `min_gas_price`, reducing the cost of all future transactions for the attacker and all other users.

3. **Cost comparison:** Filling the gas limit with normal transfer transactions costs ~0.05 NEAR per chunk (500 TGas at minimum price). The SIR function-call approach costs ~0.00165 NEAR per chunk — approximately **27× cheaper** per chunk stuffed.

---

### Likelihood Explanation

- Any account with a small NEAR balance can submit large SIR `FunctionCall` transactions via the standard JSON-RPC `broadcast_tx_async` endpoint.
- No privileged role, validator access, or special contract is required.
- The attack is sustained: the attacker submits new transactions each block to maintain the size-limited state.
- The `new_transactions_validation_state_size_soft_limit` (500 KiB storage proof) does not stop this attack because validating a SIR function call only reads the signer's account and access key — a tiny storage proof. [7](#0-6) 

---

### Recommendation

1. **Raise `function_call_cost_per_byte.send_sir`** to match or approach `send_not_sir` (47,683,715 gas/byte). The rationale for the asymmetry — that SIR transactions do not cross shards — does not apply to the state-witness size budget, which is shard-local. The `combined_transactions_size_limit` is a witness-size constraint, not a cross-shard bandwidth constraint.

2. **Alternatively, enforce a gas-per-byte floor** during chunk packing: before accepting a transaction, verify that `tx.gas_cost / tx.size >= minimum_gas_per_byte_threshold`. This prevents any transaction from occupying disproportionate byte space relative to its gas contribution.

3. **Track the issue** already noted in the codebase: the docs acknowledge the gas-to-bytes conversion rate is "rather complicated and warrants its own investigation with potential protocol changes to lower the ratio in the most extreme cases." [8](#0-7) 

---

### Proof of Concept

```python
# Attacker account: alice.near (signer == receiver, SIR)
# Protocol version >= 84 (mainnet current)

# Parameters
max_tx_size = 1_572_864          # 1.5 MiB
size_limit  = 4_194_304          # 4 MiB combined_transactions_size_limit
gas_limit   = 500_000_000_000_000  # 500 TGas max_tx_gas

# Gas cost for one max-size SIR FunctionCall
receipt_creation = 108_059_500_000 * 2          # send_sir + execution
fn_call_base     = 200_000_000_000 + 780_000_000_000  # send_sir + execution
fn_call_per_byte = (2_235_934 + 2_235_934) * max_tx_size  # send_sir + execution
gas_per_tx = receipt_creation + fn_call_base + fn_call_per_byte + 1
# gas_per_tx ≈ 8,229,119,000,001 ≈ 8.23 TGas

# Two transactions fill ~3 MiB, consuming 3.3% of gas limit
num_txs = 2
total_size = num_txs * max_tx_size   # 3,145,728 bytes < 4,194,304 ✓
total_gas  = num_txs * gas_per_tx    # ~16.46 TGas

print(f"Size utilization: {total_size/size_limit:.1%}")   # 75.0%
print(f"Gas utilization:  {total_gas/gas_limit:.1%}")     # 3.3%
# → chunk is size-limited; gas price formula sees 3.3% utilization → price decreases
```

**Submission steps:**
1. Create account `alice.near` with sufficient NEAR balance.
2. Each block, submit 2 signed `FunctionCall` transactions from `alice.near` to `alice.near` with `args = [0u8; 1_572_864]` and `gas = 1` (minimum prepaid).
3. Observe that each chunk on alice's shard contains only these 2 transactions; all other pending transactions are excluded.
4. Observe `next_gas_price` in block headers decreasing toward `min_gas_price` over successive blocks. [2](#0-1) [9](#0-8) [10](#0-9)

### Citations

**File:** chain/chain/src/runtime/mod.rs (L928-992)
```rust
        // Total amount of gas burnt for converting transactions towards receipts.
        let mut total_gas_burnt = Gas::ZERO;
        let mut total_size = 0u64;

        let transactions_gas_limit = chunk_tx_gas_limit(runtime_config, &prev_block, shard_id);

        let mut prepared_transactions = PreparedTransactions::new();
        let mut skipped_transactions = Vec::new();
        let mut num_checked_transactions = 0;

        let size_limit = runtime_config.witness_config.combined_transactions_size_limit as u64;
        // for metrics only
        let mut rejected_due_to_congestion = 0;
        let mut rejected_invalid_tx = 0;
        let mut rejected_invalid_for_chain = 0;

        // Add new transactions to the result until some limit is hit or the transactions run out.
        'add_txs_loop: while let Some(transaction_group_iter) = transaction_groups.next() {
            if total_gas_burnt >= transactions_gas_limit {
                prepared_transactions.limited_by = PrepareTransactionsLimit::Gas;
                break;
            }
            if total_size >= size_limit {
                prepared_transactions.limited_by = PrepareTransactionsLimit::Size;
                break;
            }

            if let Some(time_limit) = &time_limit {
                if start_time.elapsed() >= *time_limit {
                    prepared_transactions.limited_by = PrepareTransactionsLimit::Time;
                    break;
                }
            }

            if state_update.recorded_storage_size() as u64
                > runtime_config.witness_config.new_transactions_validation_state_size_soft_limit
            {
                prepared_transactions.limited_by = PrepareTransactionsLimit::StorageProofSize;
                break;
            }

            if let Some(cancel) = &cancel {
                if cancel.load(Ordering::Relaxed) {
                    prepared_transactions.limited_by = PrepareTransactionsLimit::Cancelled;
                    break;
                }
            }

            // Transactions taken from this group so far this visit.
            let mut examined_from_group = 0usize;

            // Take transactions from this transaction group.
            while let Some(tx_peek) = transaction_group_iter.peek_next() {
                examined_from_group += 1;
                if MAX_TXS_PER_GROUP_PER_VISIT < examined_from_group {
                    break;
                }

                // Stop adding transactions if the size limit would be exceeded
                if total_size.saturating_add(tx_peek.size_for_limits(protocol_version))
                    > size_limit as u64
                {
                    prepared_transactions.limited_by = PrepareTransactionsLimit::Size;
                    break 'add_txs_loop;
                }
```

**File:** core/parameters/res/runtime_configs/69.yaml (L34-45)
```yaml
action_function_call_per_byte: {
  old: {
    send_sir: 2_235_934,
    send_not_sir: 2_235_934,
    execution: 2_235_934,
  },
  new: {
    send_sir: 2_235_934,
    send_not_sir: 47_683_715,
    execution: 2_235_934,
  }
}
```

**File:** core/parameters/src/snapshots/near_parameters__config_store__tests__84.json.snap (L46-50)
```text
      "function_call_cost_per_byte": {
        "send_sir": 2235934,
        "send_not_sir": 47683715,
        "execution": 2235934
      },
```

**File:** core/parameters/src/snapshots/near_parameters__config_store__tests__84.json.snap (L241-245)
```text
      "max_arguments_length": 4194304,
      "max_length_returned_data": 4194304,
      "max_contract_size": 4194304,
      "max_transaction_size": 1572864,
      "max_receipt_size": 4194304,
```

**File:** core/parameters/src/snapshots/near_parameters__config_store__tests__84.json.snap (L272-289)
```text
  "congestion_control_config": {
    "max_congestion_incoming_gas": 400000000000000000,
    "max_congestion_outgoing_gas": 10000000000000000,
    "max_congestion_memory_consumption": 1000000000,
    "max_congestion_missed_chunks": 125,
    "max_outgoing_gas": 300000000000000000,
    "min_outgoing_gas": 1000000000000000,
    "allowed_shard_outgoing_gas": 1000000000000000,
    "max_tx_gas": 500000000000000,
    "min_tx_gas": 20000000000000,
    "reject_tx_congestion_threshold": 0.8,
    "outgoing_receipts_usual_size_limit": 102400,
    "outgoing_receipts_big_size_limit": 4718592
  },
  "witness_config": {
    "main_storage_proof_size_soft_limit": 4000000,
    "combined_transactions_size_limit": 4194304,
    "new_transactions_validation_state_size_soft_limit": 572864
```

**File:** core/primitives/src/block.rs (L440-477)
```rust
    pub fn compute_next_gas_price_checked(
        gas_price: Balance,
        gas_used: Gas,
        gas_limit: Gas,
        gas_price_adjustment_rate: Rational32,
        min_gas_price: Balance,
        max_gas_price: Balance,
    ) -> Option<Balance> {
        // If block was skipped, the price does not change.
        if gas_limit == Gas::ZERO {
            return Some(gas_price);
        }

        let gas_used = u128::from(gas_used.as_gas());
        let gas_limit = u128::from(gas_limit.as_gas());
        let adjustment_rate_numer = *gas_price_adjustment_rate.numer() as u128;
        let adjustment_rate_denom = *gas_price_adjustment_rate.denom() as u128;

        // This number can never be negative as long as gas_used <= gas_limit and
        // adjustment_rate_numer <= adjustment_rate_denom.
        let numerator = 2u128
            .checked_mul(adjustment_rate_denom)?
            .checked_mul(gas_limit)?
            .checked_add(2u128.checked_mul(adjustment_rate_numer)?.checked_mul(gas_used)?)?
            .checked_sub(adjustment_rate_numer.checked_mul(gas_limit)?)?;
        let denominator = 2u128.checked_mul(adjustment_rate_denom)?.checked_mul(gas_limit)?;
        let next_gas_price =
            U256::from(gas_price.as_yoctonear()) * U256::from(numerator) / U256::from(denominator);

        Some(Balance::from_yoctonear(
            next_gas_price
                .clamp(
                    U256::from(min_gas_price.as_yoctonear()),
                    U256::from(max_gas_price.as_yoctonear()),
                )
                .as_u128(),
        ))
    }
```

**File:** docs/architecture/how/receipt-congestion.md (L162-172)
```markdown
A limit in bytes would be better to argue how much memory we need exactly. But
in some sense, the two are equivalent, as producing large receipts should cost a
linear amount of gas. What exactly the conversion rate is, is rather complicated
and warrants its own investigation with potential protocol changes to lower the
ratio in the most extreme cases. And this is important regardless of how
congestion is handled, given that network bandwidth is becoming more and more
important as we add more shards. Issue
[#8214](https://github.com/near/nearcore/issues/8214) tracks our effort on
estimating what that cost should be and
[#9378](https://github.com/near/nearcore/issues/9378) tracks our best progress
on calculating what it is today.
```
