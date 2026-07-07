### Title
Linked Signer Can Unilaterally Redirect Subaccount Signing Authority to Attacker-Controlled Address — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` fast-path handler in `EndpointTx.sol` calls `validateSignedTx` with `allowLinkedSigner = true`, meaning the **current linked signer** — not just the subaccount owner — can sign a `LinkSigner` transaction that replaces the linked signer with any arbitrary address. A malicious or compromised linked signer can silently transfer signing authority over a subaccount to an attacker-controlled address, after which the attacker can sign withdrawal transactions and drain the subaccount.

---

### Finding Description

In `EndpointTx.sol`, the fast-path handler for `TransactionType.LinkSigner` is:

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
        true          // <-- allowLinkedSigner
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` passes `getLinkedSigner(sender)` to `verifier.validateSignature`, which accepts either the subaccount owner address **or** the current linked signer as a valid signer:

```solidity
function validateSignature(...) public pure {
    address recovered = ECDSA.recover(digest, signature);
    require(
        (recovered != address(0)) &&
            ((recovered == address(uint160(bytes20(sender)))) ||
                (recovered == linkedSigner)),
        ERR_INVALID_SIGNATURE
    );
}
``` [2](#0-1) 

This means the linked signer — a role the subaccount owner grants to a third party (e.g., a trading bot or API key) — can sign a `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` with any address, without the subaccount owner's knowledge or consent.

The slow-mode path for `LinkSigner` correctly restricts this to the subaccount owner via `validateSender`:

```solidity
validateSender(txn.sender, sender);
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [3](#0-2) 

`validateSender` enforces `address(bytes20(txn.sender)) == sender` (the actual `msg.sender` of the slow-mode submission). The fast path has no equivalent ownership check — it only requires a valid signature from the current linked signer.

The `linkedSigners` mapping is the sole source of truth for delegated signing authority:

```solidity
mapping(bytes32 => address) internal linkedSigners;
``` [4](#0-3) 

Once overwritten, the new address is accepted as a valid signer for all subsequent transactions for that subaccount, including withdrawals.

---

### Impact Explanation

**Corrupted state**: `linkedSigners[subaccount]` — the delegated signing authority for the subaccount.

**Asset delta**: Full balance of the subaccount can be withdrawn by the attacker after the linked signer is replaced.

Attack sequence:
1. User grants linked signer role to `LS1` (e.g., a trading bot or API key).
2. `LS1` (malicious or compromised) constructs a `SignedLinkSigner` with `tx.sender = subaccount`, `tx.signer = attacker_address`, `tx.nonce = current_nonce`, signed by `LS1`'s key.
3. The sequencer processes this as a valid transaction — `validateSignedTx` passes because `LS1` is the current linked signer and `allowLinkedSigner = true`.
4. `linkedSigners[subaccount]` is set to `attacker_address`.
5. Attacker signs a `WithdrawCollateral` (or `TransferQuote`) transaction for the subaccount using `attacker_address`.
6. The sequencer processes the withdrawal; funds leave the subaccount.

The subaccount owner can theoretically override by submitting their own `LinkSigner` transaction, but the nonce for their address has been incremented by step 3, and the attacker can front-run the override with a withdrawal in the same or next sequencer batch.

---

### Likelihood Explanation

Linked signers are a standard, widely-used feature of the protocol — users routinely grant this role to automated trading systems, API keys, and third-party integrators. Any such party that turns malicious, is compromised, or is operated by an adversary can execute this attack. No admin, governance, or sequencer compromise is required. The attacker only needs to control the linked signer key, which is a normal user-level credential, not a privileged protocol key.

---

### Recommendation

Remove `allowLinkedSigner = true` from the `LinkSigner` fast-path handler. Only the subaccount owner (the address embedded in the `sender` bytes32) should be permitted to change the linked signer. Change the call to:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only owner may change linked signer
);
```

This aligns the fast path with the slow-mode path, which already enforces owner-only authorization for `LinkSigner`.

---

### Proof of Concept

```
Preconditions:
  - subaccount S = bytes32(abi.encodePacked(userA, subaccountName))
  - linkedSigners[S] = LS1 (set by userA)
  - nonces[userA] = N
  - S has balance B in QUOTE_PRODUCT_ID

Step 1 (LS1 executes):
  tx = SignedLinkSigner {
    tx: LinkSigner { sender: S, signer: bytes32(uint256(uint160(attacker))), nonce: N },
    signature: sign(LS1_key, EIP712_digest(LinkSigner, S, attacker, N))
  }
  → submitted to sequencer → processed via fast path
  → validateSignedTx passes (LS1 == getLinkedSigner(S), allowLinkedSigner=true)
  → linkedSigners[S] = attacker
  → nonces[userA] = N+1

Step 2 (attacker executes):
  tx = SignedWithdrawCollateral {
    tx: WithdrawCollateral { sender: S, productId: QUOTE, amount: B, nonce: N+1 },
    signature: sign(attacker_key, EIP712_digest(...))
  }
  → submitted to sequencer → processed via fast path
  → validateSignedTx passes (attacker == getLinkedSigner(S))
  → B is withdrawn to attacker's address

Result: userA's subaccount is drained.
```

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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
