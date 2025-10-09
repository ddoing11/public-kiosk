import asyncio
import json
import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from .simplified_message_router import HospitalMessageRouter


class HospitalWebSocketServer:
    def __init__(self):
        """메시지 라우터 초기화"""
        self.message_router = HospitalMessageRouter()

    async def handle_client(self, websocket, path):
        """클라이언트(WebSocket) 연결을 처리하는 메인 루프"""
        client_addr = websocket.remote_address
        print(f"🌐 클라이언트 연결됨: {client_addr}")

        # 초기 연결 처리 (환영 메시지 등)
        try:
            state = await self.message_router.handle_connection(websocket)
        except Exception as e:
            print(f"⚠️ 초기 연결 처리 중 오류: {e}")
            return

        try:
            async for message in websocket:
                try:
                    # 수신 메시지 로깅
                    if isinstance(message, str):
                        preview = message[:100] + ("..." if len(message) > 100 else "")
                        print(f"📨 수신 메시지: {preview}")
                    else:
                        print("📨 (Binary 메시지 수신 - 무시됨)")

                    # JSON 형식이 아닐 수도 있으므로 그대로 router로 전달
                    await self.message_router.process_message(websocket, message)

                except json.JSONDecodeError:
                    print(f"⚠️ 잘못된 JSON 형식 메시지 수신: {message}")
                except Exception as e:
                    print(f"❌ 메시지 처리 중 오류: {e}")

        except ConnectionClosedOK:
            print(f"🔌 정상 종료: {client_addr}")

        except ConnectionClosedError as e:
            print(f"⚠️ 비정상 종료: {client_addr}, 코드={e.code}, 이유={e.reason}")

        except Exception as e:
            print(f"⚠️ WebSocket 세션 처리 중 예외 발생: {e}")

        finally:
            # 연결 종료 시 세션 정리
            await self.cleanup_connection_safe(websocket)
            print(f"🧹 연결 정리 완료: {client_addr}")

    async def cleanup_connection_safe(self, websocket):
        """연결 종료 시 안전하게 상태 정리"""
        try:
            if hasattr(self.message_router, "cleanup_connection"):
                await self.message_router.cleanup_connection(websocket)
        except Exception as e:
            print(f"⚠️ cleanup_connection 중 오류 발생: {e}")
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    async def start_server(self, host="0.0.0.0", port=8003):
        """WebSocket 서버 실행"""
        try:
            async with websockets.serve(self.handle_client, host, port):
                print(f"🚀 병원 WebSocket 서버가 {host}:{port}에서 실행 중")
                await asyncio.Future()  # 서버 무기한 실행
        except OSError as e:
            print(f"❌ 서버 실행 실패: 포트 {port} 사용 중이거나 권한이 없습니다. ({e})")
        except Exception as e:
            print(f"⚠️ 서버 실행 중 예외 발생: {e}")

def start_hospital_server():
    """외부에서 호출 시 서버 시작"""
    server = HospitalWebSocketServer()
    try:
        asyncio.run(server.start_server())
    except KeyboardInterrupt:
        print("🛑 서버가 수동으로 종료되었습니다.")
    except Exception as e:
        print(f"⚠️ 서버 실행 중 오류: {e}")
