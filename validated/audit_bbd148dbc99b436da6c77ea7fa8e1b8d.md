[1](#0-0)

### Citations

**File:** types/src/proof/definition.rs (L588-599)
```rust
impl<H> AccumulatorRangeProof<H>
where
    H: CryptoHasher,
{
    /// Constructs a new `AccumulatorRangeProof` using `left_siblings` and `right_siblings`.
    pub fn new(left_siblings: Vec<HashValue>, right_siblings: Vec<HashValue>) -> Self {
        Self {
            left_siblings,
            right_siblings,
            phantom: PhantomData,
        }
    }
```
