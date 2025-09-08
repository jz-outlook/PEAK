# 默认配置文件：当没有config.py时使用这些配置
BASE_API_URL = "https://api-test-ws.myaitalk.vip"
TEST_ACCOUNT_API = "https://watchdog.myaitalk.vip/register_test_account"
AUTH_TOKEN = "NjExveResQZUKqFXCurPed5kXeSHkZsW"
LOGIN_ENDPOINT = "/register/userLogin.php"
USER_PROFILE_ENDPOINT = "/profile/getUserProfile.php"
START_LESSON_ENDPOINT = "/lesson/startLessonv2.php"
MAX_LOGIN_ATTEMPTS = 3
MAX_ACCOUNT_ATTEMPTS = 10
WEBSOCKET_HEARTBEAT_INTERVAL = 10
USER_CHECK_INTERVAL = 10
ACCOUNT_POOL_TIMEOUT_MULTIPLIER = 1.5
DEFAULT_LOGIN_INTERVAL = 3.0
DEFAULT_ACCOUNT_MULTIPLIER = 1.5
DEFAULT_WS_CONNECT_INTERVAL = 5.0
DEFAULT_STATS_INTERVAL = 60.0
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_ENCODING = "utf-8"
WS_BASE_URL = "wss://wss-test-ws.myaitalk.vip"
WS_PARAMS = {
    "tts": "elevenlabs",
    "sid": "49",
    "bid": "108",
    "gid": "413",
    "mid": "16132",
    "alert": "1",
    "version": "2",
    "llm_model": "gpt-4",
    "age": "100",
    "protobuf": "11",
    "pv": "2",
    "game": "no",
    "audio": "wav",
    "ver": "3001000",
    "device": "Redmi-2311DRK48C",
    "bilingual": "en"
}
LESSON_PARAMS_LIST = [
    {
        "material_id": "topic_talk-b2-1/materials/topic_talk-b2-1-lesson_1-c_121102.json?sid=49&bid=51&gid=148&mid=8600",
        "character_id": "lily_white"
    },
    {
        "material_id": "topic_talk-b2-1/materials/topic_talk-b2-1-lesson_1-b_121001.json?sid=49&bid=51&gid=148&mid=8599",
        "character_id": "lily_white"
    },
    {
        "material_id": "topic_talk-b2-1/materials/topic_talk-b2-1-lesson_1-a_120701.json?sid=49&bid=51&gid=148&mid=8642",
        "character_id": "lily_white"
    }
]
TEST_MESSAGES = [
    "Hello! How are you today?",
    "What's the weather like?",
    "Can you tell me a story?",
    "I like playing games.",
    "Thank you for your help!"
]
