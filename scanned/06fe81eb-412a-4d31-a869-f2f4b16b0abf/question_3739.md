# Q3739: BeginBlocker burned address balance against total supply

## Question
Can unprivileged account influencing balances sent to configured burned addresses enter through `BeginBlocker` by send bond and non-bond denom funds to configured burned addresses before BeginBlocker while controlling funds sent to burned addresses, block height, inflation params, bond denom balances, focusing on non-bond denom supply, under the precondition that the address is configured as burned in production params, then send non-bond-denom coins to burned addresses and observe supply accounting, causing `total supply` to diverge so the invariant `decay calculations cannot mint excessive inflation rewards` fails and the attacker can cause BeginBlocker to burn spendable user funds from an address not intended as a burn sink, leading to High inflation or burn accounting flaw that materially misallocates value on-chain?

## Target
- File/function: x/inflation/abci.go::BeginBlocker
- Entrypoint: begin block execution with default-enabled inflation module
- Attacker controls: funds sent to burned addresses, block height, inflation params, bond denom balances
- Exploit idea: cause BeginBlocker to burn spendable user funds from an address not intended as a burn sink by testing the burned address balance angle against `total supply` during `send non-bond-denom coins to burned addresses and observe supply accounting`, with specific focus on non-bond denom supply.
- Invariant to test: decay calculations cannot mint excessive inflation rewards; additionally, only intended balances are burned and total supply changes match the burned coins.
- Expected Immunefi impact: High inflation or burn accounting flaw that materially misallocates value on-chain; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: app integration test advancing blocks with bank supply and inflation params assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
