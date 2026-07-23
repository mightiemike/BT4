Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Per-User Allowlist Gate — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate `msg.sender` of `MetricOmmPool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router's address rather than the actual end-user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every user—including those not individually allowlisted—can bypass the per-user gate by routing through the router.

## Finding Description
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)` at line 231, passing its own `msg.sender` (the immediate caller) as `sender`. [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` value verbatim to every configured extension via `abi.encodeCall`. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate pool caller. [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)`, making the router itself the `msg.sender` inside the pool. [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The allowlist mapping has no mechanism to recover the original end-user's address; the only two checks are `allowAllSwappers[pool]` and `allowedSwapper[pool][swapper]`. [6](#0-5) 

This creates an irreconcilable dilemma: if the router is not allowlisted, all router-mediated swaps are blocked even for individually allowlisted users; if the router is allowlisted, every user bypasses the individual allowlist by routing through the router.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd users, trusted market makers, or to exclude adversarial actors) cannot enforce that restriction when the router is involved. Any non-allowlisted user can execute swaps on the restricted pool by calling `MetricOmmSimpleRouter`, bypassing the intended access control entirely. LP funds are exposed to unauthorized trading on a pool whose entire security model depends on the allowlist being enforced. This matches the allowed impact category: broken core pool functionality causing loss of funds, and an admin-boundary break where a pool role check is bypassed by an unprivileged path.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is a public, permissionless contract reachable by any user. The bypass requires only that the pool admin has allowlisted the router—a natural and expected configuration step to allow legitimate users to trade via the router. No special privileges, malicious setup, or non-standard tokens are required. The attacker needs only to call any of the router's `exact*` functions targeting the restricted pool.

## Recommendation
The extension must check the economically relevant actor, not the immediate pool caller. The most practical fix without changing the core pool interface is to require the router to encode the original user's address in `extensionData`; the extension decodes and checks that address. The pool's `_beforeSwap` already forwards `extensionData` verbatim to every extension. Alternatively, document that the router must never be allowlisted and that allowlisted users must call the pool directly—accepting that router-mediated swaps are incompatible with per-user allowlisting.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls: extension.setAllowedToSwap(pool, router, true)
   (allowlists the router so legitimate users can trade via it)
3. Pool admin does NOT call: extension.setAllowedToSwap(pool, attacker, true)
4. attacker calls: MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls: pool.swap(params.recipient, zeroForOne, amount, ...)
   → msg.sender inside pool = router address
6. Pool calls: _beforeSwap(msg.sender=router, recipient, ...)
7. ExtensionCalling encodes sender=router and calls extension.beforeSwap(router, ...)
8. Extension evaluates: allowedSwapper[pool][router] == true → passes
9. Swap executes. Attacker successfully trades on a pool they were
   individually excluded from, bypassing the allowlist gate entirely.
```

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-29)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```
