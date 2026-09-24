from mcp.server.fastmcp import FastMCP

mcp = FastMCP("bmi")

print(f"Starting server {mcp.name}")

@mcp.tool()
def calculate_bmi(weight_kg: float, height_m: float) -> float:
    """
    Calculate BMI given weight in kg and height in meters.
    """
    if height_m <= 0:
        raise ValueError("Height must be greater than zero.")
    return weight_kg / (height_m ** 2)

if __name__ == "__main__":
    mcp.run(transport="stdio")