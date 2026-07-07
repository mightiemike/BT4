### Title
Malicious Linked Signer Can Front-Run Its Own Revocation to Maintain Persistent Subaccount Control — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` fast-path transaction in `EndpointTx.processTransactionImpl` is validated with `allowLinkedSigner=true`. This means the **currently registered linked signer** can itself sign and submit a `LinkSigner` transaction — including one that replaces itself with a different address it controls. A malicious linked signer can exploit this to front-run the subaccount owner's revocation transaction, consuming the nonce and causing the owner's transaction to fail, thereby maintaining persistent unauthorized control over the subaccount.

---

### Finding Description

In `EndpointTx.sol`, the `LinkSigner` fast-path branch calls `validateSignedTx` with `allowLinkedSigner=true`:

```solidity
// EndpointTx.sol lines 581–590
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // <-- existing linked signer is accepted as valid signer
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
```

`validateSignedTx` delegates to `validateSignature`, which passes the current linked signer to the verifier as an accepted signer:

```solidity
// EndpointTx.sol lines 172–184
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
```

Nonces are per-wallet-address and strictly sequential:

```solidity
// EndpointTx.sol lines 72–77
require(
    nonce == nonces[address(uint160(bytes20(sender)))]++,
    ERR_WRONG_NONCE
);
```

**Attack path:**

1. Subaccount owner has linked signer `S1` (e.g., a trading bot or third-party service). Current nonce is `N`.
2. Owner decides to revoke `S1` and submits a `LinkSigner` transaction with nonce `N` setting `signer = address(0)` (or a new trusted address).
3. `S1` observes this pending transaction in the sequencer queue and immediately submits its own `LinkSigner` transaction with nonce `N`, setting `signer = S2` (another address `S1` controls).
4. The sequencer processes `S1`'s transaction first. `linkedSigners[subaccount] = S2`. Nonce advances to `N+1`.
5. The owner's revocation transaction now fails with `ERR_WRONG_NONCE` (nonce `N` already consumed).
6. `S1` (via `S2`) retains full signing authority. The owner must submit a new revocation with nonce `N+1`, which `S2` can front-run again — creating a perpetual race.

Additionally, since `WithdrawCollateral` also uses `allowLinkedSigner=true`, the linked signer can interleave collateral-draining transactions before the revocation lands:

```solidity
// EndpointTx.sol lines 418–436
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // linked signer accepted
);
clearinghouse.withdrawCollateral(...);
```

---

### Impact Explanation

A malicious linked signer can:
- **Permanently block revocation** by consuming each revocation nonce with a self-replacement `LinkSigner`, maintaining indefinite control over the subaccount.
- **Drain collateral** via `WithdrawCollateral` (funds go to the subaccount owner's address, but the linked signer can open leveraged positions or trigger liquidations that result in net loss to the owner before revocation lands).
- **Corrupt subaccount state** by minting/burning NLP tokens (`MintNlp`/`BurnNlp` also use `allowLinkedSigner=true`) at unfavorable oracle prices, harming the owner's position.

The corrupted state is: `linkedSigners[subaccount]` — the ownership assumption that the subaccount owner controls who can act on their behalf is broken.

---

### Likelihood Explanation

Any subaccount that has ever granted a linked signer is exposed. The linked signer is an externally controlled address (e.g., a trading bot, API key, or third-party service). A compromised or malicious linked signer has a direct economic incentive to maintain access. The attack requires only the ability to submit transactions to the sequencer — a capability any linked signer already possesses by design. No privileged access beyond the existing linked signer role is needed.

---

### Recommendation

Remove `allowLinkedSigner=true` from the `LinkSigner` transaction validation. Only the subaccount owner (the wallet whose address is encoded in the first 20 bytes of the `sender` bytes32) should be permitted to change the linked signer. The linked signer is a delegated role and must not be able to self-perpetuate or self-replace.

```solidity
// Change from:
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, true);
// To:
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, false);
```

Apply the same fix to the slow-mode `LinkSigner` path in `processSlowModeTransactionImpl`, which already correctly uses `validateSender` (owner-only) rather than signature validation — confirming the intended design is owner-only control for signer management.

---

### Proof of Concept

**State before attack:**
- `subaccount = 0xAlice...000000000000` (Alice's subaccount)
- `linkedSigners[subaccount] = S1` (malicious bot)
- `nonces[Alice] = N`

**Step 1:** Alice submits `LinkSigner{sender=subaccount, signer=address(0), nonce=N}` signed by Alice's key → sequencer receives it.

**Step 2:** `S1` observes the pending revocation and submits `LinkSigner{sender=subaccount, signer=S2, nonce=N}` signed by `S1`'s key → sequencer receives it.

**Step 3:** Sequencer processes `S1`'s transaction first:
- `validateSignedTx` passes because `getLinkedSigner(subaccount) == S1` and `allowLinkedSigner=true`
- `linkedSigners[subaccount] = S2`
- `nonces[Alice]` increments to `N+1`

**Step 4:** Sequencer processes Alice's transaction:
- `validateNonce` fails: `N != N+1` → `ERR_WRONG_NONCE` → revert

**Result:** `linkedSigners[subaccount] = S2` (controlled by `S1`). Alice's revocation failed. `S1` retains full signing authority over Alice's subaccount indefinitely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/EndpointTx.sol (L72-77)
```text
    function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
    }
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

**File:** core/contracts/EndpointTx.sol (L418-436)
```text
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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
