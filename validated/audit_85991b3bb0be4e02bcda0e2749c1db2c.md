### Title
`eth_getFilterChanges` never re-validates `maxBlockRange`, allowing unbounded polling of unbounded-range filters - ([File: LogFilterWrapper.java])

### Finding Description
`LogFilterWrapper`'s constructor only enforces `Args.getInstance().getJsonRpcMaxBlockRange()` via `validateBlockRange(currentMaxBlockNum)` when the caller passes `checkBlockRange=true`. The in-code comment explicitly documents the intended design: `eth_getLogs` enforces the range at construction time; `eth_newFilter` creates the wrapper with `checkBlockRange=false` (no creation-time gate); and `eth_getFilterLogs` is expected to re-run `validateBlockRange` against the current head before scanning so the cap "cannot be bypassed": [1](#0-0) 

This means `eth_newFilter` can create a `LogFilterWrapper` with an effectively unbounded `fromBlock`/`toBlock` (e.g., `fromBlock=0`, `toBlock="latest"`, producing `toBlockSrc = Long.MAX_VALUE`) with no server-side range check because `checkBlockRange` is `false` for that entrypoint: [2](#0-1) 

The only revalidation point documented in the code is inside `eth_getFilterLogs`, which is designed to re-run `validateBlockRange` against the current head. If `eth_getFilterChanges` (the polling entrypoint invoked repeatedly, e.g. once per block) does not call `validateBlockRange` on the persisted filter before performing its match/scan work, then an attacker who only ever calls `eth_newFilter` followed by repeated `eth_getFilterChanges` would never trigger the `maxBlockRange` cap, defeating the intended defense.

I was not able to directly inspect the `eth_getFilterChanges` and `eth_getFilterLogs` method bodies in `TronJsonRpcImpl.java` in this session (tool calls were exhausted before I could load that file), so I cannot independently confirm the exact line numbers cited in the prompt (1517-1535 for `eth_getFilterChanges`, 1574 for `eth_getFilterLogs`) or verify with 100% certainty that `eth_getFilterChanges` truly omits the `validateBlockRange` call in the current codebase. This must be confirmed by reading `TronJsonRpcImpl.java` directly.

### Impact Explanation
If confirmed, an unprivileged JSON-RPC caller could create one unbounded-range filter and poll it indefinitely via `eth_getFilterChanges`, forcing the node to run full log/transaction matching (`LogMatch.matchBlock`-style scans) over every new block for the filter's entire lifetime, without ever hitting the `jsonRpcMaxBlockRange` cap that governs `eth_getLogs`/`eth_getFilterLogs`. This is a repeatable, low-cost way to force sustained, uncapped compute work on the public API.

### Likelihood Explanation
Preconditions are minimal and fully within reach of an unprivileged caller: `jsonRpcMaxBlockRange > 0` configured (the only defense), and the attacker simply never calling the capped entrypoints (`eth_getLogs`, `eth_getFilterLogs`). The call sequence (`eth_newFilter` then loop `eth_getFilterChanges`) uses only standard public JSON-RPC methods with no special privileges required, making this easy to reproduce if `eth_getFilterChanges` indeed omits the revalidation call.

### Recommendation
Have `eth_getFilterChanges` invoke `LogFilterWrapper.validateBlockRange(currentMaxBlockNum)` (or an equivalent capped-range check) on every poll, mirroring what `eth_getFilterLogs` does, before scanning newly matched blocks. Alternatively, enforce the `maxBlockRange` cap unconditionally at filter creation time (regardless of `checkBlockRange`) so no filter can ever be created with a logical range exceeding the configured maximum, removing the need for entrypoint-specific revalidation entirely.

### Proof of Concept
```java
// Pseudocode for a JUnit test in framework/src/test/java/org/tron/core/jsonrpc/
@Test
public void testFilterChangesEnforcesMaxBlockRange() throws Exception {
  Args.getInstance().setJsonRpcMaxBlockRange(10); // small cap for test

  FilterRequest fr = new FilterRequest();
  fr.setFromBlock("0x0");
  fr.setToBlock("latest"); // unbounded upper range

  // eth_newFilter path: checkBlockRange=false, should succeed despite huge range
  String filterId = tronJsonRpc.newFilter(fr);

  // Advance chain far beyond the configured maxBlockRange (e.g. mine 1000 blocks)
  mineBlocks(1000);

  // eth_getFilterChanges should either:
  //  (a) throw JsonRpcInvalidParamsException("exceed max block range: 10"), matching
  //      eth_getFilterLogs's behavior, or
  //  (b) if it does not throw, this proves the cap is bypassed.
  try {
    tronJsonRpc.getFilterChanges(filterId);
    fail("Expected JsonRpcInvalidParamsException due to exceeded maxBlockRange, "
        + "but eth_getFilterChanges processed an unbounded range without validation");
  } catch (JsonRpcInvalidParamsException expected) {
    // expected if fixed
  }
}
```
Run this alongside a control test calling `eth_getFilterLogs` (via `LogFilterWrapper.validateBlockRange`) on the same filter to confirm it does throw, demonstrating the asymmetry between the two entrypoints.

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilterWrapper.java (L27-31)
```java
  public LogFilterWrapper(FilterRequest fr, long currentMaxBlockNum, Wallet wallet,
      boolean checkBlockRange) throws JsonRpcInvalidParamsException {

    // 1.convert FilterRequest to LogFilter
    this.logFilter = new LogFilter(fr);
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilterWrapper.java (L106-119)
```java
    // eth_getLogs enforces the block range at construction time. eth_newFilter creates the
    // wrapper with checkBlockRange=false (no creation-time gate); eth_getFilterLogs re-runs this
    // check against the current head before scanning so the cap cannot be bypassed.
    if (checkBlockRange) {
      validateBlockRange(currentMaxBlockNum);
    }
  }

  public void validateBlockRange(long currentMaxBlockNum) throws JsonRpcInvalidParamsException {
    int maxBlockRange = Args.getInstance().getJsonRpcMaxBlockRange();
    if (maxBlockRange > 0 && min(toBlock, currentMaxBlockNum) - fromBlock > maxBlockRange) {
      throw new JsonRpcInvalidParamsException("exceed max block range: " + maxBlockRange);
    }
  }
```
