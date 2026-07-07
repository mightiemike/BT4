### Title
Intermediate `int128 × int128` Multiplication Overflow in `withdrawCollateral` Permanently Freezes User Funds — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.withdrawCollateral` performs the decimal-normalization step `int128(amount) * int128(multiplier)` entirely within `int128` arithmetic. For tokens with fewer than 18 decimals, `multiplier` can be as large as `10^18`, and the product of two `int128` values can silently overflow under Solidity ≥ 0.8.0, causing the transaction to revert. Because the overflow occurs after the token transfer has already been initiated, the entire transaction reverts and the user's collateral is permanently inaccessible.

---

### Finding Description

In `withdrawCollateral`, after the `INT128_MAX` guard on `amount`, the code computes the normalized balance delta:

```solidity
int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
int128 amountRealized = -int128(amount) * int128(multiplier);
``` [1](#0-0) 

The guard at line 399 only ensures `amount` fits in `int128`:

```solidity
require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
``` [2](#0-1) 

It does **not** ensure the *product* `int128(amount) * int128(multiplier)` fits in `int128`. Both operands are cast to `int128` before the multiplication, so the intermediate result is computed in 128-bit signed arithmetic. `int128` max is `2^127 − 1 ≈ 1.70 × 10^38`.

For a token with `decimals = 0`:
- `multiplier = 10^18`
- Safe maximum `amount` = `INT128_MAX / 10^18 ≈ 1.70 × 10^20` raw units

Any deposit of more than ~170 billion units of a zero-decimal token will cause the multiplication to overflow and revert every subsequent withdrawal attempt.

The identical pattern appears in two additional reachable paths:

- `Clearinghouse.checkMinDeposit` line 708: `int128 amountRealized = int128(multiplier) * int128(amount);` [3](#0-2) 

- `BaseWithdrawPool.fastWithdrawalFeeAmount` line 142: `int128 amountX18 = int128(amount) * int128(multiplier);` — with **no** `INT128_MAX` guard at all. [4](#0-3) 

`MAX_DECIMALS` is fixed at 18: [5](#0-4) 

---

### Impact Explanation

A user who deposits a large amount of a low-decimal token (e.g., a zero-decimal governance token) will have their collateral permanently frozen. Every call to `withdrawCollateral` will revert at the multiplication step, making the deposited funds irrecoverable through normal protocol flows. The impact is identical to the VTVL analog: user funds are locked with no on-chain remedy.

---

### Likelihood Explanation

The trigger requires a token registered in the protocol with `decimals < 18` and a user balance large enough to exceed `INT128_MAX / multiplier`. For `decimals = 0` the threshold is ~`1.70 × 10^20` raw units; for `decimals = 6` it is ~`1.70 × 10^26` raw units. Zero-decimal tokens are uncommon but not impossible in a permissionless DEX. The `BaseWithdrawPool.fastWithdrawalFeeAmount` variant has no guard at all, making it reachable with any sufficiently large `uint128` amount on a low-decimal token. Likelihood is **medium-low** but non-zero, and the impact when triggered is total loss of withdrawal access.

---

### Recommendation

Widen the intermediate multiplication to `int256` before casting back to `int128`, mirroring the fix recommended in the VTVL report:

```solidity
// Clearinghouse.sol withdrawCollateral (line 411)
int128 amountRealized = -int128(int256(amount) * multiplier);

// Clearinghouse.sol checkMinDeposit (line 708)
int128 amountRealized = int128(int256(multiplier) * int256(amount));

// BaseWithdrawPool.sol fastWithdrawalFeeAmount (line 142)
int128 amountX18 = int128(int256(amount) * multiplier);
```

Additionally, add a tighter guard: `require(amount <= uint128(type(int128).max / int128(multiplier)))` before each multiplication.

---

### Proof of Concept

1. Register a spot product backed by a zero-decimal ERC-20 token (`decimals() == 0`).
2. Deposit `2 × 10^20` raw units of that token via `Endpoint.depositCollateral`. The deposit path does not perform the same multiplication, so it succeeds.
3. Call `Endpoint` → `Clearinghouse.withdrawCollateral` for the same amount.
4. At line 411, `int128(2e20) * int128(1e18)` = `2 × 10^38 > INT128_MAX ≈ 1.70 × 10^38`. Solidity 0.8.x checked arithmetic reverts with panic `0x11`.
5. The user's `2 × 10^20` tokens are permanently frozen; no withdrawal amount above the safe threshold will ever succeed. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

**File:** core/contracts/Clearinghouse.sol (L707-708)
```text
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(multiplier) * int128(amount);
```

**File:** core/contracts/BaseWithdrawPool.sol (L134-149)
```text
    function fastWithdrawalFeeAmount(
        IERC20Base token,
        uint32 productId,
        uint128 amount
    ) public view returns (int128) {
        uint8 decimals = token.decimals();
        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
        int128 amountX18 = int128(amount) * int128(multiplier);

        int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
        int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;

        int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
        return feeX18 / int128(multiplier);
    }
```

**File:** core/contracts/common/Constants.sol (L19-19)
```text
uint8 constant MAX_DECIMALS = 18;
```
