from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import asyncio
import json
server_params = StdioServerParameters(command='python', args=['mcp-server.py'])
async def test():
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(tools.tools)
            print(type(tools.tools))

if __name__ == "__main__":
    asyncio.run(test())