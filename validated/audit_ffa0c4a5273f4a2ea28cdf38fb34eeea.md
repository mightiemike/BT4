### Title
Linked Signer Can Authorize Its Own Replacement, Enabling Permanent Subaccount Hijack - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` fast-mode path calls `validateSignedTx` with `allowLinkedSigner = true`. This means a currently-set linked signer (session key) is permitted to sign a `LinkSigner` transaction that replaces itself with an attacker-controlled address — permanently seizing signing authority over the subaccount without the main key ever being involved.

---

### Finding Description

The `processTransactionImpl` function handles the `LinkSigner` transaction type as follows:

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
        true   // <-- allowLinkedSigner
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

The `allowLinkedSigner = true` flag is passed to `validateSignedTx`, which in turn passes it to `validateSignature`:

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

`Verifier.validateSignature` then accepts the signature if it was produced by **either** the main account key **or** the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

The result: a linked signer can sign a `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` with any address the attacker controls. The main account key is never consulted.

This is structurally identical to the SSL analog: a security-critical configuration parameter (`allowLinkedSigner`) is set to the permissive value (`true`) where it should be restrictive (`false`), leaving a sensitive operation — delegation of signing authority — unprotected.

---

### Impact Explanation

Once the attacker's address is installed as the new linked signer, they can sign any transaction that accepts linked signers, including:

- `WithdrawCollateral` (fast mode, `allowLinkedSigner = true`) — drains collateral to any address
- `MintNlp` / `BurnNlp` — manipulates NLP pool positions
- `TransferQuote` — moves quote balances to attacker-controlled subaccounts
- A further `LinkSigner` — locks out the original owner permanently [4](#0-3) [5](#0-4) 

The corrupted state is `linkedSigners[subaccount]` in `EndpointStorage`, and the downstream asset delta is full collateral loss from the affected subaccount.

---

### Likelihood Explanation

Linked signers are a first-class protocol feature designed for browser session keys. Any user who has ever called `LinkSigner` is exposed. An attacker who obtains the session key (e.g., via XSS, key export, or a compromised frontend) can execute this attack in a single sequencer-submitted transaction with no further preconditions. The sequencer cannot distinguish a malicious `LinkSigner` from a legitimate one because the signature is valid.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type so that only the main account key can authorize a signer change:

```diff
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
-       true
+       false
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [6](#0-5) 

This mirrors the SSL fix exactly: a single boolean parameter that was omitted/set incorrectly must be set to its secure value to close the exposure.

---

### Proof of Concept

1. Alice sets a linked signer (session key `K_session`) via a legitimate `LinkSigner` transaction.
2. Attacker obtains `K_session` (e.g., via a compromised browser environment).
3. Attacker constructs a `SignedLinkSigner` with `sender = Alice's subaccount`, `signer = attacker_address`, valid `nonce`, signed by `K_session`.
4. Attacker submits this to the sequencer (or via slow mode after 3 days).
5. `processTransactionImpl` decodes the transaction, calls `validateSignedTx(..., true)`.
6. `validateSignature` recovers `K_session` from the signature; since `allowLinkedSigner = true`, `getLinkedSigner(Alice)` returns `K_session`, and the check passes.
7. `linkedSigners[Alice's subaccount]` is overwritten with `attacker_address`.
8. Attacker signs a `WithdrawCollateral` transaction using `attacker_address` as the linked signer, draining Alice's collateral. [7](#0-6) [2](#0-1)

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

**File:** core/contracts/EndpointTx.sol (L418-424)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
```

**File:** core/contracts/EndpointTx.sol (L539-545)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
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

**File:** core/contracts/Verifier.sol (L298-303)
```text
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
```
