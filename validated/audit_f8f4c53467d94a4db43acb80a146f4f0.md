### Title
Underpriced internal-transaction bookkeeping via freezeBalanceV2 loop leads to unbounded InternalTransaction list growth - ([File: actuator/src/main/java/org/tron/core/vm/program/Program.java])

### Finding Description
`Program.freezeBalanceV2` unconditionally calls `increaseNonce()` and `addInternalTx(...)` *before* running validation/execution of the freeze request via `FreezeBalanceV2Processor.validate`/`execute`: [1](#0-0) 

Regardless of whether the call succeeds or fails (`ContractValidateException`/`ArithmeticException`), an `InternalTransaction` object is always appended to `ProgramResult.internalTransactions` via `addInternalTx` → `ProgramResult.addInternalTransaction`, which simply does `getInternalTransactions().add(transaction)` with no size cap: [2](#0-1) 

On failure, `internalTx.reject()` is called, but this only mutates a `rejected` flag on the object — it does **not** remove the entry from the list, so it remains in `ProgramResult.getInternalTransactions()` and continues to be serialized/persisted. There is no maximum-size guard anywhere in `ProgramResult`, `addInternalTransaction`, or the opcode dispatch for `freezeBalanceV2` (confirmed by searching for `MAX_INTERNAL`/size-limit constants tied to internal transactions — none exist for this list). Each call to `freezeBalanceV2` from within a loop (whether all iterations succeed, or fail after the first due to insufficient balance, cooldown, or minimum-frozen-time validation) causes one more `InternalTransaction` to be pushed, so the list size scales linearly with the number of opcode invocations, which itself scales with the number of loop iterations the caller can afford in EVM energy.

Because `spendEnergy`/opcode gas metering for `freezeBalanceV2` is a fixed, small per-call cost (typical native-contract-wrapper energy cost, not scaled by internal state growth), an attacker can loop the precompiled `freezeBalanceV2` call many times per transaction for a cost proportional only to the fixed per-call energy, while the number of `InternalTransaction` objects grows linearly and unboundedly (bounded only by the energy limit of the transaction, which can be very large on a chain with high energy limits/multiple internal calls via `CALL` recursion combined with `MAX_DEPTH`). These internal transactions are later consumed by:
- log/trigger filters (`TransactionLogTriggerCapsule`),
- `Wallet.getTransactionInfoById` (gRPC/HTTP `getTransactionInfoById`),
- JSON-RPC receipt-building code (`JsonRpcApiUtil`). [3](#0-2) 

This means a single transaction can force the node to allocate, serialize, and transmit a large `InternalTransaction` list on every subsequent `getTransactionInfoById` query, at a cost not reflected in the fixed opcode energy price.

### Impact Explanation
An attacker paying for N calls to `freezeBalanceV2` at fixed per-opcode energy cost can inflate the transaction's `InternalTransaction` list to N entries (whether valid or intentionally invalid/rejected calls), consuming node memory during execution and increasing CPU/bandwidth cost for every subsequent `getTransactionInfoById` gRPC/HTTP/JSON-RPC query that fetches and serializes this transaction's receipt/trace. This is a public-facing resource-amplification issue: the cost of producing the bloated internal-transaction list (paid once by attacker, cheaply) is disproportionate to the ongoing cost imposed on every node serving trace queries for that transaction (paid repeatedly, by the network).

### Likelihood Explanation
Preconditions are trivial and fully within reach of an unprivileged attacker: deploy a contract with a loop calling the freeze-balance-v2 precompiled hook (`freezeBalanceV2`), broadcast one transaction with sufficient energy limit to iterate N times, and query `getTransactionInfoById`. No admin/governance access required. Repeatable on every subsequent block/tx as long as an attacker has enough TRX to pay for energy (and energy limits on TRON are large enough that hundreds/thousands of internal calls are plausible within a single transaction's energy budget, especially since `freezeBalanceV2`'s per-call cost is fixed and not proportional to internal-transaction-list bookkeeping cost).

### Recommendation
1. Impose a hard cap on the size of `ProgramResult.internalTransactions` (e.g., reject/throw `OutOfEnergyException` or similar once a maximum internal-transaction count is exceeded per transaction), mirroring caps that exist for other unbounded VM-produced collections (e.g., logs).
2. Alternatively/additionally, charge additional energy proportional to internal-transaction-list growth (similar to memory-expansion gas), so that the cost of large internal-transaction lists is reflected in the energy price rather than being a fixed per-opcode charge.
3. Ensure `internalTx.reject()` semantics are consistent with removing/excluding rejected internal transactions from being persisted/serialized in `getTransactionInfoById` responses if they are not meant to be billed for, reducing the incentive to spam failing `freezeBalanceV2` calls.

### Proof of Concept
```java
// Program.java unit/integration test outline
@Test
public void testFreezeBalanceV2LoopInflatesInternalTransactions() {
  // Deploy a contract bytecode that loops N times calling the freezeBalanceV2
  // native contract hook (via the TVM opcode/precompiled path), with N chosen
  // so that total energy cost stays within a normal energy limit
  // (e.g., N = 5000, fixed per-call energy cost * N << energyLimit).

  Program program = /* construct with looped freezeBalanceV2 bytecode */;
  VM.play(program, OperationRegistry.getTable());

  ProgramResult result = program.getResult();

  // Assert: internal transaction list size scales linearly with N,
  // regardless of how many calls failed (were rejected).
  assertEquals(N, result.getInternalTransactions().size());

  // Assert: energyUsed for the loop is far smaller (proportionally) than the
  // memory/serialization cost implied by N internal transactions, i.e.,
  // energyUsed / N == fixed per-call cost, while list size is unbounded.
  long perCallEnergy = EnergyCost.getFreezeBalanceV2(); // fixed opcode cost
  assertTrue(result.getEnergyUsed() <= perCallEnergy * N);

  // Simulate getTransactionInfoById cost: measure serialization time/size of
  // TransactionInfo built from result.getInternalTransactions() and compare
  // growth against energyUsed to demonstrate underpricing.
}
```
Fuzz across N (10, 100, 1000, 10000) and record `(energyUsed, internalTransactions.size(), serializedTransactionInfoBytes)` to demonstrate that serialized response size/time grows linearly with N while energy cost per call remains fixed and does not account for the cumulative list-growth cost.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2017-2046)
```java
  public boolean freezeBalanceV2(DataWord frozenBalance, DataWord resourceType) {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, owner,
        frozenBalance.longValue(), null,
        "freezeBalanceV2For" + convertResourceToString(resourceType), nonce, null);

    try {
      FreezeBalanceV2Param param = new FreezeBalanceV2Param();
      param.setOwnerAddress(owner);
      param.setResourceType(parseResourceCodeV2(resourceType));
      param.setFrozenBalance(frozenBalance.sValue().longValueExact());

      FreezeBalanceV2Processor processor = new FreezeBalanceV2Processor();
      processor.validate(param, repository);
      processor.execute(param, repository);
      repository.commit();
      return true;
    } catch (ContractValidateException e) {
      logger.warn("TVM FreezeBalanceV2: validate failure. Reason: {}", e.getMessage());
    } catch (ArithmeticException e) {
      logger.warn("TVM FreezeBalanceV2: frozenBalance out of long range.");
    }
    if (internalTx != null) {
      internalTx.reject();
    }
    return false;
  }
```

**File:** chainbase/src/main/java/org/tron/common/runtime/ProgramResult.java (L190-205)
```java
  public List<InternalTransaction> getInternalTransactions() {
    if (internalTransactions == null) {
      internalTransactions = new ArrayList<>();
    }
    return internalTransactions;
  }

  public InternalTransaction addInternalTransaction(byte[] parentHash, int deep,
      byte[] senderAddress, byte[] transferAddress, long value, byte[] data, String note,
      long nonce, Map<String, Long> token) {
    InternalTransaction transaction = new InternalTransaction(parentHash, deep,
        size(internalTransactions), senderAddress, transferAddress, value, data, note, nonce,
        token);
    getInternalTransactions().add(transaction);
    return transaction;
  }
```
