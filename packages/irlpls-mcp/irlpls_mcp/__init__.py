"""irlpls-mcp: the irlpls MCP server as its own package, so `uvx irlpls-mcp` starts it. The server is
irlpls.mcp_server (in the irlpls package); this is only the console entry point."""
__version__ = "0.1.0"


def main(argv=None):
    from irlpls.mcp_server import main as _main
    return _main(argv)
