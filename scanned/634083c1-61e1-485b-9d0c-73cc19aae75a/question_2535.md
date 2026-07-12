# Q2535: BurnNFT owner binding against collection index

## Question
Can NFT owner enter through `msgServer.BurnNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, DenomId, Id, escrow ownership state, focusing on denom owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then burn via authz/feegrant and compare signer to token owner, causing `collection index` to diverge so the invariant `burn removes token and all owner/collection indexes atomically` fails and the attacker can leave stale owner indexes after burn and later transfer a nonexistent token, leading to Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.BurnNFT
- Entrypoint: Cosmos SDK MsgBurnNFT transaction
- Attacker controls: Sender, DenomId, Id, escrow ownership state
- Exploit idea: leave stale owner indexes after burn and later transfer a nonexistent token by testing the owner binding angle against `collection index` during `burn via authz/feegrant and compare signer to token owner`, with specific focus on denom owner authority.
- Invariant to test: burn removes token and all owner/collection indexes atomically; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over BurnNFT plus nft-transfer refund and timeout state; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
