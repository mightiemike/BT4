### Title
Uncontrolled recursion in memtrie post-order traversal and node ref-counted deallocation, triggerable via attacker-controlled storage key structure - (File: `core/store/src/trie/mem/memtrie_update.rs`, `core/store/src/trie/mem/node/encoding.rs`)

### Summary
`MemTrieUpdate::post_order_traverse_updated_nodes` and `MemTrieNodeId::remove_ref` both recurse over the in-memory trie structure with no depth bound, mirroring the root cause of the reported mdex CVE (mutually/self-recursive tree traversal with no maximum nesting depth check, driven entirely by attacker-shaped input). Unlike other trie-walking code in the same crate, which was explicitly converted to iterative stack-based traversal specifically "to avoid any potential stack overflows" (see the comment in `trie_recording.rs`), these two functions were not converted and remain naively recursive.

### Finding Description
`post_order_traverse_updated_nodes` recurses into every `Branch`/`Extension` child while building the list of nodes to persist after a trie update: [1](#0-0) 

`MemTrieNodeId::remove_ref` recursively unrefs (and potentially deallocates) all children whenever a node's refcount reaches zero, again with no explicit stack or depth limit: [2](#0-1) 

Both call chains are driven purely by the shape of the trie for a given account's storage sub-tree, which an unprivileged transaction sender fully controls through repeated `storage_write` host calls. Recursion depth is bounded only by the number of trie nodes on a path (extension/branch nodes), which is bounded by `max_length_storage_key` (2048 bytes = up to 4096 nibbles in the current mainnet config): [3](#0-2) [4](#0-3) 

By contrast, the codebase explicitly acknowledges this class of bug and mitigates it elsewhere with iterative BFS/DFS using explicit stacks/queues, e.g. in `get_subtree_size` and `traverse_all_nodes`: [5](#0-4) [6](#0-5) 

But `post_order_traverse_updated_nodes` (called from `flatten_nodes`-equivalent memtrie construction) and `remove_ref` (called on every trie GC / root deletion, i.e., after essentially every applied block) were left as plain recursive functions.

### Impact Explanation
A stack overflow in a Rust process is not a catchable panic — it triggers `SIGSEGV`/abort and kills the entire process. Since these functions run inside the runtime apply path (memtrie update after applying a chunk's transactions/receipts) and in GC of old trie roots, a crafted transaction that builds an extremely deep/skewed storage sub-trie (many keys sharing very long common prefixes, forcing long extension+branch chains close to the ~4096-nibble bound) could cause the node process applying or garbage-collecting that state to crash. Because every validator/RPC node applying the same block would independently walk the same trie structure, this is a transaction-triggered, deterministically reproducible node crash — a liveness/availability impact reachable from a single submitted transaction.

### Likelihood Explanation
Reachability requires only ordinary `storage_write` FunctionCall actions from any account — no special privileges, staking, or validator role needed. The attacker only needs to spend enough gas/storage deposit to write enough keys to build a sufficiently deep path (bounded by `max_length_storage_key` and `max_length_storage_key` interacting with per-nibble branching, so many separate writes may be required to force a maximally deep chain of single-child extension/branch nodes rather than a shallow highly-branching trie). This raises the cost/complexity of exploitation compared to the trivial "6000 nested blockquotes" mdex PoC, but the underlying code defect — unbounded recursion depth proportional to attacker-controlled data — is structurally the same.

### Recommendation
Convert `post_order_traverse_updated_nodes` and `MemTrieNodeId::remove_ref` to iterative, explicit-stack traversal (as already done for `get_subtree_size`/`traverse_all_nodes`), or add an explicit maximum recursion-depth guard consistent with `max_length_storage_key`, returning a `StorageError` instead of recursing indefinitely.

### Proof of Concept
1. From an unprivileged account, submit a sequence of `FunctionCall` transactions/receipts invoking `storage_write` with keys engineered to share very long common prefixes and differ only in trailing nibbles at maximal allowed key length (`max_length_storage_key`), repeated enough times to force construction of a long uninterrupted extension/branch chain in that account's trie sub-tree.
2. Trigger a trie update (e.g., further writes/deletes) so `MemTrieUpdate::post_order_traverse_updated_nodes` walks the deep chain, or wait for garbage collection of an old root so `MemTrieNodeId::remove_ref` recursively deallocates the deep chain.
3. On a sufficiently deep chain (approaching the nibble-count bound), the recursive call stack exhausts the thread stack, causing the node process to abort/crash while applying or GC'ing that block — affecting every node that processes the same state transition.

### Citations

**File:** core/store/src/trie/mem/memtrie_update.rs (L292-325)
```rust
    fn post_order_traverse_updated_nodes(
        node_id: UpdatedNodeId,
        updated_nodes: &Vec<Option<UpdatedMemTrieNodeWithSize>>,
        ordered_nodes: &mut Vec<UpdatedNodeId>,
    ) {
        let node = updated_nodes[node_id].as_ref().unwrap();
        match &node.node {
            UpdatedMemTrieNode::Empty => {
                assert_eq!(node_id, 0); // only root can be empty
                return;
            }
            UpdatedMemTrieNode::Branch { children, .. } => {
                for child in children.iter() {
                    if let Some(OldOrUpdatedNodeId::Updated(child_node_id)) = child {
                        Self::post_order_traverse_updated_nodes(
                            *child_node_id,
                            updated_nodes,
                            ordered_nodes,
                        );
                    }
                }
            }
            UpdatedMemTrieNode::Extension { child, .. } => {
                if let OldOrUpdatedNodeId::Updated(child_node_id) = child {
                    Self::post_order_traverse_updated_nodes(
                        *child_node_id,
                        updated_nodes,
                        ordered_nodes,
                    );
                }
            }
            _ => {}
        }
        ordered_nodes.push(node_id);
```

**File:** core/store/src/trie/mem/node/encoding.rs (L238-265)
```rust
    /// Decrements the refcount, deallocating the node if it reaches zero.
    /// Returns the new refcount.
    pub(crate) fn remove_ref(&self, arena: &mut impl ArenaWithDealloc) -> u32 {
        // It's possible that in a hybrid memory setup, we are accessing the read-only part of memory.
        // In that case, we don't need to decrement the refcount.
        if !arena.memory_mut().is_mutable(self.pos) {
            return 1;
        }
        // Refcount is always encoded as the first four bytes of the node memory.
        // cspell:words unref
        let refcount_memory = arena.memory_mut().raw_slice_mut(self.pos, size_of::<u32>());
        let refcount = u32::from_le_bytes(refcount_memory.try_into().unwrap());
        let new_refcount = refcount.strict_sub(1);
        refcount_memory.copy_from_slice(new_refcount.to_le_bytes().as_ref());
        if new_refcount == 0 {
            let mut children_to_unref: SmallVec<[ArenaPos; NUM_CHILDREN]> = SmallVec::new();
            let node_ptr = self.as_ptr(arena.memory());
            for child in node_ptr.view().iter_children() {
                children_to_unref.push(child.id().pos);
            }
            let alloc_size = node_ptr.size_of_allocation();
            arena.dealloc(self.pos, alloc_size);
            for child in &children_to_unref {
                MemTrieNodeId { pos: *child }.remove_ref(arena);
            }
        }
        new_refcount
    }
```

**File:** core/parameters/res/runtime_configs/parameters.snap (L245-245)
```text
max_length_storage_key                                 2_048
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L4878-4884)
```rust
    if key.len() as u64 > ctx.config.limit_config.max_length_storage_key {
        return Err(HostError::KeyLengthExceeded {
            length: key.len() as u64,
            limit: ctx.config.limit_config.max_length_storage_key,
        }
        .into());
    }
```

**File:** core/store/src/trie/trie_recording.rs (L297-306)
```rust
    /// Get size of all recorded nodes and values which are under `subtree_root` (including `subtree_root`).
    fn get_subtree_size(&self, subtree_root: &CryptoHash) -> SubtreeSize {
        let mut nodes_size: usize = 0;
        let mut values_size: usize = 0;

        // Non recursive approach to avoid any potential stack overflows.
        let mut queue: VecDeque<CryptoHash> = VecDeque::new();
        queue.push_back(*subtree_root);

        let mut seen_items: HashSet<CryptoHash> = HashSet::new();
```

**File:** core/store/src/trie/state_parts.rs (L644-655)
```rust
        /// on_enter is applied for nodes as well as values
        fn traverse_all_nodes<F: FnMut(&CryptoHash) -> Result<(), StorageError>>(
            &self,
            mut on_enter: F,
        ) -> Result<(), StorageError> {
            if self.root == Trie::EMPTY_ROOT {
                return Ok(());
            }
            let mut stack: Vec<(CryptoHash, TrieStorageNodeWithSize, CrumbStatus)> = Vec::new();
            let root_node = self.retrieve_storage_node(&self.root)?;
            stack.push((self.root, root_node, CrumbStatus::Entering));
            while let Some((hash, node, position)) = stack.pop() {
```
