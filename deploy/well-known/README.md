Files to serve from the irlpls.com static site (not done; site/ is owned elsewhere):

- `/.well-known/mcp/server-card.json`  <- `well-known/mcp/server-card.json` (Smithery fallback if its scan fails; check field names against Smithery docs before publishing, the shape here is a draft from chan-directories.md).
- MCP Registry proof: DNS TXT on the irlpls.com apex (docs/LAUNCH-AGENTS.md step 5); nothing to serve here.
- Smithery wants 401 not 403 for SmitheryBot; there is no auth here, so nothing to do.
