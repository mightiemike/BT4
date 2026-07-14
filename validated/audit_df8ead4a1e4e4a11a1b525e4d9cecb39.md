### Title
`ghost_heap` Not Updated in `new_substr` for SmallAtom — Heap-Limit Accounting Inconsistency - (File: src/allocator.rs)

### Summary

`Allocator::new_substr()` creates a new `SmallAtom` result without incrementing `ghost_heap`, while every other small-atom allocation path (`new_atom`, `new_small_number`) increments both `ghost_atoms` and `ghost_heap`. Because `ghost_heap` is the consensus-critical counter that enforces the heap limit for optimized-out (small) atoms, the omission means that repeated `substr` calls on small atoms silently underreport virtual heap usage, making the heap-limit guard too lenient.

### Finding Description

The `ghost_heap` counter exists specifically to maintain consensus parity with the old Python-based CLVM, which allocated every atom—including small ones—on the heap. The code comment is explicit:

> "the ghost counters are pretend atoms/pairs, that were optimized out. We still account for them to not affect the limits of atoms and pairs. Those limits must stay the same for consensus purpose."

Every small-atom creation path increments `ghost_heap` by the atom's byte length:

`new_atom` (small-atom branch): [1](#0-0) 

`new_small_number`: [2](#0-1) 

But `new_substr` for `ObjectType::SmallAtom`, when the result still fits in a small atom, only increments `ghost_atoms` and silently omits the `ghost_heap` update: [3](#0-2) 

The heap-limit guard in `new_atom` (and `new_small_number`, `new_concat`) reads:

```rust
if start as usize + self.ghost_heap + v.len() > self.heap_limit {
    return Err(EvalErr::OutOfMemory);
}
``` [4](#0-3) 

Because `ghost_heap` is underreported by up to `(end - start)` bytes per `new_substr` call on a SmallAtom, subsequent heap-limit checks in `new_atom`, `new_small_number`, and `new_concat` are too lenient by exactly that accumulated amount.

### Impact Explanation

The `ghost_heap` counter is a consensus-critical invariant. If the reference Python CLVM would have rejected a program for exceeding the heap limit (because it allocated real heap bytes for every atom), but `clvm_rs` accepts it (because `ghost_heap` is underreported), the two implementations diverge on program validity. This is a **consensus divergence** bug: a crafted CLVM program accepted by `clvm_rs` but rejected by the reference implementation (or vice versa) can split the network or allow invalid spend bundles to pass validation on one node class.

The atom-count limit (`ghost_atoms`) is still enforced, so the bypass is bounded by `MAX_NUM_ATOMS × max_small_atom_bytes` (at most 4 bytes per atom). The default heap limit is `u32::MAX`, so practical exploitation requires a node configured with a tighter limit, or a future protocol change that lowers the limit. However, the invariant violation is present in all configurations and is a latent consensus risk.

### Likelihood Explanation

The entry path is direct: any CLVM program that calls the `substr` opcode on a small atom (atoms whose value fits in a `u32`) triggers the missing `ghost_heap` increment. Such programs are attacker-controlled CLVM bytes submitted to any Chia full node. The `op_substr` operator is a standard CLVM opcode reachable without special flags or permissions. [5](#0-4) 

### Recommendation

Add `self.ghost_heap += (end - start) as usize;` in the `SmallAtom` branch of `new_substr`, immediately after `self.ghost_atoms += 1;`, to match the accounting performed by `new_atom` and `new_small_number`:

```rust
if let Some(new_val) = fits_in_small_atom(substr) {
    self.ghost_atoms += 1;
    self.ghost_heap += (end - start) as usize; // ← add this
    Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
}
```

Also add a heap-limit pre-check at the top of the `SmallAtom` branch (mirroring `new_atom`):

```rust
if self.u8_vec.len() + self.ghost_heap + (end - start) as usize > self.heap_limit {
    return Err(EvalErr::OutOfMemory);
}
```

### Proof of Concept

```rust
// Demonstrates ghost_heap is NOT incremented by new_substr on SmallAtom
let mut a = Allocator::new_limited(10); // tight heap limit
// ghost_heap starts at 1 (for the pre-allocated one())
// new_atom(&[1]) would increment ghost_heap by 1 → total virtual heap = 2
let small = a.new_atom(&[1]).unwrap(); // ghost_heap = 2

// Now take a substr of the small atom — result is still a SmallAtom
// new_substr does NOT increment ghost_heap
let _sub = a.new_substr(small, 0, 1).unwrap(); // ghost_heap still = 2 (BUG: should be 3)

// Because ghost_heap is underreported, new_atom succeeds when it should fail
// The heap limit check sees: u8_vec.len()(0) + ghost_heap(2) + 1 = 3 < 10 → OK
// But the true virtual heap is 4 (initial 1 + atom 1 + substr 1 + new 1)
let _extra = a.new_atom(&[2]).unwrap(); // should this succeed? ghost_heap says yes
```

The inconsistency is directly visible by comparing `ghost_heap` before and after `new_substr` vs. `new_atom` for the same byte-length atom. [6](#0-5) [3](#0-2)

### Citations

**File:** src/allocator.rs (L619-638)
```rust
    pub fn new_atom(&mut self, v: &[u8]) -> Result<NodePtr> {
        let start = self.u8_vec.len() as u32;
        if start as usize + self.ghost_heap + v.len() > self.heap_limit {
            return Err(EvalErr::OutOfMemory);
        }
        let idx = self.atom_vec.len();
        self.check_atom_limit()?;
        if let Some(ret) = fits_in_small_atom(v) {
            self.ghost_atoms += 1;
            self.ghost_heap += v.len();
            Ok(self.mk_node(ObjectType::SmallAtom, ret as usize))
        } else {
            self.u8_vec.extend_from_slice(v);
            let end = self.u8_vec.len() as u32;
            self.atom_vec.push(AtomBuf { start, end });
            #[cfg(feature = "counters")]
            self.update_max_counts();
            Ok(self.mk_node(ObjectType::Bytes, idx))
        }
    }
```

**File:** src/allocator.rs (L647-648)
```rust
        self.ghost_atoms += 1;
        self.ghost_heap += len;
```

**File:** src/allocator.rs (L799-870)
```rust
    pub fn new_substr(&mut self, node: NodePtr, start: u32, end: u32) -> Result<NodePtr> {
        #[cfg(feature = "allocator-debug")]
        self.validate_node(node);

        self.check_atom_limit()?;

        fn bounds_check(node: NodePtr, start: u32, end: u32, len: u32) -> Result<()> {
            if start > len {
                Err(EvalErr::InvalidAllocArg(
                    node,
                    format!("substr start out of bounds: {start} > {len}"),
                ))?;
            }
            if end > len {
                Err(EvalErr::InvalidAllocArg(
                    node,
                    format!("substr end out of bounds: {end} > {len}"),
                ))?;
            }
            if end < start {
                Err(EvalErr::InvalidAllocArg(
                    node,
                    format!("substr invalid bounds: {end} < {start}"),
                ))?;
            }
            Ok(())
        }

        match node.object_type() {
            ObjectType::Pair => Err(EvalErr::InternalError(
                node,
                "substr expected atom, got pair".to_string(),
            ))?,
            ObjectType::Bytes => {
                let atom = self.atom_vec[node.index() as usize];
                let atom_len = atom.end - atom.start;
                bounds_check(node, start, end, atom_len)?;
                let idx = self.atom_vec.len();
                self.atom_vec.push(AtomBuf {
                    start: atom.start + start,
                    end: atom.start + end,
                });
                #[cfg(feature = "counters")]
                self.update_max_counts();
                Ok(self.mk_node(ObjectType::Bytes, idx))
            }
            ObjectType::SmallAtom => {
                let val = node.index();
                let len = len_for_value(val) as u32;
                bounds_check(node, start, end, len)?;
                let buf: [u8; 4] = val.to_be_bytes();
                let buf = &buf[4 - len as usize..];
                let substr = &buf[start as usize..end as usize];
                if let Some(new_val) = fits_in_small_atom(substr) {
                    self.ghost_atoms += 1;
                    Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
                } else {
                    let start = self.u8_vec.len();
                    let end = start + substr.len();
                    self.u8_vec.extend_from_slice(substr);
                    let idx = self.atom_vec.len();
                    self.atom_vec.push(AtomBuf {
                        start: start as u32,
                        end: end as u32,
                    });
                    #[cfg(feature = "counters")]
                    self.update_max_counts();
                    Ok(self.mk_node(ObjectType::Bytes, idx))
                }
            }
        }
    }
```
