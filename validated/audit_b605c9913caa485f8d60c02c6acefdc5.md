Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end-user identity, allowing any caller to bypass swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is the caller, `sender` is the router address. If the pool admin allowlists the router — the expected operational requirement for any router-based trading — every unprivileged address can bypass the curated-pool restriction by routing through `MetricOmmSimpleRouter`. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` (the direct caller of `pool.swap()`) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The original user's address (`msg.sender` in the router) is stored only in transient callback context for payment purposes and is never forwarded to `pool.swap()` in any argument visible to extensions: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` with the router as `msg.sender`. [5](#0-4) 

The result is a structural dilemma:

| Router allowlisted? | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| No | Blocked (router unusable) | Blocked |
| Yes | Allowed | **Also allowed — bypass** |

The extension cannot distinguish the two cases because the router's address is the only identity visible to the hook. The wrong value is `allowedSwapper[pool][router]` being checked instead of `allowedSwapper[pool][end_user]`.

`DepositAllowlistExtension` is not affected because it checks `owner` (the position beneficiary), which `MetricOmmPoolLiquidityAdder` correctly forwards as the user-supplied value: [6](#0-5) 

## Impact Explanation

Any address can trade on a curated pool (one using `SwapAllowlistExtension` to restrict swaps to KYC'd, whitelisted, or otherwise vetted counterparties) by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The allowlist guard is silently bypassed; the pool settles the swap and transfers tokens normally. This constitutes a curation failure and potential direct loss of LP assets if the pool's pricing or liquidity assumptions depend on a restricted counterparty set — a High-severity broken core pool functionality impact under the allowed impact gate.

## Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. Because the router is the standard periphery entry point, any pool that intends to support router-based trading must allowlist it, making this the expected operational state rather than an edge case. The attacker requires no special role — a standard `exactInputSingle` call suffices, and the attack is repeatable on every swap.

## Recommendation

The `SwapAllowlistExtension` must receive the true end-user identity. Two options:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. Requires coordinated changes to both the router and the extension.
2. **Reject known router addresses**: The extension stores known router addresses at construction and reverts when `sender` matches a router, forcing direct pool interaction only on curated pools. Simpler but prevents router use entirely on allowlisted pools.

## Proof of Concept

```
Setup:
  Pool P configured with SwapAllowlistExtension E
  Pool admin allowlists router R: E.setAllowedToSwap(P, R, true)
  Attacker A is NOT allowlisted: allowedSwapper[P][A] == false

Attack:
  A calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  → router calls P.swap(recipient, ...) with msg.sender = R (router)
  → MetricOmmPool.swap passes msg.sender (R) as sender to _beforeSwap
  → ExtensionCalling._beforeSwap encodes sender=R and calls E.beforeSwap
  → E checks allowedSwapper[P][R] → true → passes
  → swap executes; A receives output tokens

Expected: revert NotAllowedToSwap
Actual:   swap succeeds; allowlist bypassed

Foundry test outline:
  1. Deploy pool P with SwapAllowlistExtension E
  2. Call E.setAllowedToSwap(P, address(router), true)
  3. Assert E.isAllowedToSwap(P, attacker) == false
  4. vm.prank(attacker); router.exactInputSingle({pool: P, ...})
  5. Assert swap succeeded (no revert) — bypass confirmed
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
