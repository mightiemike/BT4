### Title
Residual Token Allowance Not Cleared After Silent Skip in `creditDeposit` - (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` approves a token amount to the `endpoint` contract before calling `depositCollateralWithReferral`, but `depositCollateralWithReferral` can silently return early without consuming the allowance. This leaves a non-zero residual approval on the token. For tokens like USDT that require the allowance to be zero before a new non-zero approval is set, the next call to `creditDeposit()` will revert on the `approve` call, permanently bricking the deposit path for that token.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, for each product token with a non-zero balance, the function unconditionally sets a token approval before calling the endpoint:

```solidity
// DirectDepositV1.sol lines 90-98
uint256 balance = token.balanceOf(address(this));
if (balance != 0) {
    token.approve(address(endpoint), balance);   // approval set here
    endpoint.depositCollateralWithReferral(
        subaccount,
        productId,
        uint128(balance),
        "-1"
    );
}
``` [1](#0-0) 

Inside `Endpoint.depositCollateralWithReferral`, there is an explicit early-return path that skips the actual `transferFrom` entirely:

```solidity
// Endpoint.sol lines 137-142
if (!isValidDepositAmount(subaccount, productId, amount)) {
    // we cannot revert here, otherwise direct deposit could be blocked...
    // we can just skip the deposit and continue with the next asset.
    return;
}
handleDepositTransfer(...);  // transferFrom only reached if above check passes
``` [2](#0-1) 

When the early return fires, `handleDepositTransfer` (and thus `transferFrom`) is never called. The full `balance` approval granted to `endpoint` remains unconsumed. On the next invocation of `creditDeposit()`, the code attempts `token.approve(address(endpoint), newBalance)` with a non-zero `newBalance` while the existing allowance is still non-zero. USDT and similar tokens revert on this pattern, permanently blocking `creditDeposit()` for that token.

`creditDeposit()` carries no access control modifier — it is callable by any external address. [3](#0-2) 

---

### Impact Explanation

Once the residual allowance is stranded, every subsequent call to `creditDeposit()` for the affected USDT-like token reverts at the `approve` line. Funds deposited into the `DirectDepositV1` contract for that token can no longer be forwarded to the protocol via the normal deposit path. The only recovery is the owner-gated `withdraw()` function, but this requires manual intervention and breaks the intended automated deposit flow. The corrupted state is: `allowance(DirectDepositV1, endpoint) > 0` while `balance > 0`, causing a permanent revert loop for USDT.

---

### Likelihood Explanation

The trigger condition — `isValidDepositAmount` returning false — is realistic and documented in the code itself (the comment explicitly acknowledges this path). It fires when a deposit amount is below `MIN_DEPOSIT_AMOUNT` for existing subaccounts or `MIN_FIRST_DEPOSIT_AMOUNT` for new ones. [4](#0-3) 

Any scenario where a small dust amount of a USDT-like token lands in the `DirectDepositV1` contract (e.g., partial transfer, rounding, or deliberate griefing by sending a tiny amount) is sufficient to trigger the residual allowance. Since `creditDeposit()` is permissionless, an attacker can call it at the right moment to lock the deposit path.

---

### Recommendation

Clear the residual allowance after the `depositCollateralWithReferral` call, or reset it to zero before setting a new non-zero approval:

```solidity
if (balance != 0) {
    token.approve(address(endpoint), 0);          // clear residual first
    token.approve(address(endpoint), balance);
    endpoint.depositCollateralWithReferral(
        subaccount,
        productId,
        uint128(balance),
        "-1"
    );
    token.approve(address(endpoint), 0);          // clear after, in case of early return
}
```

Alternatively, use OpenZeppelin's `SafeERC20.forceApprove` which handles this pattern safely.

---

### Proof of Concept

1. Deploy `DirectDepositV1` with USDT as a supported product token.
2. Send a dust amount of USDT (e.g., 1 wei) to the `DirectDepositV1` contract — below `MIN_DEPOSIT_AMOUNT`.
3. Call `creditDeposit()` (permissionless). The function sets `USDT.approve(endpoint, 1)`, then `depositCollateralWithReferral` returns early at line 141 without consuming the allowance. Residual allowance = 1.
4. Send a normal deposit amount of USDT to the contract.
5. Call `creditDeposit()` again. The function attempts `USDT.approve(endpoint, normalAmount)` with `normalAmount > 0` while existing allowance is `1 > 0`. USDT reverts. The deposit is permanently bricked. [3](#0-2) [5](#0-4)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L83-101)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
    }
```

**File:** core/contracts/Endpoint.sol (L90-101)
```text
    function isValidDepositAmount(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount
    ) internal returns (bool) {
        int256 minDepositAmount = MIN_DEPOSIT_AMOUNT;
        if (subaccount != X_ACCOUNT && (subaccountIds[subaccount] == 0)) {
            minDepositAmount = MIN_FIRST_DEPOSIT_AMOUNT;
        }
        return
            clearinghouse.checkMinDeposit(productId, amount, minDepositAmount);
    }
```

**File:** core/contracts/Endpoint.sol (L137-148)
```text
        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
```
