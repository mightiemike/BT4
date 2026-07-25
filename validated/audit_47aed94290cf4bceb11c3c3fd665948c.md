### Title
Stale `tracked_shards` in `ShardedRpcPool` causes permanent `UNAVAILABLE_SHARD` DoS for all RPC queries after dynamic resharding — (`chain/jsonrpc/src/sharded_rpc.rs`)

---

### Summary

`ShardedRpcPool` stores each remote node's shard coverage as a static `Vec<ShardId>` set once at pool initialization. After dynamic resharding (enabled at protocol version 85), parent shards split into child shards with new `ShardId`s. The pool's routing table is never refreshed, so the coordinator cannot route any query for an account on a child shard to any node in the pool. Every such query returns `UNAVAILABLE_SHARD` to the caller. The codebase itself acknowledges this with an `#[ignore]`-marked test and an explicit TODO comment.

---

### Finding Description

**Root cause — static `tracked_shards` in `ShardedRpcNode`**

`ShardedRpcNode` carries a `tracked_shards: Vec<ShardId>` field that is populated exactly once, at pool construction time:

```rust
// chain/jsonrpc/src/sharded_rpc.rs
pub struct ShardedRpcNode {
    pub client: Arc<JsonRpcClient>,
    pub tracked_shards: Vec<ShardId>,   // ← set once, never updated
}
``` [1](#0-0) 

In `ShardedRpcPool::new()`, the value is cloned directly from the static node config and stored permanently:

```rust
.map(|node_config| ShardedRpcNode {
    client: Arc::new(…),
    tracked_shards: node_config.tracked_shards.clone(),  // ← static snapshot
})
``` [2](#0-1) 

In the TestLoop harness the same pattern applies — `tracked_shards` is extracted from `TrackedShardsConfig::Shards` at setup time and frozen:

```rust
let tracked_shards = match pool.shard_tracker.tracked_shards_config() {
    TrackedShardsConfig::Shards(uids) => {
        uids.iter().map(|uid| uid.shard_id()).collect()
    }
    _ => vec![],
};
ShardedRpcNode { client, tracked_shards }
``` [3](#0-2) 

**How dynamic resharding invalidates the table**

Dynamic resharding (gated on `ProtocolFeature::DynamicResharding`, protocol version 85) splits a parent shard into two child shards with brand-new `ShardId`s. The `ShardTracker` used for the *local* node correctly handles this via `check_if_descendant_of_tracked_shard`, which walks the `ShardLayoutV3` ancestor map: [4](#0-3) 

But the *remote* nodes in the pool have no `ShardTracker`. Their routing is decided solely by the static `tracked_shards: Vec<ShardId>`. After resharding, the child shard IDs are new integers not present in any node's list. `nodes_for_account_in_epochs` finds zero matching nodes, falls through to the emergency local-node fallback, and the local node also does not track the child shard — producing `UNAVAILABLE_SHARD` for every affected query.

**Confirmed by the codebase itself**

The test `test_rpc_query_after_resharding` is marked `#[ignore]` with the explicit reason:

```
/// TODO(sharded-rpc): currently ignored because the RPC pool's tracked_shards
/// is static and becomes stale after resharding — child shards are unknown to
/// the pool, causing UNAVAILABLE_SHARD errors. Remove ignore once the pool
/// supports dynamic shard updates.
``` [5](#0-4) 

The test harness sets up two RPC nodes each tracking one of the two pre-resharding shards, triggers a dynamic split, and then attempts account queries — all of which fail with `UNAVAILABLE_SHARD` because the pool still maps only the old parent shard IDs: [6](#0-5) 

---

### Impact Explanation

After dynamic resharding fires, every JSON-RPC query that targets an account residing on a child shard — `query` (`ViewAccount`, `ViewCode`, `ViewState`, `ViewAccessKey`, `ViewAccessKeyList`, `CallFunction`), `EXPERIMENTAL_receipt`, `changes`, etc. — returns `UNAVAILABLE_SHARD` from every node in the pool. The affected accounts are permanently unreachable via the RPC layer until the pool is restarted with an updated config. This is a non-network-level denial of service: it does not affect consensus, block production, or on-chain state, but it breaks the public API path for a subset of accounts and is fixable without a hardfork. [7](#0-6) 

---

### Likelihood Explanation

`DynamicResharding` is enabled at protocol version 85, the current stable version. Once the network's trie memory usage crosses `memory_usage_threshold` (or `force_split_shards` is set), resharding fires automatically at an epoch boundary. Any operator running a sharded RPC pool (the production benchmark configuration explicitly sets this up) will hit the bug on the first resharding event. No privileged action is required from the attacker — submitting ordinary RPC queries after resharding is sufficient to observe the failure. [8](#0-7) 

---

### Recommendation

Replace the static `Vec<ShardId>` in `ShardedRpcNode` with a dynamic lookup that mirrors what `ShardTracker` already does for the local node. Concretely:

1. Store the remote node's *configured* `ShardUId`s (not bare `ShardId`s) so the `ShardLayoutV3` ancestor map can be consulted.
2. In `nodes_for_account_in_epochs` / `nodes_for_shard_in_epochs`, resolve whether a remote node covers a given shard by calling `ShardLayout::get_children_shards_uids` recursively (or reuse `check_if_descendant_of_tracked_shard`) rather than doing a plain `Vec::contains` on the static list.
3. Alternatively, expose a `ShardTracker`-per-remote-node or a shared epoch-manager reference inside `ShardedRpcPool` so the descendant check is epoch-aware.

---

### Proof of Concept

The existing (ignored) integration test is the proof of concept. Running it without the `#[ignore]` attribute reproduces the failure:

```
// test-loop-tests/src/tests/sharded_rpc_resharding.rs
#[test]
#[ignore]   // ← remove this to reproduce
fn test_rpc_query_after_resharding() {
    // Sets up 2-shard layout, triggers dynamic split to 3 shards,
    // then queries ViewAccount for an account on the split shard.
    // Fails with UNAVAILABLE_SHARD on both rpc0 and rpc1.
}
``` [9](#0-8) 

Step-by-step trigger path for an unprivileged user:

1. Network runs with dynamic resharding enabled (protocol version ≥ 85).
2. A shard's trie memory usage crosses `memory_usage_threshold` (or `force_split_shards` is configured); the epoch manager schedules a split.
3. At the epoch boundary, the parent shard splits into two child shards with new `ShardId`s.
4. User submits `query` → `ViewAccount { account_id: "alice" }` where `alice` is on a child shard.
5. `ShardedRpcPool::nodes_for_query` → `nodes_for_account_in_epochs` finds no node whose static `tracked_shards` contains the child `ShardId`.
6. Falls back to local node; local node also does not track the child shard.
7. Response: `{"name":"UNAVAILABLE_SHARD","info":{"requested_shard_id":<child_id>}}`. [10](#0-9)

### Citations

**File:** chain/jsonrpc/src/sharded_rpc.rs (L94-99)
```rust
/// A remote RPC node in the pool, along with the shards it tracks.
#[derive(Clone)]
pub struct ShardedRpcNode {
    pub client: Arc<JsonRpcClient>,
    pub tracked_shards: Vec<ShardId>,
}
```

**File:** chain/jsonrpc/src/sharded_rpc.rs (L139-145)
```rust
                    .map(|node_config| ShardedRpcNode {
                        client: Arc::new(near_jsonrpc_client_internal::new_client(
                            &node_config.address,
                        )),
                        tracked_shards: node_config.tracked_shards.clone(),
                    })
                    .collect();
```

**File:** chain/jsonrpc/src/sharded_rpc.rs (L169-259)
```rust
    /// Returns all nodes that might be able to serve a query with the given routing hints.
    pub fn nodes_for_query(
        &self,
        block_hint: BlockHint,
        shard_hint: ShardHint,
    ) -> Result<Vec<RpcNodeHandle>, RpcError> {
        // TODO(sharded-rpc): Handle all (shard_hint, block_hint) combinations.
        // TODO(sharded-rpc): what should happen when the block is not known?

        let nodes = match (&block_hint, &shard_hint) {
            (BlockHint::None, ShardHint::None) => self.all_nodes(),
            (BlockHint::Hash(block_hash), ShardHint::Account(account_id)) => {
                let epoch_id = match self.chain_store.get_block_header(block_hash) {
                    Ok(header) => *header.epoch_id(),
                    Err(Error::DBNotFoundErr(_)) => return Ok(self.all_nodes()), // Unknown block, try all nodes
                    Err(e) => return Err(make_rpc_error(e)),
                };
                self.nodes_for_account_in_epochs(vec![epoch_id], account_id)?
            }
            (BlockHint::Height(height), ShardHint::Account(account_id)) => {
                let epoch_ids: Vec<_> = self
                    .chain_store
                    .get_all_block_hashes_by_height(*height)
                    .keys()
                    .cloned()
                    .collect();
                if epoch_ids.is_empty() {
                    return Ok(self.all_nodes()); // Unknown block, try all nodes
                }
                self.nodes_for_account_in_epochs(epoch_ids, account_id)?
            }
            (BlockHint::Recent, ShardHint::Account(account_id)) => {
                let head = match self.chain_store.head() {
                    Ok(tip) => tip,
                    Err(Error::DBNotFoundErr(_)) => return Ok(self.all_nodes()), // Unknown block, try all nodes
                    Err(e) => return Err(make_rpc_error(e)),
                };

                // TODO(sharded-rpc): only check adjacent epochs if we're close to the epoch boundary.
                let mut possible_epochs = vec![head.epoch_id, head.next_epoch_id];
                if let Ok(prev_epoch) = self
                    .shard_tracker
                    .epoch_manager()
                    .get_prev_epoch_id_from_prev_block(&head.prev_block_hash)
                {
                    possible_epochs.push(prev_epoch);
                }

                self.nodes_for_account_in_epochs(possible_epochs, account_id)?
            }
            (BlockHint::Hash(block_hash), ShardHint::Id(shard_id)) => {
                let epoch_id = match self.chain_store.get_block_header(block_hash) {
                    Ok(header) => *header.epoch_id(),
                    Err(Error::DBNotFoundErr(_)) => return Ok(self.all_nodes()), // Unknown block, try all nodes
                    Err(e) => return Err(make_rpc_error(e)),
                };
                self.nodes_for_shard_in_epochs(vec![epoch_id], *shard_id)?
            }
            (BlockHint::Height(height), ShardHint::Id(shard_id)) => {
                let epoch_ids: Vec<_> = self
                    .chain_store
                    .get_all_block_hashes_by_height(*height)
                    .keys()
                    .cloned()
                    .collect();
                if epoch_ids.is_empty() {
                    return Ok(self.all_nodes()); // Unknown block, try all nodes
                }
                self.nodes_for_shard_in_epochs(epoch_ids, *shard_id)?
            }
            (BlockHint::Recent, ShardHint::Id(shard_id))
            | (BlockHint::None, ShardHint::Id(shard_id)) => {
                let head = match self.chain_store.head() {
                    Ok(tip) => tip,
                    Err(Error::DBNotFoundErr(_)) => return Ok(self.all_nodes()),
                    Err(e) => return Err(make_rpc_error(e)),
                };
                self.nodes_for_shard_in_epochs(vec![head.epoch_id], *shard_id)?
            }
            _ => self.all_nodes(),
        };

        if nodes.is_empty() {
            // Emergency fallback - if there are no nodes that can handle the query, try the local
            // one, although it'll probably fail.
            // TODO(sharded-rpc): maybe remove when we're confident in the logic.
            return Ok(vec![RpcNodeHandle::LocalNode]);
        }

        Ok(nodes)
    }
```

**File:** test-loop-tests/src/setup/builder.rs (L494-503)
```rust
                let pool = data.sharded_rpc_pool.read();
                // TODO(sharded-rpc): find the right shard_ids in TestLoop.
                let tracked_shards = match pool.shard_tracker.tracked_shards_config() {
                    TrackedShardsConfig::Shards(uids) => {
                        uids.iter().map(|uid| uid.shard_id()).collect()
                    }
                    _ => vec![],
                };
                ShardedRpcNode { client, tracked_shards }
            })
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L92-100)
```rust
            TrackedShardsConfig::Shards(tracked_shards) => {
                // TODO(#13445): Turn the check below into a debug assert and call it earlier,
                // for all `tracked_shards_config` variants.
                let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
                if !shard_layout.shard_ids().contains(&shard_id) {
                    return Ok(false);
                }
                self.check_if_descendant_of_tracked_shard(shard_id, tracked_shards, epoch_id)
            }
```

**File:** test-loop-tests/src/tests/sharded_rpc_resharding.rs (L89-101)
```rust
        let rpc0_shard = shard_uids[0];
        let rpc1_shard = shard_uids[1];
        let mut env = TestLoopBuilder::new()
            .genesis(genesis)
            .clients(clients)
            .epoch_config_store(epoch_config_store)
            .config_modifier(move |config, client_index| match client_index {
                0 => config.tracked_shards_config = TrackedShardsConfig::Shards(vec![rpc0_shard]),
                1 => config.tracked_shards_config = TrackedShardsConfig::Shards(vec![rpc1_shard]),
                _ => {}
            })
            .add_rpc_pool([rpc0.clone(), rpc1.clone()])
            .build();
```

**File:** test-loop-tests/src/tests/sharded_rpc_resharding.rs (L142-208)
```rust
/// TODO(sharded-rpc): currently ignored because the RPC pool's tracked_shards
/// is static and becomes stale after resharding — child shards are unknown to
/// the pool, causing UNAVAILABLE_SHARD errors. Remove ignore once the pool
/// supports dynamic shard updates.
#[test]
#[ignore]
fn test_rpc_query_after_resharding() {
    init_test_logger();
    let mut h = ReshardingRpcHarness::new();

    let test_account = h.test_account.clone();
    let validator = h.validator.clone();

    // Verify resharding actually happened.
    let epoch_manager = h.env.node_for_account(&validator).client().epoch_manager.clone();
    let pre_hash = h
        .env
        .node_for_account(&validator)
        .client()
        .chain
        .get_block_hash_by_height(h.pre_resharding_height)
        .unwrap();
    let pre_epoch = epoch_manager.get_epoch_id_from_prev_block(&pre_hash).unwrap();
    let post_epoch = h.env.node_for_account(&validator).head().epoch_id;
    let pre_layout = epoch_manager.get_shard_layout(&pre_epoch).unwrap();
    let post_layout = epoch_manager.get_shard_layout(&post_epoch).unwrap();
    assert_ne!(
        pre_layout, post_layout,
        "resharding did not happen: shard layout is the same before and after",
    );
    // Advance a few more blocks so finality catches up to the post-resharding epoch.
    h.env.runner_for_account(&validator).run_for_number_of_blocks(5);

    // Query from both RPC nodes. Each tracks only one shard, so queries for
    // accounts on the other shard must be forwarded within the pool.
    let rpc_nodes = [h.rpc0.clone(), h.rpc1.clone()];
    let block_refs: Vec<(&str, BlockReference)> = vec![
        ("Finality::Final", BlockReference::Finality(Finality::Final)),
        ("Finality::None", BlockReference::Finality(Finality::None)),
        ("Height(pre)", BlockReference::BlockId(BlockId::Height(h.pre_resharding_height))),
        ("Height(post)", BlockReference::BlockId(BlockId::Height(h.post_resharding_height))),
    ];

    for (ref_name, block_ref) in &block_refs {
        for node_id in &rpc_nodes {
            let result = h
                .env
                .runner_for_account(node_id)
                .run_jsonrpc_query(
                    RpcQueryRequest {
                        block_reference: block_ref.clone(),
                        request: QueryRequest::ViewAccount { account_id: test_account.clone() },
                    },
                    Duration::seconds(5),
                )
                .unwrap_or_else(|e| panic!("{ref_name} from {node_id} failed: {e:?}"));
            match result.kind {
                QueryResponseKind::ViewAccount(view) => {
                    assert_eq!(view.amount, Balance::from_near(100), "{ref_name} from {node_id}");
                }
                other => {
                    panic!("{ref_name} from {node_id}: expected ViewAccount, got: {other:?}")
                }
            }
        }
    }
}
```

**File:** chain/jsonrpc-primitives/src/types/query.rs (L13-17)
```rust
pub enum RpcQueryError {
    #[error("There are no fully synchronized blocks on the node yet")]
    NoSyncedBlocks,
    #[error("The node does not track the shard ID {requested_shard_id}")]
    UnavailableShard { requested_shard_id: near_primitives::types::ShardId },
```

**File:** pytest/tests/mocknet/sharded_bm.py (L322-362)
```python
def configure_rpc_nodes(args):
    """Configure RPC nodes with shard tracking, RPC-specific config overrides,
    and the sharded RPC pool so nodes can forward queries to each other.

    Applies base_rpc_config_patch.json, sets tracked_shards_config to divide
    shards across the RPC nodes evenly, and writes rpc.sharded_rpc into each
    node's config.json with the full list of all RPC node addresses.
    """
    rpc_instances = args.forknet_details['rpc_instances']
    if not rpc_instances:
        return

    num_rpcs = len(rpc_instances)
    num_shards = get_num_shards(args)
    rpc_config_patch = f"{BENCHNET_DIR}/cases/base_rpc_config_patch.json"
    rpc_port = 3030

    # Pre-compute shard assignments and addresses for all RPC nodes.
    sorted_instances = sorted(rpc_instances, key=lambda x: x[0])
    rpc_assignments = []
    for i, (name, ip) in enumerate(sorted_instances):
        start_shard = i * num_shards // num_rpcs
        end_shard = (i + 1) * num_shards // num_rpcs
        shard_ids = list(range(start_shard, end_shard))
        rpc_assignments.append({
            'name':
                name,
            'ip':
                ip,
            'address':
                f"http://[{ip}]:{rpc_port}"
                if ':' in ip else f"http://{ip}:{rpc_port}",
            'tracked_shards':
                shard_ids,
        })

    sharded_rpc_nodes = [{
        "address": r['address'],
        "tracked_shards": r['tracked_shards']
    } for r in rpc_assignments]
    sharded_rpc_config = json.dumps({"nodes": sharded_rpc_nodes})
```
