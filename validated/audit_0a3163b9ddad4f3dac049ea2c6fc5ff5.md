### Title
`WithdrawCollateralV2` Allows Fee-Free Collateral Withdrawal via User-Controlled `feeX18` Field Excluded from EIP-712 Digest — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `WithdrawCollateralV2` transaction path in `EndpointTx.sol` allows a user to set `feeX18 = 0` in their signed transaction, bypassing the withdrawal fee that `WithdrawCollateral` (V1) always enforces. The `feeX18` field is not included in the EIP-712 digest, so the user's signature is valid regardless of the fee value they embed. The on-chain contract only checks `0 <= feeX18 <= currentFeeX18`, which explicitly permits zero.

---

### Finding Description

`WithdrawCollateral` (V1) in `processTransactionImpl` always charges the full configured fee:

```solidity
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId
);
```

`WithdrawCollateralV2` instead charges the user-supplied `signedTx.feeX18`:

```solidity
require(signedTx.feeX18 >= 0);
require(signedTx.feeX18 <= currentFeeX18);
chargeFee(
    signedTx.tx.sender,
    signedTx.feeX18,
    signedTx.tx.productId
);
``` [1](#0-0) 

Both `require` checks pass when `feeX18 = 0`. The critical flaw is that `feeX18` is **not included in the EIP-712 digest** computed in `Verifier.sol`:

```solidity
digest = keccak256(
    abi.encode(
        keccak256(bytes(WITHDRAW_COLLATERAL_V2_SIGNATURE)),
        signedTx.tx.sender,
        signedTx.tx.productId,
        signedTx.tx.amount,
        signedTx.tx.nonce,
        signedTx.tx.sendTo,
        signedTx.tx.appendix   // feeX18 is absent
    )
);
``` [2](#0-1) 

The `SignedWithdrawCollateralV2` struct carries `feeX18` as an outer field alongside the signed inner struct:

```solidity
struct SignedWithdrawCollateralV2 {
    WithdrawCollateralV2 tx;
    CompactSignature signature;
    int128 feeX18;           // not covered by signature
}
``` [3](#0-2) 

Because `feeX18` is outside the signed payload, a user can construct a `SignedWithdrawCollateralV2` with `feeX18 = 0`, produce a valid signature over the inner `WithdrawCollateralV2` struct, and submit it. The on-chain contract accepts the signature and charges zero fee.

---

### Impact Explanation

**Impact: Medium.**

Any user can withdraw collateral from their subaccount without paying the protocol's configured `withdrawFeeX18`. The `sequencerFee` accounting is corrupted — the fee that should accrue to the protocol is never charged. Over time, across many users, this drains the protocol's fee revenue. The asset delta is: `withdrawFeeX18 * amount` per withdrawal, zero-ed out for every V2 withdrawal where the user sets `feeX18 = 0`. [4](#0-3) 

---

### Likelihood Explanation

**Likelihood: High.**

The attack requires no special privilege. Any user who wants to withdraw collateral can choose to use the `WithdrawCollateralV2` transaction type with `feeX18 = 0`. The signature is valid by construction. The sequencer processes whatever `feeX18` is embedded in the submitted bytes — the on-chain contract does not enforce that `feeX18` equals `currentFeeX18`. There is no off-chain enforcement mechanism that is verifiable on-chain. [5](#0-4) 

---

### Recommendation

Include `feeX18` in the EIP-712 digest for `WithdrawCollateralV2` so that the user's signature commits to a specific fee value. In `Verifier.sol`, add `signedTx.feeX18` to the `abi.encode` call for `WITHDRAW_COLLATERAL_V2_SIGNATURE`. This ensures the user cannot alter the fee after signing, and the sequencer cannot silently zero it out either.

Alternatively, remove the user-supplied `feeX18` field entirely and have `processTransactionImpl` always charge `currentFeeX18` for V2 withdrawals, matching the V1 behavior. [6](#0-5) 

---

### Proof of Concept

1. User constructs a `WithdrawCollateralV2` struct with their `sender`, `productId`, `amount`, `nonce`, `sendTo`, `appendix`.
2. User computes the EIP-712 digest over those fields (matching `computeDigest` in `Verifier.sol`) and signs it — `feeX18` is absent from this digest.
3. User wraps it in `SignedWithdrawCollateralV2` with `feeX18 = 0` and submits to the sequencer (or via `submitSlowModeTransaction`).
4. The sequencer includes the transaction in a batch submitted to `submitTransactionsChecked`, which calls `processTransactionImpl`.
5. `validateSignedTx` passes — the signature is valid because `feeX18` was never part of the signed data.
6. `require(signedTx.feeX18 >= 0)` passes (0 >= 0). `require(signedTx.feeX18 <= currentFeeX18)` passes (0 <= any non-negative fee).
7. `chargeFee(sender, 0, productId)` is called — zero fee is charged.
8. `clearinghouse.withdrawCollateral` executes the full withdrawal with no fee deducted. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/EndpointTx.sol (L134-141)
```text
    function chargeFee(
        bytes32 sender,
        int128 fee,
        uint32 productId
    ) internal {
        spotEngine.updateBalance(productId, sender, -fee);
        sequencerFee[productId] += fee;
    }
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

**File:** core/contracts/EndpointTx.sol (L437-465)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
```

**File:** core/contracts/Verifier.sol (L357-372)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transactionBody,
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(WITHDRAW_COLLATERAL_V2_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.productId,
                    signedTx.tx.amount,
                    signedTx.tx.nonce,
                    signedTx.tx.sendTo,
                    signedTx.tx.appendix
                )
            );
```

**File:** core/contracts/interfaces/IEndpoint.sol (L106-110)
```text
    struct SignedWithdrawCollateralV2 {
        WithdrawCollateralV2 tx;
        CompactSignature signature;
        int128 feeX18;
    }
```
