Audit Report

## Title
Router-Mediated Swaps Corrupt Swapper Identity in `SwapAllowlistExtension.beforeSwap` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the immediate caller of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, the pool receives the router as `msg.sender` and forwards it as `sender` to the extension, erasing the actual end-user identity. This makes the allowlist either trivially bypassable (if the router is allowlisted) or completely broken for router users (if it is not).

## Finding Description
In `MetricOmmPool.swap`, the pool passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` encodes that value verbatim and forwards it to the extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then checks: [2](#0-1) 

where `msg.sender` is the pool and `sender` is whatever the pool forwarded — the router address when the call came through the router.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original `msg.sender`: [3](#0-2) 

The same identity loss occurs in `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165). In `exactInput`, intermediate hops use `address(this)` (the router) as the payer, compounding the identity erasure: [4](#0-3) 

There is no existing guard that recovers the original caller. The extension has no access to `tx.origin` or any signed attestation, and the pool's hook interface only exposes the immediate `msg.sender`.

## Impact Explanation
Two concrete failure modes arise from this identity mismatch:

1. **Allowlist bypass**: A pool admin allowlists the router address so that their allowlisted users can swap via the official periphery. Any unprivileged attacker can then call `router.exactInputSingle` and pass the allowlist check, because the extension sees `sender = router` (allowlisted) regardless of who the actual caller is. The attacker executes swaps in a pool they were never meant to access — broken core pool functionality and an admin-boundary break by an unprivileged path.

2. **Broken core functionality**: A pool admin allowlists specific user addresses but not the router. Those users cannot swap through the router at all — the extension sees `sender = router` (not allowlisted) and reverts with `NotAllowedToSwap`. There is no configuration that simultaneously allows specific users via the router while blocking others. [5](#0-4) 

## Likelihood Explanation
`SwapAllowlistExtension` is a production extension explicitly designed for pools that need swap access control. Any pool admin who allowlists the router — the natural and expected step to support the official periphery — immediately exposes the pool to bypass by any address. The router is a public, permissionless contract with no caller restrictions. No special attacker capability is required beyond calling a public router function.

## Recommendation
The extension must receive the original transaction initiator, not the immediate pool caller. Concrete options:

- Pass `tx.origin` as an additional parameter in hook data (with trust assumptions documented).
- Require callers to embed their identity in `extensionData` and have the extension verify it against a signed attestation or a trusted forwarder registry.
- Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level by rejecting pool configurations that combine this extension with a known router allowlist entry.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — the natural step to allow router users.
3. Attacker (not individually allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. Pool calls `_beforeSwap(msg.sender = router, ...)`.
5. Extension checks `allowedSwapper[pool][router] == true` → passes.
6. Attacker's swap executes in a pool they were never meant to access.

Conversely, if the admin allowlists only individual users (not the router), those users' router calls revert at step 5 with `NotAllowedToSwap`, making the router permanently unusable for allowlisted users regardless of configuration. [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
```
