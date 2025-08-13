import time
import uuid

class HospitalStateManager:
    """병원 키오스크 상태 관리"""
    
    def __init__(self):
        self.connected_clients = set()
        self.client_states = {}
        self.client_sessions = {}

    def create_initial_state(self):
        return {
            "step": "init",
            "session_id": str(uuid.uuid4()),
            "patient_name": None,
            "patient_birth_date": None,
            "patient_id": None,
            "patient_verified": False,
            "selected_documents": [],
            "current_document": None,
            "document_quantity": 1,
            "total_price": 0,
            "retry_count": 0,
            "max_retries": 3,
            "last_prompt": None,
            "created_at": time.time(),
            "last_activity": time.time(),
        }

    def add_client(self, websocket):
        self.connected_clients.add(websocket)
        state = self.create_initial_state()
        self.client_states[websocket] = state
        return state

    def remove_client(self, websocket):
        if websocket in self.connected_clients:
            self.connected_clients.remove(websocket)
        if websocket in self.client_states:
            self.client_states.pop(websocket, None)

    def get_state(self, websocket):
        return self.client_states.get(websocket)

    def update_state(self, websocket, updates):
        if websocket in self.client_states:
            self.client_states[websocket].update(updates)
            self.client_states[websocket]["last_activity"] = time.time()

    def set_step(self, websocket, step: str):
        if websocket in self.client_states:
            self.client_states[websocket]["step"] = step
            self.client_states[websocket]["last_activity"] = time.time()