import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

INSTAGRAM_BASE_URL = "https://www.instagram.com"
DEFAULT_SESSIONS_DIRECTORY = Path("sessions")
POOL_STATE_FILENAME = "pool_state.json"
COOLDOWN_AFTER_CHALLENGE_SECONDS = 24 * 60 * 60
MINIMUM_SECONDS_BETWEEN_USES = 5 * 60


def build_target_url(target_kind: str, target_value: str | None) -> str:
    target_builders = {
        "hashtag": lambda: f"{INSTAGRAM_BASE_URL}/explore/tags/{quote(target_value.lstrip('#'))}/",
        "explore": lambda: f"{INSTAGRAM_BASE_URL}/explore/",
        "reels": lambda: f"{INSTAGRAM_BASE_URL}/reels/",
        "search": lambda: f"{INSTAGRAM_BASE_URL}/explore/search/keyword/?q={quote(target_value)}",
        "profile": lambda: f"{INSTAGRAM_BASE_URL}/{target_value.lstrip('@')}/",
        "location": lambda: f"{INSTAGRAM_BASE_URL}/explore/locations/{target_value}/",
    }
    if target_kind not in target_builders:
        raise ValueError(f"Unknown target kind {target_kind}, expected one of {sorted(target_builders)}")
    if target_kind not in ("explore", "reels") and not target_value:
        raise ValueError(f"{target_kind} needs a value")
    return target_builders[target_kind]()


@dataclass
class SessionState:
    session_name: str
    proxy_url: str | None = None
    last_used_at: float = 0.0
    uses: int = 0
    challenges: int = 0
    disabled_until: float = 0.0
    notes: list[str] = field(default_factory=list)

    def is_available(self, now: float) -> bool:
        return now >= self.disabled_until and now - self.last_used_at >= MINIMUM_SECONDS_BETWEEN_USES


class SessionPool:
    def __init__(self, sessions_directory: Path = DEFAULT_SESSIONS_DIRECTORY):
        self.sessions_directory = sessions_directory
        self.state_path = sessions_directory / POOL_STATE_FILENAME
        self.session_states = self.load_states()

    def load_states(self) -> dict[str, SessionState]:
        stored_states: dict[str, SessionState] = {}
        if self.state_path.exists():
            for state_values in json.loads(self.state_path.read_text()).values():
                stored_states[state_values["session_name"]] = SessionState(**state_values)
        for session_file in sorted(self.sessions_directory.glob("*.json")):
            if session_file.name == POOL_STATE_FILENAME:
                continue
            stored_states.setdefault(session_file.stem, SessionState(session_name=session_file.stem))
        return stored_states

    def save_states(self) -> None:
        self.sessions_directory.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({name: asdict(state) for name, state in self.session_states.items()}, indent=2))

    def session_path(self, session_name: str) -> Path:
        return self.sessions_directory / f"{session_name}.json"

    def register(self, session_name: str, proxy_url: str | None) -> None:
        session_state = self.session_states.setdefault(session_name, SessionState(session_name=session_name))
        if proxy_url:
            session_state.proxy_url = proxy_url
        self.save_states()

    def pick_session(self, ignore_cooldown: bool = False) -> SessionState | None:
        now = time.time()
        candidate_states = [
            state for state in self.session_states.values()
            if self.session_path(state.session_name).exists() and (ignore_cooldown or state.is_available(now))
        ]
        if not candidate_states:
            return None
        return min(candidate_states, key=lambda state: (state.last_used_at, state.uses))

    def record_use(self, session_name: str, hit_challenge: bool, note: str | None = None) -> None:
        session_state = self.session_states[session_name]
        session_state.last_used_at = time.time()
        session_state.uses += 1
        if hit_challenge:
            session_state.challenges += 1
            session_state.disabled_until = time.time() + COOLDOWN_AFTER_CHALLENGE_SECONDS
        if note:
            session_state.notes = (session_state.notes + [note])[-10:]
        self.save_states()

    def summary(self) -> list[dict]:
        now = time.time()
        return [
            {
                "session": state.session_name,
                "proxy": bool(state.proxy_url),
                "uses": state.uses,
                "challenges": state.challenges,
                "available": state.is_available(now),
                "cooldown_left_minutes": max(0, round((state.disabled_until - now) / 60)),
            }
            for state in self.session_states.values()
        ]
