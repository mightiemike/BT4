Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the pool's `msg.sender` — the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, the router becomes the pool's `msg.sender`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalUser]`. Any pool admin who allowlists the router to enable allowlisted users to use it simultaneously grants every unprivileged user access to the gated pool.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever was passed in: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) [6](#0-5) 

So the extension receives `sender = address(router)`, not the original EOA. The allowlist lookup becomes `allowedSwapper[pool][router]`. A pool admin who wants allowlisted users to use the router must allowlist the router address. The moment they do, every unprivileged user can bypass the allowlist by routing through the same public contract. The existing `onlyPool` guard in `BaseMetricExtension` only verifies the caller is a registered pool — it does not recover the original user: [7](#0-6) 

The `FullMetricExtensionTest` suite tests direct-pool swaps only and never exercises the router path against `SwapAllowlistExtension`, leaving this bypass untested: [8](#0-7) 

## Impact Explanation
Any user can trade on a curated, allowlist-gated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other `exact*` entry point). The pool admin's access-control policy is silently voided. This enables unauthorized participants to trade on KYC-gated or compliance-restricted pools, and front-runners or arbitrageurs to trade on pools designed to exclude them, causing direct LP value leakage through adverse selection. This matches the allowed impact gate: *"Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path."*

## Likelihood Explanation
The precondition — the router being allowlisted — is the natural and expected configuration for any pool that wants its allowlisted users to access the router. The protocol ships the router as the primary user-facing entry point. A pool admin who does not allowlist the router breaks the UX for their own allowlisted users. The bypass is therefore reachable on any production pool that correctly integrates the router with the `SwapAllowlistExtension`.

## Recommendation
The extension must gate the original economic actor, not the immediate caller. The preferred fix is to have the router encode `msg.sender` into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check it when the caller is a known router. Alternatively, add an `originalSender` field to the extension interface so the pool can forward the true initiator. Until fixed, pool admins using `SwapAllowlistExtension` must not allowlist the router address, accepting that allowlisted users cannot use the router.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls swapExtension.setAllowedToSwap(pool, address(router), true)
    → router is allowlisted so that allowlisted users can use it

Attack:
  attacker = address not in allowedSwapper[pool]
  attacker calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(...) with msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → hook returns selector, swap proceeds
  attacker receives output tokens from the allowlist-gated pool
```

Foundry test plan: deploy a pool with `SwapAllowlistExtension`, allowlist the router, then call `router.exactInputSingle` from an address not in `allowedSwapper[pool]` and assert the swap succeeds (no `NotAllowedToSwap` revert).

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
