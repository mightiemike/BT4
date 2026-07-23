Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual swapper, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of the pool. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. A pool admin who allowlists the router (the only way to let intended users reach the pool via the router) inadvertently grants every unpermissioned user the ability to bypass the allowlist by routing through the same contract.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called the pool: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original user's identity — the router's address becomes `sender` in the extension check: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The dilemma is irresolvable under the current design: if the admin does not allowlist the router, allowlisted users cannot use the canonical periphery; if the admin does allowlist the router, every user — allowlisted or not — can bypass the gate by routing through the router.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the economically relevant party, the position owner) rather than `sender` (the caller), demonstrating the intended pattern: [4](#0-3) 

## Impact Explanation
This is a direct admin-boundary break: a pool admin's access-control configuration (swap allowlist) is bypassed by an unprivileged path through the supported periphery contract. Any non-allowlisted address can execute swaps against a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The allowlist provides zero protection on the router path, rendering the extension's security guarantee completely ineffective for any pool that needs to support standard periphery usage.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the canonical periphery swap entry point. Any pool admin who wants allowlisted users to use the standard router must allowlist the router address — this is the natural, expected configuration. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges, no preconditions, and no capital requirements beyond the swap input tokens.

## Recommendation
The extension must verify the actual end-user identity, not the intermediary. The most consistent fix — aligned with `DepositAllowlistExtension` — is to check `recipient` (the address receiving output tokens) rather than `sender`, since `recipient` is set by the user even when routing through the router and is the economically relevant party for a swap. Alternatively, the router can encode the original `msg.sender` into `extensionData` and the extension can decode and verify it, with a trust assumption that the extension only accepts this encoding from a known router address.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so alice can use it

Attack:
  - bob (not allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: bob, ...})
  - Router calls pool.swap(bob, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Bob's swap executes successfully despite not being on the allowlist
```

`bob` receives pool output tokens. The allowlist is completely ineffective for any user routing through the supported periphery.

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
