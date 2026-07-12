# Q2589: BurnNFT owner binding against IBC voucher backing

## Question
Can NFT owner enter through `msgServer.BurnNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, DenomId, Id, escrow ownership state, focusing on collection membership, under the precondition that a valuable denom or NFT exists under the NFT module, then burn a token id with validation boundary characters, causing `IBC voucher backing` to diverge so the invariant `burning a voucher cannot allow later duplicate refund minting` fails and the attacker can burn a victim or escrowed NFT and cause permanent user asset loss, leading to Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.BurnNFT
- Entrypoint: Cosmos SDK MsgBurnNFT transaction
- Attacker controls: Sender, DenomId, Id, escrow ownership state
- Exploit idea: burn a victim or escrowed NFT and cause permanent user asset loss by testing the owner binding angle against `IBC voucher backing` during `burn a token id with validation boundary characters`, with specific focus on collection membership.
- Invariant to test: burning a voucher cannot allow later duplicate refund minting; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over BurnNFT plus nft-transfer refund and timeout state; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
