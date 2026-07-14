### Title
`new_substr` SmallAtom→SmallAtom Branch Omits `ghost_heap` Increment, Silently Undercharging Heap Accounting — (`File: src/allocator.rs`)

---

### Summary

`Allocator::new_substr` in `src/allocator.rs` has an accounting mismatch in its `ObjectType::SmallAtom` → `ObjectType::SmallAtom` branch: it increments `ghost_atoms` (atom count) but never increments `ghost_heap` (virtual heap bytes). Every other SmallAtom-producing path — `new_atom`, `new_small_number` — increments both counters. The omission means `heap_size()` silently underreports virtual heap usage after each `substr` call on a SmallAtom, and the heap-limit guard used by all subsequent allocations operates on a deflated baseline, allowing more heap to be consumed than the configured limit permits.

---

### Finding Description

**Broken invariant:** Every SmallAtom allocation must charge `ghost_heap` by the atom's byte length. `ghost_heap` is the virtual-heap accumulator that makes SmallAtoms (which live inside the `NodePtr` tag bits and consume no `u8_vec` space) count against the same limit as real heap atoms, preserving consensus-compatible resource accounting.

`new_atom` (the canonical SmallAtom path):

```rust
// src/allocator.rs lines 626-629
if let Some(ret) = fits_in_small_atom(v) {
    self.ghost_atoms += 1;
    self.ghost_heap += v.len();   // ← heap bytes charged
    Ok(self.mk_node(ObjectType::SmallAtom, ret as usize))
}
```

`new_small_number` (another SmallAtom path):

```rust
// src/allocator.rs lines 647-648
self.ghost_atoms += 1;
self.ghost_heap += len;           // ← heap bytes charged
```

`new_substr`, SmallAtom → SmallAtom branch:

```rust
// src/allocator.rs lines 852-854
if let Some(new_val) = fits_in_small_atom(substr) {
    self.ghost_atoms += 1;
    // ghost_heap NOT incremented  ← missing charge
    Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
}
```

Additionally, the `new_substr` function performs no heap-limit pre-check at all before entering the SmallAtom branch (compare `new_atom` line 621 and `new_small_number` line 643, both of which gate on `u8_vec.len() + ghost_heap + len > heap_limit`).

The heap-limit guard used by every subsequent allocation is:

```rust
// src/allocator.rs line 621 (new_atom), line 643 (new_small_number)
if start + self.ghost_heap + new_size > self.heap_limit {
    return Err(EvalErr::OutOfMemory);
}
```

Because `ghost_heap` is deflated by each `substr`-on-SmallAtom call, this guard operates on a smaller baseline than it should, allowing more real heap to be allocated than `heap_limit` permits.

`heap_size()` is the public API that reports total virtual heap:

```rust
// src/allocator.rs lines 1256-1258
pub fn heap_size(&self) -> usize {
    self.u8_vec.len() + self.ghost_heap
}
```

This value is used by callers (including the garbage-collection fuzzer) to assert resource equivalence across execution paths. An underreported `ghost_heap` makes `heap_size()` return a value smaller than the true virtual heap consumed.

---

### Impact Explanation

**Heap-limit bypass:** An attacker-controlled CLVM program that calls `substr` on SmallAtom values N times causes `ghost_heap` to be undercharged by up to `N × 4` bytes (maximum SmallAtom byte length). Subsequent `new_atom` / `new_small_number` / `new_concat` calls then succeed against a deflated baseline, allowing total virtual heap consumption to exceed `heap_limit`.

**Consensus divergence:** `heap_size()` is used to assert that two execution paths (e.g., with and without `ENABLE_GC`) produce identical resource usage. A program that exercises the SmallAtom substr path will produce a `heap_size()` that is smaller than the true virtual heap, breaking the equivalence assertion and potentially causing nodes with different heap-limit configurations to disagree on program validity.

**Corrupted result:** The exact corrupted value is `heap_size()` = `u8_vec.len() + ghost_heap`, where `ghost_heap` is underreported by `Σ (end_i - start_i)` for each SmallAtom substr call that produced a SmallAtom result.

---

### Likelihood Explanation

`op_substr` is a standard CLVM operator reachable from any attacker-supplied program bytes. Its cost is hardcoded to `1` per call:

```rust
// src/more_ops.rs lines 881-883
let r = a.new_substr(a0, start as u32, end as u32)?;
let cost: Cost = 1;
Ok(Reduction(cost, r))
```

A program can call `substr` on a SmallAtom (e.g., `(q . 0x01)`) in a tight loop at negligible cost, accumulating a large `ghost_heap` deficit before any real allocation is attempted. No special privileges, flags, or dialect configuration are required; the operator is available in the default `ChiaDialect`.

---

### Recommendation

In `new_substr`, add the missing `ghost_heap` charge and heap-limit pre-check for the SmallAtom → SmallAtom branch, mirroring the pattern in `new_atom` and `new_small_number`:

```rust
// src/allocator.rs, ObjectType::SmallAtom branch of new_substr
if let Some(new_val) = fits_in_small_atom(substr) {
    let substr_len = (end - start) as usize;
    // Add heap-limit pre-check (mirrors new_atom line 621)
    if self.u8_vec.len() + self.ghost_heap + substr_len > self.heap_limit {
        return Err(EvalErr::OutOfMemory);
    }
    self.ghost_atoms += 1;
    self.ghost_heap += substr_len;   // ← add this line
    Ok(self.mk_node(ObjectType::SmallAtom, new_val as usize))
}
```

---

### Proof of Concept

```rust
use clvmr::allocator::Allocator;

fn main() {
    // heap_limit = 100 bytes
    let mut a = Allocator::new_limited(100);

    // Allocate a 1-byte SmallAtom: ghost_heap += 1 → ghost_heap = 1
    let atom = a.new_atom(&[0x42]).unwrap();  // fits_in_small_atom → SmallAtom

    // Call new_substr 200 times on the SmallAtom.
    // Each call: ghost_atoms += 1, ghost_heap NOT incremented.
    // After 200 calls: ghost_heap still = 1 (should be 201).
    for _ in 0..200 {
        let _ = a.new_substr(atom, 0, 1).unwrap();
    }

    // heap_size() reports 1 (u8_vec=0, ghost_heap=1).
    // True virtual heap should be 201.
    println!("heap_size = {}", a.heap_size()); // prints 1, not 201

    // Now allocate 98 more bytes — should be rejected (1 + 201 + 98 > 100),
    // but succeeds because ghost_heap is deflated to 1.
    let _big = a.new_atom(&[0u8; 98]).unwrap(); // should OOM, but succeeds
    println!("Heap limit bypassed: heap_size = {}", a.heap_size());
}
```

The attacker-controlled entry path is `op_substr` → `new_substr` → SmallAtom branch, triggered by any CLVM program bytes containing a `substr` call on a SmallAtom argument. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** src/allocator.rs (L619-629)
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

**File:** src/allocator.rs (L845-854)
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
```

**File:** src/allocator.rs (L1256-1258)
```rust
    pub fn heap_size(&self) -> usize {
        self.u8_vec.len() + self.ghost_heap
    }
```

**File:** src/more_ops.rs (L854-884)
```rust
pub fn op_substr(
    a: &mut Allocator,
    input: NodePtr,
    _max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let ([a0, start, end], argc) = get_varargs::<3>(a, input, "substr")?;
    if !(2..=3).contains(&argc) {
        Err(EvalErr::InvalidOpArg(
            input,
            format!("Substring takes exactly 2 or 3 arguments, got {argc}"),
        ))?;
    }
    let size = atom_len(a, a0, "substr")?;
    let start = i32_atom(a, start, "substr")?;

    let end = if argc == 3 {
        i32_atom(a, end, "substr")?
    } else {
        size as i32
    };
    if end < 0 || start < 0 || end as usize > size || end < start {
        Err(EvalErr::InvalidOpArg(
            input,
            "Invalid Indices for Substring".to_string(),
        ))?
    } else {
        let r = a.new_substr(a0, start as u32, end as u32)?;
        let cost: Cost = 1;
        Ok(Reduction(cost, r))
    }
```
