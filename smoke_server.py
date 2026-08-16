from mcp.server.fastmcp import FastMCP

mcp = FastMCP("smoke-test")

@mcp.tool()
def add_numbers(a: float, b: float) -> float:
    """Додає два числа і повертає їхню суму."""
    return a + b

if __name__ == "__main__":
    mcp.run()
