### Title
Linked Signer Can Overwrite Itself to Hijack Subaccount Signing Authority - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.sol`, the fast-path `LinkSigner` transaction handler invokes `validateSignedTx` with `allowLinkedSigner = true`. This permits the **current linked signer** — not just the subaccount owner — to sign and submit a new `LinkSigner` transaction, directly overwriting `linkedSigners[subaccount]` with any attacker-controlled address. A compromised or malicious linked signer can silently rotate signing authority to an attacker, who then has full control over the subaccount for at least the 3-day slow-mode recovery window.

---

### Finding Description

In `EndpointTx.sol`, the fast-path `processTransactionImpl` handles `LinkSigner` as follows:

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

`validateSignedTx` with `allowLinkedSigner = true` resolves the valid signer as either the subaccount owner **or** the current linked signer:

```solidity
function validateSignedTx(..., bool allowLinkedSigner) internal {
    validateNonce(sender, nonce);
    validateCompactSignature(
        sender,
        ...,
        signature,
        allowLinkedSigner
    );
    requireSubaccount(sender);
}
``` [2](#0-1) 

And `validateCompactSignature` passes the linked signer address to the verifier when `allowLinkedSigner = true`:

```solidity
function validateCompactSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateCompactSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [3](#0-2) 

This means the current linked signer can produce a valid signature for a `LinkSigner` transaction that **replaces itself** with any arbitrary address. The assignment `linkedSigners[signedTx.tx.sender] = ...` is an unconditional overwrite — there is no check that the signer is the subaccount owner, no confirmation step, and no guard against the linked signer rotating authority to a third party.

The slow-mode `LinkSigner` path does not have this flaw — it calls `validateSender(txn.sender, sender)`, which enforces that only the wallet owner (the address encoded in the subaccount `bytes32`) can submit the transaction:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    ...
    validateSender(txn.sender, sender);
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
}
``` [4](#0-3) 

The asymmetry between the two paths is the root cause: the fast path trusts the linked signer to authorize a `LinkSigner` mutation, while the slow path correctly restricts it to the wallet owner.

---

### Impact Explanation

Once a linked signer is set, it can sign any sequencer-submitted transaction on behalf of the subaccount (withdrawals, trades, transfers, liquidations). If the linked signer rotates authority to an attacker-controlled address, the attacker gains identical capabilities. The user's only recovery path is a slow-mode `LinkSigner` transaction, which carries a hardcoded 3-day delay (`SLOW_MODE_TX_DELAY`):

```solidity
slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY,
    ...
});
``` [5](#0-4) 

During those 3 days the attacker retains full signing authority and can drain all collateral via `WithdrawCollateral` or `TransferQuote` transactions signed by the hijacked linked signer. The `linkedSigners` mapping is the sole on-chain record of signing authority:

```solidity
mapping(bytes32 => address) internal linkedSigners;
``` [6](#0-5) 

**Corrupted state**: `linkedSigners[subaccount]` is overwritten from the user's intended address to an attacker-controlled address, giving the attacker full subaccount control.

---

### Likelihood Explanation

Linked signers are the standard mechanism for automated trading bots and API keys in Nado. Any user who has set a linked signer is exposed. The attack requires only that the linked signer key be compromised (e.g., a hot wallet, an API key stored on a server, or a bot with a leaked private key). This is a realistic and common threat model for DeFi trading infrastructure. No privileged access, governance capture, or admin keys are required — the attacker only needs the linked signer's private key.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`. Only the subaccount owner should be permitted to authorize a change to the linked signer:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // only owner can change linked signer
);
```

This aligns the fast path with the slow-mode path, which already correctly enforces owner-only authorization via `validateSender`.

---

### Proof of Concept

1. User (wallet `W`) sets linked signer to address `A` (a trading bot) via a `LinkSigner` transaction.
2. `linkedSigners[subaccount_of_W] = A` is stored on-chain.
3. Attacker compromises address `A` (e.g., via a leaked API key).
4. Attacker uses `A` to sign a new `LinkSigner` transaction: `{ sender: subaccount_of_W, signer: attacker_address_B }`.
5. The sequencer includes this in `submitTransactionsChecked`, which calls `processTransactionImpl`.
6. `validateSignedTx(..., allowLinkedSigner = true)` accepts the signature from `A` (the current linked signer).
7. `linkedSigners[subaccount_of_W]` is overwritten with `attacker_address_B`.
8. Attacker now signs `WithdrawCollateral` transactions using `B`, draining all collateral from `subaccount_of_W`.
9. User submits a slow-mode `LinkSigner` to recover, but must wait 3 days — by which time the account is empty.

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

**File:** core/contracts/EndpointTx.sol (L374-384)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
