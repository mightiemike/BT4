Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `sender`, which equals `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A pool admin who allowlists the router to enable standard periphery swaps inadvertently opens the pool to every user, defeating per-user curation entirely.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking the `sender` parameter:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [1](#0-0) 

Inside this call, `msg.sender` is the pool (the pool calls the extension) and `sender` is the `msg.sender` of `pool.swap()` — i.e., whoever called the pool directly.

`ExtensionCalling._beforeSwap` passes `sender` verbatim to every extension: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

At that point `msg.sender` of `pool.swap()` is the **router address**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces two losing options:

| Admin choice | Consequence |
|---|---|
| Allowlist individual user addresses | Those users are blocked when using the router (router not allowlisted) |
| Allowlist the router address | **Every** user bypasses per-user restriction via the router |

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the second parameter — the economic actor explicitly passed by the caller), not `sender`: [4](#0-3) 

The swap extension checks the wrong field (`sender` = technical caller) while the deposit extension checks the right field (`owner` = economic actor). This asymmetry is the root cause.

## Impact Explanation
**Allowlist bypass (High):** A pool admin who allowlists the router to enable standard periphery swaps inadvertently opens the pool to every user. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle()` targeting the curated pool and the `beforeSwap` hook passes because `allowedSwapper[pool][router] == true`. The entire per-user curation is defeated — this is a broken core pool invariant with direct fund-access impact (non-allowlisted users execute swaps on a pool intended to be curated).

**Broken core swap path (Medium):** A pool admin who allowlists individual user addresses instead of the router blocks those users from using the standard periphery path, making the allowlist-protected pool's primary swap entrypoint unusable for legitimately allowlisted users.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any production deployment of a curated pool that expects users to swap through the router will encounter this immediately. The pool admin has no in-protocol mechanism to enforce per-user restrictions through the router without also opening the pool to all users. No special attacker capability is required — any EOA can call the public router.

## Recommendation
The extension should gate the **economic actor**, not the technical caller. The `beforeSwap` signature already receives `recipient` as its second parameter (currently unnamed and ignored):

```solidity
function beforeSwap(address sender, address recipient, ...)
```

When the router calls `pool.swap()`, it sets `recipient` to the actual user address (`params.recipient`). Switching the allowlist check to `recipient` aligns the gate with the economic beneficiary and mirrors the `DepositAllowlistExtension` pattern of checking the explicitly passed economic actor (`owner`). [1](#0-0) 

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension wired to beforeSwap.
2. Pool admin calls:
       swapExtension.setAllowedToSwap(pool, router, true);
   (necessary to allow any router-mediated swap)
3. Non-allowlisted user `eve` calls:
       router.exactInputSingle({pool: pool, recipient: eve, ...})
4. Router calls pool.swap(recipient=eve, ...) → msg.sender of pool.swap() = router.
5. Pool calls extension.beforeSwap(sender=router, recipient=eve, ...).
6. Extension evaluates: allowedSwapper[pool][router] == true → passes.
7. Eve's swap executes on the curated pool despite never being individually allowlisted.

Conversely, if the admin allowlists alice directly and not the router:
1. swapExtension.setAllowedToSwap(pool, alice, true);
2. Alice calls router.exactInputSingle({pool: pool, ...})
3. Router calls pool.swap() → sender = router.
4. allowedSwapper[pool][router] == false → NotAllowedToSwap revert.
5. Alice cannot use the standard swap path despite being explicitly allowlisted.
``` [5](#0-4) [6](#0-5)

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
