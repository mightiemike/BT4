[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/aptos-dkg/src/sigma_protocol/homomorphism/fixed_base_msms.rs (L103-119)
```rust
    fn normalize_output(projective_output: Self::Codomain) -> Self::CodomainNormalized
    where
        Self::Codomain:
            EntrywiseMap<Self::MsmOutput, Output<Self::Base> = Self::CodomainNormalized>,
    {
        // 1. Collect all elements into a Vec
        let msm_vec: Vec<Self::MsmOutput> = projective_output.clone().into_iter().collect();
        // TODO: want projective_output.iter().cloned().collect();

        // 2. Apply batch_normalize
        let normalized_vec: Vec<Self::Base> = Self::batch_normalize(msm_vec);

        // 3. Replace elements in projective_output with normalized values
        let mut iter = normalized_vec.into_iter();

        projective_output.map(|_t| iter.next().expect("Not enough elements, somehow"))
    }
```

**File:** crates/aptos-dkg/src/pvss/chunky/chunked_scalar_mul.rs (L99-120)
```rust
    fn try_map<U, E, F>(self, f: F) -> Result<Self::Output<U>, E>
    where
        F: FnMut(T) -> Result<U, E>,
        U: CanonicalSerialize + CanonicalDeserialize + Clone + Debug + Eq,
    {
        Ok(CodomainShape(
            self.0.into_iter().map(f).collect::<Result<Vec<_>, _>>()?,
        ))
    }
}

impl<T> IntoIterator for CodomainShape<T>
where
    T: CanonicalSerialize + CanonicalDeserialize + Clone,
{
    type IntoIter = std::vec::IntoIter<T>;
    type Item = T;

    fn into_iter(self) -> Self::IntoIter {
        self.0.into_iter()
    }
}
```

**File:** crates/aptos-dkg/src/sigma_protocol/homomorphism/tuple.rs (L184-215)
```rust
impl<T, A, B> IntoIterator for TupleCodomainShape<A, B>
where
    A: IntoIterator<Item = T>,
    B: IntoIterator<Item = T>,
{
    type IntoIter = std::iter::Chain<A::IntoIter, B::IntoIter>;
    type Item = T;

    fn into_iter(self) -> Self::IntoIter {
        self.0.into_iter().chain(self.1.into_iter())
    }
}

impl<T, A, B> EntrywiseMap<T> for TupleCodomainShape<A, B>
where
    A: EntrywiseMap<T>,
    B: EntrywiseMap<T>,
{
    type Output<U: CanonicalSerialize + CanonicalDeserialize + Clone + Debug + Eq> =
        TupleCodomainShape<A::Output<U>, B::Output<U>>;

    fn try_map<U, E, F>(self, mut f: F) -> Result<Self::Output<U>, E>
    where
        F: FnMut(T) -> Result<U, E>,
        U: CanonicalSerialize + CanonicalDeserialize + Clone + Debug + Eq,
    {
        Ok(TupleCodomainShape(
            self.0.try_map(&mut f)?,
            self.1.try_map(f)?,
        ))
    }
}
```
