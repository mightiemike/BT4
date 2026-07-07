### Title
Linked Signer Can Permanently Escalate Its Own Authority via `LinkSigner` Transaction - (File: `core/contracts/EndpointTx.sol`)

### Summary

In `EndpointTx.processTransactionImpl`, `LinkSigner` transactions are validated with `allowLinkedSigner = true`, meaning the current linked signer is permitted to authorize a transaction that **replaces** the linked signer. This is a direct analog to the reported bug class: the authorization check reads the current linked signer state (read-level protection), but the operation mutates that same state (requires write-level protection). A compromised or malicious linked signer can permanently escalate its own authority by replacing itself with an attacker-controlled address, enabling unauthorized withdrawals and trades.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` fast-path branch at lines 576–590 calls `validateSignedTx` with `allowLinkedSigner = true`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // ← allowLinkedSigner
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
``` [1](#0-0) 

`validateSignedTx` calls `validateSignature`, which calls `getLinkedSigner(sender)` and passes the result to `verifier.validateSignature`:

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

`getLinkedSigner` reads `linkedSigners[subaccount]` (or the parent's entry for isolated subaccounts): [3](#0-2) 

The `linkedSigners` mapping is stored in `EndpointStorage`: [4](#0-3) 

**Root cause:** The authorization check reads `linkedSigners[sender]` (the current linked signer) to validate the signature, but the operation then writes to `linkedSigners[sender]` to install a new linked signer. The protection level (linked signer authorization) is insufficient for the operation (mutating the linked signer). This is structurally identical to the reported bug: a read-level guard is used where a write-level guard is required.

**Contrast with the slow-mode path**, which correctly restricts `LinkSigner` to the subaccount owner only via `validateSender`: [5](#0-4) 

The slow-mode path enforces `address(uint160(bytes20(txn.sender))) == sender` (the `msg.sender` who submitted the slow-mode tx), so only the subaccount owner can change the linked signer through that path. The fast path has no equivalent restriction.

---

### Impact Explanation

A compromised or malicious linked signer (e.g., a trading bot whose key is exposed) can:

1. Sign a `SignedLinkSigner{sender: victim_subaccount, signer: attacker_address, nonce: N}` transaction.
2. Submit it to the sequencer's public API; the sequencer includes it in the next batch.
3. `processTransactionImpl` validates the signature against the current linked signer — which is the attacker's compromised key — and accepts it.
4. `linkedSigners[victim_subaccount]` is overwritten with `attacker_address`.
5. The attacker now permanently controls the linked signer for the victim's subaccount.
6. The attacker signs `SignedWithdrawCollateral` or `SignedTransferQuote` transactions to drain the subaccount's collateral.

The corrupted state is `linkedSigners[subaccount]` — a signer state corruption that directly enables asset theft. The victim cannot recover without submitting a slow-mode `LinkSigner` revocation (3-day delay), during which the attacker can drain funds.

---

### Likelihood Explanation

Linked signers are the standard mechanism for automated trading bots and API integrations in perpetual DEX protocols. Hot-wallet keys used for high-frequency trading are materially more likely to be compromised than a user's main wallet. Additionally, users may intentionally grant linked signer access to third-party services without understanding that the service can permanently replace the linked signer. The sequencer processes all cryptographically valid signed transactions, so no sequencer compromise is required — only a valid signature from the current linked signer.

---

### Recommendation

Change `allowLinkedSigner` from `true` to `false` for `LinkSigner` transactions in `processTransactionImpl`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // only the subaccount owner may change the linked signer
);
``` [6](#0-5) 

This aligns the fast path with the slow-mode path, which already correctly restricts `LinkSigner` to the subaccount owner.

---

### Proof of Concept

1. Alice deposits collateral into `alice_subaccount` and sets linked signer to `bot_address` (a trading bot).
2. Attacker obtains `bot_address`'s private key.
3. Attacker constructs `SignedLinkSigner{tx: {sender: alice_subaccount, signer: attacker_address, nonce: current_nonce}, signature: ECDSA_sign(bot_key, digest)}`.
4. Attacker submits the transaction to the sequencer API.
5. Sequencer calls `submitTransactionsChecked([...LinkSigner_tx...])` → `processTransaction` → `processTransactionImpl`.
6. `validateSignedTx(..., true)` reads `linkedSigners[alice_subaccount] == bot_address`, verifies the signature against `bot_address` — passes.
7. `linkedSigners[alice_subaccount] = attacker_address` is written.
8. Attacker signs `SignedWithdrawCollateral{sender: alice_subaccount, productId: QUOTE, amount: full_balance, nonce: N+1}` with `attacker_address`.
9. Sequencer processes the withdrawal; Alice's collateral is transferred to the attacker.

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
