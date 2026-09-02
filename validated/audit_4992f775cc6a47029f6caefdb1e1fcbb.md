[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/sequencer/src/mempool.rs (L102-107)
```rust
        if transaction.transaction().signer() == SYSTEM_SIGNER {
            return Err(PoolError::other(
                *transaction.hash(),
                "system transactions from rpc are not allowed",
            ));
        }
```

**File:** crates/evm/src/evm/conversions.rs (L105-115)
```rust
impl TryFrom<RlpEvmTransaction> for Recovered<TransactionSigned> {
    type Error = ConversionError;

    fn try_from(evm_tx: RlpEvmTransaction) -> Result<Self, Self::Error> {
        let tx = TransactionSigned::try_from(evm_tx)?;
        if tx.signature() == &SYSTEM_SIGNATURE {
            return Ok(Self::new_unchecked(tx, SYSTEM_SIGNER));
        }
        tx.try_into_recovered()
            .map_err(|_| ConversionError::InvalidSignature)
    }
```

**File:** crates/sequencer/src/tx_validator.rs (L46-68)
```rust
impl TransactionValidator for CitreaTransactionValidator {
    type Transaction = EthPooledTransaction;

    async fn validate_transaction(
        &self,
        origin: TransactionOrigin,
        transaction: Self::Transaction,
    ) -> TransactionValidationOutcome<Self::Transaction> {
        // Stock validation first. This is the only `.await`; everything below is synchronous
        // CPU work, so no non-`Send` revm value ever crosses an await point.
        let outcome = self.inner.validate_transaction(origin, transaction).await;

        // Only transactions reth deems valid proceed to the L1-fee reservation. Invalid/Error
        // outcomes pass straight through.
        let TransactionValidationOutcome::Valid {
            balance,
            state_nonce,
            transaction,
            propagate,
        } = outcome
        else {
            return outcome;
        };
```

**File:** crates/evm/src/evm/system_events.rs (L87-112)
```rust
/// Creates a single signed system transaction from a system event with the given nonce.
pub fn signed_system_transaction(
    event: SystemEvent,
    nonce: u64,
    chain_id: u64,
) -> Recovered<TransactionSigned> {
    let transaction = system_event_to_transaction(event, nonce, chain_id);
    let signed_no_hash = TransactionSigned::new_unhashed(transaction, SYSTEM_SIGNATURE);
    Recovered::new_unchecked(signed_no_hash, SYSTEM_SIGNER)
}

/// Creates a list of system transactions from a list of system events.
pub fn create_system_transactions<I: IntoIterator<Item = SystemEvent>>(
    events: I,
    mut nonce: u64,
    chain_id: u64,
) -> Vec<Recovered<TransactionSigned>> {
    events
        .into_iter()
        .map(|event| {
            let tx = signed_system_transaction(event, nonce, chain_id);
            nonce += 1;
            tx
        })
        .collect()
}
```
