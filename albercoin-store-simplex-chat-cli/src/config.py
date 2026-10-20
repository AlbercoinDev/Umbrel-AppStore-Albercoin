import hashlib
import json
import os
import secrets
from pathlib import Path

DATA = Path(os.environ.get("APP_DATA", "/data"))


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


class Config:
    def __init__(self):
        self.path = DATA / "manager/settings.json"
        if self.path.exists():
            self.value = json.loads(self.path.read_text())
        else:
            self.value = {"integration_enabled": False, "integration_token": secrets.token_urlsafe(32),
                          "smp_servers": "", "xftp_servers": "", "socks_proxy": ""}
            self.save()
        auth = DATA / "manager/admin-auth.json"
        if not auth.exists():
            password = os.environ.get("APP_PASSWORD") or secrets.token_urlsafe(24)
            if not os.environ.get("APP_PASSWORD"):
                initial = DATA / "manager/initial-password.txt"
                initial.write_text(password, encoding="utf-8")
                initial.chmod(0o600)
            salt = secrets.token_hex(16)
            atomic_json(auth, {"salt": salt, "hash": self.derive(password, salt)})
        self.auth = json.loads(auth.read_text())

    @staticmethod
    def derive(password, salt):
        return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

    def verify(self, password):
        return secrets.compare_digest(self.auth["hash"], self.derive(password, self.auth["salt"]))

    def save(self):
        atomic_json(self.path, self.value)
