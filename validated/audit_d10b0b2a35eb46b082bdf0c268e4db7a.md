### Title
Signature Position Hardcoded to Array Index Causes Fast Withdrawal Rejection for Valid Multi-Signer Sets — (`File: core/contracts/Verifier.sol`)

---

### Summary

`Verifier.requireValidTxSignatures` maps each signature to a signer by its **array index**, not by recovering the signer and checking membership. Any caller who provides a fully valid set of signatures in a non-canonical order will receive a hard revert with `"invalid signature"`, permanently preventing that `submitFastWithdrawal` call from succeeding with those inputs, even though every required signer has legitimately signed.

---

### Finding Description

In `requireValidTxSignatures`, the loop passes `uint8(i)` — the loop counter — directly as the `signerIndex` argument to `checkIndividualSignature`:

```solidity
for (uint256 i = 0; i < signatures.length; i++) {
    if (signatures[i].length > 0) {
        nSignatures += 1;
        require(
            checkIndividualSignature(hashedMsg, signatures[i], uint8(i)),
            "invalid signature"
        );
    }
}
``` [1](#0-0) 

`checkIndividualSignature` then compares the recovered address against `getPubkeyAddress(signerIndex)` — the pubkey registered at that exact slot:

```solidity
function checkIndividualSignature(bytes32 digest, bytes memory signature, uint8 signerIndex)
    public view returns (bool) {
    address expectedAddress = getPubkeyAddress(signerIndex);
    address recovered = ECDSA.recover(digest, signature);
    return expectedAddress == recovered;
}
``` [2](#0-1) 

This means `signatures[0]` must be from `pubkeys[0]`, `signatures[1]` from `pubkeys[1]`, and so on. There is no mechanism to identify which signer produced which signature; the position in the array is the sole identity claim.

The only public entry point that calls this function is `submitFastWithdrawal` in `BaseWithdrawPool`:

```solidity
Verifier v = Verifier(verifier);
v.requireValidTxSignatures(transaction, idx, signatures);
``` [3](#0-2) 

`submitFastWithdrawal` is an unrestricted `public` function callable by any address: [4](#0-3) 

---

### Impact Explanation

A user who has obtained valid signatures from all `nSigner` registered signers but presents them in any order other than the exact slot-ascending order will receive `"invalid signature"` and their fast withdrawal will be rejected. Because the `markedIdxs[idx] = true` write precedes the verifier call and the whole transaction reverts on failure, the slot is not permanently consumed — but the user must know to reorder and retry, which is not documented anywhere in the contract. If the off-chain tooling or SDK assembles signatures in a different order (e.g., sorted by address, by arrival time, or by signer index descending), every fast withdrawal attempt will silently fail with a misleading error, effectively locking users out of the fast withdrawal path for their collateral. [5](#0-4) 

---

### Likelihood Explanation

The `signatures` array is assembled off-chain and passed directly by the caller. Any SDK, relayer, or front-end that does not happen to sort signatures in strict ascending-slot order will trigger this revert on every call. Because the error message is `"invalid signature"` rather than something like `"signature at wrong position"`, the root cause is non-obvious and difficult to diagnose. The likelihood of encountering this in practice is high whenever more than one signer is registered and the off-chain assembly order differs from slot order.

---

### Recommendation

Recover the signer address from each non-empty signature and look up which registered slot it corresponds to, rather than assuming `signatures[i]` belongs to slot `i`. One approach:

```solidity
for (uint256 i = 0; i < signatures.length; i++) {
    if (signatures[i].length > 0) {
        address recovered = ECDSA.recover(hashedMsg, signatures[i]);
        bool matched = false;
        for (uint8 j = 0; j < 8; j++) {
            if (!isPointNone(pubkeys[j]) && getPubkeyAddress(j) == recovered) {
                require(!seen[j], "duplicate signer");
                seen[j] = true;
                matched = true;
                nSignatures += 1;
                break;
            }
        }
        require(matched, "unrecognized signer");
    }
}
```

This makes verification order-agnostic, matching the intended specification that any quorum of valid signers suffices regardless of array position.

---

### Proof of Concept

Assume `nSigner = 2`, with signer 0 at `pubkeys[0]` and signer 1 at `pubkeys[1]`.

1. Both signers produce valid ECDSA signatures over `hashedMsg`.
2. Caller submits `signatures = [sig_from_signer1, sig_from_signer0]` (reversed order).
3. Loop iteration `i=0`: calls `checkIndividualSignature(hashedMsg, sig_from_signer1, 0)` → recovers signer 1's address, compares against `getPubkeyAddress(0)` (signer 0's address) → **returns false** → `require` reverts with `"invalid signature"`.
4. Fast withdrawal is rejected despite both required signers having signed.

The correct submission `[sig_from_signer0, sig_from_signer1]` would succeed, demonstrating that the outcome depends entirely on caller-controlled array ordering, not on the cryptographic validity of the signatures. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/Verifier.sol (L248-256)
```text
    function checkIndividualSignature(
        bytes32 digest,
        bytes memory signature,
        uint8 signerIndex
    ) public view returns (bool) {
        address expectedAddress = getPubkeyAddress(signerIndex);
        address recovered = ECDSA.recover(digest, signature);
        return expectedAddress == recovered;
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

**File:** core/contracts/BaseWithdrawPool.sol (L81-113)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
```
