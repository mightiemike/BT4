Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Real Swapper, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` receives the `sender` argument forwarded verbatim from `MetricOmmPool.swap()`, which is always `msg.sender` of the pool call — the router contract when users go through `MetricOmmSimpleRouter`. The extension therefore gates the router's address rather than the originating EOA. No configuration simultaneously allows allowlisted EOAs through the router while blocking non-allowlisted ones, making the access-control mechanism either fully bypassed or fully broken for router users.

## Finding Description
`MetricOmmPool.swap()` captures `msg.sender` and passes it as `sender` to every `beforeSwap` hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap()` then checks that value against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle()` and `exactInput()` call `pool.swap()` directly, so the pool sees `msg.sender == address(router)`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80 (exactInputSingle)
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, never the originating EOA. Two mutually exclusive broken outcomes result:

| Admin configuration | Outcome |
|---|---|
| Router **not** allowlisted | Every allowlisted EOA is silently blocked when using the router |
| Router **allowlisted** | Every address on chain can swap through the router, defeating the allowlist entirely |

No existing guard in the extension or pool corrects for the router-as-intermediary case. The `_setNextCallbackContext` call in the router records the real payer for payment purposes only; it is never surfaced to the extension. [5](#0-4) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that protection entirely once the pool admin allowlists the router. Any unprivileged EOA can call `exactInputSingle()` or `exactInput()` and the extension passes them through, allowing unauthorized swaps against a pool explicitly configured to exclude them. This is a direct, fund-impacting bypass of a core access-control mechanism — unauthorized users can drain liquidity at oracle-derived prices. This meets the "admin-boundary break" and "broken core pool functionality causing loss of funds" criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Pool admins configuring a swap allowlist will naturally expect it to work through the router. When allowlisted users discover they cannot swap via the router (because the router is not allowlisted), the admin's natural remediation is to allowlist the router — which opens the gate to all users. The bypass requires no special privileges, no flash loans, and no unusual token behavior. Any EOA can call the router. The precondition (router allowlisted) is a near-certain operational step.

## Recommendation
The extension must check the economically relevant actor, not the intermediate router. The least invasive fix given the existing architecture: the router already records the originating payer in transient storage via `_setNextCallbackContext`. Expose a `realSender()` view on the router and have the extension query it when `sender == router`. The extension would then check `allowedSwapper[pool][router.realSender()]` instead of `allowedSwapper[pool][router]`. Alternatively, have the router encode the real user identity in `extensionData` and have the extension decode and verify it. [6](#0-5) 

## Proof of Concept
**Bypass path (router allowlisted):**
1. Deploy a pool with `SwapAllowlistExtension` as `EXTENSION_1`, `beforeSwap` order = `1`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` (required so router-mediated swaps don't revert for everyone).
3. Unprivileged EOA `attacker` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Pool calls `_beforeSwap(msg.sender=router, ...)`.
5. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
6. Attacker's swap executes against the curated pool despite never being allowlisted.

**Denial path (router not allowlisted):**
1. Pool admin calls `setAllowedToSwap(pool, alice, true)`.
2. Alice calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
3. Pool calls `_beforeSwap(msg.sender=router, ...)`.
4. Extension evaluates `allowedSwapper[pool][router] == false` → `NotAllowedToSwap` revert.
5. Alice, a legitimately allowlisted user, cannot use the supported periphery path.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-103)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
