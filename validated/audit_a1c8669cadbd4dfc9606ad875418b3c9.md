### Title
Signers Cannot Cancel Pending Signed Transactions Due to Missing Nonce Increment Function - (File: `core/contracts/EndpointTx.sol`)

---

### Summary
The Nado protocol uses a sequential nonce system to authorize signed transactions (withdrawals, link-signer, transfer-quote, mint/burn NLP, liquidations). However, there is no external function allowing a user to increment their own nonce. Once a user has signed and submitted a transaction to the sequencer, they have no on-chain mechanism to invalidate that signature before it is executed.

---

### Finding Description
`EndpointTx.validateNonce` enforces a strictly sequential nonce per subaccount address:

```solidity
// EndpointTx.sol line 72-77
function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
    require(
        nonce == nonces[address(uint160(bytes20(sender)))]++,
        ERR_WRONG_NONCE
    );
}
```

This `internal` function is the only place where `nonces[...]` is ever incremented. It is called exclusively from `validateSignedTx` during sequencer-submitted transaction processing. There is no `external` or `public` function exposed to users that allows them to advance their own nonce.

The following signed transaction types are all protected by this nonce and are therefore affected:
- `WithdrawCollateral` / `WithdrawCollateralV2`
- `LinkSigner`
- `TransferQuote`
- `MintNlp` / `BurnNlp`
- `LiquidateSubaccount`

Once a user signs any of these transactions and hands the signature to the sequencer, the sequencer holds a valid, replayable authorization. The user cannot invalidate it. [1](#0-0) [2](#0-1) 

---

### Impact Explanation
The most severe scenario is `LinkSigner`: a user signs a `LinkSigner` transaction to delegate signing authority to an address. If the user later discovers the linked signer is compromised, or simply wants to revoke the delegation before the sequencer processes it, they cannot. The sequencer can submit the old `LinkSigner` transaction at any time, granting the attacker-controlled address full signing authority over the subaccount.

For `WithdrawCollateral`: a user signs a withdrawal, market conditions change (e.g., they are near liquidation), and they want to cancel the withdrawal to preserve collateral. They cannot invalidate the signed withdrawal. [3](#0-2) [4](#0-3) 

---

### Likelihood Explanation
Medium. Every user who interacts with the protocol via signed transactions is affected. The sequencer is a trusted party in normal operation, but the inability to cancel is a structural limitation that applies regardless of sequencer behavior. A user who changes their mind after signing — or whose linked signer key is compromised between signing and sequencer submission — has no recourse. The slow-mode path (`processSlowModeTransactionImpl`) also processes `LinkSigner` without a nonce, meaning a user cannot use slow mode to "race" a cancellation. [5](#0-4) 

---

### Recommendation
Add an external `increaseNonce()` function that allows any caller to increment their own nonce, invalidating all previously signed transactions with the old nonce value:

```solidity
function increaseNonce(uint8 count) external {
    address sender = msg.sender;
    nonces[sender] += count;
}
```

This is the same pattern recommended in the external report and implemented by the referenced fix. It should be placed in `Endpoint.sol` or `EndpointTx.sol` as an `external` function so users can call it directly on-chain. [1](#0-0) 

---

### Proof of Concept
1. Alice signs a `LinkSigner` transaction linking address `0xAttacker` as her subaccount signer, with `nonce = 5`.
2. Alice submits this to the sequencer but then realizes `0xAttacker` is malicious and wants to cancel.
3. Alice has no on-chain function to call to advance her nonce from `5` to `6`.
4. The sequencer (or anyone who obtained the signed bytes) can submit the `LinkSigner` transaction at any time.
5. `validateNonce` passes because `nonces[Alice] == 5`, the nonce increments to `6`, and `linkedSigners[Alice.subaccount]` is set to `0xAttacker`.
6. `0xAttacker` now has full signing authority over Alice's subaccount and can sign withdrawals, transfers, and liquidations on her behalf. [3](#0-2) [6](#0-5)

### Citations

**File:** core/contracts/EndpointTx.sol (L72-77)
```text
    function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L86-106)
```text
    function validateSignedTx(
        bytes32 sender,
        uint64 nonce,
        bytes calldata transaction,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal {
        validateNonce(sender, nonce);
        validateSignature(
            sender,
            _hashTypedDataV4(
                computeDigest(
                    IEndpoint.TransactionType(uint8(transaction[0])),
                    transaction[1:]
                )
            ),
            signature,
            allowLinkedSigner
        );
        requireSubaccount(sender);
    }
```

**File:** core/contracts/EndpointTx.sol (L232-239)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```

**File:** core/contracts/EndpointTx.sol (L413-436)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L576-590)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLinkSigner)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```

**File:** core/contracts/Verifier.sol (L291-303)
```text
    function validateSignature(
        bytes32 sender,
        address linkedSigner,
        bytes32 digest,
        bytes memory signature
    ) public pure {
        address recovered = ECDSA.recover(digest, signature);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
```
