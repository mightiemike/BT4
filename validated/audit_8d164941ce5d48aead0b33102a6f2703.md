### Title
Unbounded linear scan of the delayed-declares queue enables mempool admission gas/CPU griefing - ([File: crates/apollo_mempool/src/mempool.rs])

### Summary
`AddTransactionQueue` (used as `Mempool::delayed_declares`) stores pending Declare transactions in a `VecDeque` and exposes a `contains(address, nonce)` lookup implemented as a full linear scan [1](#0-0) . This lookup is invoked on the hot path of every incoming transaction's validation (`validate_no_delayed_declare_front_run`) and on every `commit_block` call for every address in the committed block (`update_accounts_with_gap`), so an attacker who fills this queue with many distinct-address Declare transactions turns every subsequent `add_tx`/`validate_tx`/`commit_block` invocation into an O(n) operation, mirroring the reported bug class ("Sequential Search Leads To Gas Griefing" — replacing an unbounded linear-search removal/lookup on an attacker-growable list with a constant/logarithmic-time structure).

### Finding Description
`Mempool::delayed_declares` is an `AddTransactionQueue` backed by a `VecDeque<(DateTime, AddTransactionArgs)>` [2](#0-1) . Its `contains` method iterates the entire deque on every call:

```rust
fn contains(&self, contract_address: ContractAddress, nonce: Nonce) -> bool {
    self.elements.iter().any(|(_, tx_args)| {
        let tx = &tx_args.tx;
        tx.contract_address() == contract_address && tx.nonce() == nonce
    })
}
``` [1](#0-0) 

This `contains` is called from two places reachable by an unprivileged transaction sender:

1. `validate_no_delayed_declare_front_run`, executed for **every** incoming transaction (declare or not) during `validate_tx`/`add_tx_validations`: [3](#0-2) 

2. `update_accounts_with_gap`, executed once per address included in **every committed block**, i.e., for every block the batcher/sequencer produces: [4](#0-3) 

An attacker can grow `delayed_declares` cheaply and in O(1) per insertion by submitting many valid Declare transactions from distinct addresses (each addition is a simple `push_back`, see `AddTransactionQueue::push_back` [5](#0-4) ). Declares only leave this queue after `declare_delay` elapses (`add_ready_declares`, called before every `get_txs`) [6](#0-5) , so during that window (configurable, default 1s in code but 20s in the deployed app config [7](#0-6) ) the queue can be filled up to the mempool's byte capacity (default 1 GiB) with many small Declare entries.

Once populated, every honest transaction submitted to the gateway pays an O(n) cost in `validate_no_delayed_declare_front_run`, and every committed block pays O(n × addresses_in_block) in `update_accounts_with_gap`, both proportional to the attacker-controlled queue length rather than to the honest sender's own workload — the asymptotic mismatch (O(1) cost to grow, O(n) cost imposed on every other participant) is exactly the "Sequential Search Leads To Gas Griefing" bug class from the external report, transplanted from a Move `vector::index_of` scan to a Rust `VecDeque::iter().any()` scan in the sequencer's mempool.

### Impact Explanation
This is a mempool-admission/liveness degradation: as the delayed-declares queue grows, per-transaction validation latency and per-block commit latency in the mempool component scale linearly with the number of queued declares, which is fully attacker-controlled (limited only by mempool capacity in bytes, not by transaction count). This can materially slow down or stall the node's ability to admit and process new transactions, degrading throughput for all users — a network-availability impact ("network unable to confirm new transactions" in the worst case if sustained). It does not corrupt state or forge signatures, so it is not a fund-loss or consensus-divergence bug, keeping it in the Medium range for likely severity classification (DoS/gas-griefing analog).

### Likelihood Explanation
Reaching this path requires only submitting ordinary, validly-signed Declare transactions from many different funded accounts — no special privileges, no malicious operator/peer assumption, and no p2p/consensus manipulation. The mempool's only defense is a byte-capacity limit, not an entry-count limit for the delayed-declares queue, so an attacker with sufficient (but bounded) funds/addresses can trivially maximize queue length. Likelihood is therefore reasonably high given only economic cost as a barrier.

### Recommendation
Replace the `Vec`/`VecDeque` linear scan in `AddTransactionQueue::contains` with an indexed lookup (e.g., a `HashMap<(ContractAddress, Nonce), _>` or `HashSet<(ContractAddress, Nonce)>` maintained alongside the `VecDeque`, mirroring how `TransactionPool`/`AccountTransactionIndex` already use `HashMap`/`BTreeMap` for O(1)/O(log n) lookups elsewhere in the mempool) [8](#0-7) . Update `push_back`/`pop_front` to keep the index consistent, so both `validate_no_delayed_declare_front_run` and `update_accounts_with_gap` become O(1) per call regardless of the number of delayed declares.

### Proof of Concept
1. Fund N distinct accounts (N bounded only by `capacity_in_bytes`, default 1 GiB).
2. Submit one valid `Declare` transaction per account to the gateway; each is admitted into `Mempool::delayed_declares` via `add_tx_inner` → `AddTransactionQueue::push_back` in O(1) [5](#0-4) .
3. Before `declare_delay` elapses, continue submitting ordinary Invoke/DeployAccount transactions from any other accounts. Each call into `Mempool::validate_tx`/`add_tx` now performs an O(N) scan via `validate_no_delayed_declare_front_run` → `AddTransactionQueue::contains` [3](#0-2) , and every `commit_block` call pays O(N × addresses_in_block) via `update_accounts_with_gap` [4](#0-3) .
4. Measure mempool `add_tx`/`commit_block` latency growth as N approaches the capacity-bound maximum versus an empty `delayed_declares` queue.

Note: I was unable to fully verify the exact minimum cost/rate limits the gateway enforces on Declare submissions (e.g., per-account throughput throttling outside the mempool crate), so the precise attacker cost to reach a queue length that causes measurable degradation is not confirmed from the indexed code alone; a live Devin session with the full repo/build would be needed to benchmark actual latency impact.

### Citations

**File:** crates/apollo_mempool/src/mempool.rs (L196-201)
```rust
// A queue to hold transactions that are waiting to be added to the tx pool.
struct AddTransactionQueue {
    elements: VecDeque<(DateTime, AddTransactionArgs)>,
    // Keeps track of the total size of the transactions in this queue.
    size_in_bytes: u64,
}
```

**File:** crates/apollo_mempool/src/mempool.rs (L208-214)
```rust
    fn push_back(&mut self, submission_time: DateTime, args: AddTransactionArgs) {
        self.size_in_bytes = self
            .size_in_bytes
            .checked_add(args.tx.total_bytes())
            .expect("Overflow when adding a transaction to AddTransactionQueue.");
        self.elements.push_back((submission_time, args));
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L231-236)
```rust
    fn contains(&self, contract_address: ContractAddress, nonce: Nonce) -> bool {
        self.elements.iter().any(|(_, tx_args)| {
            let tx = &tx_args.tx;
            tx.contract_address() == contract_address && tx.nonce() == nonce
        })
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L619-630)
```rust
    fn add_ready_declares(&mut self) {
        let now = self.clock.now();
        while let Some((submission_time, _args)) = self.delayed_declares.front() {
            if now - self.config.static_config.declare_delay < *submission_time {
                break;
            }
            let (_submission_time, args) =
                self.delayed_declares.pop_front().expect("Delay declare should exist.");
            self.add_tx_inner(args);
        }
        self.update_state_metrics();
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L713-726)
```rust
    /// Validates that the given transaction does not front run a delayed declare. This means in
    /// particular that no fee escalation can occur to a declare that is being delayed.
    fn validate_no_delayed_declare_front_run(
        &self,
        tx_reference: TransactionReference,
    ) -> MempoolResult<()> {
        if self.delayed_declares.contains(tx_reference.address, tx_reference.nonce) {
            return Err(MempoolError::DuplicateNonce {
                address: tx_reference.address,
                nonce: tx_reference.nonce,
            });
        }
        Ok(())
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L958-965)
```rust
    fn update_accounts_with_gap(&mut self, address_to_nonce: AddressToNonce) {
        for (address, account_nonce) in address_to_nonce {
            // If a delayed declare transaction exists at the account nonce, it is next to execute,
            // so no gap exists.
            if self.delayed_declares.contains(address, account_nonce) {
                self.remove_from_accounts_with_gap(address);
                continue;
            }
```

**File:** crates/apollo_deployments/resources/app_configs/mempool_config.json (L1-8)
```json
{
  "mempool_config.dynamic_config.transaction_ttl": 300,
  "mempool_config.static_config.capacity_in_bytes": 1073741824,
  "mempool_config.static_config.committed_nonce_retention_block_count": 100,
  "mempool_config.static_config.declare_delay": 20,
  "mempool_config.static_config.enable_fee_escalation": true,
  "mempool_config.static_config.fee_escalation_percentage": 10
}
```

**File:** crates/apollo_mempool/src/transaction_pool.rs (L272-296)
```rust
#[derive(Debug, Default, Eq, PartialEq)]
struct AccountTransactionIndex(HashMap<ContractAddress, BTreeMap<Nonce, TransactionReference>>);

impl AccountTransactionIndex {
    /// If the transaction already exists in the mapping, the old value is returned.
    fn insert(&mut self, tx: TransactionReference) -> Option<TransactionReference> {
        self.0.entry(tx.address).or_default().insert(tx.nonce, tx)
    }

    fn remove(&mut self, tx: TransactionReference) -> Option<TransactionReference> {
        let TransactionReference { address, nonce, .. } = tx;
        let account_txs = self.0.get_mut(&address)?;

        let removed_tx = account_txs.remove(&nonce);

        if removed_tx.is_some() && account_txs.is_empty() {
            self.0.remove(&address);
        }

        removed_tx
    }

    fn get(&self, address: ContractAddress, nonce: Nonce) -> Option<TransactionReference> {
        self.0.get(&address)?.get(&nonce).copied()
    }
```
