import os
import socket


class Settings:
    HOST: str = os.environ.get("HOST", "0.0.0.0")
    PORT: int = int(os.environ.get("PORT", "8080"))
    PUBLIC_HOST: str = os.environ.get("PUBLIC_HOST", "")
    CREDENTIAL_FILE: str = os.environ.get("CREDENTIAL_FILE", "data/credentials.json")
    EXTRACT_TIMEOUT_S: int = int(os.environ.get("EXTRACT_TIMEOUT_S", "45"))

    def get_public_host(self, request_port: int | None = None) -> str:
        if self.PUBLIC_HOST:
            return self.PUBLIC_HOST
        port_to_use = request_port if request_port else self.PORT
        return self._detect_lan_ip(port_to_use)

    def _detect_lan_ip(self, port: int) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return f"{ip}:{port}"
        except Exception:
            return f"127.0.0.1:{port}"


settings = Settings()
