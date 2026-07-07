### Title
Linked Signer Can Unilaterally Replace Itself Without Subaccount Owner Consent - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` fast-path transaction in `EndpointTx.sol` accepts a signature from the **current linked signer** (`allowLinkedSigner = true`) to overwrite `linkedSigners[subaccount]` with an arbitrary new address. The subaccount owner never consents to this replacement. A compromised or malicious linked signer can silently hand off full signing authority over a victim's subaccount to an attacker-controlled address.

---

### Finding Description

In `EndpointTx.sol`, the fast-path handler for `LinkSigner` calls `validateSignedTx` with `allowLinkedSigner = true`:

```solidity
// EndpointTx.sol L576-590
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
        true          // ← allowLinkedSigner
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
```

`validateSignedTx` delegates to `validateCompactSignature`, which in turn calls `Verifier.validateCompactSignature`. That function accepts either the subaccount owner **or** the linked signer as a valid signer:

```solidity
// Verifier.sol L306-318
function validateCompactSignature(...) public pure {
    address recovered = ECDSA.recover(digest, signature.r, signature.vs);
    require(
        (recovered != address(0)) &&
            ((recovered == address(uint160(bytes20(sender)))) ||
                (recovered == linkedSigner)),   // ← linked signer accepted
        ERR_INVALID_SIGNATURE
    );
}
```

Because `LinkSigner` itself passes `allowLinkedSigner = true`, the current linked signer can produce a valid signature for a `LinkSigner` transaction that names **any** new address as the replacement signer. The state write `linkedSigners[signedTx.tx.sender] = ...` then executes unconditionally, with no check that the subaccount owner authorized the change.

By contrast, the slow-mode path for `LinkSigner` (lines 232–239) uses `validateSender`, which enforces that `msg.sender` is the subaccount owner — the correct consent model. The fast path lacks this constraint.

---

### Impact Explanation

`linkedSigners[subaccount]` is the authorization root for all fast-path operations. Once an attacker controls this slot they can sign:

- `WithdrawCollateral` / `WithdrawCollateralV2` — drain collateral to an arbitrary `sendTo` address
- `TransferQuote` — move quote balance to any subaccount
- `BurnNlp` — redeem NLP tokens held by the victim
- Further `LinkSigner` transactions — perpetuate control indefinitely

The corrupted state is `linkedSigners[victim_subaccount]`. The asset delta is the full collateral balance of the victim subaccount.

---

### Likelihood Explanation

Any address that has ever been granted linked-signer status for a subaccount can trigger this. The attack requires no privileged access beyond the linked signer key itself. A linked signer key that is leaked, phished, or operated by a malicious API service is sufficient. The sequencer submits the transaction without any on-chain check that the subaccount owner consented.

---

### Recommendation

`LinkSigner` is a privileged, owner-only operation — it changes who can act on behalf of a subaccount. The fast-path handler should pass `allowLinkedSigner = false` so that only the subaccount owner's key can authorize a signer change:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
```

This mirrors the slow-mode path, which already enforces `validateSender(txn.sender, sender)` (owner-only) before writing to `linkedSigners`.

---

### Proof of Concept

1. Alice owns subaccount `A` and has linked Bob (`address B`) as her signer via a prior `LinkSigner` transaction.
2. Bob (or an attacker who obtained Bob's key) constructs a `SignedLinkSigner` struct:
   - `tx.sender = A`
   - `tx.signer = bytes32(uint256(uint160(Mallory)))` (attacker address)
   - `tx.nonce = current_nonce`
3. Bob signs the EIP-712 digest for this transaction.
4. The sequencer submits it to `Endpoint` → `EndpointTx.processTransactionImpl`.
5. `validateSignedTx(..., true)` passes because Bob is the current `linkedSigners[A]`.
6. `linkedSigners[A]` is overwritten with Mallory's address.
7. Mallory now signs a `WithdrawCollateralV2` transaction for subaccount `A`, draining Alice's collateral to an attacker-controlled wallet.

Alice never signed or consented to step 2–6. The broken invariant — that only the subaccount owner may designate a linked signer — is violated entirely within the production fast-path code at `EndpointTx.sol` lines 576–590. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/EndpointTx.sol (L108-128)
```text
    function validateSignedTx(
        bytes32 sender,
        uint64 nonce,
        bytes calldata transaction,
        IEndpoint.CompactSignature memory signature,
        bool allowLinkedSigner
    ) internal {
        validateNonce(sender, nonce);
        validateCompactSignature(
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

**File:** core/contracts/Verifier.sol (L306-318)
```text
    function validateCompactSignature(
        bytes32 sender,
        address linkedSigner,
        bytes32 digest,
        IEndpoint.CompactSignature memory signature
    ) public pure {
        address recovered = ECDSA.recover(digest, signature.r, signature.vs);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
```
