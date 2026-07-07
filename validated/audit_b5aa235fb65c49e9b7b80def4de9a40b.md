### Title
`_decimalMultiplier` Analog: Unguarded `18 - token.decimals()` Reverts for Tokens with Decimals > 18 — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.isWrapVaultAssetReady()` and `ContractOwner.isDirectDepositV1Ready()` both compute `10**(18 - token.decimals())` without first asserting `decimals <= 18`. Under Solidity `^0.8.0` checked arithmetic, if any registered product token (or ERC4626 vault token) reports `decimals() > 18`, the subtraction underflows and the call reverts unconditionally. The core deposit/withdrawal path in `Clearinghouse.sol` is protected by `require(decimals <= MAX_DECIMALS)`, but these two readiness-check entry points carry no such guard, making them permanently broken for any such token.

---

### Finding Description

`MAX_DECIMALS = 18` is defined as a protocol constant. [1](#0-0) 

The core `Clearinghouse.depositCollateral` and `withdrawCollateral` paths both enforce this bound before computing the multiplier: [2](#0-1) 

However, `ContractOwner.isWrapVaultAssetReady()` performs the same normalization arithmetic with no guard:

```solidity
wrappedBalance *= 10**(18 - IERC20Base(tokenAddr).decimals());
``` [3](#0-2) 

And `ContractOwner.isDirectDepositV1Ready()` iterates every registered product and applies the same unguarded subtraction:

```solidity
balance *= 10**(18 - token.decimals());
``` [4](#0-3) 

In `isDirectDepositV1Ready`, the loop covers **all** product IDs returned by `spotEngine.getProductIds()`. A single product whose token reports `decimals() > 18` causes the entire function to revert, blocking readiness checks for every product simultaneously. [5](#0-4) 

For `isWrapVaultAssetReady`, `tokenAddr` is the ERC4626 vault token, not the underlying asset. ERC4626 implementations may add decimal offsets (e.g., OpenZeppelin's `ERC4626` adds `_decimalsOffset()` to the underlying asset's decimals), making vault token decimals > 18 a realistic scenario even when the underlying asset has ≤ 18 decimals. [6](#0-5) 

---

### Impact Explanation

Both functions are `external` and callable by any user or off-chain bot. They serve as the readiness gate for the `DirectDepositV1` flow — off-chain infrastructure calls them to decide when to trigger `creditDepositV1`. If either function reverts permanently, the direct deposit pipeline is broken: users who have pre-funded a `DirectDepositV1` address cannot have their deposits credited, effectively locking their tokens in the deposit contract with no protocol-side path to process them.

---

### Likelihood Explanation

- Any ERC4626 vault token registered as a product whose `decimals()` exceeds 18 triggers the bug. OpenZeppelin's default `ERC4626` adds a `_decimalsOffset()` (default 0, but overridable), and third-party vaults routinely use higher precision.
- No privileged caller is required to trigger the revert — any external caller of `isWrapVaultAssetReady` or `isDirectDepositV1Ready` hits it.
- Product registration does not enforce `decimals <= MAX_DECIMALS`; the guard exists only at deposit execution time, leaving the readiness-check path unprotected.

---

### Recommendation

Mirror the guard already present in `Clearinghouse.depositCollateral`. In both functions, assert `decimals <= 18` before computing the exponent, and handle the `decimals > 18` case by dividing instead of multiplying:

```solidity
uint8 dec = IERC20Base(tokenAddr).decimals();
if (dec <= 18) {
    balance *= 10**(18 - dec);
} else {
    balance /= 10**(dec - 18);
}
```

Apply the same fix to both `isWrapVaultAssetReady` (line 552) and `isDirectDepositV1Ready` (line 579). [3](#0-2) [7](#0-6) 

---

### Proof of Concept

1. Register a spot product whose token is an ERC4626 vault with `decimals() == 20` (e.g., a vault wrapping a 14-decimal asset with a 6-decimal offset).
2. Call `ContractOwner.isWrapVaultAssetReady(recipient, productId, false)`.
3. Execution reaches line 552: `10**(18 - 20)` → `10**(uint8(18) - uint8(20))` → underflow panic under Solidity 0.8 checked arithmetic → revert.
4. Call `ContractOwner.isDirectDepositV1Ready(recipient, false)`.
5. The loop reaches the same product at line 579 and reverts identically, breaking readiness checks for **all** products in the same call. [8](#0-7) [4](#0-3)

### Citations

**File:** core/contracts/common/Constants.sol (L19-19)
```text
uint8 constant MAX_DECIMALS = 18;
```

**File:** core/contracts/Clearinghouse.sol (L203-204)
```text
        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
```

**File:** core/contracts/ContractOwner.sol (L536-562)
```text
    function isWrapVaultAssetReady(
        address recipient,
        uint32 productId,
        bool isFirstDeposit
    ) external returns (bool) {
        address tokenAddr = spotEngine.getToken(productId);
        require(tokenAddr != address(0));

        address assetTokenAddr = IERC4626Base(tokenAddr).asset();
        require(assetTokenAddr != address(0));

        uint256 assetBalance = IERC20Base(assetTokenAddr).balanceOf(recipient);
        uint256 wrappedBalance = IERC4626Base(tokenAddr).previewDeposit(
            assetBalance
        );
        if (wrappedBalance != 0) {
            wrappedBalance *= 10**(18 - IERC20Base(tokenAddr).decimals());

            return
                _isDepositAmountReady(
                    productId,
                    wrappedBalance,
                    isFirstDeposit
                );
        }
        return false;
    }
```

**File:** core/contracts/ContractOwner.sol (L568-584)
```text
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0));

            IERC20Base token = IERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(recipient);
            if (tokenAddr == wrappedNative) {
                balance += recipient.balance;
            }
            balance *= 10**(18 - token.decimals());
            if (_isDepositAmountReady(productId, balance, isFirstDeposit)) {
                return true;
            }
        }
        return false;
```
