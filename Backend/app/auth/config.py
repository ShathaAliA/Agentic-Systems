import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AuthSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    app_env: str = "development"
    mode: str = "disabled"
    session_seconds: int = Field(default=900, ge=60, le=3600)
    login_window_seconds: int = Field(default=60, ge=30, le=3600)
    login_account_limit: int = Field(default=5, ge=1, le=20)
    login_ip_limit: int = Field(default=20, ge=1, le=100)
    login_global_limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def fail_closed(self):
        if self.mode not in {"disabled", "development_password"}:
            raise ValueError("Unsupported AUTH_MODE; no identity-provider fallback")
        if self.mode == "development_password" and self.app_env not in {"development", "test"}:
            raise ValueError("Development authentication is forbidden outside development/test")
        return self

    @classmethod
    def from_environment(cls):
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        values = {"app_env": os.getenv("APP_ENV", "unspecified"),
                  "mode": os.getenv("AUTH_MODE", "disabled")}
        for name in ("session_seconds", "login_window_seconds", "login_account_limit",
                     "login_ip_limit", "login_global_limit"):
            value = os.getenv("AUTH_" + name.upper())
            if value is not None:
                values[name] = value
        return cls(**values)
