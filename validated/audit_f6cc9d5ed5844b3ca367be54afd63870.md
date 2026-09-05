### Title
STX-only signer reward settlement runs with `shares == 0`, corrupting `signer-rewards-per-token-for-cycle` and paying rewards to a staker whose signer never earned them - (File: stackslib/src/chainstate/stacks/boot/pox-5.clar)

### Summary
This is the same bug class as the MetaStreet report — an accounting function is invoked (or an entry is allowed to remain live) on a value/shares state that is degenerate (zero shares / near-zero value), and the resulting stale/garbage snapshot is later used to pay out real value to a party who never contributed. In `pox-5.clar`, `settle-rewards` is called unconditionally on a signer's STX-only reward bucket for a cycle even when that signer's `total-shares-staked-for-cycle`/`signer-shares-staked-for-cycle` bucket is `0` (i.e., the signer never crossed `SIGNER_SET_MIN_USTX` for STX-only staking that cycle, analogous to a "node" with no real value/shares). This mutates `signer-rewards-per-token-for-cycle` based on stale global state, and a staker who is later added to that signer bucket inherits a `rewards-per-token-settled` snapshot that lets them claim rewards for a window in which their signer was never actually a paying member of the reward set.

### Finding Description
`add-staker-to-signer-for-cycle` (used when staking or updating a bond registration) and `remove-staker-from-signer-for-cycle` (used on unstake) both invoke `settle-rewards signer cycle none` *before* checking whether the signer is currently over `SIGNER_SET_MIN_USTX` for that cycle's STX-only bucket: [1](#0-0) [2](#0-1) 

This mirrors the LiquidityManager pattern: an operation that should only be meaningful when `shares > 0` (a healthy, participating node/signer) is instead run against a degenerate bucket (`shares == 0`), and the resulting settled state (`rewards-per-token-settled`) is then carried forward and later used to compute another party's claimable rewards via `staker-rewards-per-token-settled-for-cycle`: [3](#0-2) 

The in-repo test suite already contains a concrete, currently-present regression demonstrating this exact class of bug reachable by an unprivileged staker/signer-owner combination with no majority or admin assistance: [4](#0-3) 

The scenario: `signer1`'s only STX-only contribution (`alice`'s stake) plus a bond staker (`bob`) is deliberately kept below `SIGNER_SET_MIN_USTX`, so `signer1` never enters the STX-only reward-earning set for cycle 1 (`getSignerSharesStakedForCycle(signer1, 1n, null)` returns `0`). A second signer (`signer2`) has an independently qualifying STX-only staker, so the *global* per-token accounting for the STX-only reward pool advances during `calculateRewards`. When `bob`'s bond claim later triggers `settle-rewards` on `signer1`'s STX-only bucket (with `shares = 0`), the settlement snapshot for `signer1` is advanced/corrupted — and per the test's own annotation, this snapshot advance leaks into `alice`'s owed STX-only rewards even though `alice`'s signer (`signer1`) never actually earned anything from the STX-only reward pool that cycle.

### Impact Explanation
This breaks the equality "a staker's claimable reward must correspond exactly to the reward-per-token accrued by shares that were actually part of the earning set." Because the settlement runs on a zero-shares bucket and still advances a rewards-per-token cursor that is later inherited by a real staker, STX-only PoX rewards can be mis-paid to a party whose signer was not a qualifying member of the reward set for that cycle — a reward mis-payment bounded to the STX-only rewards pool. This falls in the "High" category defined by the rules: a reward mis-payment bounded to fees/rewards, achievable by an unprivileged staker/signer combination without requiring a majority of stakers or any privileged key.

### Likelihood Explanation
Triggering this requires only: (1) a signer whose delegated ustx hovers below `SIGNER_SET_MIN_USTX` for STX-only accounting while backed by a bond registration, (2) a second, independent signer with a qualifying STX-only staker so the global reward-per-token cursor is non-zero, and (3) a bond claim or stake/unstake action that invokes `settle-rewards`/`settle-staker-rewards` on the below-threshold signer's bucket. None of these steps require cooperation from a majority of stakers, an admin, or another party's key — a single staker can arrange their own stake size and trigger the claim themselves, matching the "minority-triggerable, unprivileged" scope requirement.

### Recommendation
Guard `settle-rewards`/`settle-staker-rewards` (and any caller of them in `add-staker-to-signer-for-cycle` / `remove-staker-from-signer-for-cycle`) so that they are a no-op (or explicitly skip advancing `signer-rewards-per-token-for-cycle`) whenever the signer's `total-shares-staked-for-cycle`/`signer-shares-staked-for-cycle` bucket for that cycle is `0`, i.e. treat a zero-shares bucket the same way the referenced fix treats an "impaired/empty" node: block accounting mutations and reward accrual against it until real shares exist.

### Proof of Concept
The existing repository test is a self-contained PoC for this exact scenario: [4](#0-3) 

It sets up `signer1` deliberately below `SIGNER_SET_MIN_USTX` (bond staker `bob` + STX-only staker `alice`), a second signer `signer2` above threshold to advance the global STX-only rewards-per-token, funds rewards, calls `calculateRewards`, and then triggers `bob`'s bond claim (`testSigner.claimRewards`) which runs `settle-rewards` on `signer1`'s zero-shares STX-only bucket. The test's final assertion — that `alice` (staked to `signer1`, which never earned any STX-only rewards) must still be owed `0` — is explicitly noted in the test comments to fail on the unfixed code path, confirming the reward-mispayment root cause identified above.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1526-1545)
```text
            ;; Get the total uSTX delegated (through protocol bonds and STX-only
            ;; staking) to this signer.
            (cur-delegated-for-signer (get-amount-delegated-for-signer signer reward-cycle))
            ;; uSTX staked for this signer (through STX-only staking)
            (cur-staked-for-signer (get-signer-shares-staked-for-cycle signer reward-cycle none))
            ;; Total uSTX staked (through stx-only staking) this cycle
            (total-shares-staked (get-total-shares-staked-for-cycle reward-cycle none))
            (amount (get amount-ustx membership))
            (is-stx-staking (get is-stx-staking accumulator))
            (stake-amount (if is-stx-staking
                amount
                u0
            ))
            (new-delegated (- cur-delegated-for-signer amount))
            (is-in-signer-set (is-some (get-signer-set-item-for-cycle signer reward-cycle)))
        )
        ;; Settle STX-only rewards before mutating anything
        (settle-rewards signer reward-cycle none)
        (settle-staker-rewards signer reward-cycle none staker)

```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1690-1705)
```text
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

        (if (>= new-delegated SIGNER_SET_MIN_USTX)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1770-1782)
```text
        (map-set ustx-delegated-per-cycle cycle
            (+ (get-ustx-delegated-for-cycle cycle) amount)
        )
        ;; Mark settled rewards for this cycle
        (map-set staker-rewards-per-token-settled-for-cycle {
            reward-cycle: cycle,
            bond-index: none,
            signer: signer,
            staker: staker,
        }
            (get-signer-rewards-per-token-for-cycle signer cycle none)
        )
        (ok accumulator)
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
