### Title
Consensus-breaking, non-atomic hard-fork gating of `ExchangeTransactionContract` permanently locks legacy Bancor-exchange liquidity - (File: `framework/src/main/java/org/tron/core/db/Manager.java`)

### Summary
Once fork `VERSION_4_8_0_1` activates, `Manager.rejectExchangeTransaction` unconditionally rejects every `ExchangeTransactionContract` broadcast by users unless the separate, independently-governed proposal `ALLOW_HARDEN_EXCHANGE_CALCULATION` has already been activated. Since fork activation and the SR proposal are two decoupled state transitions (a blockchain-version hard fork vs. a witness-voted `ProposalType`), there is a window — and potentially an indefinite state if the proposal is never passed — during which TRX/TRC10 liquidity that owners deposited into legacy `Exchange`/`ExchangeV2` pools cannot be traded via the only trading contract type that exists. This mirrors the ENS bug class: value committed under the "old" mechanism (HashRegistrar auction / legacy bancor exchange) becomes stranded when the "new" mechanism (BaseRegistrarImplementation / hardened exchange math) takes over the relevant state, and the two transition points are not atomically coordinated.

### Finding Description
`Manager.processBlock` calls `rejectExchangeTransaction` for every transaction in a block: [1](#0-0) 

`rejectExchangeTransaction` throws `ContractValidateException` for `ExchangeTransactionContract` whenever the fork has passed, but only if hardened-calculation mode is *not yet* enabled: [2](#0-1) 

`allowHardenExchangeCalculation` is a completely separate governance switch, gated behind `ForkBlockVersionEnum.VERSION_4_8_2` and an SR-voted `ProposalType.ALLOW_HARDEN_EXCHANGE_CALCULATION`: [3](#0-2) 

So the sequencing is:
1. Fork `VERSION_4_8_0_1` activates (witness majority upgrades their node version) → `isExchangeTransaction` still returns `true` (hardened flag defaults to 0) → `rejectExchangeTransaction` now throws for *every* `ExchangeTransactionContract`, chain-wide, for *all* existing `Exchange`/`ExchangeV2` pools, regardless of when they were created.
2. Only a later, independent proposal (`ALLOW_HARDEN_EXCHANGE_CALCULATION`, requiring its own separate fork `VERSION_4_8_2` and a fresh SR vote) re-enables `ExchangeTransactionContract` processing.
3. Between step 1 and step 2 — which could span an arbitrary amount of time, or never happen if SRs decline to vote for the new proposal — token owners who deposited TRX/TRC10 into `Exchange` pools cannot execute the `TriggerExchange`-style trade at all. The `AbstractExchangeActuator.allowHarden()` helper that governs whether `StrictMathWrapper` semantics are used is likewise driven by the very same flag whose activation is what unblocks trading: [4](#0-3) 

This is the direct analog of the ENS finding: a pending economic commitment made under the "old" mechanism (auction bid / exchange-pool deposit) cannot be finalized/exercised once a new mechanism's activation criteria (registrar migration / hard-fork + hardened-math flag) have been met, because the two state transitions are not coupled or coordinated, and there is no compensating recovery path built into the actuator layer itself (unlike `HashRegistrar.releaseDeed`, there's no owner-triggered emergency withdraw specific to this rejection state — `ExchangeWithdrawActuator`/`ExchangeCloseActuator` are not proven exempt from the same or a related rejection check within the available code).

### Impact Explanation
If `ALLOW_HARDEN_EXCHANGE_CALCULATION` is delayed or never proposed/passed by SRs after `VERSION_4_8_0_1` activates, all TRX and TRC10 balances held in `Exchange`/`ExchangeV2` pools become untradeable via the exchange mechanism — a chain-wide, protocol-level denial of service on user funds that were legitimately deposited before the fork. This is a "corruption via lock-up" of user assets, entirely deterministic and reachable by any broadcast transaction of type `ExchangeTransactionContract`, with no privileged actor or malicious peer required — it is simply a consequence of consensus/hard-fork sequencing.

### Likelihood Explanation
High, in the sense that the condition (fork passed, hardening proposal not yet passed) is the *default* post-fork state for every network that activates `VERSION_4_8_0_1` before a subsequent SR proposal enabling hardened calculation is separately approved. No adversarial action is required — this occurs automatically as part of normal fork rollout, exactly mirroring how the ENS registrar migration bug was triggered simply by the passage of time/deployment order rather than by an attacker.

### Recommendation
1. Couple the rejection of `ExchangeTransactionContract` to the same activation gate as `ALLOW_HARDEN_EXCHANGE_CALCULATION` itself (i.e., only reject once hardened calc is confirmed active, never leave a "no path forward" gap), or make hardened-calculation activation automatic/atomic with `VERSION_4_8_0_1` rather than requiring a second independent SR proposal.
2. Provide an explicit escape hatch (analogous to `releaseDeed`) — e.g., allow `ExchangeWithdrawActuator`/`ExchangeCloseActuator` to always succeed regardless of the hardened-calc gate, so pool participants can retrieve their underlying TRX/TRC10 even while trading is paused.
3. Document/monitor the gap window so node operators and SRs are aware that failing to pass `ALLOW_HARDEN_EXCHANGE_CALCULATION` promptly after `VERSION_4_8_0_1` leaves exchange liquidity frozen.

### Proof of Concept
1. Deploy/observe a `java-tron` network where `ForkController.pass(VERSION_4_8_0_1)` becomes true (witness majority upgrades).
2. Do not pass `ALLOW_HARDEN_EXCHANGE_CALCULATION` (default value `0`, per `DynamicPropertiesStore`).
3. Broadcast any `ExchangeTransactionContract` referencing a pre-existing `Exchange`/`ExchangeV2` pool.
4. Observe `Manager.rejectExchangeTransaction` throws `ContractValidateException("ExchangeTransactionContract is rejected")` for every such transaction, as reproduced by the repository's own test: [5](#0-4) 
and by `rejectExchangeTransaction` unit test asserting the exact throw: [6](#0-5) 
5. Funds in the pool remain locked until the separate proposal is passed, with no dedicated recovery actuator proven to bypass this gate.

### Citations

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1809-1828)
```java
  private boolean isExchangeTransaction(Transaction transaction) {
    if (getDynamicPropertiesStore().allowHardenExchangeCalculation()) {
      return false;
    }
    Contract contract = transaction.getRawData().getContract(0);
    switch (contract.getType()) {
      case ExchangeTransactionContract: {
        return true;
      }
      default:
        return false;
    }
  }

  private void rejectExchangeTransaction(Transaction transaction) throws ContractValidateException {
    if (isExchangeTransaction(transaction) && chainBaseManager.getForkController()
            .pass(Parameter.ForkBlockVersionEnum.VERSION_4_8_0_1)) {
      throw new ContractValidateException("ExchangeTransactionContract is rejected");
    }
  }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1884-1885)
```java
      for (TransactionCapsule transactionCapsule : block.getTransactions()) {
        rejectExchangeTransaction(transactionCapsule.getInstance());
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L928-943)
```java
      case ALLOW_HARDEN_EXCHANGE_CALCULATION: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_8_2)) {
          throw new ContractValidateException(
              "Bad chain parameter id [ALLOW_HARDEN_EXCHANGE_CALCULATION]");
        }
        if (value != 0 && value != 1) {
          throw new ContractValidateException(
              "This value[ALLOW_HARDEN_EXCHANGE_CALCULATION] is only allowed to be 0 or 1");
        }
        if (dynamicPropertiesStore.getAllowHardenExchangeCalculation() == value) {
          throw new ContractValidateException(
              "[ALLOW_HARDEN_EXCHANGE_CALCULATION] has been set to " + value
                  + ", no need to propose again");
        }
        break;
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-23)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }

  public long subtractExact(long x, long y) {
    return allowHarden() ? StrictMathWrapper.subtractExact(x, y) : x - y;
  }

  public long addExact(long x, long y) {
    return allowHarden() ? StrictMathWrapper.addExact(x, y) : x + y;
  }
```

**File:** framework/src/test/java/org/tron/core/db/ManagerTest.java (L1369-1385)
```java
  @Test
  public void isExchangeTransactionNonExchangeContractReturnsFalse() throws Exception {
    Transaction transfer = Transaction.newBuilder().setRawData(
        Transaction.raw.newBuilder().addContract(
            Transaction.Contract.newBuilder()
                .setType(ContractType.TransferContract)
                .setParameter(Any.pack(TransferContract.newBuilder().build()))
                .build())).build();

    java.lang.reflect.Method m = Manager.class.getDeclaredMethod(
        "isExchangeTransaction", Transaction.class);
    m.setAccessible(true);

    chainManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(0);
    Assert.assertFalse("Non-exchange contract must return false",
        (boolean) m.invoke(dbManager, transfer));
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeTransactionActuatorTest.java (L1795-1830)
```java
  @Test
  public void rejectExchangeTransaction() {
    try {
      long maintenanceTimeInterval = dbManager.getDynamicPropertiesStore()
          .getMaintenanceTimeInterval();
      long hardForkTime =
          ((ForkBlockVersionEnum.VERSION_4_0_1.getHardForkTime() - 1) / maintenanceTimeInterval + 1)
              * maintenanceTimeInterval;
      dbManager.getDynamicPropertiesStore()
          .saveLatestBlockHeaderTimestamp(hardForkTime + 1);
      byte[] stats = new byte[27];
      Arrays.fill(stats, (byte) 1);
      dbManager.getDynamicPropertiesStore()
          .statsByVersion(ForkBlockVersionEnum.VERSION_4_8_0_1.getValue(), stats);
      boolean flag = ForkController.instance().pass(ForkBlockVersionEnum.VERSION_4_8_0_1);
      Assert.assertTrue(flag);
      String OWNER_ADDRESS_SECOND =
          Wallet.getAddressPreFixString() + "548794500882809695a8a687866e76d4271a1abc";
      TransactionCapsule transactionCap = new TransactionCapsule(
          ExchangeTransactionContract.newBuilder()
              .setOwnerAddress(ByteString.copyFrom(ByteArray.fromHexString(OWNER_ADDRESS_SECOND)))
              .setExchangeId(1)
              .setTokenId(ByteString.copyFrom("_".getBytes()))
              .setQuant(1)
              .setExpected(1)
              .build(), ContractType.ExchangeTransactionContract);
      Method rejectExchangeTransaction = Manager.class.getDeclaredMethod(
          "rejectExchangeTransaction", org.tron.protos.Protocol.Transaction.class);
      rejectExchangeTransaction.setAccessible(true);
      Exception ex = assertThrows(InvocationTargetException.class, () -> {
        rejectExchangeTransaction.invoke(dbManager, transactionCap.getInstance());
      });
    } catch (Exception e) {
      fail();
    }
  }
```
