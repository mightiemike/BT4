### Title
Linked Signer Can Overwrite Its Own Authorization to Permanently Hijack a Subaccount - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction handler in `EndpointTx.sol` accepts a signature from the **currently linked signer** (`allowLinkedSigner = true`) to overwrite the linked signer mapping. This means a linked signer — intended only as a trading delegate — can unilaterally replace itself with an attacker-controlled address, permanently locking the original owner out of their subaccount and granting the attacker full withdrawal, transfer, and liquidation authority.

---

### Finding Description

In `EndpointTx.sol`, the sequencer-path handler for `LinkSigner` transactions calls `validateSignedTx` with `allowLinkedSigner = true`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // ← linked signer accepted
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
``` [1](#0-0) 

`validateSignedTx` delegates to `validateSignature`, which passes `getLinkedSigner(sender)` as the accepted signer when `allowLinkedSigner` is `true`:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

`Verifier.validateSignature` then accepts the recovered address if it equals either the subaccount owner **or** the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

There is no guard that restricts the `LinkSigner` operation to the subaccount owner only. The linked signer has identical signing authority for this state-mutation operation.

---

### Impact Explanation

Once the linked signer overwrites `linkedSigners[victim_subaccount]` to an attacker-controlled address, the attacker's address is accepted as a valid signer for **every** subsequent signed transaction on that subaccount, including:

- `WithdrawCollateral` / `WithdrawCollateralV2` — drains all collateral
- `TransferQuote` — moves quote balance to any recipient
- `LiquidateSubaccount` — forces liquidation of the victim's positions
- `MintNlp` / `BurnNlp` — manipulates NLP positions
- A further `LinkSigner` — permanently locks out the original owner

The original owner cannot recover without submitting a slow-mode `LinkSigner` transaction (which requires on-chain interaction and may be front-run by the attacker relinking again). The corrupted state is: `linkedSigners[victim_subaccount]` permanently set to attacker address, with all collateral and positions at risk.

**Impact: High** — direct theft of all collateral in the subaccount.

---

### Likelihood Explanation

Linked signers are the standard mechanism for API/bot trading in Nado. Any user who has linked a hot wallet (e.g., an automated trading key) is exposed. The attacker only needs:

1. Control of the currently linked signer key (e.g., a compromised API key, a malicious trading bot, or a rogue linked signer the user trusted).
2. Knowledge of the victim's current nonce (public on-chain state).
3. Ability to submit a transaction to the sequencer (open to any user).

No privileged access, no admin keys, no governance capture required.

**Likelihood: Medium** — requires the linked signer key to be attacker-controlled, but this is the exact threat model for hot-wallet API keys.

---

### Recommendation

Restrict the `LinkSigner` transaction to accept **only** the subaccount owner's signature. Change `allowLinkedSigner` to `false` for this specific transaction type:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // only owner can change linked signer
);
``` [1](#0-0) 

This mirrors the slow-mode path, which already enforces `validateSender(txn.sender, sender)` — requiring the transaction to originate from the subaccount owner's address directly. [4](#0-3) 

---

### Proof of Concept

1. Alice links Bob (`0xBob`) as her linked signer via a normal `LinkSigner` transaction. Alice's subaccount nonce is now `N`.
2. Bob reads Alice's current nonce `N` from `nonces[address(uint160(bytes20(alice_subaccount)))]`.
3. Bob crafts a `SignedLinkSigner` payload: `{sender: alice_subaccount, signer: attacker_address_as_bytes32, nonce: N}`.
4. Bob signs the EIP-712 digest of this payload using his own key.
5. Bob submits the transaction to the sequencer.
6. The sequencer calls `EndpointTx._processTxImpl` → `LinkSigner` branch → `validateSignedTx(..., true)`.
7. `validateSignature` recovers Bob's address, matches it against `getLinkedSigner(alice_subaccount)` = `0xBob` → passes.
8. `linkedSigners[alice_subaccount]` is set to `attacker_address`.
9. Attacker immediately submits a `WithdrawCollateralV2` signed by `attacker_address`, draining Alice's collateral. [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/EndpointTx.sol (L72-76)
```text
    function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
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

**File:** core/contracts/EndpointTx.sol (L177-184)
```text
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

**File:** core/contracts/EndpointTx.sol (L581-590)
```text
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
