# Q2630: BurnNFT owner binding against owner index

## Question
Can NFT owner enter through `msgServer.BurnNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, DenomId, Id, escrow ownership state, focusing on IBC-prefixed denom separation, under the precondition that a valuable denom or NFT exists under the NFT module, then burn via authz/feegrant and compare signer to token owner, causing `owner index` to diverge so the invariant `authz and feegrant cannot burn a token outside the granted owner authority` fails and the attacker can burn a victim or escrowed NFT and cause permanent user asset loss, leading to Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.BurnNFT
- Entrypoint: Cosmos SDK MsgBurnNFT transaction
- Attacker controls: Sender, DenomId, Id, escrow ownership state
- Exploit idea: burn a victim or escrowed NFT and cause permanent user asset loss by testing the owner binding angle against `owner index` during `burn via authz/feegrant and compare signer to token owner`, with specific focus on IBC-prefixed denom separation.
- Invariant to test: authz and feegrant cannot burn a token outside the granted owner authority; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over BurnNFT plus nft-transfer refund and timeout state; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
