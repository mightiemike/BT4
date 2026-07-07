### Title
Linked Signer Can Unilaterally Rotate Its Own Authority Without Subaccount Owner Consent - (`File: core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.sol`, the fast-path `LinkSigner` handler calls `validateSignedTx` with `allowLinkedSigner = true`, meaning the current linked signer's signature is accepted as sufficient authorization to overwrite `linkedSigners[subaccount]` to any arbitrary address. The subaccount owner's signature is never required for this state mutation. A compromised or malicious linked signer can silently replace itself with an attacker-controlled address, granting that address full signing authority over the subaccount — including the ability to sign `WithdrawCollateral` and `TransferQuote` transactions that drain all collateral.

---

### Finding Description

The `processTransactionImpl` function in `EndpointTx.sol` handles the sequencer-submitted `LinkSigner` transaction type as follows:

```solidity
// EndpointTx.sol:576-590
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
}
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` delegates to `validateSignature`, which passes `getLinkedSigner(sender)` as the accepted alternate signer:

```solidity
// EndpointTx.sol:172-184
function validateSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

`Verifier.validateSignature` then accepts either the subaccount owner address or the linked signer address as a valid recovered signer:

```solidity
// Verifier.sol:297-303
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

The result: the current linked signer can sign a `LinkSigner` transaction that sets `linkedSigners[subaccount]` to any address — including an attacker-controlled one — without the subaccount owner ever signing or consenting to this change. This is the direct analog to the external report's pattern: a state-changing operation that modifies critical authorization state proceeds without requiring the consent of the party whose state is being changed.

Every other sensitive fast-path operation (`WithdrawCollateral`, `WithdrawCollateralV2`, `TransferQuote`) also accepts `allowLinkedSigner = true`, meaning once the linked signer is rotated to an attacker address, the attacker can immediately sign those transactions to drain the subaccount. [4](#0-3) 

---

### Impact Explanation

**Severity: High**

A malicious or compromised linked signer can permanently take over a subaccount's signing authority by rotating `linkedSigners[subaccount]` to an attacker-controlled address. Once rotated, the attacker can sign `WithdrawCollateral` or `TransferQuote` transactions to drain all collateral from the subaccount. The subaccount owner loses all assets held in the protocol with no on-chain recourse, since the attacker controls the nonce-advancing signing key.

The corrupted state is `linkedSigners[subaccount]` in `EndpointStorage`, and the downstream asset delta is the full collateral balance of the victim subaccount across all spot products. [5](#0-4) 

---

### Likelihood Explanation

**Likelihood: Medium**

The precondition is that a user has set a linked signer (a common and documented protocol feature for hot-wallet delegation). Any user who has done so is exposed. The attacker only needs to compromise the linked signer key — a hot wallet by design — which is a realistic threat model. No admin access, governance capture, or privileged sequencer compromise is required. The sequencer processes the transaction as a normal signed submission.

---

### Recommendation

`LinkSigner` is a meta-authorization operation — it changes who is authorized to act on behalf of a subaccount. Unlike `WithdrawCollateral` or `TransferQuote` (which are actions the linked signer is explicitly delegated to perform), rotating the linked signer itself should require the subaccount owner's signature exclusively. The fix is to pass `allowLinkedSigner = false` when validating the `LinkSigner` transaction in `processTransactionImpl`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // owner-only: linked signer must not be able to rotate itself
);
``` [6](#0-5) 

---

### Proof of Concept

1. Alice owns subaccount `alice_subaccount` and has previously set `linkedSigners[alice_subaccount] = bob_address` via a legitimate `LinkSigner` transaction.
2. Bob (or an attacker who obtained Bob's private key) constructs a `SignedLinkSigner` payload:
   - `signedTx.tx.sender = alice_subaccount`
   - `signedTx.tx.signer = attacker_address` (bytes32-padded)
   - `signedTx.tx.nonce = current_nonce_for_alice`
   - `signedTx.signature` = Bob's ECDSA signature over the EIP-712 digest of this transaction
3. Bob submits this to the sequencer. The sequencer calls `processTransactionImpl`.
4. `validateSignedTx(..., true)` recovers Bob's address from the signature, checks it against `getLinkedSigner(alice_subaccount)` which returns `bob_address` — validation passes.
5. `linkedSigners[alice_subaccount]` is overwritten to `attacker_address`.
6. The attacker now signs a `SignedWithdrawCollateral` with `sender = alice_subaccount`, submits to the sequencer. `validateSignedTx(..., true)` now resolves `getLinkedSigner(alice_subaccount) = attacker_address`, signature validates, and `clearinghouse.withdrawCollateral` drains Alice's collateral to the attacker. [7](#0-6)

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
