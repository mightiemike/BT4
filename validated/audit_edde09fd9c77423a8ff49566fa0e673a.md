### Title
Linked Signer Can Self-Escalate by Authorizing Its Own Replacement via `LinkSigner` — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` fast-path handler in `EndpointTx.sol` passes `allowLinkedSigner = true` to `validateSignedTx`, meaning the **current linked signer** — a delegated key with limited authority — can sign a `LinkSigner` transaction to replace itself with any attacker-controlled address. This violates the invariant that only the subaccount owner should be able to mutate the linked signer mapping. Once replaced, the attacker holds persistent signing authority over the victim's subaccount and can sign trading orders, withdrawals, and other privileged transactions.

---

### Finding Description

**Root cause — `EndpointTx.sol` lines 576–590:**

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
        true                          // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
```

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which in `Verifier.sol` lines 291–304 accepts a signature from **either** the subaccount owner **or** the current linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
```

There is no check that the signer of a `LinkSigner` transaction must be the subaccount owner. The linked signer — a delegated key — can therefore sign a `LinkSigner` transaction to overwrite `linkedSigners[subaccount]` with an attacker-controlled address.

**Contrast with the slow-mode path** (`EndpointTx.sol` lines 232–239), which uses `validateSender` — a check that `msg.sender == address(uint160(bytes20(txn.sender)))` — meaning only the owner's EOA can submit a slow-mode `LinkSigner`. The fast path has no equivalent owner-only gate.

**Post-escalation impact surface:**

Once the attacker's address is installed as the linked signer, it can sign:

- **`MatchOrders` / `MatchOrdersWithAmount`** (lines 495–533): The linked signer is passed directly to the offchain exchange as `takerLinkedSigner` / `makerLinkedSigner` for order signature verification. The attacker can sign orders at adversarial prices to drain the subaccount through trading losses.
- **`WithdrawCollateral`** (lines 413–436): `allowLinkedSigner = true`; funds go to the owner's address, but the attacker can force-drain collateral, collapsing open positions.
- **`LiquidateSubaccount`** (lines 391–412): `allowLinkedSigner = true`; the attacker can initiate liquidations from the victim's subaccount.
- **`MintNlp` / `BurnNlp`** (lines 534–573): `allowLinkedSigner = true`; the attacker can manipulate NLP positions.

**Lockout window:** The victim can only revoke via the slow-mode path, which enforces a hardcoded 3-day delay (`SLOW_MODE_TX_DELAY`). During this window the attacker retains full signing authority.

---

### Impact Explanation

A compromised linked signer key (e.g., a trading-bot API key) can be used to permanently escalate to full subaccount signing control. The attacker can sign malicious orders through `MatchOrders`, causing unbounded trading losses from the victim's collateral. The 3-day slow-mode revocation delay means the victim cannot quickly recover. The corrupted state is `linkedSigners[subaccount]`, and the corrupted asset delta is the victim's entire collateral balance, reachable through adversarial order matching.

---

### Likelihood Explanation

Linked signers are a standard feature used by trading bots and API integrations. Any user who has set a linked signer and whose linked-signer key is compromised (e.g., leaked API key, compromised hot wallet) is immediately vulnerable. The attacker needs only the linked signer's private key and the ability to submit a transaction through the sequencer — no privileged protocol access required.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` fast-path handler so that only the subaccount owner can authorize a signer change:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only owner may change the linked signer
);
```

This mirrors the slow-mode path's owner-only enforcement and ensures the linked signer cannot mutate its own delegation.

---

### Proof of Concept

1. Alice sets `linkedSigners[aliceSubaccount] = botAddress` via a prior `LinkSigner` transaction.
2. Attacker compromises `botAddress`'s private key.
3. Attacker crafts a `SignedLinkSigner` transaction: `sender = aliceSubaccount`, `signer = attackerAddress`, signed by `botAddress`.
4. Sequencer processes it; `validateSignedTx(..., true)` passes because `botAddress == getLinkedSigner(aliceSubaccount)`.
5. `linkedSigners[aliceSubaccount]` is now `attackerAddress`.
6. Attacker signs `MatchOrders` transactions using `attackerAddress`, placing Alice's subaccount as taker/maker in adversarial trades at off-market prices, draining her collateral.
7. Alice submits a slow-mode `LinkSigner` to revoke, but must wait 3 days. The attacker has a 3-day window to complete the drain. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** core/contracts/EndpointTx.sol (L495-514)
```text
        } else if (txType == IEndpoint.TransactionType.MatchOrders) {
            IEndpoint.MatchOrders memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.MatchOrders)
            );
            requireSubaccount(txn.taker.order.sender);
            requireSubaccount(txn.maker.order.sender);

            IEndpoint.MatchOrdersWithSigner memory txnWithSigner = IEndpoint
                .MatchOrdersWithSigner({
                    matchOrders: txn,
                    takerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.taker.order.sender
                    ),
                    makerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.maker.order.sender
                    ),
                    takerAmountDelta: 0
                });
            IOffchainExchange(offchainExchange).matchOrders(txnWithSigner);
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
