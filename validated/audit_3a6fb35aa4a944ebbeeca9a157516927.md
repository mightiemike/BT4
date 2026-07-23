Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted rather than the actual user. If the pool admin allowlists the router to enable legitimate users to trade, every unprivileged address on-chain gains unrestricted swap access to the curated pool.

## Finding Description

**Step 1 — Router calls `pool.swap()` with itself as `msg.sender`:**

In `MetricOmmSimpleRouter.exactInputSingle`, the real user's address is stored only in transient callback context for payment purposes, then the router calls the pool directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(params.recipient, params.zeroForOne, ...);
``` [1](#0-0) 

The pool receives this call with `msg.sender = address(router)`.

**Step 2 — Pool passes `msg.sender` (router) as `sender` to `_beforeSwap`:**

```solidity
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
``` [2](#0-1) 

**Step 3 — `ExtensionCalling` forwards the router address as `sender` to the extension:**

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, zeroForOne, ...))
``` [3](#0-2) 

**Step 4 — `SwapAllowlistExtension` checks the router address, not the user:**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [4](#0-3) 

Here `msg.sender` is the pool and `sender` is the router. The check `allowedSwapper[pool][router]` is evaluated — the actual user's address is never consulted. The real payer stored in transient storage via `_setNextCallbackContext` is never exposed to or read by the extension.

The same wrong-actor binding applies to `exactInput` (all hops call the pool with `msg.sender = router`) and `exactOutputSingle`. [5](#0-4) 

**The irresolvable contradiction for pool admins:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on-chain can bypass the allowlist via the router |

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd users, whitelisted market makers) is completely defeated. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and execute swaps on the restricted pool, draining LP liquidity at oracle-derived prices. This constitutes a broken core pool functionality and admin-boundary break: the pool admin's access control invariant is fully bypassed by an unprivileged path with no special setup required. [6](#0-5) 

## Likelihood Explanation
- `MetricOmmSimpleRouter` is a deployed, public, permissionless contract — any EOA or contract can call it.
- The bypass requires zero privileged access, no special tokens, and no multi-transaction setup.
- The only precondition is that the pool admin has allowlisted the router, which is the natural and necessary action when the admin wants legitimate users to be able to use the router.
- The `SwapAllowlistExtension` is a production-ready extension explicitly designed for curated pools, making deployment with this misconfiguration highly likely. [7](#0-6) 

## Recommendation
The extension must recover the original user's address rather than trusting the `sender` argument forwarded by the pool. The cleanest fix: the router already stores the real payer in transient storage via `_setNextCallbackContext(..., msg.sender, ...)`. Expose a `getPayer()` view on the router and have the extension, when it detects `sender` is a known trusted router, call `router.getPayer()` to recover the actual user identity and check that address against `allowedSwapper`. Alternatively, require that `SwapAllowlistExtension` pools document and enforce that users must call `pool.swap()` directly, and the router must never be allowlisted. [8](#0-7) 

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as extension1, beforeSwap order = extension1.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    // allowlist the router so legitimate users can use it
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle(ExactInputSingleParams({
          pool: pool,
          recipient: attacker,
          tokenIn: token0,
          amountIn: 1e18,
          ...
      }))

Trace:
  - pool.swap() is called with msg.sender = router
  - _beforeSwap passes sender = router to SwapAllowlistExtension
  - allowedSwapper[pool][router] == true → check passes
  - attacker receives token1 output from the curated pool

Result:
  - The allowlist invariant is broken: attacker was never allowlisted
  - Any unprivileged user can repeat this indefinitely
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
