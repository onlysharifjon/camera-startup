from pydantic_settings import BaseSettings
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    APP_NAME: str = "Camera Attendance System"
    VERSION:  str = "2.0.0"
    HOST:     str = "0.0.0.0"
    PORT:     int = 8000

    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/attendance.db"

    CAPTURES_DIR: Path = BASE_DIR / "captures"
    FACES_DIR:    Path = BASE_DIR / "faces"

    # V380 paths
    V380_SCREENSHOT_DIR: Path = Path.home() / "Documents" / "V380" / "Screenshot"
    V380_RECORD_DIR:     Path = Path.home() / "Documents" / "V380" / "Record"

    # Face detection
    FACE_DETECTION_INTERVAL: float = 1.0   # seconds between detections
    FACE_TOLERANCE:          float = 0.5   # lower = stricter match
    MIN_FACE_CONFIDENCE:     float = 0.6

    # Attendance
    DEFAULT_CHECKIN_TIME:  str = "09:00"
    DEFAULT_CHECKOUT_TIME: str = "18:00"
    LATE_THRESHOLD_MINUTES: int = 15

    class Config:
        env_file = ".env"

settings = Settings()
