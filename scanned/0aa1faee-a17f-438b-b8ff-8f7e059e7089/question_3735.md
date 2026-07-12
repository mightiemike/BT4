# Q3735: BeginBlocker burned address balance against total supply

## Question
Can unprivileged account influencing balances sent to configured burned addresses enter through `BeginBlocker` by send bond and non-bond denom funds to configured burned addresses before BeginBlocker while controlling funds sent to burned addresses, block height, inflation params, bond denom balances, focusing on non-bond denom supply, under the precondition that the address is configured as burned in production params, then send funds to a configured burned address and advance BeginBlocker, causing `total supply` to diverge so the invariant `non-bond denoms cannot corrupt bond-denom supply or reward accounting` fails and the attacker can burn or mint the wrong denom and corrupt total supply, leading to High inflation or burn accounting flaw that materially misallocates value on-chain?

## Target
- File/function: x/inflation/abci.go::BeginBlocker
- Entrypoint: begin block execution with default-enabled inflation module
- Attacker controls: funds sent to burned addresses, block height, inflation params, bond denom balances
- Exploit idea: burn or mint the wrong denom and corrupt total supply by testing the burned address balance angle against `total supply` during `send funds to a configured burned address and advance BeginBlocker`, with specific focus on non-bond denom supply.
- Invariant to test: non-bond denoms cannot corrupt bond-denom supply or reward accounting; additionally, only intended balances are burned and total supply changes match the burned coins.
- Expected Immunefi impact: High inflation or burn accounting flaw that materially misallocates value on-chain; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: app integration test advancing blocks with bank supply and inflation params assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
