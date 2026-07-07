### Title
Linked Signer Can Unilaterally Replace Itself, Permanently Hijacking Subaccount Signing Authority — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction type in `EndpointTx.processTransactionImpl` is validated with `allowLinkedSigner = true`, meaning the current linked signer — not just the subaccount owner — can sign a `LinkSigner` transaction to replace itself with any arbitrary address. This allows a compromised or malicious linked signer to permanently transfer signing authority over a subaccount to an attacker-controlled address without the subaccount owner's knowledge or consent.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` case is handled as follows:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // allowLinkedSigner = true
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
``` [1](#0-0) 

The `allowLinkedSigner = true` flag is passed to `validateSignedTx`, which in turn calls `validateSignature` with `getLinkedSigner(sender)` as the permitted signer:

```solidity
function validateSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

`Verifier.validateSignature` then accepts a signature from either the subaccount owner address or the linked signer address:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

This means the current linked signer can sign a `LinkSigner` transaction to overwrite `linkedSigners[subaccount]` with any address it chooses.

**The slow-mode path for `LinkSigner` correctly restricts this to the subaccount owner** via `validateSender`, which checks `msg.sender` against the address embedded in the subaccount:

```solidity
validateSender(txn.sender, sender);   // sender == msg.sender
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

The asymmetry is the root cause: the slow-mode path enforces owner-only control over the linked signer, but the sequencer path allows the linked signer itself to change it.

---

### Impact Explanation

A compromised or malicious linked signer can:

1. Sign a `LinkSigner` transaction (using the owner's current nonce, observable via `getNonce`) to set a new attacker-controlled address as the linked signer.
2. The new linked signer can sign `BurnNlp` transactions, which charge a burn fee (`burnFee = max(ONE, quoteAmount / 1000)`) and reduce the subaccount's NLP holdings. [5](#0-4) 

3. The new linked signer can sign `LiquidateSubaccount` transactions, charging a `LIQUIDATION_FEE` ($1) from the subaccount per call. [6](#0-5) 

4. The new linked signer can sign yet another `LinkSigner` transaction to persist the takeover, racing against any owner revocation attempt. Since both the owner and the attacker's linked signer use the same per-address nonce, only one can succeed — and the sequencer controls ordering. [7](#0-6) 

The net result is permanent loss of signing authority over the subaccount and ongoing fee-based drainage of its balances.

---

### Likelihood Explanation

Linked signers are explicitly designed for high-frequency trading and delegated execution (session keys). Private key compromise of a session key is a realistic threat: frontend exploits, phishing, clipboard hijacking, or key leakage from a trading bot are all common attack vectors. Once a linked signer key is compromised, the attacker can execute this takeover in a single sequencer batch before the owner detects the breach.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` case in `processTransactionImpl`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
```

This aligns the sequencer path with the slow-mode path, which already correctly enforces owner-only control over linked signer assignment.

---

### Proof of Concept

1. Alice sets Bob as her linked signer via a `LinkSigner` transaction signed by Alice.
2. Bob's private key is compromised by an attacker.
3. The attacker reads Alice's current nonce: `endpoint.getNonce(alice_address)` → `N`.
4. The attacker constructs `LinkSigner{sender: alice_subaccount, signer: attacker_address, nonce: N}` and signs it with Bob's key.
5. The attacker submits this to the sequencer. The sequencer calls `processTransactionImpl` → `validateSignedTx(..., true)` → `verifier.validateSignature(alice_subaccount, bob_address, digest, sig)` → passes because `recovered == bob_address == linkedSigner`.
6. `linkedSigners[alice_subaccount]` is overwritten with `attacker_address`. Alice's nonce is incremented to `N+1`.
7. Alice notices and signs a revocation `LinkSigner{sender: alice_subaccount, signer: address(0), nonce: N+1}`. But the attacker also signs `LinkSigner{sender: alice_subaccount, signer: attacker_address2, nonce: N+1}` using `attacker_address` (now the current linked signer). Whichever the sequencer processes first wins.
8. If the attacker wins the race, they maintain persistent control and can repeatedly call `BurnNlp` to drain Alice's NLP balance through burn fees. [1](#0-0) [8](#0-7)

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

**File:** core/contracts/EndpointTx.sol (L408-410)
```text
                if (signedTx.tx.productId != type(uint32).max) {
                    chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
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

**File:** core/contracts/Clearinghouse.sol (L503-504)
```text
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
```
