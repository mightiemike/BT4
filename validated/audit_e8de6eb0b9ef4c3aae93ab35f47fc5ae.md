Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router's address rather than the end user's address. If the pool admin allowlists the router (required for any legitimate user to use it), every non-allowlisted user can bypass the curated-pool restriction by routing through the public router.

## Finding Description
**Step 1 – Pool passes `msg.sender` as `sender` to the hook.**

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the first argument:

```solidity
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

**Step 2 – ExtensionCalling forwards that value as `sender` to every configured extension.**

`ExtensionCalling._beforeSwap` encodes `sender` and dispatches it to all extensions in order:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
``` [2](#0-1) 

**Step 3 – SwapAllowlistExtension gates on that `sender` value.**

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct for the pool-keyed mapping), and `sender` is whoever called `pool.swap()`.

**Step 4 – MetricOmmSimpleRouter calls `pool.swap()` as itself.**

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

When this executes, `msg.sender` inside `pool.swap()` is the **router address**, not the end user. The hook therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The dilemma this creates for pool admins:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every non-allowlisted user can bypass the restriction by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same. The same issue applies to `exactInput` and `exactOutputSingle` / `exactOutput` paths in the router. [5](#0-4) 

## Impact Explanation
Any non-allowlisted user can trade on a curated pool that deploys `SwapAllowlistExtension` by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`). The pool receives and settles the swap normally; the only guard that was supposed to block the trade silently passes because it sees the router's address, which the admin had to allowlist. This is a direct, fund-impacting policy bypass: the pool's LP positions are exposed to counterparties the pool admin explicitly intended to exclude. This constitutes an admin-boundary break where an unprivileged path bypasses a pool admin's access control configuration, with direct fund impact on LP positions. [6](#0-5) 

## Likelihood Explanation
The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user who knows the pool uses a swap allowlist can trivially route through the router to bypass it. No special privileges, flash loans, or multi-step setup are required. The bypass is a single transaction. The precondition (router must be allowlisted) is a necessary operational requirement for any legitimate router usage, making the vulnerability automatically present in any real-world deployment of this extension with the router. [7](#0-6) 

## Recommendation
The extension must gate the **economic actor**, not the intermediary. Two viable approaches:

1. **Preferred – require the router to embed the original `msg.sender` in `extensionData`**: have the extension decode the real swapper from the extension payload and verify it against the allowlist. This requires a trusted router that signs or encodes the caller identity.
2. **Check `tx.origin` inside the extension**: acceptable only if the threat model excludes contract-based attackers; generally fragile and not recommended for production.
3. **Document and enforce at factory level**: document that the allowlist gates the direct pool caller and that the router must never be allowlisted on curated pools, and enforce this with a factory-level check that rejects pools pairing `SwapAllowlistExtension` with a known router address in the allowlist. [8](#0-7) 

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin must do this for legitimate users
  allowedSwapper[pool][alice]  = true   // alice is the intended allowlisted user
  allowedSwapper[pool][bob]    = false  // bob is explicitly excluded

Attack (bob bypasses the allowlist):
  bob calls router.exactInputSingle({
      pool: pool,
      zeroForOne: true,
      amountIn: X,
      recipient: bob,
      ...
  })

  router calls pool.swap(bob, true, X, ...)
    → msg.sender inside pool.swap() = router
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓ passes
    → swap executes, bob receives tokens

Bob successfully trades on a pool that was supposed to exclude him.
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` configured.
2. Call `setAllowedToSwap(pool, router, true)` and `setAllowedToSwap(pool, alice, true)`.
3. As `bob` (not allowlisted), call `router.exactInputSingle(...)` targeting the pool.
4. Assert the swap succeeds and bob receives output tokens, demonstrating the allowlist bypass. [3](#0-2)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
