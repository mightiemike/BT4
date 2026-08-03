[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/aptos-crypto/src/bls12381/bls12381_pop.rs (L143-160)
```rust
impl std::hash::Hash for ProofOfPossession {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        let encoded_signature = self.to_bytes();
        state.write(&encoded_signature);
    }
}

impl fmt::Debug for ProofOfPossession {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", hex::encode(self.to_bytes()))
    }
}

impl fmt::Display for ProofOfPossession {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", hex::encode(self.to_bytes()))
    }
}
```

**File:** types/src/ledger_info.rs (L305-310)
```rust
    pub fn verify_signatures(
        &self,
        validator: &ValidatorVerifier,
    ) -> ::std::result::Result<(), VerifyError> {
        validator.verify_multi_signatures(self.ledger_info(), &self.signatures)
    }
```

**File:** types/src/ledger_info.rs (L312-321)
```rust
    pub fn check_voting_power(
        &self,
        validator: &ValidatorVerifier,
    ) -> ::std::result::Result<u128, VerifyError> {
        validator.check_voting_power(
            self.get_voters(&validator.get_ordered_account_addresses_iter().collect_vec())
                .iter(),
            true,
        )
    }
```

**File:** types/src/ledger_info.rs (L366-375)
```rust
    pub fn aggregate_signatures(
        &self,
        verifier: &ValidatorVerifier,
    ) -> Result<LedgerInfoWithSignatures, VerifyError> {
        let aggregated_sig = verifier.aggregate_signatures(self.partial_sigs.signatures_iter())?;
        Ok(LedgerInfoWithSignatures::new(
            self.ledger_info.clone(),
            aggregated_sig,
        ))
    }
```
