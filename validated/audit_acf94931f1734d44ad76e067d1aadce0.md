### Title
Unsafe ERC20 `approve` Without Prior Reset to Zero in `creditDeposit()` Bricks DDA for Non-Standard Tokens — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` without first resetting the allowance to zero. For non-standard ERC20 tokens like USDT that revert when setting a non-zero allowance on top of an existing non-zero allowance, any residual allowance from a prior call permanently bricks the deposit path for that token in the DDA contract.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, for each registered spot product, the contract approves the full token balance to the endpoint and then calls `depositCollateralWithReferral`: [1](#0-0) 

The `approve` call uses the raw `IIERC20Base.approve` interface with no prior reset to zero: [2](#0-1) 

The `ERC20Helper` library provides `safeTransfer` and `safeTransferFrom` but **no `safeApprove` helper**, so there is no safe wrapper available to `DirectDepositV1`: [3](#0-2) 

Critically, the developers **are aware** of the USDT approve pattern and correctly handle it in `ContractOwner.wrapVaultAsset()` by resetting to zero first: [4](#0-3) 

But this safe pattern was not applied in `creditDeposit()`.

A residual non-zero allowance can arise when `depositCollateralWithReferral` is called with `uint128(balance)` while the approve was issued for the full `uint256 balance`. If `balance > type(uint128).max`, the endpoint's `transferFrom` only consumes `uint128(balance)` worth of allowance, leaving `balance - uint128(balance)` as residual. On the next call to `creditDeposit()`, USDT's `approve` reverts because the existing allowance is non-zero. [5](#0-4) 

---

### Impact Explanation

Once a residual allowance exists for a USDT-like token, every subsequent call to `creditDeposit()` reverts at the `approve` line for that token. The DDA contract holds the user's tokens but cannot credit them to the subaccount. Funds remain stranded in the DDA until an admin calls `ContractOwner.withdrawFromDirectDepositV1()`. The deposit flow for that subaccount and token is permanently bricked without admin intervention.

---

### Likelihood Explanation

`creditDeposit()` has **no access control** — any unprivileged caller can invoke it: [6](#0-5) 

The uint128 overflow trigger requires a balance exceeding `2^128 - 1`, which is astronomically large for most tokens. However, the unsafe pattern also fires if the endpoint's `depositCollateralWithReferral` ever partially consumes the allowance (e.g., due to an internal deposit cap or minimum check). Given that USDT is a common collateral token in DeFi protocols and the developers already know to reset to zero in `wrapVaultAsset`, the omission here is a concrete gap. Likelihood is **medium** — it depends on whether USDT or a USDT-like token is listed as a spot product and whether any partial-consumption path exists in the endpoint.

---

### Recommendation

Apply the same pattern already used in `ContractOwner.wrapVaultAsset()`: reset the allowance to zero before setting the new value. Add a `safeApprove` helper to `ERC20Helper` and use it in `creditDeposit()`:

```solidity
// In ERC20Helper.sol
function safeApprove(IERC20Base self, address spender, uint256 amount) internal {
    (bool success, bytes memory data) = address(self).call(
        abi.encodeWithSelector(IERC20Base.approve.selector, spender, 0)
    );
    require(success && (data.length == 0 || abi.decode(data, (bool))), ERR_TRANSFER_FAILED);
    (success, data) = address(self).call(
        abi.encodeWithSelector(IERC20Base.approve.selector, spender, amount)
    );
    require(success && (data.length == 0 || abi.decode(data, (bool))), ERR_TRANSFER_FAILED);
}
```

Then in `DirectDepositV1.creditDeposit()`, replace:
```solidity
token.approve(address(endpoint), balance);
```
with a reset-then-set pattern, or use `safeIncreaseAllowance` from OpenZeppelin.

---

### Proof of Concept

1. A USDT-like token (reverts on non-zero → non-zero approve) is registered as a spot product in `SpotEngine`.
2. A DDA is created for subaccount `S` via `ContractOwner.createDirectDepositV1(S)`.
3. Attacker sends a balance exceeding `type(uint128).max` of the token to the DDA address (or any partial-consumption path in the endpoint leaves a residual allowance).
4. Anyone calls `DirectDepositV1(dda).creditDeposit()`:
   - `token.approve(endpoint, balance)` succeeds (allowance was 0).
   - `endpoint.depositCollateralWithReferral(S, productId, uint128(balance), "-1")` transfers only `uint128(balance)`, leaving residual allowance = `balance - uint128(balance)`.
5. More tokens arrive at the DDA.
6. Anyone calls `creditDeposit()` again:
   - `token.approve(endpoint, newBalance)` **reverts** — USDT sees existing non-zero allowance.
7. The DDA is permanently bricked for that token; funds accumulate but cannot be credited to subaccount `S` without admin withdrawal. [7](#0-6)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L11-11)
```text
    function approve(address spender, uint256 amount) external returns (bool);
```

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

**File:** core/contracts/libraries/ERC20Helper.sol (L8-21)
```text
library ERC20Helper {
    function safeTransfer(
        IERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/ContractOwner.sol (L530-531)
```text
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
```
