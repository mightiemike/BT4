### Title
CEI Violation in `withdrawCollateral` Enables Double-Withdrawal of Collateral via Slow-Mode Reentrancy — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.withdrawCollateral` transfers tokens to `withdrawPool` and calls `withdrawPool.submitWithdrawal` **before** deducting the subaccount balance in `spotEngine.updateBalance`. This violates the check-effect-interaction pattern. Because `executeSlowModeTransaction` on the Endpoint is callable by any unprivileged address, an attacker who receives a callback during `handleWithdrawTransfer` can re-enter the Endpoint to process a second queued withdrawal before the first withdrawal's balance deduction has occurred, enabling double-withdrawal of collateral.

---

### Finding Description

`withdrawCollateral` in `Clearinghouse.sol` executes in this order:

```
handleWithdrawTransfer(token, sendTo, amount, idx);   // ← external calls first
...
spotEngine.updateBalance(productId, sender, amountRealized);  // ← balance deducted after
spotEngine.assertUtilization(productId);
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
``` [1](#0-0) 

`handleWithdrawTransfer` makes two external calls before any state is written:

```solidity
function handleWithdrawTransfer(...) internal virtual {
    token.safeTransfer(withdrawPool, uint256(amount));
    BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
}
``` [2](#0-1) 

If either call triggers a callback to an attacker-controlled address — for example, via an ERC-777 `tokensReceived` hook on `withdrawPool`, or if `BaseWithdrawPool.submitWithdrawal` notifies `to` — the attacker can re-enter the system. Because `withdrawCollateral` is `onlyEndpoint`, the re-entry path is through `Endpoint.executeSlowModeTransaction`, which is **callable by any address with no access restriction**:

```solidity
function executeSlowModeTransaction() external {
    SlowModeConfig memory _slowModeConfig = slowModeConfig;
    _executeSlowModeTransaction(_slowModeConfig, false);
    nSubmissions += 1;
    slowModeConfig = _slowModeConfig;
}
``` [3](#0-2) 

There is no `nonReentrant` guard on `withdrawCollateral`, `handleWithdrawTransfer`, or `executeSlowModeTransaction`.

---

### Impact Explanation

An attacker with balance `X` in product A and sufficient collateral `Y` in product B can:

1. Queue two slow-mode `WithdrawCollateral` transactions (W1 and W2), each for amount `X` from product A.
2. Trigger execution of W1 via `executeSlowModeTransaction`.
3. During `handleWithdrawTransfer` for W1 (tokens already sent to `withdrawPool`, balance not yet deducted), receive a callback and call `executeSlowModeTransaction` again.
4. W2 executes: `spotEngine.updateBalance` deducts `X` from product A (balance → 0). Health check passes because collateral `Y` in product B covers it.
5. W2 completes. Execution returns to W1.
6. W1 continues: `spotEngine.updateBalance` deducts `X` again (balance → `-X`). Health check passes if `Y` still covers `-X`.
7. Both withdrawals succeed. Attacker received `2X` tokens while only depositing `X`.

The corrupted state delta is `spotEngine` balance for product A going to `-X` while the attacker has received `2X` worth of tokens from `withdrawPool`. This is a direct collateral theft from the protocol's token reserves.

---

### Likelihood Explanation

The attack requires:
- A token registered in the protocol that supports transfer callbacks (ERC-777, or any non-standard ERC-20 with hooks), **or** `BaseWithdrawPool.submitWithdrawal` making a call to the `to` address.
- The attacker to hold sufficient cross-product collateral to pass the post-double-deduction health check.
- Two slow-mode withdrawal transactions queued and past their time delay.

The slow-mode path (`executeSlowModeTransaction`) is permissionless and requires no sequencer involvement, making it reachable by any unprivileged caller. ERC-777 tokens are a realistic integration scenario for a DEX. Likelihood is **medium**.

---

### Recommendation

Apply the check-effect-interaction pattern in `withdrawCollateral`: deduct the subaccount balance and assert health **before** calling `handleWithdrawTransfer`.

```solidity
// Deduct balance and check health FIRST
int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
int128 amountRealized = -int128(amount) * int128(multiplier);
spotEngine.updateBalance(productId, sender, amountRealized);
spotEngine.assertUtilization(productId);
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);

// THEN perform the external transfer
handleWithdrawTransfer(token, sendTo, amount, idx);
```

Additionally, add a `nonReentrant` guard (OpenZeppelin `ReentrancyGuard`) to `withdrawCollateral` and `executeSlowModeTransaction` as defense-in-depth.

---

### Proof of Concept

**Setup:**
- Token A is an ERC-777 token registered as a spot product.
- Attacker contract holds `X` units of Token A (product A) and `Y` units of Token B (product B), where `Y` is sufficient to maintain health at `-X` in product A.
- Attacker queues W1 and W2 via `submitSlowModeTransaction` (both `WithdrawCollateral` for `X` of product A). Time delay passes.

**Execution:**
```
Attacker calls executeSlowModeTransaction()  // processes W1
  → Endpoint._executeSlowModeTransaction(W1)
    → Clearinghouse.withdrawCollateral(sender, productA, X, attackerContract, idx)
      → handleWithdrawTransfer(tokenA, attackerContract, X, idx)
        → tokenA.safeTransfer(withdrawPool, X)
          → [ERC-777] withdrawPool.tokensReceived() called
            → [if withdrawPool notifies `to`] attackerContract.callback()
              → Attacker calls executeSlowModeTransaction()  // processes W2
                → Clearinghouse.withdrawCollateral(sender, productA, X, attackerContract, idx+1)
                  → handleWithdrawTransfer(tokenA, attackerContract, X, idx+1)  // 2nd transfer
                  → spotEngine.updateBalance(productA, sender, -X)  // balance = 0
                  → getHealth() >= 0  ✓ (Y covers it)
                  // W2 COMPLETES SUCCESSFULLY
        → withdrawPool.submitWithdrawal(tokenA, attackerContract, X, idx)
      // returns to W1
      → spotEngine.updateBalance(productA, sender, -X)  // balance = -X
      → getHealth() >= 0  ✓ (Y still covers -X)
      // W1 COMPLETES SUCCESSFULLY

// Result: 2X tokens sent to withdrawPool for attacker, balance = -X
``` [4](#0-3) [3](#0-2)

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

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```
