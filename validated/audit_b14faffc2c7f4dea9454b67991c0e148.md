### Title
Linked Signer Can Unilaterally Hijack Subaccount Signing Authority via `LinkSigner` Self-Replacement - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, `LinkSigner` transactions are validated with `allowLinkedSigner = true`. This means the **current linked signer** is treated as an authorized signer for the `LinkSigner` transaction itself — the very transaction that changes who the linked signer is. A malicious linked signer can therefore unilaterally replace itself with any address it controls, permanently hijacking the subaccount's signing authority without the owner's knowledge or consent.

---

### Finding Description

When `processTransactionImpl` handles a `LinkSigner` transaction, it calls `validateSignedTx` with `allowLinkedSigner = true`:

```solidity
// EndpointTx.sol lines 577–590
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
        true                          // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
``` [1](#0-0) 

`validateSignedTx` resolves the authorized signer set dynamically at call time via `getLinkedSigner(sender)`:

```solidity
// EndpointTx.sol lines 172–184
function validateSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

`getLinkedSigner` reads the live storage value of `linkedSigners[subaccount]`: [3](#0-2) 

And `verifier.validateSignature` accepts the signature if it recovers to either the subaccount owner **or** the linked signer: [4](#0-3) 

**The broken invariant**: Only the subaccount owner (the address embedded in the `sender` bytes32) should be able to change the linked signer. The slow-mode path for `LinkSigner` correctly enforces this by calling `validateSender`, which checks `msg.sender == subaccount owner`:

```solidity
// EndpointTx.sol lines 232–239
validateSender(txn.sender, sender);
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [5](#0-4) 

The normal sequencer path has no equivalent restriction. The current linked signer — a value that is itself being mutated by the transaction — is used as the authorization check for that same mutation. This is the direct analog to H-1: the "correct" authorized party is computed dynamically from live state that the transaction is about to overwrite, so the check passes even though the invariant (owner-only control of linked signer) is violated.

---

### Impact Explanation

A malicious linked signer can:

1. Sign a `LinkSigner` transaction naming a new address (under their control) as the linked signer.
2. Submit it to the sequencer (a normal, unprivileged operation).
3. After inclusion, the new address becomes the linked signer.
4. The new linked signer can sign `WithdrawCollateral` transactions (`allowLinkedSigner = true` at line 418) to drain the subaccount. [6](#0-5) 

The subaccount owner retains the ability to sign directly and can eventually revoke the rogue signer, but the window between the hijack and the owner's response is sufficient to drain all collateral. The attack is also repeatable: the rogue linked signer can keep re-linking to a fresh address to stay ahead of any revocation.

---

### Likelihood Explanation

Linked signers are user-designated delegates — API keys, bots, or hot wallets. Any linked signer that becomes compromised (key leak, malicious service provider, insider) can execute this attack without any privileged access, governance capture, or sequencer compromise. The sequencer is required only to include the transaction, which it does for all valid user-submitted transactions. The attack requires no special conditions beyond holding the linked signer's private key.

---

### Recommendation

Change `allowLinkedSigner` to `false` for `LinkSigner` transactions in `processTransactionImpl`, so only the subaccount owner can authorize a linked-signer change — consistent with the slow-mode path:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
``` [7](#0-6) 

---

### Proof of Concept

1. Alice owns subaccount `0xAlice000...`. She sets Bob (`0xBob`) as her linked signer by signing a `LinkSigner` transaction.
2. Bob constructs a new `LinkSigner` transaction: `sender = 0xAlice000...`, `signer = 0xCharlie` (Bob's second address), `nonce = current_nonce`.
3. Bob signs this transaction with his own key (`0xBob`).
4. Bob submits it to the sequencer via the normal transaction path.
5. `processTransactionImpl` calls `validateSignedTx(..., true)`. `getLinkedSigner(0xAlice000...)` returns `0xBob`. The signature from `0xBob` is valid. The check passes.
6. `linkedSigners[0xAlice000...]` is now set to `0xCharlie`.
7. Charlie signs `WithdrawCollateral` transactions to drain Alice's subaccount. Alice's original linked signer Bob is now powerless, and Alice must notice and react before her funds are gone.

### Citations

**File:** core/contracts/EndpointTx.sol (L143-157)
```text
    function getLinkedSigner(bytes32 subaccount)
        public
        view
        virtual
        returns (address)
    {
        return
            RiskHelper.isIsolatedSubaccount(subaccount)
                ? linkedSigners[
                    IOffchainExchange(offchainExchange).getParentSubaccount(
                        subaccount
                    )
                ]
                : linkedSigners[subaccount];
    }
```

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

**File:** core/contracts/Verifier.sol (L291-304)
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
    }
```
