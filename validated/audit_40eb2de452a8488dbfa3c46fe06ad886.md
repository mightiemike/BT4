## Finding

Based on my review, this is a genuine, non-trivial correctness bug in the deterministic block-partitioning path (not a "malicious peer" issue — it is triggered purely by the *content* of transactions an unprivileged sender submits).

### Title
Sender transaction ordering can be reversed by pre-partition group scheduling, causing sharded execution to commit writes out of original submission order - (File: execution/block-partitioner/src/v2/partition_to_matrix.rs)

### Summary
`PartitionerV2::remove_cross_shard_dependencies` (via `PartitionToMatrix`) enforces "same-sender relative order preservation" using `PrePartitionedTxnIdx` comparisons, not `OriginalTxnIdx`: [1](#0-0) 

This is only correct if `PrePartitionedTxnIdx` is guaranteed monotonic with `OriginalTxnIdx` for every sender's transactions. That guarantee is not actually enforced by `ConnectedComponentPartitioner::pre_partition`.

### Finding Description
`ConnectedComponentPartitioner` groups conflicting transactions (union-find over senders/state-keys) into a FIFO queue per connected component, then splits each component into fixed-size chunks ("groups") once it exceeds `group_size_limit`: [2](#0-1) 

These groups are then assigned to shards using `longest_processing_time_first` (LPT bin-packing), which sorts tasks purely by size and greedily assigns to the least-loaded shard — with no regard to group id / chronological order: [3](#0-2) 

Within a shard, groups are still materialized in ascending `group_id` order, preserving local temporal order, but **across shards** an earlier chunk (lower `group_id`, earlier `OriginalTxnIdx`) can be scheduled to a *later* shard index than a later chunk from the same connected component: [4](#0-3) 

Since `PrePartitionedTxnIdx` is then assigned strictly by shard order (shard 0's txns get the lowest indices, shard 1 next, etc.): [5](#0-4) 

a sender whose own transactions get split into two or more groups (because the connected component containing them exceeds `group_size_limit`) can end up with a *later*-submitted transaction receiving a *smaller* `PrePartitionedTxnIdx` than an *earlier* one. The code's own comment acknowledges this exact risk ("If we create actual txn groups now and then do load-balanced scheduling, we break the relative order of txns from the same sender") but the claimed "workaround" only fixes ordering *within* a shard, not *across* shards.

Because every transaction from a given sender necessarily writes to that sender's account resource (sequence-number bump), all of a sender's transactions land in the same connected component and are tracked by the same `ConflictingTxnTracker` key. The final execution/dependency order used to build cross-shard edges (`add_edges`/`take_txn_with_dep`, using `finalized_writes`/`ShardedTxnIndexV2` ordering) is based on `(round_id, shard_id, PrePartitionedTxnIdx)` — i.e., exactly the possibly-reversed order: [6](#0-5) 

So when the "preserve relative order" logic in `partition_to_matrix.rs` fails to correctly identify the true earliest-original-index discarded txn (because it compares `PrePartitionedTxnIdx`, not `OriginalTxnIdx`), a later-submitted transaction can be finally accepted in an earlier sub-block/round than an earlier-submitted transaction from the same sender. The dependency-edge construction then binds writes to the sender's account resource in that (incorrect) schedule order rather than the original block order that a canonical single-threaded/sequential execution would produce.

### Impact Explanation
This affects the deterministic mapping from a block's transaction list to committed writes: the sharded partitioner is required to reproduce exactly the same state transition as sequential execution of the original block order. If the ordering bug is triggered, the sharded executor commits account-resource writes (balance/sequence number) in the wrong order relative to what correct VM semantics (and non-sharded/differently-sharded validators) would produce for the identical block. Because `num_executor_shards` and `group_size_limit`/`load_imbalance_tolerance` are local, non-consensus configuration values, different honest validators (or the same validator resharding under different local settings) can derive different `PrePartitionedTxnIdx`/shard assignments for the same block and therefore potentially different state roots — a hard-fork-class divergence, or, at minimum, incorrect commitment of a sender's writes out of sequence-number order.

### Likelihood Explanation
Triggering requires that a single connected component (a sender's own txns, or a group of senders merged via shared write keys) exceed `group_size_limit`, and that the LPT scheduler assigns the resulting chunks to shards out of chronological order — both are plausible for an unprivileged attacker who submits enough transactions touching a shared/contended resource within one block (e.g., many sybil accounts writing to the same hot resource, or one account submitting a large burst of transactions) to force group splitting. I could not verify from the available files whether default production configuration (`load_imbalance_tolerance`, `num_executor_shards`, mempool per-account transaction caps) makes this exploitable at current mainnet parameter values, nor could I find or rule out an additional safety check elsewhere in the executor pipeline (e.g., in `add_edges`/`build_index_from_txn_matrix`) that might re-validate and correct sender ordering before commit. This uncertainty should be resolved by tracing `build_index_from_txn_matrix` and the full `add_edges` implementation and by testing with realistic mempool/consensus block-size and shard-count defaults.

### Recommendation
- In `PartitionToMatrix::process_round` (or equivalent), compare using `OriginalTxnIdx` (via `state.ori_idxs_by_pre_partitioned[txn_idx]`) instead of raw `PrePartitionedTxnIdx` when computing `min_discard_table` and the `txn_idx < min_discarded` check, so the "preserve relative order" invariant is anchored to the true original submission order rather than an incidental scheduling artifact.
- Add an explicit invariant check/assertion (in debug and fuzz/property tests) that for every sender, the sequence of `PrePartitionedTxnIdx` (and ultimately `FinalTxnIdx`) assigned to that sender's transactions is monotonic with `OriginalTxnIdx`, and fail loudly (not silently corrupt) if violated.
- Constrain `ConnectedComponentPartitioner`'s LPT group-to-shard assignment so that when a single sender's/component's chunks are split, their shard assignment preserves chunk order (e.g., only bin-pack whole components, or add an ordering constraint into the LPT scheduling for same-component groups).

### Proof of Concept
Conceptual reproduction (unit-test style) for `execution/block-partitioner/src/v2/partition_to_matrix.rs` / `execution/block-partitioner/src/pre_partition/connected_component/mod.rs`:
1. Construct a block where a single sender `S` submits `N > group_size_limit` transactions, all writing to `S`'s own account resource key (guaranteeing they form a single connected component along with any other senders sharing that key), with `num_executor_shards >= 3`.
2. Run `ConnectedComponentPartitioner::pre_partition`; inspect `group_metadata` to find two groups both entirely composed of `S`'s transactions, with group ids `g1 < g2` (i.e., `g1`'s chunk is temporally earlier in `OriginalTxnIdx`).
3. Craft/pick transaction sizes (`task_costs`) so that `longest_processing_time_first` assigns `g2` to a lower shard index than `g1` (achievable since LPT sorts by size only).
4. Observe that `ori_idxs_by_pre_partitioned` maps a smaller `PrePartitionedTxnIdx` to a *larger* `OriginalTxnIdx` for `S`'s transactions, violating monotonicity.
5. Run `remove_cross_shard_dependencies`; assert that the resulting `finally_accepted`/`FinalTxnIdx` ordering for sender `S`'s transactions does not match ascending `OriginalTxnIdx`, demonstrating the invariant break described in the exploit question.

### Citations

**File:** execution/block-partitioner/src/v2/partition_to_matrix.rs (L152-165)
```rust
                    txn_idxs.into_par_iter().for_each(|txn_idx| {
                        let ori_txn_idx = state.ori_idxs_by_pre_partitioned[txn_idx];
                        let sender_idx = state.sender_idx(ori_txn_idx);
                        let min_discarded = min_discard_table
                            .get(&sender_idx)
                            .map(|kv| kv.load(Ordering::SeqCst))
                            .unwrap_or(usize::MAX);
                        if txn_idx < min_discarded {
                            state.update_trackers_on_accepting(txn_idx, round_id, shard_id);
                            finally_accepted[shard_id].write().unwrap().push(txn_idx);
                        } else {
                            discarded[shard_id].write().unwrap().push(txn_idx);
                        }
                    });
```

**File:** execution/block-partitioner/src/pre_partition/connected_component/mod.rs (L88-106)
```rust
        // Calculate txn group size limit.
        let group_size_limit = ((state.num_txns() as f32) * self.load_imbalance_tolerance
            / (state.num_executor_shards as f32))
            .ceil() as usize;

        // Prepare `group_metadata`, a group_metadata (i, r) will later be converted to a real group that takes `r` txns from set `i`.
        // NOTE: If we create actual txn groups now and then do load-balanced scheduling, we break the relative order of txns from the same sender.
        // The workaround is to only fix the group set and their sizes for now, then schedule, and materialize the txn groups at the very end (when assigning groups to shards).
        let group_metadata: Vec<(usize, usize)> = txns_by_set
            .iter()
            .enumerate()
            .flat_map(|(set_idx, txns)| {
                let num_chunks = txns.len().div_ceil(group_size_limit);
                let mut ret = vec![(set_idx, group_size_limit); num_chunks];
                let last_chunk_size = txns.len() - group_size_limit * (num_chunks - 1);
                ret[num_chunks - 1] = (set_idx, last_chunk_size);
                ret
            })
            .collect();
```

**File:** execution/block-partitioner/src/pre_partition/connected_component/mod.rs (L116-132)
```rust
        // Prepare `groups_by_shard`: a mapping from a shard to the txn groups assigned to it.
        let mut groups_by_shard: Vec<Vec<usize>> = vec![vec![]; state.num_executor_shards];
        for (group_id, shard_id) in shards_by_group.into_iter().enumerate() {
            groups_by_shard[shard_id].push(group_id);
        }

        let mut ori_txns_idxs_by_shard: Vec<Vec<OriginalTxnIdx>> =
            vec![vec![]; state.num_executor_shards];
        for (shard_id, group_ids) in groups_by_shard.into_iter().enumerate() {
            for group_id in group_ids.into_iter() {
                let (set_id, amount) = group_metadata[group_id];
                for _ in 0..amount {
                    let ori_txn_idx = txns_by_set[set_id].pop_front().unwrap();
                    ori_txns_idxs_by_shard[shard_id].push(ori_txn_idx);
                }
            }
        }
```

**File:** execution/block-partitioner/src/pre_partition/connected_component/mod.rs (L134-144)
```rust
        // Prepare `ori_txn_idxs` and `start_txn_idxs_by_shard`.
        let mut start_txn_idxs_by_shard = vec![0; state.num_executor_shards];
        let mut ori_txn_idxs = vec![0; state.num_txns()];
        let mut pre_partitioned_txn_idx = 0;
        for (shard_id, txn_idxs) in ori_txns_idxs_by_shard.iter().enumerate() {
            start_txn_idxs_by_shard[shard_id] = pre_partitioned_txn_idx;
            for &i0 in txn_idxs {
                ori_txn_idxs[pre_partitioned_txn_idx] = i0;
                pre_partitioned_txn_idx += 1;
            }
        }
```

**File:** execution/block-partitioner/src/v2/load_balance.rs (L11-34)
```rust
pub fn longest_processing_time_first(task_costs: &[u64], num_workers: usize) -> (u64, Vec<usize>) {
    assert!(num_workers >= 1);
    let num_tasks = task_costs.len();
    let mut cost_tid_pairs: Vec<(u64, usize)> = task_costs
        .iter()
        .enumerate()
        .map(|(tid, cost)| (*cost, tid))
        .collect();
    cost_tid_pairs.sort_by(|a, b| b.cmp(a));
    let mut worker_prio_heap: BinaryHeap<(u64, usize)> =
        BinaryHeap::from((0..num_workers).map(|wid| (u64::MAX, wid)).collect_vec());
    let mut worker_ids_by_tid = vec![usize::MAX; num_tasks];
    for (cost, tid) in cost_tid_pairs.into_iter() {
        let (availability, worker_id) = worker_prio_heap.pop().unwrap();
        worker_ids_by_tid[tid] = worker_id;
        let new_availability = availability - cost;
        worker_prio_heap.push((new_availability, worker_id));
    }
    let longest_pole = worker_prio_heap
        .into_iter()
        .map(|(a, _i)| u64::MAX - a)
        .max()
        .unwrap();
    (longest_pole, worker_ids_by_tid)
```

**File:** execution/block-partitioner/src/v2/state.rs (L290-321)
```rust
    /// Take a txn out, wrap it as a `TransactionWithDependencies`.
    pub(crate) fn take_txn_with_dep(
        &self,
        round_id: RoundId,
        shard_id: ShardId,
        txn_idx: PrePartitionedTxnIdx,
    ) -> TransactionWithDependencies<AnalyzedTransaction> {
        let ori_txn_idx = self.ori_idxs_by_pre_partitioned[txn_idx];
        let txn = self.txns[ori_txn_idx].write().unwrap().take().unwrap();
        let mut deps = CrossShardDependencies::default();

        // Build required edges.
        let write_set = self.write_sets[ori_txn_idx].read().unwrap();
        let read_set = self.read_sets[ori_txn_idx].read().unwrap();
        for &key_idx in write_set.iter().chain(read_set.iter()) {
            let tracker_ref = self.trackers.get(&key_idx).unwrap();
            let tracker = tracker_ref.read().unwrap();
            if let Some(txn_idx) = tracker
                .finalized_writes
                .range(..ShardedTxnIndexV2::new(round_id, shard_id, 0))
                .last()
            {
                let src_txn_idx = ShardedTxnIndex {
                    txn_index: *self.final_idxs_by_pre_partitioned[txn_idx.pre_partitioned_txn_idx]
                        .read()
                        .unwrap(),
                    shard_id: txn_idx.shard_id(),
                    round_id: txn_idx.round_id(),
                };
                deps.add_required_edge(src_txn_idx, tracker.storage_location.clone());
            }
        }
```
