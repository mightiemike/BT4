### Title
Phantom STX-only reward leak for below-threshold signers in `pox-5.clar` reward settlement - (File: `stackslib/src/chainstate/stacks/boot/pox-5.clar`)

### Summary
`pox-5.clar`'s reward-settlement logic contains the same class of bug as the Canto Market.sol finding: a reward snapshot is advanced for a party whose current-period contribution should not count, and a downstream party's "since-last-settlement" reward computation is later compared against that corrupted snapshot, producing a reward it never earned. Here it manifests as a signer that never crossed `SIGNER_SET_MIN_USTX` (and therefore has zero `signer-shares-staked-for-cycle`) nonetheless causing its stakers to be credited with STX-only rewards for a reward cycle in which the signer was not a member of the reward set.

### Finding Description
`settle-rewards` is the function that must run before any staked-share mutation to "crystallize" earned-but-unclaimed rewards for a signer: [1](#0-0) 

It reads the signer's current shares, computes `earned` off the *global* `rewards-per-token-for-cycle`, and unconditionally updates `signer-rewards-per-token-settled-for-cycle` to that global value — but it only updates `signer-rewards-per-token-for-cycle` (the per-signer value that stakers key their own settlement off of) when `shares > 0`:

```
(map-set signer-rewards-per-token-settled-for-cycle {...} rewards-per-token)
(if (> shares u0)
    (map-set signer-rewards-per-token-for-cycle {...} rewards-per-token)
    true)
```

Stakers compute their own earnings against `signer-rewards-per-token-for-cycle` (not the "settled" map used for the signer's own accounting): [2](#0-1) 

`settle-staker-rewards`, invoked whenever a staker's shares change, snapshots the staker against `get-signer-rewards-per-token-for-cycle`: [3](#0-2) 

`add-staker-to-signer-for-cycle` only records the signer/staker as having shares once the *delegated* total crosses `SIGNER_SET_MIN_USTX`; below that, `signer-shares-staked-for-cycle` stays at 0 even though `settle-rewards` is still called (and still runs the unconditional `settled` update): [4](#0-3) 

`claim-rewards` unconditionally calls `update-claimable-rewards` (→ `settle-rewards`) for `bond-index none` (the STX-only pool) every time *any* bond in `bond-periods` is claimed, regardless of whether the signer is actually part of the STX-only reward set for that cycle: [5](#0-4) 

Because a query on `signer-rewards-per-token-for-cycle` for a key that was never explicitly `map-set` falls back to reading the current global rate (rather than being pinned at the time the signer's `shares` were zero), a staker whose `staker-rewards-per-token-settled-for-cycle` was snapshotted early (when the global rate was low) will, after `calculate-rewards` advances the global rate and a bond claim re-triggers `settle-rewards` on a signer still at `shares == 0`, see `get-earned-staker-rewards` compute a large non-zero `earned` value — for a period during which their signer held no weight in the reward set at all.

This is directly analogous to the Canto bug: a party's reward-claim/settlement bookkeeping (`rewardsLastClaimedValue` / `signer-rewards-per-token-settled-for-cycle`) is updated using a fee/rate split that has already progressed past the point the party's real contribution should be counted, causing rewards to be paid to (or withheld from) the wrong party.

### Impact Explanation
This breaks the equality that STX-only rewards for a cycle must only accrue to signers/stakers whose delegated stake met `SIGNER_SET_MIN_USTX` (i.e., were actually part of the reward-earning set) during that cycle. An unprivileged staker can be paid sBTC rewards they never earned, at the expense of the shared reward pool (which otherwise would go to the reserve or to legitimately-participating stakers/signers). This is a reward mis-payment bounded to the sBTC rewards pool of `pox-5`, matching the "High – reward mis-payment bounded to fees" impact class; it requires no majority collusion, no admin key, and no node-operator privilege — any staker below threshold can trigger it via ordinary staking + a bond claim.

### Likelihood Explanation
Reachable by a single unprivileged account: stake STX to a signer that stays below `SIGNER_SET_MIN_USTX`, have any other participant on that same signer register a bond, wait for a `calculate-rewards` call that advances the global STX-only rate (e.g., because bond surplus flows into the STX waterfall), then have anyone call `claim-rewards` for that bond. This is a normal user flow, not requiring cooperation from a majority of signers.

### Recommendation
`settle-rewards` should not advance `signer-rewards-per-token-settled-for-cycle` (or leave `signer-rewards-per-token-for-cycle` implicitly defaulting to the live global rate) for a signer whose `shares == 0`; the per-signer rate snapshot used by staker settlement must remain pinned at the value it had the last time the signer actually had `shares > 0`, exactly as the Canto fix required fees to be split (state advanced) before, not after, the requesting party's own reward baseline is read/written.

### Proof of Concept
The bug is already reproduced by the repository's own regression test, which fails against the current unfixed contract: [6](#0-5) 

The test sets up `signer1` with a bond participant (`bob`) plus an STX-only staker (`alice`) sized so `signer1` stays below `SIGNER_SET_MIN_USTX`; a second signer (`signer2`) is independently above threshold so the global STX-only rate advances after `calculateRewards`. After `testSigner.claimRewards([0n], 1n)` is called (a bond-0 claim, which internally triggers `update-claimable-rewards` for the STX-only pool as well), the test asserts `getEarnedStakerRewards(alice, 1n, null)` is still `0n` — and the test's own comments state this assertion is expected to fail on the unfixed code, i.e., alice ends up owed phantom STX-only rewards for a cycle her signer never earned in.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1663-1704)
```text
(define-private (add-staker-to-signer-for-cycle
        (cycle-index uint)
        (accumulator-res (response {
            signer: principal,
            staker: principal,
            amount-ustx: uint,
            first-reward-cycle: uint,
            is-stx-staking: bool,
        }
            uint
        ))
    )
    (let (
            (accumulator (try! accumulator-res))
            (cycle (+ cycle-index (get first-reward-cycle accumulator)))
            (signer (get signer accumulator))
            ;; Get the total uSTX delegated (through protocol bonds and STX-only
            ;; staking) to this signer.
            (cur-delegated-for-signer (get-amount-delegated-for-signer signer cycle))
            (amount (get amount-ustx accumulator))
            (stake-amount (if (get is-stx-staking accumulator)
                amount
                u0
            ))
            (staker (get staker accumulator))
            (prev-staked (get-signer-pending-staked-ustx-per-cycle signer cycle))
            (prev-total-shares-staked (get-total-shares-staked-for-cycle cycle none))
            (new-delegated (+ cur-delegated-for-signer amount))
            (prev-staker-shares (get-staker-shares-staked-for-cycle staker cycle none signer))
        )
        ;; Crystallize STX-only rewards before mutating anything
        (settle-rewards signer cycle none)
        ;; When zero, this is a no-op (`earned = shares * (rpt - rpt-paid) = 0`). In this case,
        ;; we skip calling `settle-staker-rewards` to reduce cost.
        (if (> prev-staker-shares u0)
            (settle-staker-rewards signer cycle none staker)
            {
                earned: u0,
                rewards-per-token: u0,
            }
        )

```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L2356-2373)
```text
;; Get the total amount of _staker_ rewards earned since the last
;; rewards snapshot.
(define-read-only (get-earned-staker-rewards
        (signer principal)
        (reward-cycle uint)
        (bond-index (optional uint))
        (staker principal)
    )
    (compute-earned-rewards
        (get-staker-shares-staked-for-cycle staker reward-cycle bond-index signer)
        (get-signer-rewards-per-token-for-cycle signer reward-cycle bond-index)
        (get-staker-rewards-per-token-settled-for-cycle signer reward-cycle
            bond-index staker
        )
        (get-staker-unclaimed-rewards-for-cycle signer reward-cycle bond-index
            staker
        ))
)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L2387-2438)
```text
(define-public (claim-rewards
        (bond-periods (list 6 uint))
        (reward-cycle uint)
    )
    (let (
            (signer contract-caller)
            (stx-rewards (update-claimable-rewards signer reward-cycle none))
            (bond-rewards (fold update-claimable-bond-rewards bond-periods {
                signer: signer,
                total: u0,
                bond-rewards: (list),
                reward-cycle: reward-cycle,
            }))
            (bond-totals (get total bond-rewards))
            (total-rewards (+ (get earned stx-rewards) bond-totals))
            (prev-accrued-rewards (var-get last-accounted-rewards-only))
        )
        (asserts! (not (var-get rewards-paused)) ERR_REWARDS_PAUSED)
        ;; ensure no reentrancy through signer-manager trait calls
        (try! (validate-no-reentrancy))

        (asserts! (> total-rewards u0) ERR_NO_CLAIMABLE_REWARDS)
        (try! (as-contract?
            ((with-ft 'SM3VDXK3WZZSA84XXFKAFAF15NNZX32CTSG82JFQ4.sbtc-token
                "sbtc-token" total-rewards
            ))
            (try! (contract-call? 'SM3VDXK3WZZSA84XXFKAFAF15NNZX32CTSG82JFQ4.sbtc-token
                transfer total-rewards tx-sender signer none
            ))
        ))
        ;; Update contract reward snapshot to prevent issues in next calculation
        (var-set last-accounted-rewards-only
            (- prev-accrued-rewards total-rewards)
        )

        (let ((result {
                stx-rewards: stx-rewards,
                bond-rewards: (get bond-rewards bond-rewards),
                bond-totals: bond-totals,
                total-rewards: total-rewards,
            }))
            (print (merge {
                topic: "claim-rewards",
                reward-cycle: reward-cycle,
                signer-manager: contract-caller,
            }
                result
            ))
            (ok result)
        )
    )
)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L2530-2574)
```text
(define-private (settle-rewards
        (signer principal)
        (reward-cycle uint)
        (bond-index (optional uint))
    )
    (let (
            (shares (get-signer-shares-staked-for-cycle signer reward-cycle bond-index))
            (rewards-per-token (get-rewards-per-token-for-cycle reward-cycle bond-index))
            (earned (compute-earned-rewards
                shares
                rewards-per-token
                (get-signer-rewards-per-token-settled-for-cycle signer reward-cycle bond-index)
                (get-signer-unclaimed-rewards-for-cycle signer reward-cycle bond-index)
            ))
        )
        (map-set signer-unclaimed-rewards-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: bond-index,
            signer: signer,
        }
            earned
        )
        (map-set signer-rewards-per-token-settled-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: bond-index,
            signer: signer,
        }
            rewards-per-token
        )
        (if (> shares u0)
            (map-set signer-rewards-per-token-for-cycle {
                signer: signer,
                reward-cycle: reward-cycle,
                bond-index: bond-index,
            }
                rewards-per-token
            )
            true
        )
        {
            earned: earned,
            rewards-per-token: rewards-per-token,
        }
    )
)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L2581-2610)
```text
(define-private (settle-staker-rewards
        (signer principal)
        (reward-cycle uint)
        (bond-index (optional uint))
        (staker principal)
    )
    (let (
            (earned (get-earned-staker-rewards signer reward-cycle bond-index staker))
            (rewards-per-token (get-signer-rewards-per-token-for-cycle signer reward-cycle
                bond-index
            ))
        )
        (map-set staker-unclaimed-rewards-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: bond-index,
            signer: signer,
            staker: staker,
        }
            earned
        )
        (map-set staker-rewards-per-token-settled-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: bond-index,
            signer: signer,
            staker: staker,
        }
            rewards-per-token
        )
        {
            earned: earned,
```

**File:** contrib/core-contract-tests/tests/pox-5/pox-5.test.ts (L6064-6157)
```typescript
test('below-threshold signer leaks phantom stx-only rewards via bond co-claim', () => {
  const signer1 = testSigner.identifier;
  const signer2 = deployTestSigner('phantom-bond-signer-2').identifier;
  const bobSbtc = 400_000n;
  const targetRate = 1200n;

  registerSigner();

  // Bond 0 with bob as the lone participant on signer1. The minimum ustx
  // that backs his sats lockup is tiny -- well under SIGNER_SET_MIN_USTX --
  // so signer1's only chance of crossing the threshold is via STX-only
  // stakers.
  txOk(
    pox5.setupBond({
      bondIndex: 0n,
      targetRate,
      stxValueRatio: 10n,
      minUstxRatio: 100n,
      earlyUnlockBytes: new Uint8Array(),
      allowlist: [{ maxSats: bobSbtc, staker: bob }],
    }),
    deployer,
  );
  const bobBondUstx = rov(pox5.minUstxForSatsAmount(bobSbtc, 10n, 100n));
  txOk(
    pox5.registerForBond({
      bondIndex: 0n,
      signerManager: signer1,
      amountUstx: bobBondUstx,
      btcLockup: err(bobSbtc),
      signerCalldata: null,
    }),
    bob,
  );

  // Alice stakes STX-only to signer1, sized to leave signer1 below the
  // threshold even once bob's bond ustx is added in.
  const aliceStake = stxToUStx(40_000);
  expect(aliceStake + bobBondUstx).toBeLessThan(
    pox5.constants.SIGNER_SET_MIN_USTX,
  );
  txOk(
    pox5.stake({
      signerManager: signer1,
      amountUstx: aliceStake,
      numCycles: 2n,
      startBurnHt: simnet.burnBlockHeight,
      signerCalldata: null,
    }),
    alice,
  );

  // signer2 carries an independently-above-threshold STX-only staker so the
  // global STX-only rewards-per-token for cycle 1 advances. Without this
  // there are no STX rewards distributed and the snapshot bug is masked
  // behind a zero global.
  txOk(
    pox5.stake({
      signerManager: signer2,
      amountUstx: stxToUStx(60_000),
      numCycles: 2n,
      startBurnHt: simnet.burnBlockHeight,
      signerCalldata: null,
    }),
    charlie,
  );

  expect(isSignerInCycle({ signer: signer1, cycle: 1n })).toBe(false);
  expect(isSignerInCycle({ signer: signer2, cycle: 1n })).toBe(true);
  expect(rov(pox5.getSignerSharesStakedForCycle(signer1, 1n, null))).toBe(0n);

  // Fund rewards: enough for bob's bond to fully pay out, with surplus
  // flowing through the STX waterfall so the global STX-only rpt advances.
  sbtcTransfer(1000n, deployer, pox5.identifier);
  mineUntil(rov(pox5.rewardCycleToBurnHeight(1n)) + HALF_CYCLE_LENGTH);
  txOk(pox5.calculateRewards([0n]), deployer);

  // Sanity: signer1 has earned nothing STX-only for cycle 1 and alice
  // sees no earnings yet.
  expect(rov(pox5.getEarned(signer1, 1n, null))).toBe(0n);
  expect(rov(testSigner.getEarnedStakerRewards(alice, 1n, null))).toBe(0n);

  // Trigger the bond claim. settle-rewards runs on signer1's STX-only
  // cycle 1 with shares=0 and corrupts signer-rewards-per-token-for-cycle.
  txOk(testSigner.claimRewards([0n], 1n), deployer);

  // signer1's STX-only earnings remain 0 -- it never contributed.
  expect(rov(pox5.getEarned(signer1, 1n, null))).toBe(0n);

  // Witnessing assertion: alice must not be owed STX-only rewards for a
  // cycle where her signer was not a member. Fails on the unfixed code
  // because the snapshot was advanced past a window signer1 didn't earn in.
  expect(rov(testSigner.getEarnedStakerRewards(alice, 1n, null))).toBe(0n);
});
```
