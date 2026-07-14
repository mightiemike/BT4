### Title
`new_substr` on `SmallAtom` Fails to Update `ghost_heap`, Causing Heap-Limit Under-Accounting — (File: `src/allocator.rs`)

---

### Summary

`Allocator::new_substr()` increments `ghost_atoms` when a `SmallAtom` input produces a `SmallAtom` result, but it never increments `ghost_heap`. Every other `SmallAtom`-producing path (`new_atom`, `new_small_number`) increments both counters. Because `ghost_heap` is the sole mechanism that keeps the heap-limit check consensus-compatible after the `SmallAtom` optimisation, omitting the increment silently relaxes the limit for every `substr` call on a small atom, diverging from the pre-optimisation accounting that all nodes are supposed to agree on.

---

### Finding Description

**Background — why `ghost_heap` exists.**
Before the `SmallAtom` optimisation, every atom (including tiny integers) was stored in `u8_vec`. The heap-limit check in `new_atom` is:

```
u8_vec.len() + ghost_heap + v.len() > heap_limit
```

After the optimisation, small atoms are encoded directly in the `NodePtr` index and never touch `u8_vec`. To keep the limit semantically identical, every `SmallAtom` creation must add its byte-length to `ghost_heap` so the check behaves as if the bytes were still in `u8_vec`.

**Consistent paths — `new_atom` and `new_small_number`.**

`new_atom` (line 626–629):
```rust
if let Some(ret) = fits_in_small_atom(v) {
    self.ghost_atoms += 1;
    self.ghost_heap += v.len();   // ← both counters updated
    Ok(self.mk_node(ObjectType::SmallAtom, ret as usize))
}
```

`new_small_number` (line 647–648):
```rust
self.ghost_atoms += 1;
self.ghost_heap += len;           // ← both counters updated
```

**Broken path — `new_substr` on `SmallAtom`** (lines 852–854):
```rust
if let Some(new_val) = fits_in_small_atom(substr) {
    self.ghost_atoms += 1;        // ← only ghost_atoms updated
    // ghost_heap NOT incremented ← BUG
    Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
}
```

Every call to `substr` on a small atom that produces a small atom silently under-counts `ghost_heap` by `substr.len()` bytes (1–3 bytes per call, since a `SmallAtom` is at most 3 bytes wide in canonical form).

**How the under-count propagates.**
The heap-limit check fires inside `new_atom` and `new_concat`. Because `ghost_heap` is lower than it should be, the effective limit seen by those checks is `heap_limit + Σ(substr_len)` instead of `heap_limit`. A program that should be rejected with `OutOfMemory` is instead accepted.

**Attacker-controlled entry path.**
`new_substr` is called by the `substr` CLVM operator, which is part of the standard operator set reachable from any attacker-supplied program bytes passed to `run_program` / `run_serialized_chia_program`. No privilege is required.

---

### Impact Explanation

1. **Heap-limit bypass.** Programs can allocate more heap than the configured `heap_limit` allows. The bypass magnitude grows linearly with the number of `substr` calls on small atoms. Each call contributes up to 3 bytes of under-counting; the total is bounded only by the cost limit.

2. **Consensus divergence.** Nodes that enforce the heap limit (e.g., via `Allocator::new_limited`) may accept a program that a reference implementation (or a node with a tighter limit) would reject, or vice versa. Because `ghost_heap` is explicitly described as a consensus-compatibility mechanism, any deviation in its value is a consensus-layer defect.

---

### Likelihood Explanation

The `substr` operator is a standard, unconditionally available CLVM operator. Any externally submitted program can invoke it on small-integer atoms (e.g., `(substr (q . 1) 0 1)`). No special flags, softfork extensions, or privileged keys are needed. The trigger is a single valid CLVM byte sequence, making exploitation straightforward for any attacker who can submit programs to a Chia node.

---

### Recommendation

In the `SmallAtom` branch of `new_substr`, add the missing `ghost_heap` increment to match `new_atom` and `new_small_number`:

```rust
if let Some(new_val) = fits_in_small_atom(substr) {
    self.ghost_atoms += 1;
    self.ghost_heap += substr.len();   // ← add this line
    Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
}
```

---

### Proof of Concept

```
Program bytes (CLVM text): (substr (q . 1) (q . 0) (q . 1))
```

Each evaluation of this program calls `new_substr` with a `SmallAtom` input (`1`) and produces a `SmallAtom` result (`1`). `ghost_heap` is incremented by 0 instead of 1. Wrapping this in a recursive loop (bounded by cost) accumulates an arbitrarily large under-count of `ghost_heap`, allowing subsequent `new_atom` calls to succeed past the intended `heap_limit`.

**Relevant lines:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** src/allocator.rs (L619-637)
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
```

**File:** src/allocator.rs (L640-649)
```rust
    pub fn new_small_number(&mut self, v: u32) -> Result<NodePtr> {
        debug_assert!(v <= NODE_PTR_IDX_MASK);
        let len = len_for_value(v);
        if self.u8_vec.len() + self.ghost_heap + len > self.heap_limit {
            return Err(EvalErr::OutOfMemory);
        }
        self.check_atom_limit()?;
        self.ghost_atoms += 1;
        self.ghost_heap += len;
        Ok(self.mk_node(ObjectType::SmallAtom, v as usize))
```

**File:** src/allocator.rs (L845-868)
```rust
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
```
