import logging

logger = logging.getLogger('kiosk')

class StateManager:
    """클라이언트 상태 관리 클래스"""
    
    def __init__(self):
        self.states = {}
    
    def create_initial_state(self):
        """초기 상태 생성"""
        return {
            'step': 'idle',  # idle → prompting → listening → advising
            'session_id': None,
            'conversation_history': [],
            'last_interaction': None,
            'error_count': 0
        }
    
    def get_state(self, client_id):
        """클라이언트 상태 조회"""
        return self.states.get(client_id)
    
    def set_state(self, client_id, state):
        """클라이언트 상태 저장"""
        self.states[client_id] = state
        logger.debug(f"State updated for {client_id}: {state['step']}")
    
    def remove_state(self, client_id):
        """클라이언트 상태 제거"""
        if client_id in self.states:
            del self.states[client_id]
            logger.info(f"State removed for {client_id}")
    
    def reset_to_idle(self, client_id):
        """idle 상태로 초기화"""
        if client_id in self.states:
            self.states[client_id]['step'] = 'idle'
            self.states[client_id]['conversation_history'] = []
            logger.info(f"State reset to idle for {client_id}")
    
    def is_valid_transition(self, from_state, to_state):
        """상태 전이 유효성 검사"""
        valid_transitions = {
            'idle': ['prompting'],
            'prompting': ['listening'],
            'listening': ['advising', 'idle'],
            'advising': ['listening', 'idle']
        }
        
        return to_state in valid_transitions.get(from_state, [])
    
    def log_state_transition(self, client_id, from_state, to_state):
        """상태 전이 로깅"""
        if self.is_valid_transition(from_state, to_state):
            logger.info(f"State transition: {client_id} {from_state} → {to_state}")
        else:
            logger.warning(f"Invalid state transition: {client_id} {from_state} → {to_state}")