Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual swapper, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user. Any pool admin who allowlists the router (required for allowlisted users to trade via the router) simultaneously opens the gate for every non-allowlisted address, completely nullifying the per-pool swap allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` to the pool: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, never inspecting the actual user's address. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and gates on `owner` — the economic beneficiary: [5](#0-4) 

The asymmetry is structural: the deposit guard keys on the economically relevant actor (`owner`); the swap guard keys on the transport layer (`sender`/router).

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC pools, institutional pools) is completely unenforceable for all router-mediated swaps once the router is allowlisted. Any unprivileged address can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router and bypass the allowlist check. This constitutes broken core pool functionality — the allowlist extension's sole purpose is to restrict who may swap, and that restriction is fully nullified. The wrong value is the extension's access-control decision: `allowedSwapper[pool][router]` evaluates to `true` when it should be evaluating `allowedSwapper[pool][actualUser]`.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who configure `SwapAllowlistExtension` and want their allowlisted users to trade normally will inevitably allowlist the router. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — any address can call `exactInputSingle` on the router at any time. The precondition (router allowlisted) is a natural operational step, not an adversarial one.

## Recommendation
Replace the `sender`-based check with a check on `recipient` as a minimal fix consistent with the deposit extension pattern:

```solidity
// current (broken for router flows):
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) { ... }

// candidate fix — gate on the economic beneficiary:
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) { ... }
```

Note that `recipient` is also caller-controlled, so a complete fix for strict per-user gating requires a trusted-forwarder pattern or signed-identity approach passed through `extensionData`. Alternatively, document and enforce at the factory level that pools using `SwapAllowlistExtension` must not allowlist the router and must require direct `pool.swap()` calls only.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is meant to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. The router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. `_beforeSwap(router, bob, ...)` is dispatched; the extension checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite never being allowlisted.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
