### Title
Linked Signer Can Hijack Subaccount by Self-Replacing via Fast-Mode `LinkSigner` Transaction — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the fast-mode `LinkSigner` transaction is validated with `allowLinkedSigner = true`, meaning the **current linked signer** is accepted as a valid authorizer to change the linked signer. This allows a compromised or malicious linked signer to replace itself with an attacker-controlled address, permanently hijacking the subaccount's signing authority. The slow-mode `LinkSigner` path correctly restricts this operation to the subaccount owner only, revealing an inconsistent and exploitable authorization boundary.

---

### Finding Description

**Root cause — inconsistent authorization for `LinkSigner`:**

In the fast-mode path, `processTransactionImpl` handles `LinkSigner` at lines 576–590:

```solidity
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

`validateSignedTx` calls `validateSignature` with `allowLinkedSigner = true`, which passes `getLinkedSigner(sender)` to `verifier.validateSignature`:

```solidity
function validateSignature(...) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

`verifier.validateSignature` accepts the recovered address if it equals **either** the owner address embedded in `sender` **or** the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

This means the **current linked signer** can produce a valid signature for a `LinkSigner` transaction that sets `signedTx.tx.signer` to any attacker-controlled address.

**Contrast with slow-mode path:**

The slow-mode `LinkSigner` handler uses `validateSender`, which enforces that `msg.sender == address(bytes20(txn.sender))` — i.e., only the actual subaccount owner address can change the linked signer:

```solidity
validateSender(txn.sender, sender);
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

The slow-mode path correctly restricts `LinkSigner` to the owner. The fast-mode path does not. This is the broken invariant.

**Exploit flow:**

1. User creates subaccount `S` (owner address `O`) and links signer `A` (e.g., an API key for automated trading).
2. Attacker obtains signing capability for `A` (e.g., leaked API key, compromised hot wallet).
3. Attacker uses `A` to sign a `LinkSigner` transaction: `{sender: S, signer: attacker_address_B, nonce: current_nonce}`.
4. Sequencer processes this fast-mode transaction. `validateSignedTx` passes because `A == getLinkedSigner(S)`.
5. `linkedSigners[S]` is now set to `B` (attacker-controlled).
6. Attacker uses `B` to sign `WithdrawCollateral` or `WithdrawCollateralV2` transactions (both accept `allowLinkedSigner = true`) to drain all collateral from `S`.
7. Owner `O` can only recover via slow mode (`LinkSigner` slow-mode path), which has a hardcoded 3-day delay (`SLOW_MODE_TX_DELAY`). Funds are drained before recovery is possible. [5](#0-4) 

---

### Impact Explanation

A compromised linked signer — a realistic scenario for users who link API keys or hot wallets for automated trading — can permanently escalate its own authority by replacing itself with an attacker-controlled address. The attacker then holds full signing authority over the subaccount and can drain all collateral via `WithdrawCollateral` or `WithdrawCollateralV2`. The 3-day slow-mode delay means the owner cannot prevent the theft once the linked signer has been replaced. This is a direct, complete theft of user funds with no on-chain recourse within the attack window.

---

### Likelihood Explanation

Linked signers are a core feature of the protocol, used by traders who delegate signing to API keys or automated systems. Leaked API keys and compromised hot wallets are common real-world events. Any user who has ever linked a signer is exposed. The attacker needs only a single valid signature from the linked signer — no privileged protocol access, no governance capture, no sequencer compromise.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`. Only the subaccount owner (the address embedded in the `sender` bytes32) should be authorized to change the linked signer, consistent with the slow-mode path:

```solidity
// In processTransactionImpl, LinkSigner branch:
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // <-- must be false; only owner may change linked signer
);
``` [6](#0-5) 

---

### Proof of Concept

```
State before:
  subaccount S = bytes32(abi.encodePacked(owner_O, subaccountName))
  linkedSigners[S] = address_A  (attacker has key for A)

Step 1 — Attacker constructs LinkSigner tx:
  tx = {
    type: LinkSigner,
    sender: S,
    signer: bytes32(abi.encodePacked(attacker_B, bytes12(0))),
    nonce: nonces[owner_O]   // current nonce
  }
  signature = sign(tx, key_A)

Step 2 — Sequencer submits batch containing this tx.
  processTransactionImpl dispatches to LinkSigner branch.
  validateSignedTx(S, nonce, tx, sig, true):
    getLinkedSigner(S) == address_A
    ECDSA.recover(digest, sig) == address_A  ✓
  linkedSigners[S] = attacker_B

Step 3 — Attacker constructs WithdrawCollateral tx:
  tx2 = {
    type: WithdrawCollateral,
    sender: S,
    productId: QUOTE_PRODUCT_ID,
    amount: full_balance,
    nonce: nonces[owner_O]
  }
  signature2 = sign(tx2, key_B)

Step 4 — Sequencer processes tx2.
  validateSignedTx(S, nonce, tx2, sig2, true):
    getLinkedSigner(S) == attacker_B
    ECDSA.recover(digest2, sig2) == attacker_B  ✓
  clearinghouse.withdrawCollateral(S, ...) drains funds to attacker.
```

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
