Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end-user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of the pool. When users route through `MetricOmmSimpleRouter`, the router is the immediate caller, so the extension checks whether the **router** is allowlisted rather than the actual end-user. Any user can bypass the per-user swap allowlist by routing through `MetricOmmSimpleRouter` if the router is allowlisted, which is required for normal router-mediated operation.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // always the immediate caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...))
);
```

`SwapAllowlistExtension.beforeSwap` checks that `sender` value against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user routes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The router is `msg.sender` of the pool call, so `sender = router_address`. The extension evaluates `allowedSwapper[pool][router_address]`, never consulting the actual end-user's address. The same flaw applies to `exactOutputSingle` and the multi-hop `exactInput`/`exactOutput` paths.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` — the economically relevant actor — so the deposit path does not share this flaw.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of counterparties. Because the extension checks the router address instead of the end-user:

- **If the router is allowlisted** (required for any router-mediated swap to succeed): every user on the network can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`. The curation is completely nullified.
- **If the router is not allowlisted**: all router-mediated swaps revert for every user, including legitimately allowlisted ones, breaking the standard periphery path.

In the operationally realistic scenario (router allowlisted), any disallowed user can trade against a curated pool's liquidity, causing direct loss of LP assets through unwanted price exposure and fee leakage to unauthorized counterparties. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break where the pool admin's access control is bypassed by an unprivileged path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the protocol's standard swap periphery. Pool admins who deploy a curated pool and want it to be usable via the router must allowlist the router address — this is the expected operational path. The bypass is reachable by any user with no special privileges, no malicious setup, and no non-standard tokens. The attacker simply calls `exactInputSingle` on the router targeting the curated pool. The condition is self-inflicted by normal pool administration.

## Recommendation
Gate on the end-user rather than the immediate pool caller. The simplest fix consistent with the existing interface is to check `recipient` instead of `sender`, since `recipient` is the address that receives output tokens and is the economically relevant actor for a swap:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, require the router to forward the original `msg.sender` via a `swapperOverride` field in `extensionData` that the extension decodes and checks when present.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    so that normal router-mediated swaps work.
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not in allowlist) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: attacker, ...})
  2. Router calls pool.swap(attacker, ...) — msg.sender of pool.swap = router.
  3. Pool calls _beforeSwap(sender=router, recipient=attacker, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker receives output tokens.

Expected: revert NotAllowedToSwap (attacker is not in allowlist).
Actual:   swap succeeds — allowlist bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
