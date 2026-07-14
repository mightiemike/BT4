Looking at the external report's vulnerability class — **mutable shared state overwritten by subsequent operations before the first completes, causing incorrect accounting** — I need to find an analog in clvm_rs where a cached/stored value is overridden by a later operation, producing incorrect results in a consensus-critical path.

I examined the following candidates:

**`ObjectCache<T>` (`src/serde/object_cache.rs`):** The `set()` method calls `cache.insert(*node, v)` which would overwrite existing values. However, `calculate()` only calls `set()` when the cache has no entry for the node (`None` branch), so no override occurs.

**`TreeCache.update()` sentinel handling (`src/serde/tree_cache.rs`):** Each call to `update()` creates a new `NodeEntry` for the sentinel and overwrites `node_map[sentinel]`. The old entry is orphaned in `node_entries`. However, parents are explicitly transferred at the start of `update()` before the overwrite, making this intentional and correct. After `restore()`, stale parent links in orphaned entries do not propagate back to old entries in a way that corrupts `find_path()` results.

**`validated_g1_points`/`validated_g2_points` caches (`src/allocator.rs`):** These persist across `restore_checkpoint()` calls inside softfork guards. However, BLS point validity is purely mathematical and context-independent, so persistence across restore is not a semantic error.

**`traverse_path_with_vec` cache (`src/serde/de_br.rs`):** The `x.1` cache is local to each `node_from_stream_backrefs` call and is not shared across invocations.

**`clvm_tree_to_lazy_node` identity map (`wheel/src/api.rs`):** Keyed by Python object pointer, which could alias after GC, but this is not attacker-controlled via CLVM bytes.

**`run_program` softfork/GC stacks (`src/run_program.rs`):** The `softfork_stack` and `allocator_stack` are proper LIFO stacks; each guard/checkpoint is independent and correctly scoped.

None of these produce a concrete broken invariant — a corrupted cost, wrong tree hash, invalid NodePtr, or incorrect back-reference path — that is reachable via attacker-controlled CLVM bytes with consensus or wallet impact matching the