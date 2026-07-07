### Title
Zero-Signer Bypass in `requireValidTxSignatures` Allows Unauthorized Fast Withdrawal Execution — (`core/contracts/Verifier.sol`)

---

### Summary

`Verifier.requireValidTxSignatures` validates fast-withdrawal authorization by checking `nSignatures == nSigner`. When `nSigner == 0` (no sequencer signing keys are registered), passing an empty `signatures[]` array satisfies the equality check with zero actual signatures, bypassing the multi-sig guard entirely. This is the direct analog to M-17: a critical action passes with zero authorizing inputs because the threshold itself is zero.

---

### Finding Description

`requireValidTxSignatures` in `Verifier.sol` counts how many non-empty entries appear in the caller-supplied `signatures[]` array and then asserts:

```solidity
require(nSignatures == nSigner, "not enough signatures");
``` [1](#0-0) 

`nSigner` is a storage counter that is incremented only when a pubkey is assigned via `_assignPubkey` and decremented when one is deleted via `deletePubkey`. [2](#0-1) 

The `initialize` function populates `nSigner` only for non-zero points in the supplied `initialSet`. If all eight slots are zero (the default), `nSigner` remains `0` after initialization. [3](#0-2) 

When `nSigner == 0`, the loop body in `requireValidTxSignatures` never executes (because `signatures.length == 0`), `nSignatures` stays `0`, and `0 == 0` is trivially true — the function returns without reverting. No cryptographic check is performed.

This function is called from `BaseWithdrawPool.sol` to authorize fast-withdrawal transactions, meaning an attacker can submit a fast-withdrawal request with an empty `signatures` array and have it accepted as fully authorized.

---

### Impact Explanation

An unprivileged caller can drain funds from the `WithdrawPool` by submitting fast-withdrawal transactions with an empty `signatures[]` array whenever `nSigner == 0`. The `WithdrawPool` holds user collateral earmarked for fast exits; a successful bypass allows the attacker to redirect those funds to an arbitrary address without any legitimate sequencer authorization.

---

### Likelihood Explanation

The window exists at deployment (before `assignPubKey` is called) and any time all signers are deleted via `deletePubkey`. The `deletePubkey` function is `onlyOwner`, so the realistic attack window is the deployment gap. Because `requireValidTxSignatures` is a `public view` function callable by anyone, an attacker monitoring the chain for a freshly deployed `Verifier` with `nSigner == 0` can immediately submit a crafted fast-withdrawal call — exactly the same off-chain monitoring pattern described in M-17. [4](#0-3) 

---

### Recommendation

Add an explicit guard requiring at least one registered signer before the equality check is evaluated:

```solidity
require(nSigner > 0, "no signers registered");
require(nSignatures == nSigner, "not enough signatures");
```

This mirrors the M-17 fix of requiring `forVotes > 0`: it ensures the check can never be trivially satisfied by a zero-vs-zero comparison. Alternatively, change the check to `nSignatures >= nSigner && nSignatures > 0` to make the minimum explicit.

---

### Proof of Concept

1. Deploy `Verifier` with `initialSet` containing all zero points → `nSigner == 0`.
2. Call `requireValidTxSignatures(txn, idx, new bytes[](0))` — passes with no revert.
3. Call the `BaseWithdrawPool` fast-withdrawal entry point supplying the same empty `signatures[]` — the `requireValidTxSignatures` guard is satisfied, and the withdrawal executes without any sequencer key having signed it. [5](#0-4)

### Citations

**File:** core/contracts/Verifier.sol (L41-48)
```text
    function initialize(Point[8] memory initialSet) external initializer {
        __Ownable_init();
        for (uint256 i = 0; i < 8; ++i) {
            if (!isPointNone(initialSet[i])) {
                _assignPubkey(i, initialSet[i].x, initialSet[i].y);
            }
        }
    }
```

**File:** core/contracts/Verifier.sol (L69-91)
```text
    function _assignPubkey(
        uint256 i,
        uint256 x,
        uint256 y
    ) internal {
        require(i < 8);
        if (isPointNone(pubkeys[i])) {
            nSigner += 1;
        }
        pubkeys[i] = Point(x, y);
        for (uint256 s = (1 << i); s < 256; s = (s + 1) | (1 << i)) {
            isAggregatePubkeyLatest[s] = false;
        }
        emit AssignPubKey(i, x, y);
    }

    function deletePubkey(uint256 index) public onlyOwner {
        if (!isPointNone(pubkeys[index])) {
            nSigner -= 1;
            delete pubkeys[index];
        }
        emit DeletePubkey(index);
    }
```

**File:** core/contracts/Verifier.sol (L261-289)
```text
    function requireValidTxSignatures(
        bytes calldata txn,
        uint64 idx,
        bytes[] calldata signatures
    ) public view {
        require(signatures.length <= 256, "too many signatures");
        bytes32 data = keccak256(
            abi.encodePacked(uint256(block.chainid), uint256(idx), txn)
        );
        bytes32 hashedMsg = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", data)
        );

        uint256 nSignatures = 0;
        for (uint256 i = 0; i < signatures.length; i++) {
            if (signatures[i].length > 0) {
                nSignatures += 1;
                require(
                    checkIndividualSignature(
                        hashedMsg,
                        signatures[i],
                        uint8(i)
                    ),
                    "invalid signature"
                );
            }
        }
        require(nSignatures == nSigner, "not enough signatures");
    }
```
