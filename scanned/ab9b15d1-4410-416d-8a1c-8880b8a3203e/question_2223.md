# Q2223: IssueDenom owner binding against denom owner

## Question
Can unprivileged account enter through `msgServer.IssueDenom` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Id, Name, Schema, focusing on burn/delete finality, under the precondition that a valuable denom or NFT exists under the NFT module, then issue denom, transfer NFTs, and attempt IBC send through nft-transfer, causing `denom owner` to diverge so the invariant `denom metadata cannot corrupt collection or class-trace interpretation` fails and the attacker can take over or spoof an IBC voucher denom and mint unbacked NFTs, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.IssueDenom
- Entrypoint: Cosmos SDK MsgIssueDenom transaction
- Attacker controls: Sender, Id, Name, Schema, Uri, address prefix
- Exploit idea: take over or spoof an IBC voucher denom and mint unbacked NFTs by testing the owner binding angle against `denom owner` during `issue denom, transfer NFTs, and attempt IBC send through nft-transfer`, with specific focus on burn/delete finality.
- Invariant to test: denom metadata cannot corrupt collection or class-trace interpretation; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over IssueDenom, MintNFT, IBC class hash, and collection lookup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
