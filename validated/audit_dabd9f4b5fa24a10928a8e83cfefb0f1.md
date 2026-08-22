### Title
`DataWord.longValue()`/`intValue()` silently truncate out-of-range 256-bit stack values instead of throwing, contrary to their documented contract - (File: common/src/main/java/org/tron/common/runtime/vm/DataWord.java)

### Summary
`DataWord.intValue()` and `DataWord.longValue()` are documented as "checking for lost information" and to "throw `ArithmeticException`" when the 256-bit `DataWord` does not fit into an `int`/`long`, but the implementations never perform that check — they simply left-shift and OR every byte of the 32-byte word, silently producing a truncated (and potentially negative/wrapped) result for any value ≥ 2^32 or ≥ 2^64 respectively. This is the same bug class as the `int128(int256(_value))` unsafe narrowing cast described in the external report: a value that is far larger than the target type's capacity is silently wrapped instead of causing a revert, so amounts derived from this conversion can diverge from the real (larger) value that is actually used/transferred elsewhere via `sValue()`/`value()` (full-width `BigInteger`) paths.

### Finding Description [1](#0-0) [2](#0-1) 

The Javadoc for both methods explicitly promises overflow detection via `ArithmeticException`, but the loop bodies unconditionally shift-and-accumulate over the full 32-byte array with no bounds check, so any word occupying more than 4 (for `intValue()`) or 8 (for `longValue()`) significant bytes is silently truncated to the low-order bits — mirroring the `int128(int256(_value))` truncation in the referenced Solidity bug.

There are "safe" counterparts, `intValueSafe()`/`longValueSafe()`, that do check `bytesOccupied()` and clamp to `Integer.MAX_VALUE`/`Long.MAX_VALUE`, and there are strict counterparts, `sValue().longValueExact()`, that correctly throw. This shows the codebase is aware that `intValue()`/`longValue()` are unsafe, yet many call sites in `actuator/src/main/java/org/tron/core/vm/program/Program.java` still call the unchecked `longValue()`/`intValue()` directly on values that originate from EVM/TVM stack words that can be attacker-controlled up to 2^256-1 (e.g., `msg.getEnergy().longValue()` at [3](#0-2)  and [4](#0-3) , `msg.getTokenId().longValue()` at [5](#0-4) , and `frozenBalance.longValue()`/`unfreezeBalance.longValue()` used when constructing internal transaction records at [6](#0-5)  and [7](#0-6) ).

In the confirmed-safe accounting paths (e.g. `param.setFrozenBalance(frozenBalance.sValue().longValueExact())` at [8](#0-7)  and `param.setUnfreezeBalance(...)` at [9](#0-8) ), the developers deliberately use `sValue().longValueExact()` instead of `longValue()`, which confirms the team recognizes `longValue()` is unsafe for values that must be range-checked, yet the unchecked variants remain used elsewhere in the same class for values that flow into logs/internal-transaction metadata (`addInternalTx`) and into `isTokenTransfer`/`checkTokenId` gating logic when `allowMultiSign` is disabled.

### Impact Explanation
Where `longValue()`/`intValue()` feed only into internal-transaction trace metadata (as with `frozenBalance.longValue()` passed to `addInternalTx`), the practical impact is limited to inaccurate/cosmetic trace/log values since the authoritative accounting elsewhere uses `sValue().longValueExact()`, which does throw and abort on out-of-range input. However, `isTokenTransfer(MessageCall msg)` at [10](#0-9)  uses the unchecked `msg.getTokenId().longValue() != 0` as a security-relevant branch when `allowMultiSign` is not enabled: a token id crafted so its low 8 bytes are zero but higher bytes are non-zero would be silently treated as `tokenId == 0` (i.e., "not a token transfer") by this check, while other logic in the same file that does use `sValue().longValueExact()`/`checkTokenId` would treat the value differently depending on which code path is exercised, creating an inconsistency between what is validated and what is executed. This is a real state-transition/branching-logic risk, but I could not fully trace every downstream consumer of `isTokenTransfer()`'s boolean result within the tool budget available, so I cannot conclusively demonstrate concrete asset loss or fund lock analogous to the original `int128` bug (where truncation directly zeroed out credited balance while real tokens were transferred). The `allowMultiSign` flag being enabled (which appears to be the currently active hardfork gate in most later Java-tron versions) would route through the safe `sValue().longValueExact()` path instead, reducing real-world exploitability.

### Likelihood Explanation
Reaching these code paths requires only deploying and invoking a smart contract via a normal broadcast transaction that performs a `CALLTOKEN`/message-call with a crafted token id or energy value on the stack — no privileged access is needed. However, exploitability is gated by the `allowMultiSign` hard-fork flag: if it is enabled network-wide (which is typical for mature Tron mainnets), the vulnerable unchecked path in `isTokenTransfer()` is bypassed in favor of the exact/checked path, making practical exploitation unlikely on current networks. The Javadoc/implementation mismatch itself is a real code-quality bug and a latent risk for any future call site that trusts the documented "throws ArithmeticException" contract.

### Recommendation
1. Fix `DataWord.intValue()` and `DataWord.longValue()` to actually implement their documented contracts: check `bytesOccupied()` (or the equivalent leading-zero-byte count) before accumulating, and throw `ArithmeticException` when the value does not fit in the target type, matching the Javadoc.
2. Audit all call sites of the unchecked `intValue()`/`longValue()` in `Program.java`, `OperationActions.java`, `EnergyCost.java`, and `PrecompiledContracts.java` to determine which should use `longValueSafe()`/`intValueSafe()` (best-effort clamping for non-critical paths) versus `sValue().longValueExact()`/a strict throwing conversion (for security-relevant/accounting-relevant branches such as `isTokenTransfer()`).
3. Add unit tests asserting that `intValue()`/`longValue()` throw `ArithmeticException` for words that occupy more than 4/8 significant bytes, to prevent regression and to make the contract enforceable by future callers.

### Proof of Concept
1. Craft a `DataWord` whose 32-byte big-endian representation has a non-zero byte at, e.g., byte-index 20 (i.e., a value ≥ 2^96) but whose low 8 bytes are all zero (e.g., value = `0x0100000000000000000000000000000000000000000000000000000000`... arranged so the trailing 8 bytes are `0x00`).
2. Call `dataWord.longValue()` — per the implementation at [11](#0-10) , this returns `0` instead of throwing `ArithmeticException` as documented.
3. If this token id is passed to a smart contract that triggers `isTokenTransfer(msg)` at [10](#0-9)  while `allowMultiSign` is disabled, the VM treats a non-zero, huge token id as `tokenId == 0` ("not a token transfer"), diverging from the intended validation semantics used elsewhere (e.g. `checkTokenId`, which uses the exact/throwing conversion).

### Citations

**File:** common/src/main/java/org/tron/common/runtime/vm/DataWord.java (L202-217)
```java
  /**
   * Converts this DataWord to an int, checking for lost information. If this DataWord is out of the
   * possible range for an int result then an ArithmeticException is thrown.
   *
   * @return this DataWord converted to an int.
   * @throws ArithmeticException - if this will not fit in an int.
   */
  public int intValue() {
    int intVal = 0;

    for (byte aData : data) {
      intVal = (intVal << 8) + (aData & 0xff);
    }

    return intVal;
  }
```

**File:** common/src/main/java/org/tron/common/runtime/vm/DataWord.java (L231-246)
```java
  /**
   * Converts this DataWord to a long, checking for lost information. If this DataWord is out of the
   * possible range for a long result then an ArithmeticException is thrown.
   *
   * @return this DataWord converted to a long.
   * @throws ArithmeticException - if this will not fit in a long.
   */
  public long longValue() {

    long longVal = 0;
    for (byte aData : data) {
      longVal = (longVal << 8) + (aData & 0xff);
    }

    return longVal;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1744-1744)
```java
        this.refundEnergy(msg.getEnergy().longValue() - requiredEnergy, CALL_PRE_COMPILED);
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1793-1793)
```java
          refundEnergy(msg.getEnergy().longValue(), REFUND_ENERGY_FROM_MESSAGE_CALL);
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1813-1819)
```java
  public boolean isTokenTransfer(MessageCall msg) {
    if (VMConfig.allowMultiSign()) { //allowMultiSign proposal
      return msg.isTokenTransferMsg();
    } else {
      return msg.getTokenId().longValue() != 0;
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1922-1924)
```java
    InternalTransaction internalTx = addInternalTx(null, owner, receiver,
        frozenBalance.longValue(), null,
        "freezeFor" + convertResourceToString(resourceType), nonce, null);
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1936-1936)
```java
      param.setFrozenBalance(frozenBalance.sValue().longValueExact());
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2053-2055)
```java
    InternalTransaction internalTx = addInternalTx(null, owner, owner,
        unfreezeBalance.longValue(), null,
        "unfreezeBalanceV2For" + convertResourceToString(resourceType), nonce, null);
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2060-2060)
```java
      param.setUnfreezeBalance(unfreezeBalance.sValue().longValueExact());
```
