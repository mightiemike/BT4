# Q2525: BurnNFT owner binding against NFT existence

## Question
Can NFT owner enter through `msgServer.BurnNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, DenomId, Id, escrow ownership state, focusing on denom owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then burn via authz/feegrant and compare signer to token owner, causing `NFT existence` to diverge so the invariant `burning a voucher cannot allow later duplicate refund minting` fails and the attacker can leave stale owner indexes after burn and later transfer a nonexistent token, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.BurnNFT
- Entrypoint: Cosmos SDK MsgBurnNFT transaction
- Attacker controls: Sender, DenomId, Id, escrow ownership state
- Exploit idea: leave stale owner indexes after burn and later transfer a nonexistent token by testing the owner binding angle against `NFT existence` during `burn via authz/feegrant and compare signer to token owner`, with specific focus on denom owner authority.
- Invariant to test: burning a voucher cannot allow later duplicate refund minting; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over BurnNFT plus nft-transfer refund and timeout state; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
