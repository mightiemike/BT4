Audit Report

## Title
SwapAllowlistExtension Checks Router Address as Swapper Instead of Actual End User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter against a per-pool allowlist, where `sender` is the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. If the router is allowlisted (the natural admin action to support router-mediated swaps), any unprivileged user bypasses the allowlist entirely by calling the router.

## Finding Description
**Root cause — `pool.swap()` passes `msg.sender` (the router) as `sender` to extensions:**

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` in `ExtensionCalling` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called `pool.swap()`: [3](#0-2) 

**How the router breaks identity:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the router contract, not the end user: [4](#0-3) 

The extension never sees the actual user's address — it only ever sees the router's address as `sender`. The real caller (`msg.sender` in the router) is stored only in transient callback context for payment purposes, not passed to the pool or extensions.

**Two failure modes from the same root cause:**

| Admin configuration | Outcome |
|---|---|
| Router address **not** allowlisted | Every allowlisted user is silently blocked from using the router; they must call the pool directly. |
| Router address **allowlisted** | Every unprivileged user bypasses the allowlist by routing through the router. |

## Impact Explanation
A curated pool using `SwapAllowlistExtension` is designed so that only approved counterparties trade against its LP liquidity. When the router is allowlisted (the only way to restore router usability for allowlisted users), any unprivileged user can call `router.exactInputSingle` and execute swaps against the pool. LP funds are directly exposed to unauthorized counterparties — a direct loss of LP principal if the pool's pricing or depth is calibrated for a restricted set of known traders. This meets the "Broken core pool functionality causing loss of funds" and "Admin-boundary break: pool admin exceeds caps or role checks bypassed by unprivileged path" criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap entry point deployed alongside the protocol. A pool admin who wants allowlisted users to be able to use the router will naturally call `setAllowedToSwap(pool, address(router), true)`. The mistake is non-obvious because the admin sees "router is trusted" rather than "router exposes all users." The trigger requires only a standard admin `setAllowedToSwap` call followed by any unprivileged user calling the router — no special permissions, exotic tokens, or complex setup needed. Repeatable by any address at any time once the router is allowlisted.

## Recommendation
The extension must gate on the actual end user, not the intermediary contract. Two viable approaches:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool. The extension decodes and checks that address. This requires a convention between router and extension.
2. **Require direct pool calls for allowlisted pools:** Document and enforce that pools using `SwapAllowlistExtension` must not be accessed through the router, and add a guard in the extension that reverts if `sender` is a known router address.

## Proof of Concept
```solidity
// 1. Pool admin deploys pool with SwapAllowlistExtension
// 2. Admin allowlists the router so legitimate users can use it
extension.setAllowedToSwap(pool, address(router), true);

// 3. Unprivileged attacker (not individually allowlisted) calls the router
vm.prank(attacker); // attacker is NOT in allowedSwapper[pool]
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Router calls pool.swap(...) → pool's msg.sender = address(router)
// Pool calls _beforeSwap(address(router), ...)
// Extension checks allowedSwapper[pool][router] == true → passes
// Attacker successfully swaps on the curated pool
// LP funds are exposed to an unauthorized counterparty
```

The extension's `beforeSwap` receives `sender = address(router)`, finds it allowlisted, and permits the swap. The actual attacker address is never checked. [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
