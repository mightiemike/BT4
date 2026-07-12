# Q2594: BurnNFT owner binding against escrowed token state

## Question
Can NFT owner enter through `msgServer.BurnNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, DenomId, Id, escrow ownership state, focusing on collection membership, under the precondition that a valuable denom or NFT exists under the NFT module, then burn a token id with validation boundary characters, causing `escrowed token state` to diverge so the invariant `authz and feegrant cannot burn a token outside the granted owner authority` fails and the attacker can burn a victim or escrowed NFT and cause permanent user asset loss, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.BurnNFT
- Entrypoint: Cosmos SDK MsgBurnNFT transaction
- Attacker controls: Sender, DenomId, Id, escrow ownership state
- Exploit idea: burn a victim or escrowed NFT and cause permanent user asset loss by testing the owner binding angle against `escrowed token state` during `burn a token id with validation boundary characters`, with specific focus on collection membership.
- Invariant to test: authz and feegrant cannot burn a token outside the granted owner authority; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over BurnNFT plus nft-transfer refund and timeout state; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
