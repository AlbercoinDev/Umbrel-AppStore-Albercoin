"""Cookie-authenticated local Tor control (same protocol as SimpleX Server)."""
import re
import socket


def query(directory):
    cookie = (directory / "control.authcookie").read_bytes()
    if len(cookie) != 32:
        raise OSError("Invalid Tor cookie")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(directory / "control"))
        with connection.makefile("rwb") as stream:
            info = {}
            for command in ("AUTHENTICATE " + cookie.hex(), "GETINFO status/bootstrap-phase status/circuit-established"):
                stream.write(command.encode() + b"\r\n")
                stream.flush()
                for _ in range(100):
                    line = stream.readline(16384).decode().rstrip("\r\n")
                    if not line.startswith("250"):
                        raise OSError("Tor control failed")
                    if "=" in line[4:]:
                        key, value = line[4:].split("=", 1)
                        info[key] = value
                    if line[3:4] == " ":
                        break
                else:
                    raise OSError("Tor response too long")
    progress = re.search(r"\bPROGRESS=(\d+)", info.get("status/bootstrap-phase", ""))
    return {"progress": int(progress.group(1)) if progress else 0,
            "circuit": info.get("status/circuit-established") == "1"}
