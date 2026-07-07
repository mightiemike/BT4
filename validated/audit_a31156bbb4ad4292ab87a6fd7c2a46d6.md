### Title
Unconsumed ERC20 Approval in `DirectDepositV1.creditDeposit()` When Deposit Is Silently Skipped - (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` sets a token approval to the `Endpoint` contract before calling `depositCollateralWithReferral`. However, `depositCollateralWithReferral` deliberately returns early — without consuming the approval — when the deposit amount is below the minimum threshold. This leaves a residual, unconsumed allowance on the `Endpoint`. For tokens with USDT-like approval semantics (requiring allowance to be reset to zero before a new non-zero approval), this permanently blocks future `creditDeposit()` calls for that token.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, for each product token held by the contract, the code unconditionally approves the full `balance` to the `Endpoint` and then calls `depositCollateralWithReferral`:

```solidity
// core/contracts/DirectDepositV1.sol, lines 91-99
token.approve(address(endpoint), balance);
endpoint.depositCollateralWithReferral(
    subaccount,
    productId,
    uint128(balance),
    "-1"
);
```

However, `Endpoint.depositCollateralWithReferral` contains an explicit early-return path that skips the actual `transferFrom` when the deposit amount is below the minimum:

```solidity
// core/contracts/Endpoint.sol, lines 137-142
if (!isValidDepositAmount(subaccount, productId, amount)) {
    // we cannot revert here, otherwise direct deposit could be blocked...
    // we can just skip the deposit and continue with the next asset.
    return;
}

handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,
    uint256(amount)
);
```

When this early return is taken, `handleDepositTransfer` is never called, so no tokens are pulled from `DirectDepositV1`. The approval of `balance` tokens to `Endpoint` remains fully unconsumed.

On the next invocation of `creditDeposit()` for the same token, `token.approve(address(endpoint), balance)` is called again while a non-zero allowance already exists. For tokens like USDT that revert when `approve` is called with a non-zero current allowance, this permanently bricks the `creditDeposit()` function for that token.

Additionally, there is a secondary truncation issue: `balance` is a `uint256` but is cast to `uint128` when passed to `depositCollateralWithReferral`. If `balance > type(uint128).max`, the endpoint only pulls `uint128(balance)` tokens while the approval covers the full `uint256 balance`, leaving a residual approval of `balance - uint128(balance)`.

---

### Impact Explanation

For any collateral token with USDT-like approval semantics (non-zero-to-non-zero `approve` reverts), a single sub-minimum deposit event permanently blocks `creditDeposit()` for that token in the affected `DirectDepositV1` instance. User funds already held in the DDA contract become undepositable through the normal credit path, requiring owner intervention via `withdraw()` to recover them. This is a token-transfer integration bug with a concrete asset-locking impact.

---

### Likelihood Explanation

`creditDeposit()` has no access control — it is callable by any external address. An attacker can deliberately send a dust amount of a USDT-like collateral token to a victim's `DirectDepositV1` address, then call `creditDeposit()` to trigger the approval-without-consumption path. This sets a non-zero allowance. All subsequent legitimate `creditDeposit()` calls for that token will revert on the `approve` call, permanently blocking deposits. The attack requires only a dust token transfer and a public function call.

---

### Recommendation

Reset the approval to zero after `depositCollateralWithReferral` returns, or check whether the approval was consumed and reset it if not. The safest pattern is:

```solidity
token.approve(address(endpoint), balance);
endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
// Reset residual approval to prevent accumulation
token.approve(address(endpoint), 0);
```

Alternatively, check the remaining allowance after the call and reset it to zero if it is non-zero.

---

### Proof of Concept

1. A `DirectDepositV1` instance exists for a subaccount using a USDT-like collateral token.
2. Attacker sends 1 wei of the token to the DDA address (below `MIN_DEPOSIT_AMOUNT`).
3. Attacker calls `creditDeposit()` (no access control).
4. `token.approve(address(endpoint), 1)` executes — allowance is now 1.
5. `endpoint.depositCollateralWithReferral(...)` is called; `isValidDepositAmount` returns `false`; function returns early without calling `handleDepositTransfer`. Allowance remains 1.
6. Later, the legitimate user's balance grows above the minimum. `ContractOwner.creditDepositV1()` is called.
7. `creditDeposit()` runs again; `token.approve(address(endpoint), largeBalance)` is called while allowance is still 1 — USDT reverts.
8. The DDA is permanently blocked from depositing that token. [1](#0-0) [2](#0-1) [3](#0-2)

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
