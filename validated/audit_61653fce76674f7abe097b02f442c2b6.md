### Title
Unsigned `feeX18` Field in `SignedWithdrawCollateralV2` Allows Withdrawal Fee Bypass via Slow Mode — (File: `core/contracts/EndpointTx.sol`, `core/contracts/Verifier.sol`)

---

### Summary

The `SignedWithdrawCollateralV2` struct contains a `feeX18` field that is **not covered by the user's EIP-712 signature**. The signature only commits to the inner `WithdrawCollateralV2` struct. Any user submitting a `WithdrawCollateralV2` transaction via the slow mode path can set `feeX18 = 0`, pass all on-chain checks, and withdraw collateral without paying the configured withdrawal fee.

---

### Finding Description

`SignedWithdrawCollateralV2` is defined as:

```solidity
struct SignedWithdrawCollateralV2 {
    WithdrawCollateralV2 tx;
    CompactSignature signature;
    int128 feeX18;   // ← outer field, NOT part of the signed struct
}
``` [1](#0-0) 

The EIP-712 digest computed in `Verifier.computeDigest` covers only the inner `WithdrawCollateralV2` fields (`sender`, `productId`, `amount`, `nonce`, `sendTo`, `appendix`):

```solidity
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
``` [2](#0-1) 

The type string confirms `feeX18` is absent from the signed domain:

```
"WithdrawCollateralV2(bytes32 sender,uint32 productId,uint128 amount,uint64 nonce,address sendTo,uint128 appendix)"
``` [3](#0-2) 

In `EndpointTx.processTransactionImpl`, after signature validation, `feeX18` is read directly from the decoded struct and used to charge the fee:

```solidity
require(signedTx.feeX18 >= 0);
require(signedTx.feeX18 <= currentFeeX18);
chargeFee(signedTx.tx.sender, signedTx.feeX18, signedTx.tx.productId);
``` [4](#0-3) 

Both `require` checks pass when `feeX18 = 0` (zero is `>= 0` and `<= currentFeeX18`). The signature remains valid because `feeX18` is not part of the digest. The fee charged is therefore zero.

The slow mode submission path stores the raw transaction bytes as-is and later executes them through `processTransactionImpl` without any re-encoding or sequencer override of `feeX18`:

```solidity
slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY,
    sender: sender,
    tx: transaction   // ← attacker-controlled bytes, including feeX18
});
``` [5](#0-4) 

`WithdrawCollateralV2` is not in the owner-only slow mode list, so any user can submit it: [6](#0-5) 

---

### Impact Explanation

A user can withdraw any amount of collateral via `WithdrawCollateralV2` through the slow mode path and pay **zero withdrawal fee** by setting `feeX18 = 0` in the submitted bytes. The protocol's fee revenue from `WithdrawCollateralV2` withdrawals is entirely bypassable by any unprivileged user. The corrupted state delta is `sequencerFee[productId]` — it receives 0 instead of the configured `withdrawFeeX18` amount per bypassed withdrawal. [7](#0-6) 

---

### Likelihood Explanation

**Medium.** The slow mode path imposes a 3-day delay (`SLOW_MODE_TX_DELAY`), which is a friction cost but not a security barrier. Any user who wants to avoid the withdrawal fee has a direct, permissionless path to do so. No privileged access, leaked keys, or social engineering is required. The user simply crafts the outer `SignedWithdrawCollateralV2` struct with `feeX18 = 0` before signing the inner struct and submitting.

---

### Recommendation

Include `feeX18` in the EIP-712 digest for `WithdrawCollateralV2`. Update `WITHDRAW_COLLATERAL_V2_SIGNATURE` and `computeDigest` to commit to `feeX18`:

```
"WithdrawCollateralV2(bytes32 sender,uint32 productId,uint128 amount,uint64 nonce,address sendTo,uint128 appendix,int128 feeX18)"
```

This ensures the user explicitly signs the fee they agree to pay, making it impossible to substitute a lower value after signing — directly analogous to the BatchTrade fix of requiring all trade parameters to be signed by a trusted key.

---

### Proof of Concept

1. User constructs a `WithdrawCollateralV2` inner struct: `{sender, productId, amount, nonce, sendTo, appendix}`.
2. User signs the EIP-712 digest of that inner struct (which does not include `feeX18`).
3. User wraps it in `SignedWithdrawCollateralV2` with `feeX18 = 0` and prepends the `WithdrawCollateralV2` transaction type byte.
4. User calls `submitSlowModeTransaction(transaction)` on `Endpoint`.
5. After `SLOW_MODE_TX_DELAY`, `processTransactionImpl` is called with the stored bytes.
6. `validateSignedTx` succeeds — the signature is valid because `feeX18` was never part of the digest.
7. `require(signedTx.feeX18 >= 0)` → passes (0 ≥ 0). `require(signedTx.feeX18 <= currentFeeX18)` → passes (0 ≤ fee).
8. `chargeFee(sender, 0, productId)` → zero fee charged.
9. `clearinghouse.withdrawCollateral(...)` executes the full withdrawal with no fee deducted. [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L106-110)
```text
    struct SignedWithdrawCollateralV2 {
        WithdrawCollateralV2 tx;
        CompactSignature signature;
        int128 feeX18;
    }
```

**File:** core/contracts/Verifier.sol (L24-25)
```text
    string internal constant WITHDRAW_COLLATERAL_V2_SIGNATURE =
        "WithdrawCollateralV2(bytes32 sender,uint32 productId,uint128 amount,uint64 nonce,address sendTo,uint128 appendix)";
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

**File:** core/contracts/EndpointTx.sol (L361-372)
```text
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointTx.sol (L374-380)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
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
