### Title
DoS on DAO Vault-Initialization Proposal by Front-Running the Permissionless `initialize()` Function - (File: `mainnet/contracts/vault/v0-vault-sbtc.clar` and sibling vaults)

### Summary
Each vault contract's `initialize()` function has no caller-authorization check — it only checks `(not (var-get initialized))` — so anyone can call it directly before the DAO's `proposal-init-vaults` executes it. This mirrors the H-08 analog: an unprotected, front-runnable initializer that a griefer can race to break a downstream multi-step deployment/initialization flow.

### Finding Description
`initialize()` in the vault contracts (e.g. `v0-vault-sbtc.clar`) is a public function guarded only by an already-initialized flag, with no restriction on `tx-sender`/`contract-caller`: [1](#0-0) 

The intended flow is for the DAO to initialize all five vaults atomically inside a single proposal, alongside setting supply/debt caps and authorizing the `market` contract, all wrapped in `try!` calls so the whole proposal is atomic: [2](#0-1) 

Because `initialize()` carries no auth check, any address can call `.vault-sbtc initialize` (or any of the other vault contracts) directly, in a separate preceding transaction, setting `initialized` to `true` before the DAO's proposal transaction executes. When the DAO's `proposal-init-vaults` later runs and reaches `(try! (contract-call? .vault-sbtc initialize))`, it will fail with `ERR-ALREADY-INITIALIZED`, and since the whole `execute()` is chained with `try!` calls inside `dao-executor`'s `execute-proposal`, the entire proposal transaction reverts atomically: [3](#0-2) 

This is the same bug class as the reported issue: a value-bearing initializer that must be reserved for a specific caller/flow is instead open to anyone, and a single front-run transaction permanently invalidates the state the later multi-step transaction depends on, causing the whole downstream flow to abort.

### Impact Explanation
Because `proposal-init-vaults.execute()` bundles setting supply/debt caps, calling `initialize` on all five vaults, and authorizing the `market` contract as one atomic sequence, a single front-run `initialize()` call on any one vault causes the entire proposal to revert on every subsequent attempt (as long as the proposal script is unchanged). Until governance drafts, votes on, and deploys a new proposal script that tolerates the already-initialized vault, none of the vaults can have their caps set nor have `market` authorized to interact with them — this is a temporary freezing of protocol functionality (deposits/borrows blocked by zero caps, and market unable to call vault reserve functions), which falls under the in-scope "temporary freezing of funds" impact class.

### Likelihood Explanation
Likelihood is moderate: this requires an attacker to monitor deployment activity and front-run the DAO's initialization proposal with a plain, permissionless `initialize()` call on any vault before the proposal transaction confirms — feasible given that `initialize()` has no gating and vault contract addresses are known/predictable pre-deployment. This is a single-transaction race, consistent with the "single-transaction analog" scope (a guard the mutation should have observed but that is not checked against the correct principal), and does not require any privileged access or DAO compromise.

### Recommendation
Restrict `initialize()` to a specific authorized caller (e.g. the DAO/`dao-executor` contract via `check-dao-auth`, or a deployer-only check similar to `dao-multisig`'s `init`), and/or make the `proposal-init-vaults` initialization step tolerant of an already-initialized vault (e.g. check `initialized` before calling `initialize`, or catch the error and continue) so a single front-run cannot abort the entire atomic proposal.

### Proof of Concept
1. Deployer deploys the five vault contracts (`v0-vault-sbtc.clar`, `v0-vault-usdh.clar`, `v0-vault-usdc.clar`, `v0-vault-ststx.clar`, `v0-vault-stx.clar`) and the `dao-executor`/`dao-multisig` contracts, but has not yet submitted/executed `proposal-init-vaults`.
2. Attacker observes the pending deployment (mempool or public chain state) and calls `(contract-call? .vault-sbtc initialize)` directly; since there is no caller check, this succeeds and sets `initialized` to `true`.
3. DAO signers later submit and execute `proposal-init-vaults` via `dao-multisig`/`dao-executor.execute-proposal`.
4. Inside `execute()`, the call `(try! (contract-call? .vault-sbtc initialize))` returns `ERR-ALREADY-INITIALIZED` because the attacker already flipped `initialized`.
5. The `try!` in `execute()` propagates the error up through `dao-executor.execute-proposal`'s own `try!`, causing the entire proposal transaction — including the cap-setting and `market` authorization calls for all five vaults — to revert.
6. The protocol cannot complete vault initialization/authorization until governance re-drafts and re-executes a modified proposal, during which all vault functionality remains blocked (zero caps / unauthorized market), constituting a temporary freeze of protocol operation.

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L492-513)
```text
(define-public (initialize)
  (begin
    (asserts! (not (var-get initialized)) ERR-ALREADY-INITIALIZED)
    (var-set initialized true)
    (try! (deposit MINIMUM-LIQUIDITY u0 NULL-ADDRESS))
    
    (print {
      action: "vault-initialize",
      caller: contract-caller,
      data: {
        vault: UNDERLYING,
        minimum-liquidity: MINIMUM-LIQUIDITY
      }
    })
    
    (ok true)))

;; -- Auth management --------------------------------------------------------

(define-public (set-authorized-contract (contract principal) (authorized bool))
  (begin
    (try! (check-dao-auth))
```

**File:** local-testing/contracts/proposals/proposal-init-vaults.clar (L10-43)
```text
(define-public (execute)
  (begin
    ;; Set vault caps before initialization
    ;; Called directly since we're already in dao-executor's context
    (try! (contract-call? .vault-sbtc set-cap-supply CAP))
    (try! (contract-call? .vault-sbtc set-cap-debt CAP))
    (try! (contract-call? .vault-usdh set-cap-supply CAP))
    (try! (contract-call? .vault-usdh set-cap-debt CAP))
    (try! (contract-call? .vault-usdc set-cap-supply CAP))
    (try! (contract-call? .vault-usdc set-cap-debt CAP))
    (try! (contract-call? .vault-ststx set-cap-supply CAP))
    (try! (contract-call? .vault-ststx set-cap-debt CAP))
    (try! (contract-call? .vault-stx set-cap-supply CAP))
    (try! (contract-call? .vault-stx set-cap-debt CAP))
    
    ;; Initialize vaults (mints minimum liquidity)
    ;; Called directly - dao-executor's with-all-assets-unsafe handles asset transfers
    (try! (as-contract? ((with-all-assets-unsafe))
      (try! (contract-call? .vault-sbtc initialize))
      (try! (contract-call? .vault-usdh initialize))
      (try! (contract-call? .vault-usdc initialize))
      (try! (contract-call? .vault-ststx initialize))
      (try! (contract-call? .vault-stx initialize))
    ))
    
    ;; Authorize market contract
    ;; Called directly since we're already in dao-executor's context
    (try! (contract-call? .vault-sbtc set-authorized-contract .market true))
    (try! (contract-call? .vault-usdh set-authorized-contract .market true))
    (try! (contract-call? .vault-usdc set-authorized-contract .market true))
    (try! (contract-call? .vault-ststx set-authorized-contract .market true))
    (try! (contract-call? .vault-stx set-authorized-contract .market true))
    
    (ok true)))
```

**File:** mainnet/contracts/dao/dao-executor.clar (L71-77)
```text
(define-public (execute-proposal (script <proposal-script>))
  (begin
    (try! (check-impl-auth))
    (try! (as-contract? ((with-all-assets-unsafe))
      (try! (contract-call? script execute))
      true))
    (ok true)))
```
