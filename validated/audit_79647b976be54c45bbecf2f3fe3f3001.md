Audit Report

## Title
`SwapAllowlistExtension` checks router address as swapper instead of actual user, allowing allowlist bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. Any non-allowlisted user can bypass a per-user swap allowlist by calling the pool through the router, breaking the core access-control invariant the extension is designed to enforce.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` value is allowlisted: [3](#0-2) 

The allowlist storage is keyed by swapper address, confirming per-user intent: [4](#0-3) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)`: [5](#0-4) 

Inside the pool, `msg.sender` is the router contract. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. If the router is allowlisted (a natural admin action to permit users to use the standard entry point), every user — regardless of allowlist status — can trade in the restricted pool. If the router is not allowlisted, even allowlisted users cannot trade through the router, breaking the extension in both directions.

Note: `DepositAllowlistExtension` does **not** share this flaw — it correctly ignores `sender` and checks `owner` (the actual depositor): [6](#0-5) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers) provides no effective restriction when users route through `MetricOmmSimpleRouter`. Any address can trade in the restricted pool by calling the router. This is an admin-boundary break: an unprivileged, non-allowlisted user executes swaps that the pool admin explicitly prohibited. The wrong value is the extension decision — `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][actualUser]`.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any user who discovers the allowlist restriction on direct pool calls will naturally try the router. No special privileges, flash loans, or unusual token behavior are required — a standard router call suffices. The condition is trivially reachable by any unprivileged address.

## Recommendation
`SwapAllowlistExtension.beforeSwap` should check the actual originating user, not the intermediary caller. Two options:

1. **Preferred**: Add an explicit `swapper` field to `extensionData` that the router populates with `msg.sender` before calling the pool. The extension decodes it and verifies `msg.sender` (the pool) is a trusted factory-registered pool before accepting the embedded identity.
2. **Alternative**: The pool could expose a dedicated `swapper` parameter separate from `sender` in the swap hook signature, allowing the router to pass the real user address explicitly.

## Proof of Concept
1. Pool admin deploys a pool and attaches `SwapAllowlistExtension`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted to enable normal usage.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. Router calls `pool.swap(recipient, ...)`. Inside the pool, `msg.sender = router`.
6. `_beforeSwap(router, recipient, ...)` is called; extension checks `allowedSwapper[pool][router]`.
7. Router is allowlisted → check passes → Bob's swap executes despite not being on the allowlist.
8. Alternatively, if the router is not allowlisted, Alice's swap also fails through the router — the allowlist is broken in both directions.

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
