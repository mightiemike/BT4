### Title
Linked Signer Can Overwrite Itself via `LinkSigner` Transaction, Permanently Hijacking Subaccount Signing Authority - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner: true`. This means the current linked signer — a delegated, limited-privilege signer — can sign a `LinkSigner` transaction to replace itself with any arbitrary address. The subaccount owner never consents to this change. The new address then inherits full signing authority over the subaccount, including the ability to sign `WithdrawCollateral` transactions.

---

### Finding Description

The `LinkSigner` transaction type is intended to allow a subaccount owner to delegate signing authority to a secondary address (the "linked signer"). This is used for automated trading bots, third-party services, or off-chain order signers.

In `processTransactionImpl`, the `LinkSigner` case is handled as follows:

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
        true   // <-- allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
``` [1](#0-0) 

The final `true` argument to `validateSignedTx` is `allowLinkedSigner`. When `true`, `validateSignature` passes the current linked signer address to `verifier.validateSignature` as an accepted signer:

```solidity
function validateSignature(
    bytes32 sender,
    bytes32 digest,
    bytes memory signature,
    bool allowLinkedSigner
) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

`Verifier.validateSignature` then accepts the signature if it was produced by either the subaccount owner address or the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

Because `allowLinkedSigner: true` is passed for `LinkSigner`, the current linked signer can sign a `LinkSigner` transaction that writes any new address into `linkedSigners[subaccount]`. The subaccount owner's consent is never required. [4](#0-3) 

---

### Impact Explanation

A malicious or compromised linked signer can:

1. Craft a `LinkSigner` transaction pointing `signer` to an attacker-controlled address.
2. Sign it with the current linked signer key and submit it to the sequencer.
3. `processTransactionImpl` accepts the signature (because `allowLinkedSigner: true`) and writes `linkedSigners[subaccount] = attackerAddress`.
4. The attacker address now has full signing authority over the subaccount.
5. The attacker can immediately sign `WithdrawCollateral` or `WithdrawCollateralV2` (with `sendTo == address(0)`) transactions to drain all collateral from the subaccount.

The subaccount owner can recover via the slow-mode `LinkSigner` path (which only requires `msg.sender == address(bytes20(subaccount))`), but the slow-mode queue has a `SLOW_MODE_TX_DELAY` of three days. During that window, the attacker has unrestricted signing authority and can drain all collateral. [5](#0-4) 

The broken invariant: `linkedSigners[subaccount]` should only be writable by the subaccount owner (the address encoded in the high 20 bytes of the `bytes32` subaccount identifier). The linked signer is a delegated key, not the owner, and must not be able to modify the delegation itself.

---

### Likelihood Explanation

Linked signers are routinely granted to automated trading bots, third-party portfolio managers, or off-chain order-routing services. Any of these can be:

- Compromised via a private key leak.
- Operated by a malicious third-party service that a user trusted.
- Exploited by a front-end or SDK that constructs and submits a `LinkSigner` transaction on behalf of the linked signer key.

No special privileges, governance access, or sequencer compromise are required. The attacker only needs the linked signer's private key, which is the exact key already used for routine trading operations. The attack is a single sequencer-submitted transaction with no on-chain preconditions beyond having been previously linked.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
``` [6](#0-5) 

This mirrors the design of `WithdrawCollateralV2`, which already restricts linked signer authority when a non-zero `sendTo` address is specified — demonstrating that the codebase already recognises the principle that certain sensitive operations must be owner-only. [7](#0-6) 

---

### Proof of Concept

1. Owner `0xAlice` has subaccount `bytes32(abi.encodePacked(address(0xAlice), bytes12("default")))` and has previously called `LinkSigner` to set linked signer to `0xBot` (an automated trading service).
2. `0xBot` (or an attacker who has compromised `0xBot`'s key) constructs:
   ```
   LinkSigner {
       sender: aliceSubaccount,
       signer: bytes32(uint256(uint160(0xAttacker))),
       nonce: currentNonce
   }
   ```
3. `0xBot` signs the EIP-712 digest of this struct and submits it to the sequencer.
4. The sequencer includes it in a `submitTransactionsChecked` batch.
5. `processTransactionImpl` reaches the `LinkSigner` branch, calls `validateSignedTx(..., true)`, which calls `verifier.validateSignature(aliceSubaccount, 0xBot, digest, sig)`. The signature from `0xBot` is accepted.
6. `linkedSigners[aliceSubaccount]` is overwritten with `0xAttacker`.
7. `0xAttacker` immediately signs a `WithdrawCollateral` transaction for the full collateral balance of `aliceSubaccount`. The sequencer accepts it; `validateSignedTx(..., true)` accepts `0xAttacker` as the new linked signer.
8. Alice's collateral is drained. Alice can submit a slow-mode `LinkSigner` to recover control, but must wait three days, during which the attacker can continue to act. [8](#0-7)

### Citations

**File:** core/contracts/EndpointTx.sol (L172-184)
```text
    function validateSignature(
        bytes32 sender,
        bytes32 digest,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal virtual {
        verifier.validateSignature(
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

**File:** core/contracts/EndpointTx.sol (L442-448)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
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

**File:** core/contracts/Verifier.sol (L297-303)
```text
        address recovered = ECDSA.recover(digest, signature);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```

**File:** core/contracts/Endpoint.sol (L152-153)
```text
        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
```
