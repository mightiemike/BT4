Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the real swapper, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension` is intended to restrict which addresses may swap on a curated pool. When a user routes through `MetricOmmSimpleRouter`, the pool receives the router as `msg.sender` and passes it as `sender` to `beforeSwap`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`. A pool admin who allowlists the router to support standard periphery swaps silently opens the pool to every user on the internet, defeating the entire purpose of the extension.

## Finding Description

**Root cause — `beforeSwap` checks the direct caller, not the economic actor:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is whatever the pool passed as the first argument to `_beforeSwap`.

**What the pool passes as `sender`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, so `sender` is whoever called `pool.swap()` directly: [2](#0-1) 

**What the router passes to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` stores the real user in transient storage for the payment callback only, then calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the **router contract**, not the end user: [3](#0-2) 

The real user (`msg.sender` of `exactInputSingle`) is stored via `_setNextCallbackContext` and is never forwarded to the pool or the extension.

**Result:** The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`. This creates two mutually exclusive broken states:

| Router allowlist status | Effect |
|---|---|
| Router **not** allowlisted | Every allowlisted user is blocked from using the router; they must call the pool directly |
| Router **allowlisted** | Every user on the internet can bypass the allowlist by routing through the router |

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner, the real economic actor) rather than `sender` (the direct caller): [4](#0-3) 

The swap hook has no equivalent `owner` parameter — `sender` is the only identity available, and it resolves to the router.

**Existing guards are insufficient:** The extension has no fallback check on `extensionData`, no router registry, and no mechanism to decode the real user. The `extensionData` bytes are passed through to the extension but are ignored by `beforeSwap`. [5](#0-4) 

## Impact Explanation

**High — broken core pool functionality causing allowlist bypass and exposure of LP assets.**

A curated pool using `SwapAllowlistExtension` to restrict trading to a whitelist of counterparties (e.g., KYC'd addresses, protocol-owned bots, or specific market makers) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The pool's LP assets are exposed to unrestricted trading at oracle prices, which the pool admin explicitly intended to prevent. This matches the "admin-boundary break" and "broken core pool functionality" impact classes: disallowed users can trade on a pool that should have rejected them, and the allowlist invariant is silently violated with no on-chain signal to the pool admin.

## Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the canonical periphery swap path. Any user who discovers the bypass can exploit it immediately with no special setup, no privileged access, and no capital requirement beyond the swap amount. The pool admin has no on-chain signal that the router is being used as a bypass vector; the allowlist appears correctly configured from their perspective.

## Recommendation

The extension must check the **economically relevant actor**, not the direct caller. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `sender` against a router registry and fall through to a user-level check**: If `sender` is a known router, decode the real user from `extensionData` and check that address instead.

The deposit allowlist avoids this problem by checking `owner` (the position owner, always the real economic actor) rather than `sender` (the direct caller). The swap allowlist should adopt the same pattern: gate the address that bears the economic consequence of the swap, not the intermediary that submitted the transaction. [6](#0-5) 

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — the only way to allow router-mediated swaps.
3. Unauthorized user `attacker` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. Router stores `attacker` in transient storage via `_setNextCallbackContext`, then calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`. [3](#0-2) 
5. Pool calls `_beforeSwap(router, ...)`. [7](#0-6) 
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`. [1](#0-0) 
7. Swap executes. `attacker` successfully trades on a pool that should have rejected them.

**Foundry test sketch:**
```solidity
// 1. Deploy pool with SwapAllowlistExtension in beforeSwap order
// 2. setAllowedToSwap(pool, address(router), true)
// 3. vm.prank(attacker); router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
// 4. Assert swap succeeded despite attacker not being in allowedSwapper[pool]
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
