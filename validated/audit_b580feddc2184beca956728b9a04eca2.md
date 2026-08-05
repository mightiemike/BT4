### Title
`estimateEnergy` binary search assumes monotonic success/failure w.r.t. `feeLimit`, allowing energy under-estimation for gas-dependent contracts - (File: `framework/src/main/java/org/tron/core/Wallet.java`)

### Summary
`Wallet.estimateEnergy` performs a binary search over a candidate `feeLimit` value to find the minimal energy required for a smart-contract call to succeed, exactly the same "test-function must be monotonic in the search space" pattern flagged in the sudoswap `VeryFastRouter` binary-search report. The implementation implicitly assumes that if a call succeeds (`code.SUCESS`) at some `feeLimit`, it will succeed at every larger `feeLimit`, and if it fails (`code.FAILED`) at some `feeLimit`, it fails at every smaller one. This assumption does not hold for contracts whose logic branches on the amount of energy/gas remaining (a well known TVM/EVM pattern, analogous to Solidity's `gasleft()`).

### Finding Description
The search is implemented as: [1](#0-0) 

It repeatedly calls `cleanContextAndTriggerConstantContract` with a candidate `mid` fee limit and narrows the `[low, high]` range purely based on whether the returned transaction result is `code.FAILED` or not: [2](#0-1) 

This mirrors the flaw in the external report: the binary search treats the sequence of pass/fail outcomes as `[0,0,...,0,1,1,...,1]` over increasing `feeLimit`, but a contract can be written to check the energy/gas available during execution (analogous to EVM `gasleft()`/`GASLEFT` opcode support in the TVM interpreter, confirmed present in `EnergyCost.java`/`Program.java` energy-left tracking) and deliberately revert or behave differently when it detects an unusually high energy allowance (e.g. reentrancy/griefing guards, "gas-bomb" patterns, or contracts that intentionally fail above a threshold to force clients into paying inflated fees). In such cases the pass/fail sequence becomes non-monotonic (e.g. `[0,1,1,0,1,...]`), which breaks the correctness guarantee of binary search: the algorithm converges to *some* `feeLimit` where the probe succeeded, but not necessarily the true minimal value, and can produce an inconsistent or artificially low/high `energyRequired` result depending on where the non-monotonic transition happens to fall relative to the search path.

### Impact Explanation
`estimateEnergy` is the public-facing energy estimation API used by wallets/dApps to pick a `feeLimit`/`ethAmount` equivalent before broadcasting a real, fee-paying transaction. If the binary search returns an underestimated energy requirement due to non-monotonic contract behavior, transactions built using that estimate will run out of energy on-chain and fail, burning bandwidth/energy and TRX fees for the caller with no state change — an underpriced/mis-priced public-work outcome directly caused by the flawed search invariant, not by user error. Because `triggerConstantContract`/`estimateEnergy` execute against arbitrary, attacker-deployable contract bytecode, any unprivileged user can construct a contract that manipulates this estimation for arbitrary counterparties who query the endpoint (e.g. as part of a griefing/DoS strategy against a dApp's users, or to make an exchange/DEX-like contract mis-quote gas to trading counterparties).

### Likelihood Explanation
Reaching this code path requires only calling the public `estimateEnergy`/`estimateenergy` wallet API against a contract that conditionally reverts or changes behavior based on remaining energy — a pattern that is easy to author in TVM smart contracts and does not require any special privilege, matching the same "attacker crafts an adversarial monotonicity-breaking input" scenario described in the source report. The bug is triggered purely by normal, permitted usage of a supported public API (`estimateEnergy`), so likelihood is moderate: it depends on a contract author deliberately or incidentally introducing gas-dependent branching, but no protocol-level restriction prevents it.

### Recommendation
Do not rely on unconditional binary search over the constant-call success/failure boundary. Instead: (1) after finding a candidate `high` via binary search, verify monotonicity by re-checking a slightly larger `feeLimit` still succeeds and a slightly smaller one still fails before returning the result; (2) consider falling back to a bounded linear/step-wise scan or repeating the search with different step sizes when a non-monotonic transition is detected; (3) clearly document that `estimateEnergy` is a best-effort heuristic for contracts with gas-independent logic and cannot guarantee correctness for adversarial/gas-dependent contracts, and treat any failure post-broadcast due to underestimation purely as a fee/UX issue rather than a consensus-safety one.

### Proof of Concept
Conceptual PoC (mirrors the sudoswap PoC structure):
1. Deploy a TVM contract whose fallback/entry function inspects the remaining energy at runtime (via the `GASLEFT`/energy-left opcode path exposed through `Program.java`/`EnergyCost.java`) and intentionally `REVERT`s when energy-left is above some threshold `T1` but succeeds when energy-left is below `T1` but above a lower threshold `T2` (i.e., success only in a "band," not a monotonic tail).
2. Call `Wallet.estimateEnergy` (via the `estimateenergy` HTTP/gRPC endpoint) against this contract.
3. Observe that the initial `high = dps.getMaxFeeLimit()` probe succeeds (since it may fall in an unexpected region), the binary search in [1](#0-0)  then walks based on FAILED/non-FAILED outcomes and converges to a `feeLimit` inside the success band that is not the true minimal successful value — potentially reporting an `energyRequired` that fails when a real transaction is submitted with a slightly different execution context (e.g., additional gas from surrounding calldata/state), reproducing the "user gets a suboptimal/incorrect result despite sufficient overall resources" outcome from the original report.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L3051-3072)
```java
    while (low + TRX_PRECISION < high) {
      long mid = (low + high) / 2;

      while (true) {
        try {
          transaction = cleanContextAndTriggerConstantContract(
              triggerSmartContract, txCap, txExtBuilder, txRetBuilder, mid);
          break;
        } catch (Program.OutOfTimeException e) {
          retry--;
          if (retry < 0) {
            throw e;
          }
        }
      }

      if (transaction.getRet(0).getRet().equals(code.FAILED)) {
        low = mid;
      } else {
        high = mid;
      }
    }
```
