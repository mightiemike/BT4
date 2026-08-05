### Title
Unbounded linear-search list mutation in legacy DelegatedResourceAccountIndex causes gas/compute griefing - ([File: actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java] and [File: actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java])

### Summary
When `DynamicPropertiesStore.supportAllowDelegateOptimization()` is disabled, `FreezeBalanceActuator` and `UnfreezeBalanceActuator` maintain the `DelegatedResourceAccountIndexCapsule.toAccountsList` / `fromAccountsList` for the owner and receiver addresses using linear `List.contains()` / `List.remove()` operations, exactly mirroring the root cause of the reported Move `vector::index_of` pattern. Because any address can freeze a trivial amount and delegate it to an arbitrary receiver address without that receiver's consent, an attacker can inflate a victim receiver's `fromAccountsList` with many junk entries from throwaway owner accounts. Anyone later delegating to or undelegating from that same receiver pays an O(n) list scan proportional to the attacker-inflated list size, while their own transaction fee (bandwidth) is fixed and independent of that cost.

### Finding Description
In the legacy (non-optimized) delegation index path, growing an account's index list is O(1) `contains()`-guarded append per call: [1](#0-0) 

and removal on unfreeze is an explicit linear search/removal by value: [2](#0-1) 

Both `toAccountsList` (owned by the delegator) and `fromAccountsList` (owned by the receiver) are mutated this way. Critically, the receiver's `fromAccountsList` entry is appended whenever *any* other account delegates to that receiver — the receiver does not sign or approve this operation (`DelegateResourceContract`/`FreezeBalanceContract` require only the owner/delegator's signature). This is the same trust asymmetry as the Move report: an unprivileged, unrelated actor can cause growth of a data structure attached to a victim address, and a *different* party later pays for the O(n) traversal cost caused by that growth. In java-tron this differs from EVM/Aptos gas metering in one respect: system contract execution here is billed via fixed-size bandwidth points, not per-computation gas, so the list-size-dependent CPU cost is not charged proportionally to the caller — this shifts the impact from "attacker under-prices a victim's tx" to "attacker forces disproportionate, unpriced node computation" (state/accounting + underpriced-public-work class).

The `contains()` calls in `FreezeBalanceActuator` (lines 329, 342) are also O(n) per invocation, meaning even the append operation degrades as the shared list grows, worsening the effect for every future delegator to the same address.

### Impact Explanation
Any address delegating to or undelegating from a receiver whose index list has been artificially inflated is forced to pay for an O(n) scan inside `UnfreezeBalanceActuator.execute()` / `FreezeBalanceActuator.delegateResource()`, with cost that is entirely attacker-controlled and unbounded (limited only by attacker's willingness to create many throwaway freeze/delegate transactions targeting the same receiver, each of which is cheap since it only costs the fixed bandwidth fee for that tx type). This constitutes underpriced public work: transaction fees are flat regardless of list size, but node processing time for genuine, legitimate delegate/undelegate transactions touching the poisoned address grows linearly, degrading validator/node throughput and potentially serving as a targeted DoS vector against specific high-traffic receiver addresses (e.g., exchanges or resource-delegation services that receive delegations from many parties).

### Likelihood Explanation
Exploitability is gated entirely on `DynamicPropertiesStore.supportAllowDelegateOptimization()` being disabled for the relevant chain/network. This flag is committee-controlled (proposal-gated, per its usage across `ProposalService.java`/`Wallet.java`), so mainnet may have already enabled the optimized path, in which case `delegate()`/`unDelegate()` use a prefix-scan KV design (`DelegatedResourceAccountIndexStore.delegate`/`unDelegate`) rather than the vulnerable in-memory list mutation. I was unable to confirm from the available index whether this flag defaults to enabled on the target deployment, or whether the legacy branch remains reachable on any live/private network built from this codebase. If the optimization is not enabled (e.g., private chains, testnets, or the flag not yet been voted on for a given network), the legacy vulnerable code path is fully reachable by any unprivileged user via ordinary `FreezeBalanceContract`/`DelegateResourceContract`/`UnfreezeBalanceContract` transactions.

### Recommendation
- Remove or fully retire the legacy `!supportAllowDelegateOptimization()` list-based branch in `FreezeBalanceActuator` and `UnfreezeBalanceActuator`, forcing all networks onto the prefix-scan KV-based `DelegatedResourceAccountIndexStore.delegate`/`unDelegate` design, which avoids storing/searching an unbounded list per account.
- If backward compatibility must be preserved, cap the number of distinct addresses a single account's `toAccountsList`/`fromAccountsList` may contain, or convert to a keyed (per-pair) store analogous to `DelegatedResourceStore`'s design instead of an embedded repeated field requiring linear scans.
- Audit other `List.remove(Object)` / `List.contains(Object)` usages over protobuf-repeated fields tied to attacker-influenceable, third-party-writable state for the same pattern.

### Proof of Concept
1. On a network where `supportAllowDelegateOptimization()` is false, an attacker creates N throwaway accounts A1..AN, each funds a minimal TRX balance, freezes it (`FreezeBalanceContract`), and delegates it to victim receiver address R (`FreezeBalanceActuator.delegateResource`, since `receiverAddress` need not consent) — this appends one entry per attacker account to `R`'s `fromAccountsList` at [3](#0-2) .
2. A legitimate third party V, who has an unrelated pre-existing delegation to R, submits `UnfreezeBalanceContract` to undelegate from R.
3. `UnfreezeBalanceActuator.execute()` reaches the branch at [4](#0-3) , performing `new ArrayList<>(receiverIndexCapsule.getFromAccountsList())` followed by `fromAccountsList.remove(ByteString.copyFrom(ownerAddress))` — an O(N) linear scan/removal over the attacker-inflated list, executed at V's expense in node processing time despite V paying only the fixed bandwidth fee for an `UnfreezeBalanceContract` transaction.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L328-345)
```java
      List<ByteString> toAccountsList = ownerIndexCapsule.getToAccountsList();
      if (!toAccountsList.contains(ByteString.copyFrom(receiverAddress))) {
        ownerIndexCapsule.addToAccount(ByteString.copyFrom(receiverAddress));
      }
      delegatedResourceAccountIndexStore.put(ownerAddress, ownerIndexCapsule);

      DelegatedResourceAccountIndexCapsule receiverIndexCapsule
          = delegatedResourceAccountIndexStore.get(receiverAddress);
      if (receiverIndexCapsule == null) {
        receiverIndexCapsule = new DelegatedResourceAccountIndexCapsule(
            ByteString.copyFrom(receiverAddress));
      }
      List<ByteString> fromAccountsList = receiverIndexCapsule
          .getFromAccountsList();
      if (!fromAccountsList.contains(ByteString.copyFrom(ownerAddress))) {
        receiverIndexCapsule.addFromAccount(ByteString.copyFrom(ownerAddress));
      }
      delegatedResourceAccountIndexStore.put(receiverAddress, receiverIndexCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L163-182)
```java
        if (!dynamicStore.supportAllowDelegateOptimization()) {
          DelegatedResourceAccountIndexCapsule ownerIndexCapsule =
              delegatedResourceAccountIndexStore.get(ownerAddress);
          if (ownerIndexCapsule != null) {
            List<ByteString> toAccountsList = new ArrayList<>(ownerIndexCapsule
                .getToAccountsList());
            toAccountsList.remove(ByteString.copyFrom(receiverAddress));
            ownerIndexCapsule.setAllToAccounts(toAccountsList);
            delegatedResourceAccountIndexStore.put(ownerAddress, ownerIndexCapsule);
          }

          DelegatedResourceAccountIndexCapsule receiverIndexCapsule =
              delegatedResourceAccountIndexStore.get(receiverAddress);
          if (receiverIndexCapsule != null) {
            List<ByteString> fromAccountsList = new ArrayList<>(receiverIndexCapsule
                .getFromAccountsList());
            fromAccountsList.remove(ByteString.copyFrom(ownerAddress));
            receiverIndexCapsule.setAllFromAccounts(fromAccountsList);
            delegatedResourceAccountIndexStore.put(receiverAddress, receiverIndexCapsule);
          }
```
