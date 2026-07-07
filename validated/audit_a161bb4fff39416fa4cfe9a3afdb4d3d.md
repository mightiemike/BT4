### Title
Fast Withdrawal Fee Charged to Submitter Instead of Deducted from Withdrawal Amount — (`File: core/contracts/BaseWithdrawPool.sol`)

### Summary

In `BaseWithdrawPool.submitFastWithdrawal`, when the resolved `sendTo` address differs from `msg.sender`, the fast-withdrawal fee is pulled directly from the submitter's wallet via `safeTransferFrom` rather than being deducted from the withdrawal amount. This mirrors the M-05 pattern: a payment obligation is placed on the wrong party. The recipient receives the full withdrawal amount while the submitter bears the fee cost, breaking the expected economic invariant that the fee is always borne by the withdrawal itself.

### Finding Description

`BaseWithdrawPool.submitFastWithdrawal` resolves the recipient address from the signed transaction and then branches on whether `sendTo == msg.sender`:

```solidity
if (sendTo == msg.sender) {
    require(transferAmount > uint128(fee), "Fee larger than balance");
    transferAmount -= uint128(fee);          // fee deducted from payout
} else {
    safeTransferFrom(token, msg.sender, uint128(fee));  // fee pulled from submitter
}
fees[productId] += fee;
handleWithdrawTransfer(token, sendTo, transferAmount);  // full amount sent to sendTo
``` [1](#0-0) 

When `sendTo != msg.sender` (e.g., a relayer executing a `WithdrawCollateralV2` transaction on behalf of a user who specified a custom `sendTo`):

- `safeTransferFrom(token, msg.sender, uint128(fee))` pulls the fee **from the submitter's own balance** into the pool.
- `handleWithdrawTransfer(token, sendTo, transferAmount)` sends the **full, undeducted** `transferAmount` to `sendTo`.

The pool's net outflow is identical in both branches (`transferAmount − fee`), but in the second branch the submitter absorbs the fee cost rather than it being deducted from the withdrawal amount. The recipient receives more than they are entitled to, and the submitter loses the fee amount with no compensating benefit.

The `resolveFastWithdrawal` function confirms that `sendTo != msg.sender` is a reachable, supported code path for `WithdrawCollateralV2` transactions where `sendTo` is explicitly set to a non-zero address other than the submitter:

```solidity
address resolvedSendTo = signedTx.tx.sendTo == address(0)
    ? address(uint160(bytes20(signedTx.tx.sender)))
    : signedTx.tx.sendTo;
return (signedTx.tx.productId, resolvedSendTo, signedTx.tx.amount);
``` [2](#0-1) 

### Impact Explanation

Any submitter (relayer, liquidity provider, or user executing a fast withdrawal on behalf of another address) who calls `submitFastWithdrawal` with a `WithdrawCollateralV2` transaction where `sendTo != msg.sender` will have the fee amount transferred out of their own token balance. The recipient receives the full withdrawal amount. The submitter suffers a direct, concrete token loss equal to `fastWithdrawalFeeAmount`, with no mechanism to recover it. The pool owner accumulates the fee via `fees[productId]`. [3](#0-2) 

### Likelihood Explanation

`submitFastWithdrawal` is a public, permissionless function. Any caller who holds a valid sequencer-signed `WithdrawCollateralV2` transaction where `sendTo` is set to a non-zero address other than themselves can trigger this path. Relayer services or smart-contract wallets executing withdrawals on behalf of users are the natural callers and will encounter this on every such execution.

### Recommendation

Remove the `sendTo == msg.sender` branch entirely and always deduct the fee from `transferAmount`:

```diff
-if (sendTo == msg.sender) {
-    require(transferAmount > uint128(fee), "Fee larger than balance");
-    transferAmount -= uint128(fee);
-} else {
-    safeTransferFrom(token, msg.sender, uint128(fee));
-}
+require(transferAmount > uint128(fee), "Fee larger than balance");
+transferAmount -= uint128(fee);
```

This ensures the fee is always borne by the withdrawal amount regardless of who calls the function.

### Proof of Concept

1. User signs a `WithdrawCollateralV2` transaction with `sendTo = alice` and `amount = 1000 USDC`. The sequencer co-signs it.
2. Relayer (`msg.sender = relayer`) calls `submitFastWithdrawal` with this transaction.
3. `resolveFastWithdrawal` returns `sendTo = alice`, `transferAmount = 1000`.
4. `sendTo != msg.sender`, so `safeTransferFrom(token, relayer, fee)` pulls `fee` (e.g., 5 USDC) from the relayer's balance.
5. `handleWithdrawTransfer(token, alice, 1000)` sends the full 1000 USDC to Alice.
6. Result: Alice receives 1000 USDC (5 USDC more than she should), the relayer loses 5 USDC, and the pool owner gains 5 USDC via `fees[productId]`. [4](#0-3)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L67-77)
```text
        if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            // V2 appendix is intentionally ignored until fast-withdraw features use it.
            address resolvedSendTo = signedTx.tx.sendTo == address(0)
                ? address(uint160(bytes20(signedTx.tx.sender)))
                : signedTx.tx.sendTo;
            return (signedTx.tx.productId, resolvedSendTo, signedTx.tx.amount);
        }
```

**File:** core/contracts/BaseWithdrawPool.sol (L81-114)
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
    }
```
