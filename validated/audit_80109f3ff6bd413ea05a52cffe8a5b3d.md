Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the direct pool caller (`sender`) instead of the actual end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `pool.swap()`'s direct caller. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address. Because the pool admin must allowlist the router for any router-based swap to succeed on a curated pool, every unpermissioned user can bypass the per-user allowlist by calling `router.exactInputSingle(...)` instead of `pool.swap(...)` directly. The allowlist is rendered completely ineffective for all router-mediated swaps.

## Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap`:**

The extension checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool via `_callExtensionsInOrder`) and `sender` is the first argument forwarded by the pool — always `msg.sender` of `pool.swap()`.

**Pool passes its own `msg.sender` as `sender`:**

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

**Router becomes `msg.sender` of `pool.swap()`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The actual end user is stored only in transient callback context for payment settlement and is never forwarded to the pool or extension:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., "", params.extensionData);
``` [3](#0-2) 

**Exploit flow:**

1. Pool admin deploys pool with `SwapAllowlistExtension`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — required for any router-based swap to work.
3. Pool admin does NOT allowlist `userB`.
4. `userB` calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap executes.
8. The check `allowedSwapper[pool][userB]` is never evaluated.

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position owner explicitly passed by the caller), not `sender`. This correctly gates the economically relevant actor regardless of who the intermediary is. [4](#0-3) 

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-controlled bots). The bypass allows any unpermissioned address to trade against the pool's liquidity at oracle-anchored prices. LP funds are directly exposed to unauthorized traders, and the curation policy is rendered completely ineffective. This constitutes broken core pool functionality causing direct exposure of LP assets to unauthorized counterparties — a High severity impact under the allowed impact gate.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical periphery swap path. Pool admins who want to support router-based swaps for their allowlisted users must allowlist the router — this is the expected operational configuration, not an edge case. Any user who observes the allowlist configuration (all state is public) can immediately exploit the bypass with a standard router call. No special privileges, flash loans, or multi-step setup are required. The bypass is repeatable on every curated pool that allowlists the router.

## Recommendation

The extension must authenticate the actual end user, not the intermediary contract. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. Requires a trusted encoding convention between the router and the extension.
2. **Reject known router addresses in the allowlist setter**: In `setAllowedToSwap`, revert if the `swapper` is a known router address. This is the simplest mitigation but restricts UX for router-based curated pools.

## Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin calls: swapExtension.setAllowedToSwap(pool, router, true)
    → allowedSwapper[pool][router] = true
  pool admin does NOT allowlist userB
    → allowedSwapper[pool][userB] = false

Attack:
  userB calls router.exactInputSingle({pool: pool, recipient: userB, ...})
    → router calls pool.swap(userB, ...) with msg.sender = router
    → pool calls extension.beforeSwap(router, userB, ...)
    → extension checks allowedSwapper[pool][router] ← true
    → swap executes; userB trades on the curated pool without being allowlisted

Foundry test sketch:
  1. Deploy pool with SwapAllowlistExtension.
  2. setAllowedToSwap(pool, address(router), true).
  3. vm.prank(userB); router.exactInputSingle({pool: pool, ...}).
  4. Assert swap succeeds (no NotAllowedToSwap revert).
  5. Assert allowedSwapper[pool][userB] == false (userB was never allowlisted).
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-40)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```
