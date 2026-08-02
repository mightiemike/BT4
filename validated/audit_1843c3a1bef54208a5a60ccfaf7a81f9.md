[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** execution/block-partitioner/src/v2/union_find.rs (L23-65)
```rust
impl UnionFind {
    pub fn new(num_participants: usize) -> Self {
        Self {
            parent_of: (0..num_participants).collect(),
            height_of: vec![0; num_participants],
        }
    }

    pub fn find(&mut self, a: usize) -> usize {
        let mut root = self.parent_of[a];
        while self.parent_of[root] != root {
            root = self.parent_of[root];
        }

        let mut element = a;
        while element != root {
            let next_element = self.parent_of[element];
            self.parent_of[element] = root;
            element = next_element;
        }
        root
    }

    pub fn union(&mut self, x: usize, y: usize) {
        let px = self.find(x);
        let py = self.find(y);
        if px == py {
            return;
        }

        match self.height_of[px].cmp(&self.height_of[py]) {
            Ordering::Less => {
                self.parent_of[py] = px;
            },
            Ordering::Greater => {
                self.parent_of[px] = py;
            },
            Ordering::Equal => {
                self.parent_of[px] = py;
                self.height_of[py] += 1;
            },
        }
    }
```

**File:** execution/block-partitioner/src/pre_partition/connected_component/mod.rs (L137-144)
```rust
        let mut pre_partitioned_txn_idx = 0;
        for (shard_id, txn_idxs) in ori_txns_idxs_by_shard.iter().enumerate() {
            start_txn_idxs_by_shard[shard_id] = pre_partitioned_txn_idx;
            for &i0 in txn_idxs {
                ori_txn_idxs[pre_partitioned_txn_idx] = i0;
                pre_partitioned_txn_idx += 1;
            }
        }
```

**File:** execution/block-partitioner/src/pre_partition/connected_component/mod.rs (L146-157)
```rust
        // Prepare `pre_partitioned`.
        let pre_partitioned = (0..state.num_executor_shards)
            .map(|shard_id| {
                let start = start_txn_idxs_by_shard[shard_id];
                let end: PrePartitionedTxnIdx = if shard_id == state.num_executor_shards - 1 {
                    state.num_txns()
                } else {
                    start_txn_idxs_by_shard[shard_id + 1]
                };
                (start..end).collect()
            })
            .collect();
```
