The code confirms all claims. Let me verify the key details:

Audit Report

## Title
`uint32` Overflow in `_afterTimelock` Allows Pool Admin to Bypass Stop-Loss Timelock — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary
`_afterTimelock` computes `uint32(block.timestamp + oracleStopLossConfig[pool_].timelock)`, performing the addition in `uint256` space before truncating to `uint32`. When `timelock` is set to any value exceeding `type(uint32).max − block.timestamp` (≈ 2.54 billion seconds at current timestamps), the result wraps to a value already in the past. All four `propose*` paths store this wrapped value as `executeAfter`, causing `_requireElapsed` to pass immediately and allowing the pool admin to execute any stop-loss parameter change in the same block as the proposal — eliminating LP reaction time entirely.

## Finding Description
The root cause is in `_afterTimelock`:

```solidity
// OracleValueStopLossExtension.sol L297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

`block.timestamp` is `uint256`; `timelock` is `uint32`. The addition is evaluated in `uint256` space and then silently truncated. No upper-bound validation exists on `timelock` in either `initialize` (L56-62, which only validates `drawdownE6` and `decayPerSecondE8`) or `proposeOracleStopLossTimelock` (L78-84, which accepts any `uint32 newTimelock`).

With `block.timestamp ≈ 1,753,000,000` and `timelock = type(uint32).max = 4,294,967,295`:
- Sum = `6,047,967,295`
- `uint32(6,047,967,295)` = `1,752,999,999` — one second in the past

The guard at L301-303 (`if (block.timestamp < executeAfter) revert`) passes unconditionally when `executeAfter` is already less than `block.timestamp`.

Exploit path:
1. Pool admin calls `proposeOracleStopLossTimelock(pool, type(uint32).max)`.
2. Waits through the existing timelock once and calls `executeOracleStopLossTimelock` — this is the only honest wait required.
3. `oracleStopLossConfig[pool_].timelock` is now `type(uint32).max`.
4. Pool admin calls `proposeOracleStopLossDrawdown(pool, 0)` — `_afterTimelock` returns a wrapped past timestamp.
5. In the **same block**, calls `executeOracleStopLossDrawdown` — `_requireElapsed` passes immediately.
6. `drawdownE6` is now `0`; the `afterSwap` guard at L217 (`if (drawdown == 0) return;`) exits early, disabling the stop-loss entirely with zero LP notice.

## Impact Explanation
This is a direct admin-boundary break: the pool admin bypasses the timelock that LPs rely on as their sole protection against sudden stop-loss parameter changes. With the stop-loss disabled (`drawdownE6 = 0`), swaps that would have been blocked by `OracleStopLossTriggered` execute freely, enabling LP principal loss with no advance warning or withdrawal opportunity. This meets the allowed impact gate for admin-boundary break and direct loss of LP assets.

## Likelihood Explanation
The pool admin must pay the cost of one honest timelock wait to install the overflowing `timelock` value. After that single wait, every subsequent proposal on that pool is immediately executable with no further delay. The deceptive aspect is that `type(uint32).max` (≈ 136 years) superficially appears to be an extremely conservative protective setting, making the attack non-obvious to observers.

## Recommendation
Cast `block.timestamp` to `uint32` **before** adding the timelock, so the addition is performed in `uint32` space and wraps consistently with the stored type:

```diff
 function _afterTimelock(address pool_) private view returns (uint32) {
-    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
+    return uint32(block.timestamp) + oracleStopLossConfig[pool_].timelock;
 }
```

Additionally, add an upper-bound cap on `timelock` in both `initialize` and `proposeOracleStopLossTimelock` (e.g., `require(timelock <= 365 days)`) to prevent any future overflow path.

## Proof of Concept
```solidity
function test_timelockOverflowBypassesDelay() public {
    OracleValueStopLossExtension freshExt = new OracleValueStopLossExtension(address(factoryStub));
    MockExtensionExtsloadPool freshPool = new MockExtensionExtsloadPool(address(factoryStub), MIN_SHARES);
    factoryStub.setPoolAdmin(address(freshPool), admin);
    vm.prank(address(factoryStub));
    freshExt.initialize(address(freshPool), abi.encode(uint32(500_000), uint32(0), uint32(7 days)));

    vm.startPrank(admin);
    // Step 1: install overflowing timelock (one honest wait required)
    freshExt.proposeOracleStopLossTimelock(address(freshPool), type(uint32).max);
    vm.warp(block.timestamp + 7 days);
    freshExt.executeOracleStopLossTimelock(address(freshPool));

    // Step 2: propose drawdown = 0; _afterTimelock wraps to the past
    freshExt.proposeOracleStopLossDrawdown(address(freshPool), 0);

    // Step 3: execute in the SAME block — no warp needed
    freshExt.executeOracleStopLossDrawdown(address(freshPool));

    (uint32 dd,,,) = freshExt.oracleStopLossConfig(address(freshPool));
    assertEq(dd, 0); // stop-loss silently disabled, zero LP notice
    vm.stopPrank();
}
```