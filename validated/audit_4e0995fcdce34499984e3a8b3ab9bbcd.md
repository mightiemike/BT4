### Title
Linked Signer Can Unilaterally Reassign Itself to a New Malicious Address, Permanently Compromising the Subaccount - (`core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction in `EndpointTx.processTransactionImpl` is validated with `allowLinkedSigner = true`. This means the currently-linked signer of a subaccount can sign and submit a new `LinkSigner` transaction to replace itself with any arbitrary address — including a fresh malicious signer — without the subaccount owner's knowledge or consent. The subaccount owner loses control of their account's signing authority, and the new malicious linked signer can place harmful orders against the subaccount.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is processed as follows:

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

`validateSignedTx` with `allowLinkedSigner = true` resolves to `validateSignature` → `verifier.validateSignature`, which accepts a signature from either the subaccount owner address OR the currently-registered linked signer:

```solidity
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [2](#0-1) 

This means the current linked signer can sign a `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` with any new address. The subaccount owner is never consulted.

By contrast, the slow-mode path for `LinkSigner` uses `validateSender`, which enforces that `msg.sender` must be the subaccount owner's EOA address — it does not accept linked signer signatures:

```solidity
validateSender(txn.sender, sender);
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [3](#0-2) 

The sequencer-processed path has no equivalent ownership check, creating an asymmetry: the slow-mode path correctly restricts `LinkSigner` to the owner, but the fast sequencer path does not.

---

### Impact Explanation

Once the malicious linked signer installs a new signer address it controls, that new signer can:

1. **Place harmful orders** — `MatchOrders` and `MatchOrdersWithAmount` both pass the linked signer fetched at execution time to `_validateOrder`, which accepts signatures from the linked signer. The new malicious signer can sell the subaccount's positions at dust prices to a colluding counterparty, draining value. [4](#0-3) 

2. **Withdraw collateral to the subaccount owner's address** — `WithdrawCollateral` (V1) allows linked signers and sends funds to the subaccount owner's address, so direct theft via withdrawal is blocked. However, the malicious signer can still destroy value through adversarial order placement.

3. **Mint/Burn NLP and TransferQuote** — Both `MintNlp` and `BurnNlp` are validated with `allowLinkedSigner = true`, as is `TransferQuote`. The new malicious signer can trigger NLP burns (which incur a burn fee and can reduce health) or transfer quote between the owner's own subaccounts. [5](#0-4) 

The net impact is **loss of funds** through adversarial order execution against the victim's subaccount.

---

### Likelihood Explanation

The scenario is realistic wherever users delegate signing authority to automated systems (trading bots, keeper scripts, API integrations). A compromised or malicious linked signer key is a plausible real-world event. The attack requires no admin access, no governance capture, and no external oracle manipulation — only possession of the currently-linked signer's private key. The attacker-controlled entry path is a standard sequencer-submitted transaction.

---

### Recommendation

Change the `allowLinkedSigner` flag for `LinkSigner` processing in `processTransactionImpl` from `true` to `false`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // only the subaccount owner may change the linked signer
);
``` [6](#0-5) 

This aligns the sequencer-processed path with the slow-mode path, which already correctly restricts `LinkSigner` to the subaccount owner.

---

### Proof of Concept

1. Alice owns subaccount `ALICE_SUB` and links `BOT_A` as her trading bot via `LinkSigner`.
2. `BOT_A`'s key is compromised (or `BOT_A` is a malicious service).
3. `BOT_A` constructs a `SignedLinkSigner` transaction with `sender = ALICE_SUB`, `signer = MALICIOUS_ADDR`, signed by `BOT_A`'s key.
4. `BOT_A` submits this to the sequencer. The sequencer calls `processTransactionImpl`.
5. `validateSignedTx(..., true)` recovers `BOT_A`'s address, matches it against `linkedSigners[ALICE_SUB]` = `BOT_A` → passes.
6. `linkedSigners[ALICE_SUB]` is overwritten with `MALICIOUS_ADDR`.
7. `MALICIOUS_ADDR` now signs `MatchOrders` transactions that sell `ALICE_SUB`'s positions at adversarial prices to a colluding counterparty, draining Alice's collateral.
8. Alice has no on-chain mechanism to prevent this once `BOT_A`'s key is compromised, because the malicious signer can keep re-linking itself faster than Alice can revoke via slow mode (which has a 3-day delay). [1](#0-0) [7](#0-6)

### Citations

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

**File:** core/contracts/EndpointTx.sol (L503-514)
```text
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

**File:** core/contracts/EndpointTx.sol (L539-546)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
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
