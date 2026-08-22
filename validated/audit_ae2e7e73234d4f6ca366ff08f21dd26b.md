### Title
Untested, platform-dependent custom math library (`MathWrapper`/`Maths`/`StrictMathWrapper`) risks silent consensus divergence - (File: `common/src/main/java/org/tron/common/math/Maths.java`, `platform/src/main/java/{x86,arm}/org/tron/common/math/MathWrapper.java`)

### Summary
java-tron ships a custom, hand-rolled arithmetic abstraction layer (`Maths`, `StrictMathWrapper`, and two platform-specific `MathWrapper` implementations for `x86` and `arm`) that decides at runtime — via a `disableJavaLangMath` flag threaded through `AbstractActuator`, `VMActuator`, `ResourceProcessor`, `AccountCapsule`, `ReceiptCapsule`, `Program`, `Memory`, `TransactionTrace`, `Manager`, `DynamicPropertiesStore`, `EnergyProcessor`, and other actuator/VM/resource-accounting code — whether to use `java.lang.Math`, `StrictMath`, or a hardcoded historical lookup table for arithmetic such as `pow`, `addExact`, `multiplyExact`, `subtractExact`. None of these math wrapper classes have any dedicated unit tests. [1](#0-0) [2](#0-1) 

### Finding Description
The `arm` variant of `MathWrapper.pow` does not simply delegate to `StrictMath.pow`; it consults a hardcoded map (`powData`) of specific bit-pattern input pairs collected from historical mainnet blocks, and only falls back to `StrictMath.pow` for inputs not present in that table: [3](#0-2) [4](#0-3) 

This patch table exists precisely because floating-point `pow` results have historically diverged across CPU architectures/JVMs in ways that matter for consensus (comments reference specific mainnet block numbers). The `x86` variant of the same class has no such table and calls `Math.pow` directly: [5](#0-4) 

The selection between these implementations, and between the "strict" and "non-strict" code paths inside actuators/VM (`disableJavaLangMath()`), is exercised throughout consensus-critical logic: energy/bandwidth accounting (`ResourceProcessor`, `EnergyProcessor`), balance adjustments (`AbstractActuator`, `AccountCapsule`), receipt fee calculation (`ReceiptCapsule`), VM memory/program execution (`Program`, `Memory`, `VMActuator`), and exchange math. However there is no test file anywhere in the repository for `Maths`, `StrictMathWrapper`, or either `MathWrapper` implementation — grep and glob searches for `MathWrapperTest`, `MathsTest`, `StrictMathWrapperTest`, or a `**/math/*Test.java` pattern returned no results, whereas nearly every other arithmetic-adjacent utility in the codebase (`BIUtil`, `Maths.addExact` usage in `ExchangeCreateActuatorTest`, `ExchangeInjectActuatorTest`, `ExchangeProcessorTest`, `JsonRpcApiUtilTest`) is covered by dedicated overflow tests. This mirrors the external report's root cause: a custom, SafeMath-like arithmetic library that underlies critical state transitions but has zero direct unit-test coverage, so future refactors of `Maths`/`MathWrapper`/`StrictMathWrapper` (e.g., accidentally editing the ARM `powData` table, or changing fallback/overflow behavior) would go undetected by the existing test suite.

### Impact Explanation
Because the ARM and x86 `MathWrapper.pow` implementations are not behaviorally identical (one uses a historical override table, the other calls `Math.pow` directly), any future edit to this untested code — e.g., correcting, removing, or mis-transcribing an entry in the ARM lookup table, or adding a new entry — could cause TRON full nodes running on different CPU architectures to compute different results for the same block/transaction, leading to a chain split (consensus divergence) that would not be caught by CI. Because this code sits in balance, receipt, and resource-accounting paths reachable during ordinary block/transaction processing, an undetected regression could also silently under/over-charge energy or corrupt balance/reward accounting.

### Likelihood Explanation
Likelihood of exploitation by an external attacker directly is low (this is not an attacker-triggerable input bug), but likelihood of a maintainer-introduced regression going unnoticed is elevated precisely because there is no automated test coverage for this shared, multi-architecture arithmetic layer, despite its comments explicitly calling out it exists to fix past cross-platform inconsistencies. Any node operator running the `arm` build variant is implicitly relying on untested logic for consensus-critical `pow` behavior.

### Recommendation
Add dedicated unit tests for `common/src/main/java/org/tron/common/math/Maths.java`, `StrictMathWrapper.java`, and both platform-specific `MathWrapper.java` implementations (`platform/src/main/java/x86/...` and `platform/src/main/java/arm/...`), including: (1) parity tests confirming `x86` and `arm` `MathWrapper` produce identical outputs for all historically-recorded `powData` inputs and for a broad randomized/edge-case input set; (2) overflow/boundary tests for `addExact`/`subtractExact`/`multiplyExact`/`floorDiv` on `int`/`long` boundaries; (3) a regression test that fails if the `powData` table is ever modified without an explicit, reviewed justification (e.g., snapshot test of the table's contents/hash).

### Proof of Concept
Not applicable — this is a test-coverage/process gap rather than a directly exploitable input-driven vulnerability. The concrete risk is demonstrated by inspection: the ARM-specific override table in `platform/src/main/java/arm/org/tron/common/math/MathWrapper.java` (lines 16-79) diverges from the plain `Math.pow` call in `platform/src/main/java/x86/org/tron/common/math/MathWrapper.java` (lines 11-13), and no test file exists anywhere in the codebase to assert their outputs remain equivalent, unlike comparable arithmetic utilities (`BIUtilTest`, `JsonRpcApiUtilTest`, `ExchangeProcessorTest`) which are tested for overflow behavior.

### Citations

**File:** common/src/main/java/org/tron/common/math/Maths.java (L1-19)
```java
package org.tron.common.math;

/**
 * This class is deprecated and should not be used in new code,
 * for cross-platform consistency, please use {@link StrictMathWrapper} instead,
 * especially for floating-point calculations.
 */
@Deprecated
public class Maths {

  /**
   * Returns the value of the first argument raised to the power of the second argument.
   * @param a the base.
   * @param b the exponent.
   * @return the value {@code a}<sup>{@code b}</sup>.
   */
  public static double pow(double a, double b, boolean useStrictMath) {
    return useStrictMath ? StrictMathWrapper.pow(a, b) : MathWrapper.pow(a, b);
  }
```

**File:** platform/src/main/java/arm/org/tron/common/math/MathWrapper.java (L1-22)
```java
package org.tron.common.math;

import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import java.util.Objects;

/**
 * This class is deprecated and should not be used in new code,
 * for cross-platform consistency, please use {@link StrictMathWrapper} instead,
 * especially for floating-point calculations.
 */
@Deprecated
public class MathWrapper {

  private static final Map<PowData, Double> powData = Collections.synchronizedMap(new HashMap<>());
  private static final String EXPONENT = "3f40624dd2f1a9fc"; // 1/2000 = 0.0005

  public static double pow(double a, double b) {
    double strictResult = StrictMath.pow(a, b);
    return powData.getOrDefault(new PowData(a, b), strictResult);
  }
```

**File:** platform/src/main/java/arm/org/tron/common/math/MathWrapper.java (L27-79)
```java
  static {
    // init main-net pow data start
    addPowData("3ff0192278704be3", EXPONENT, "3ff000033518c576"); //  4137160(block)
    addPowData("3ff000002fc6a33f", EXPONENT, "3ff0000000061d86"); //  4065476
    addPowData("3ff00314b1e73ecf", EXPONENT, "3ff0000064ea3ef8"); //  4071538
    addPowData("3ff0068cd52978ae", EXPONENT, "3ff00000d676966c"); //  4109544
    addPowData("3ff0032fda05447d", EXPONENT, "3ff0000068636fe0"); //  4123826
    addPowData("3ff00051c09cc796", EXPONENT, "3ff000000a76c20e"); //  4166806
    addPowData("3ff00bef8115b65d", EXPONENT, "3ff0000186893de0"); //  4225778
    addPowData("3ff009b0b2616930", EXPONENT, "3ff000013d27849e"); //  4251796
    addPowData("3ff00364ba163146", EXPONENT, "3ff000006f26a9dc"); //  4257157
    addPowData("3ff019be4095d6ae", EXPONENT, "3ff0000348e9f02a"); //  4260583
    addPowData("3ff0123e52985644", EXPONENT, "3ff0000254797fd0"); //  4367125
    addPowData("3ff0126d052860e2", EXPONENT, "3ff000025a6cde26"); //  4402197
    addPowData("3ff0001632cccf1b", EXPONENT, "3ff0000002d76406"); //  4405788
    addPowData("3ff0000965922b01", EXPONENT, "3ff000000133e966"); //  4490332
    addPowData("3ff00005c7692d61", EXPONENT, "3ff0000000bd5d34"); //  4499056
    addPowData("3ff015cba20ec276", EXPONENT, "3ff00002c84cef0e"); //  4518035
    addPowData("3ff00002f453d343", EXPONENT, "3ff000000060cf4e"); //  4533215
    addPowData("3ff006ea73f88946", EXPONENT, "3ff00000e26d4ea2"); //  4647814
    addPowData("3ff00a3632db72be", EXPONENT, "3ff000014e3382a6"); //  4766695
    addPowData("3ff000c0e8df0274", EXPONENT, "3ff0000018b0aeb2"); //  4771494
    addPowData("3ff00015c8f06afe", EXPONENT, "3ff0000002c9d73e"); //  4793587
    addPowData("3ff00068def18101", EXPONENT, "3ff000000d6c3cac"); //  4801947
    addPowData("3ff01349f3ac164b", EXPONENT, "3ff000027693328a"); //  4916843
    addPowData("3ff00e86a7859088", EXPONENT, "3ff00001db256a52"); //  4924111
    addPowData("3ff00000c2a51ab7", EXPONENT, "3ff000000018ea20"); //  5098864
    addPowData("3ff020fb74e9f170", EXPONENT, "3ff00004346fbfa2"); //  5133963
    addPowData("3ff00001ce277ce7", EXPONENT, "3ff00000003b27dc"); //  5139389
    addPowData("3ff005468a327822", EXPONENT, "3ff00000acc20750"); //  5151258
    addPowData("3ff00006666f30ff", EXPONENT, "3ff0000000d1b80e"); //  5185021
    addPowData("3ff000045a0b2035", EXPONENT, "3ff00000008e98e6"); //  5295829
    addPowData("3ff00e00380e10d7", EXPONENT, "3ff00001c9ff83c8"); //  5380897
    addPowData("3ff00c15de2b0d5e", EXPONENT, "3ff000018b6eaab6"); //  5400886
    addPowData("3ff00042afe6956a", EXPONENT, "3ff0000008892244"); //  5864127
    addPowData("3ff0005b7357c2d4", EXPONENT, "3ff000000bb48572"); //  6167339
    addPowData("3ff00033d5ab51c8", EXPONENT, "3ff0000006a279c8"); //  6240974
    addPowData("3ff0000046d74585", EXPONENT, "3ff0000000091150"); //  6279093
    addPowData("3ff0010403f34767", EXPONENT, "3ff0000021472146"); //  6428736
    addPowData("3ff00496fe59bc98", EXPONENT, "3ff000009650a4ca"); //  6432355,6493373
    addPowData("3ff0012e43815868", EXPONENT, "3ff0000026af266e"); //  6555029
    addPowData("3ff00021f6080e3c", EXPONENT, "3ff000000458d16a"); //  7092933
    addPowData("3ff000489c0f28bd", EXPONENT, "3ff00000094b3072"); //  7112412
    addPowData("3ff00009d3df2e9c", EXPONENT, "3ff00000014207b4"); //  7675535
    addPowData("3ff000def05fa9c8", EXPONENT, "3ff000001c887cdc"); //  7860324
    addPowData("3ff0013bca543227", EXPONENT, "3ff00000286a42d2"); //  8292427
    addPowData("3ff0021a2f14a0ee", EXPONENT, "3ff0000044deb040"); //  8517311
    addPowData("3ff0002cc166be3c", EXPONENT, "3ff0000005ba841e"); //  8763101
    addPowData("3ff0000cc84e613f", EXPONENT, "3ff0000001a2da46"); //  9269124
    addPowData("3ff000057b83c83f", EXPONENT, "3ff0000000b3a640"); //  9631452
    // init main-net pow data end
    // add pow data
  }
```

**File:** platform/src/main/java/x86/org/tron/common/math/MathWrapper.java (L9-13)
```java
public class MathWrapper {

  public static double pow(double a, double b) {
    return Math.pow(a, b);
  }
```
