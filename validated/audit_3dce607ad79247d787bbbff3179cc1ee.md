Audit Report

## Title
Swap Allowlist Bypassed via Router: `sender` Identity Mismatch in `SwapAllowlistExtension` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the direct caller of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user's address. Any pool admin who allowlists the router (required for normal router-mediated operation) inadvertently grants every unprivileged user the ability to bypass the curated swap gate entirely.

## Finding Description

`ExtensionCalling._beforeSwap` encodes `sender` as the first argument forwarded to every extension in `BEFORE_SWAP_ORDER`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` keys its allowlist lookup on `(msg.sender [= pool], sender)`: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, `msg.sender` at the pool level is the router contract: [3](#0-2) 

So `sender` delivered to `beforeSwap` is always the router address, never the originating user. For the router to be usable at all with an allowlisted pool, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once that entry exists, `allowedSwapper[pool][router] == true` satisfies the guard for every caller of the public router, regardless of their identity.

`DepositAllowlistExtension` avoids the same problem by ignoring `sender` (the intermediary) and checking `owner` (the LP position owner — the economically relevant actor): [4](#0-3) 

No equivalent second identity field exists in the swap hook signature, so `SwapAllowlistExtension` has no way to recover the originating user when the router is in the call path.

## Impact Explanation

A pool admin who deploys a curated (e.g., KYC-gated or institutional) pool with `SwapAllowlistExtension` in `BEFORE_SWAP_ORDER` intends to restrict swap execution to a specific set of addresses. Because `MetricOmmSimpleRouter` is a public, permissionless contract, any non-allowlisted address can execute swaps against the pool by calling the router. The pool receives and settles the swap normally; the extension's guard is satisfied because the router's address is allowlisted. The actual user's identity is never checked. This constitutes a complete bypass of admin-configured access control — an unprivileged actor trades against a pool designed to be curated, which is a broken core pool functionality / admin-boundary break under the contest's allowed impact gate.

## Likelihood Explanation

The three required conditions are all reachable by any unprivileged actor with no special privileges, tokens, flash loans, or multi-block manipulation:
1. A pool is deployed with `SwapAllowlistExtension` in `BEFORE_SWAP_ORDER`.
2. The pool admin allowlists the router — a near-certain operational requirement for any pool that expects standard user interaction.
3. A non-allowlisted user calls any of the router's public swap entry points targeting that pool.

## Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on `recipient` (the address receiving swap output) rather than `sender` (the intermediary caller), mirroring how `DepositAllowlistExtension` gates on `owner` rather than `sender`. Alternatively, the router should forward the originating user's address in `extensionData`, and the extension should decode and verify that value. Either approach closes the bypass without requiring the router to be individually allowlisted.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` wired into `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Non-allowlisted `attacker` calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The router calls `pool.swap(attacker_recipient, ...)` — `msg.sender` at the pool level is the router.
5. `_beforeSwap` encodes `sender = router_address` and dispatches to `SwapAllowlistExtension.beforeSwap`.
6. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. The swap executes and settles in full; the attacker's identity was never checked. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
