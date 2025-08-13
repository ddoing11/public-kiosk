import asyncio
import websockets
from .simplified_message_router import HospitalMessageRouter

class HospitalWebSocketServer:
    def __init__(self):
        self.message_router = HospitalMessageRouter()
    
    async def handle_client(self, websocket, path):
        print(f"🔗 클라이언트 연결됨")
        state = await self.message_router.handle_connection(websocket)
        
        try:
            async for message in websocket:
                await self.message_router.process_message(websocket, message)
        except Exception as e:
            print(f"❌ 연결 오류: {e}")
        finally:
            await self.message_router.cleanup_connection(websocket)

    async def start_server(self, host="0.0.0.0", port=8003):
        async with websockets.serve(self.handle_client, host, port):
            print(f"✅ 서버가 {host}:{port}에서 실행 중")
            await asyncio.Future()

def start_hospital_server():
    server = HospitalWebSocketServer()
    asyncio.run(server.start_server())