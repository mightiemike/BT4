### Title
Linked Signer Can Overwrite Its Own Entry to Hijack Subaccount Signing Authority — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`, meaning the current linked signer can sign a `LinkSigner` transaction to replace itself with any arbitrary address. This allows a compromised or malicious linked signer to permanently transfer signing authority over a victim subaccount to an attacker-controlled address, enabling full theft of all deposited assets. The slow-mode path for the same transaction type correctly restricts this operation to the subaccount owner only, exposing an inconsistency that constitutes the root cause.

---

### Finding Description

`linkedSigners` is a critical state variable mapping subaccounts to their authorized delegate signers. It is used throughout the protocol to authorize high-value operations including `WithdrawCollateral`, `TransferQuote`, `MintNlp`, `BurnNlp`, and order matching.

In `processTransactionImpl`, the `LinkSigner` case passes `allowLinkedSigner = true` to `validateSignedTx`: [1](#0-0) 

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
        transaction[1:], (IEndpoint.SignedLinkSigner)
    );
    validateSignedTx(
        signedTx.tx.sender,
        signedTx.tx.nonce,
        transaction,
        signedTx.signature,
        true              // <-- allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
```

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which passes the current linked signer as an accepted signer to the verifier: [2](#0-1) 

```solidity
function validateSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
```

This means the verifier accepts a valid signature from **either** the subaccount owner **or** the current linked signer. Because the `LinkSigner` transaction then unconditionally overwrites `linkedSigners[signedTx.tx.sender]`, the current linked signer can sign a transaction that replaces itself with any attacker-controlled address.

The slow-mode path for the same `LinkSigner` transaction type does **not** have this flaw — it uses `validateSender`, which enforces that only the subaccount owner (`address(uint160(bytes20(txn.sender))) == sender`) can change the linked signer: [3](#0-2) 

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.LinkSigner memory txn = abi.decode(...);
    validateSender(txn.sender, sender);   // owner-only check
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```

The inconsistency between the two paths is the root cause: the sequencer path omits the owner-only restriction for the one transaction type that mutates signing authority itself.

---

### Impact Explanation

Once a linked signer is replaced with an attacker-controlled address, the attacker becomes the authorized delegate for the victim subaccount. The attacker can immediately sign and submit `WithdrawCollateral` or `WithdrawCollateralV2` transactions through the sequencer to drain all collateral. All other linked-signer-permitted operations (`TransferQuote`, `MintNlp`, `BurnNlp`, order placement) are also available to the attacker.

The `linkedSigners` mapping is the direct analog to the Nocturne `spendKey`: it is the critical credential that authorizes all protocol operations on behalf of a subaccount. Unrestricted mutation of this variable by the current linked signer is equivalent to any origin being able to overwrite the `spendKey`.

**Severity: High** — direct, complete theft of all deposited assets for any subaccount whose linked signer is compromised or malicious.

---

### Likelihood Explanation

Linked signers are the standard mechanism for automated trading bots, API integrations, and third-party front-ends in perpetuals DEX protocols. Users are explicitly encouraged to set linked signers for normal operation. A compromised API key, a malicious third-party trading interface, or a front-end that sets itself as the linked signer during onboarding can all trigger this path. No privileged access, governance capture, or cryptographic break is required — only possession of the current linked signer's private key.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`, consistent with the slow-mode path:

```diff
- validateSignedTx(
-     signedTx.tx.sender,
-     signedTx.tx.nonce,
-     transaction,
-     signedTx.signature,
-     true
- );
+ validateSignedTx(
+     signedTx.tx.sender,
+     signedTx.tx.nonce,
+     transaction,
+     signedTx.signature,
+     false   // only the subaccount owner may change the linked signer
+ );
```

This ensures that changing the linked signer is an owner-only operation in both the sequencer and slow-mode paths, eliminating the privilege escalation vector.

---

### Proof of Concept

1. Alice sets `tradingBot` (address `0xBOT`) as the linked signer for `alice_subaccount` via a normal `LinkSigner` transaction.
2. Attacker obtains the `0xBOT` private key (e.g., leaked API key, compromised trading bot).
3. Attacker crafts a `SignedLinkSigner` struct: `sender = alice_subaccount`, `signer = attacker_address`, `nonce = current_nonce`, signed by `0xBOT`.
4. Attacker submits this transaction to the sequencer.
5. Sequencer calls `submitTransactionsChecked` → `processTransaction` → `processTransactionImpl`.
6. `validateSignedTx(..., true)` accepts the `0xBOT` signature because `getLinkedSigner(alice_subaccount) == 0xBOT`.
7. `linkedSigners[alice_subaccount]` is overwritten with `attacker_address`.
8. Attacker signs `WithdrawCollateral` transactions using `attacker_address` as the linked signer.
9. All of Alice's deposited collateral is transferred to the attacker.

The attacker-controlled entry path is fully reachable by any unprivileged party who holds a linked signer key, with no additional preconditions. The corrupted state is `linkedSigners[alice_subaccount]`, and the resulting asset delta is complete loss of all collateral in the subaccount. [1](#0-0) [4](#0-3) [2](#0-1) [3](#0-2)

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

**File:** core/contracts/EndpointStorage.sol (L50-51)
```text
    mapping(bytes32 => address) internal linkedSigners;

```
