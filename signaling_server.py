import asyncio
import json
import logging
import websockets
import traceback # Import traceback for detailed error logging

# Configure logging for the signaling server
handler_log = logging.StreamHandler() # Renamed to avoid conflict with handler function
handler_log.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
logger = logging.getLogger('signaling_server')
logger.setLevel(logging.INFO)
logger.addHandler(handler_log)

# Global set to keep track of connected WebSocket clients
CONNECTED_CLIENTS = set()

async def handler(websocket):
    """
    Handles a new WebSocket connection, adds it to the set of connected clients,
    and relays messages between clients.
    """
    logger.info(f"Client connected: {websocket.remote_address}")
    CONNECTED_CLIENTS.add(websocket) # Add the new client
    try:
        async for message in websocket:
            logger.info(f"Received message from {websocket.remote_address}: {message}")
            
            # Prepare a list to hold clients that need to be removed
            clients_to_remove = []
            
            # Broadcast the message to all other connected clients
            for client in CONNECTED_CLIENTS:
                if client != websocket: # Only send to other clients
                    try:
                        await client.send(message)
                    except websockets.exceptions.ConnectionClosed:
                        # If sending fails due to closed connection, mark for removal
                        logger.warning(f"Client {client.remote_address} was already closed, marking for removal.")
                        clients_to_remove.append(client)
                    except Exception as e:
                        logger.error(f"Error sending message to client {client.remote_address}: {e}")
                        traceback.print_exc() # Log detailed traceback
                        clients_to_remove.append(client) # Also mark for removal on other errors
            
            # Remove clients that were marked for removal
            for client in clients_to_remove:
                if client in CONNECTED_CLIENTS: # Double check in case it was already removed by its own handler
                    CONNECTED_CLIENTS.remove(client)
                    logger.info(f"Removed disconnected client {client.remote_address} during broadcast cleanup.")


    except websockets.exceptions.ConnectionClosedOK:
        logger.info(f"Client disconnected gracefully: {websocket.remote_address}")
    except websockets.exceptions.ConnectionClosedError as e:
        logger.warning(f"Client disconnected with error: {websocket.remote_address}, code: {e.code}, reason: {e.reason}")
        # Optionally, you can remove the client here if it wasn't caught by the broadcast loop
    except Exception as e:
        logger.error(f"Error in client handler for {websocket.remote_address}: {e}")
        traceback.print_exc() # Print full traceback
    finally:
        if websocket in CONNECTED_CLIENTS: # Ensure it's still in the set before trying to remove
            CONNECTED_CLIENTS.remove(websocket) # Remove the client when connection closes
        logger.info(f"Client removed: {websocket.remote_address}. Active clients: {len(CONNECTED_CLIENTS)}")

async def main():
    """
    Main function to start the WebSocket signaling server.
    """
    host = "0.0.0.0"
    port = 8765
    async with websockets.serve(handler, host, port) as server:
        logger.info(f"Signaling server started on ws://{host}:{port}")
        await server.wait_closed() # Keep the server running until manually stopped

if __name__ == "__main__":
    asyncio.run(main())