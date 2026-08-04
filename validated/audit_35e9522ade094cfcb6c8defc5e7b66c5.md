[1](#0-0) [1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L21-32)
```text
/// 3. To create a new transaction, an owner can call create_transaction with the transaction payload. This will store
/// the full transaction payload on chain, which adds decentralization (censorship is not possible as the data is
/// available on chain) and makes it easier to fetch all transactions waiting for execution. If saving gas is desired,
/// an owner can alternatively call create_transaction_with_hash where only the payload hash is stored. Later execution
/// will be verified using the hash. Only owners can create transactions and a transaction id (incremeting id) will be
/// assigned.
/// 4. To approve or reject a transaction, other owners can call approve() or reject() with the transaction id.
/// 5. If there are enough approvals, any owner can execute the transaction using the special MultisigTransaction type
/// with the transaction id if the full payload is already stored on chain or with the transaction payload if only a
/// hash is stored. Transaction execution will first check with this module that the transaction payload has gotten
/// enough signatures. If so, it will be executed as the multisig account. The owner who executes will pay for gas.
/// 6. If there are enough rejections, any owner can finalize the rejection by calling execute_rejected_transaction().
```

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L95-96)
```text
    /// The sequence number provided is invalid. It must be between [1, next pending transaction - 1].
    const EINVALID_SEQUENCE_NUMBER: u64 = 17;
```
