### Title
Residual Allowance From Skipped Deposit Permanently Bricks `creditDeposit()` for Non-Zero-to-Non-Zero Restricted Tokens — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` without first resetting the allowance to zero. Because `Endpoint.depositCollateralWithReferral()` contains an early-return path that silently skips the transfer (and therefore never consumes the allowance), a residual non-zero allowance can persist. On the next invocation of `creditDeposit()`, tokens such as USDT that revert when `approve` is called with a non-zero existing allowance will cause the entire function to revert, permanently bricking the deposit path for that token.

---

### Finding Description

`DirectDepositV1.creditDeposit()` iterates over all registered product IDs and, for each product whose token balance is non-zero, executes:

```solidity
token.approve(address(endpoint), balance);
endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
``` [1](#0-0) 

`Endpoint.depositCollateralWithReferral()` contains an explicit early-return guard:

```solidity
if (!isValidDepositAmount(subaccount, productId, amount)) {
    return;
}
``` [2](#0-1) 

When this guard fires, `handleDepositTransfer` is never reached, so the `transferFrom` that would consume the allowance is never executed. The allowance set by `token.approve(address(endpoint), balance)` remains intact on the token contract. [3](#0-2) 

On the next call to `creditDeposit()`, the code again calls `token.approve(address(endpoint), newBalance)` with a non-zero `newBalance` while the existing allowance is already non-zero. Tokens like USDT implement a guard that reverts this exact pattern (non-zero → non-zero `approve`), causing the entire `creditDeposit()` call to revert. [4](#0-3) 

By contrast, `ContractOwner.wrapVaultAsset()` correctly uses the reset-to-zero pattern before setting a new allowance:

```solidity
assetToken.approve(tokenAddr, 0);
assetToken.approve(tokenAddr, assetBalance);
``` [5](#0-4) 

`DirectDepositV1.creditDeposit()` lacks this reset step entirely. [6](#0-5) 

---

### Impact Explanation

Once a residual allowance is established, every subsequent call to `creditDeposit()` for that token reverts. User funds deposited into the DDA in USDT (or any token with the same restriction) can no longer be forwarded to the endpoint via the normal deposit path. The only recovery path is the `onlyOwner` `withdraw()` function, which returns tokens to the owner rather than crediting the intended subaccount. The deposit flow for the affected token is permanently broken until the contract is redeployed or the owner manually intervenes. [7](#0-6) 

---

### Likelihood Explanation

`creditDeposit()` has no access control — it is callable by any address. [8](#0-7) 

An attacker can deliberately trigger the condition by:
1. Sending a dust amount of USDT (below `MIN_DEPOSIT_AMOUNT` or `MIN_FIRST_DEPOSIT_AMOUNT`) to the DDA address.
2. Calling `creditDeposit()` — the approve executes and sets a non-zero allowance, but `depositCollateralWithReferral` returns early, leaving the allowance unconsumed.
3. The DDA is now permanently bricked for USDT deposits.

This can also occur non-maliciously if a user sends a sub-minimum deposit before the subaccount has been registered (triggering the `MIN_FIRST_DEPOSIT_AMOUNT` path). [9](#0-8) 

---

### Recommendation

Apply the reset-to-zero pattern before setting a new allowance, consistent with the pattern already used in `ContractOwner.wrapVaultAsset()`:

```solidity
token.approve(address(endpoint), 0);
token.approve(address(endpoint), balance);
```

Alternatively, use `increaseAllowance` if the token supports it, or check and reset the existing allowance only when it is non-zero before setting the new value.

---

### Proof of Concept

1. Deploy a DDA for a subaccount that has never deposited (so `MIN_FIRST_DEPOSIT_AMOUNT` applies, e.g. `1e18`).
2. Transfer `1` wei of USDT to the DDA.
3. Call `creditDeposit()`:
   - `token.approve(endpoint, 1)` succeeds — USDT allowance is now `1`.
   - `depositCollateralWithReferral` returns early (amount `1` < `MIN_FIRST_DEPOSIT_AMOUNT`).
   - Allowance `1` remains on USDT.
4. Transfer `1e18` USDT to the DDA (a legitimate deposit).
5. Call `creditDeposit()`:
   - `token.approve(endpoint, 1e18)` — USDT reverts: existing allowance is `1` (non-zero), new value is `1e18` (non-zero).
   - The entire `creditDeposit()` call reverts.
6. The `1e18` USDT is permanently stuck in the DDA, recoverable only by the owner via `withdraw()` (which sends to the owner, not the subaccount). [4](#0-3) [10](#0-9)

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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```

**File:** core/contracts/Endpoint.sol (L95-101)
```text
        int256 minDepositAmount = MIN_DEPOSIT_AMOUNT;
        if (subaccount != X_ACCOUNT && (subaccountIds[subaccount] == 0)) {
            minDepositAmount = MIN_FIRST_DEPOSIT_AMOUNT;
        }
        return
            clearinghouse.checkMinDeposit(productId, amount, minDepositAmount);
    }
```

**File:** core/contracts/Endpoint.sol (L123-141)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
```

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```

**File:** core/contracts/ContractOwner.sol (L530-531)
```text
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
```
