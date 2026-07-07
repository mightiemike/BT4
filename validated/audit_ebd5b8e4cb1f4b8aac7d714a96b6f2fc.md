### Title
Linked Signer Can Unauthorized Mutate `linkedSigners` Mapping via `LinkSigner` Transaction — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl()`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`. This means the currently-active linked signer for a subaccount can sign and submit a `LinkSigner` transaction through the sequencer to replace the linked signer with any arbitrary address — without the subaccount owner's consent. The slow-mode path for the same transaction type correctly restricts this action to the subaccount owner only, revealing a direct inconsistency in authorization enforcement.

---

### Finding Description

In `EndpointTx.processTransactionImpl()`, the `LinkSigner` fast-path handler is:

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
``` [1](#0-0) 

`validateSignedTx(..., allowLinkedSigner: true)` resolves the valid signer as either the subaccount owner **or** the currently-registered linked signer. [2](#0-1) 

This means the linked signer — an externally-controlled address granted only trading authority — can sign a `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` with any address it chooses.

By contrast, the slow-mode path for the same transaction type enforces owner-only authorization via `validateSender`, which requires `address(uint160(bytes20(txn.sender))) == msg.sender`:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.LinkSigner memory txn = abi.decode(...);
    validateSender(txn.sender, sender);   // owner must be msg.sender
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [3](#0-2) 

The slow-mode path correctly restricts `LinkSigner` to the subaccount owner. The fast-path does not. This is the root cause: the absence of an owner-only authorization check in the sequencer-submitted `LinkSigner` handler, directly analogous to the missing `_spendAllowance()` in the referenced Pool report.

---

### Impact Explanation

A malicious or compromised linked signer can:

1. Submit a `LinkSigner` transaction via the sequencer, replacing the current linked signer with an attacker-controlled address.
2. The new linked signer inherits full trading authority over the subaccount — it can submit `WithdrawCollateral` (V1, `allowLinkedSigner = true`) to drain the subaccount's collateral to the owner's address, or submit `TransferQuote` to move quote balances to attacker-controlled subaccounts.
3. The original subaccount owner may not detect the change until after damage is done.
4. Even if the owner revokes via slow mode (3-day delay), the attacker has a window to act.

The corrupted state is `linkedSigners[subaccount]` — a mapping that controls who has signing authority over the subaccount's trading operations. [4](#0-3) 

---

### Likelihood Explanation

**Medium.** The attack requires a linked signer that is either malicious at setup or becomes compromised (e.g., a hot-wallet key leak). Linked signers are explicitly designed for automated/bot trading, making key compromise a realistic operational risk. The sequencer path is the normal high-throughput path, so no special access is needed beyond having been set as a linked signer. The 3-day slow-mode delay for the owner to respond amplifies the window of exposure.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only subaccount owner may change linked signer
);
```

This aligns the fast-path with the slow-mode path, which already enforces owner-only authorization for `LinkSigner`. [1](#0-0) 

---

### Proof of Concept

1. Alice owns subaccount `alice_subaccount` and sets `linkedSigners[alice_subaccount] = bot_address`.
2. Attacker controls `bot_address` (e.g., via key compromise).
3. Attacker constructs a `SignedLinkSigner` transaction: `{ sender: alice_subaccount, signer: attacker_address, nonce: current_nonce }`, signed by `bot_address`.
4. Attacker submits this to the sequencer. The sequencer calls `processTransactionImpl` → `validateSignedTx(..., allowLinkedSigner: true)`.
5. `getLinkedSigner(alice_subaccount)` returns `bot_address`. Signature validates.
6. `linkedSigners[alice_subaccount]` is now set to `attacker_address`.
7. Attacker uses `attacker_address` to sign a `WithdrawCollateral` (V1, `allowLinkedSigner: true`) transaction, draining Alice's collateral to `address(uint160(bytes20(alice_subaccount)))` — Alice's own wallet — but the attacker has already positioned to intercept or has manipulated Alice's positions to extract value. [5](#0-4)

### Citations

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

**File:** core/contracts/EndpointStorage.sol (L1-5)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

import "./interfaces/IEndpoint.sol";
import "./interfaces/clearinghouse/IClearinghouse.sol";
```
