import asyncio
import httpx
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
import mcp.types as types
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, Response
import uvicorn

# Initialize the MCP Server
server = Server("custom-weather-mcp-server")

# Define a root endpoint so http://localhost:8000/ does not give a 404
async def homepage(request):
    return JSONResponse({"status": "healthy", "message": "Weather MCP Server is running successfully"})

# Define the weather tool that Claude will see
@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_state_weather_alerts",
            description="Fetches real-time active weather alerts for a specific US State from the National Weather Service.",
            inputSchema={
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "description": "The two-letter US State code (e.g., CA, TX, NY, FL)."
                    }
                },
                "required": ["state"]
            }
        )
    ]

# Handle execution logic when Claude runs the weather tool
@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    if name != "get_state_weather_alerts":
        raise ValueError(f"Unknown tool: {name}")
    
    if not arguments or "state" not in arguments:
        raise ValueError("Missing 'state' argument.")

    state_code = arguments.get("state").upper()

    # The NWS target API endpoint
    TARGET_EXTERNAL_API = f"https://api.weather.gov/alerts/active?area={state_code}"
    
    # CRITICAL: NWS requires a custom User-Agent. Replace with your own app name/email.
    headers = {
        "User-Agent": "MyCustomMCPAgent/1.0 (contact: your-email@example.com)",
        "Accept": "application/geo+json"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                TARGET_EXTERNAL_API,
                headers=headers,
                timeout=15.0
            )
            response.raise_for_status()
            api_data = response.json()
            
            # Extract basic alert information out of the NWS payload response
            features = api_data.get("features", [])
            if not features:
                return [types.TextContent(type="text", text=f"No active weather alerts found for state {state_code}.")]
                
            alert_summaries = []
            for item in features[:5]:  # Limit to top 5 alerts to keep Claude's context clean
                props = item.get("properties", {})
                alert_summaries.append(
                    f"- **{props.get('event')}** ({props.get('severity')} severity):\n  {props.get('headline')}\n"
                )
                
            joined_alerts = "\n".join(alert_summaries)
            return [
                types.TextContent(
                    type="text",
                    text=f"Active Weather Alerts for {state_code}:\n\n{joined_alerts}"
                )
            ]
        except Exception as e:
            return [
                types.TextContent(
                    type="text",
                    text=f"Error communicating with National Weather Service API: {str(e)}"
                )
            ]

# Set up Starlette HTTP and Server-Sent Events (SSE)
sse = SseServerTransport("/messages")

async def handle_sse(request):
    # Unpack the returned stream tuple directly inside the context manager
    async with sse.connect_sse(
        request.scope, 
        request.receive, 
        request._send
    ) as (read_stream, write_stream):
        
        # Pass the unpacked streams to the server runner
        await server.run(
            read_stream,   
            write_stream,  
            InitializationOptions(
                server_name="custom-weather-mcp-server",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={}
                )
            )
        )
    return Response()

app = Starlette(
    routes=[
        Route("/", endpoint=homepage, methods=["GET"]),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages", app=sse.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)