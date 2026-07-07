### Title
Linked Signer Can Overwrite Itself via `LinkSigner` Transaction — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl()`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`. This permits the **current linked signer** — not just the subaccount owner — to sign and submit a `LinkSigner` transaction that overwrites the linked signer mapping for any subaccount it controls. A compromised or malicious linked signer can silently redirect the linked signer slot to an attacker-controlled address, granting persistent delegated access to the subaccount.

---

### Finding Description

In the sequencer-path handler `processTransactionImpl`, the `LinkSigner` transaction is processed as follows:

```solidity
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
        true          // <-- allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

The fifth argument `true` is the `allowLinkedSigner` flag. `validateSignedTx` internally calls `validateCompactSignature`, which passes the current linked signer address to the verifier when this flag is set:

```solidity
function validateCompactSignature(
    bytes32 sender,
    bytes32 digest,
    IEndpoint.CompactSignature memory signature,
    bool allowLinkedSigner
) internal virtual {
    verifier.validateCompactSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

The verifier then accepts the signature if it was produced by **either** the subaccount owner **or** the linked signer

### Citations

**File:** core/contracts/EndpointTx.sol (L186-198)
```text
    function validateCompactSignature(
        bytes32 sender,
        bytes32 digest,
        IEndpoint.CompactSignature memory signature,
        bool allowLinkedSigner
    ) internal virtual {
        verifier.validateCompactSignature(
            sender,
            allowLinkedSigner ? getLinkedSigner(sender) : address(0),
            digest,
            signature
        );
    }
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
