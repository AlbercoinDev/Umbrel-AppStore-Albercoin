"""Bounded, persistent diagnostics. Never persist command bodies or message text."""
import json
import logging
import re
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler


def technical(value, depth=0):
    """Keep structural error codes, IDs and numeric context, not arbitrary strings."""
    if depth > 10:
        return "[profundidad limitada]"
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:60]:
            if key in ("type", "code", "errno", "msgRcptStatus", "sndProgress") and isinstance(item, str):
                result[key] = item if re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", item) else "[omitido]"
            elif isinstance(item, (dict, list)):
                result[key] = technical(item, depth + 1)
            elif isinstance(item, (int, float, bool)) or item is None:
                result[key] = item
            else:
                result[key] = "[texto privado omitido]"
        return result
    if isinstance(value, list):
        return [technical(item, depth + 1) for item in value[:30]]
    return value if isinstance(value, (int, float, bool)) or value is None else "[texto privado omitido]"


class PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        return open(self.baseFilename, self.mode, encoding=self.encoding, errors=self.errors,
                    opener=lambda path, flags: os.open(path, flags, 0o600))


class Diagnostics:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "diagnostics.jsonl"
        self.logger = logging.Logger("simplex-diagnostics")
        handler = PrivateRotatingFileHandler(self.path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(handler)
        os.chmod(self.path, 0o600)

    def record(self, level, component, phase, summary, **context):
        entry = {"at": datetime.now(timezone.utc).isoformat(), "level": level,
                 "component": component, "phase": phase, "summary": summary, **context}
        self.logger.info(json.dumps(entry, ensure_ascii=False))

    def entries(self, limit=500):
        entries = []
        for path in (self.path.with_name(self.path.name + ".1"), self.path):
            try:
                with path.open(encoding="utf-8") as stream:
                    for line in stream:
                        try:
                            entries.append(json.loads(line))
                        except ValueError:
                            continue
            except FileNotFoundError:
                continue
        return entries[-limit:]


def guidance(details):
    codes = json.dumps(details).lower()
    for code, text in (
        ("contactnotfound", "El ID no existe en esta identidad. Actualiza las conversaciones y revisa SIMPLEX_ALLOWED_USERS en Hermes."),
        ("contactnotready", "El contacto todavía no está listo. Comprueba la aceptación de la solicitud y espera a completar la conexión."),
        ("commanderror", "SimpleX no reconoce la sintaxis de la orden. Comprueba la compatibilidad del cliente o adaptador con SimpleX 7.0.3."),
        ("broker", "Error del transporte con el servidor. Revisa el proxy, la salida a Internet y el estado SMP; el código anidado concreta el fallo."),
        ("auth", "El servidor rechazó la autenticación. Revisa el contexto: puede afectar a una cola SMP y no a la contraseña del panel."),
        ("quota", "Posible límite de la cola del servidor. Revisa si el destinatario se conecta y si el error persiste."),
        ("network", "Fallo relacionado con la red. Comprueba proxy, DNS y conectividad desde el contenedor."),
        ("timeout", "Tiempo agotado. El resultado puede ser incierto; consulta el historial antes de repetir el envío."),
        ("errorstore", "Error de almacenamiento o entidad. Revisa el código anidado, los IDs y el espacio disponible."),
    ):
        if code in codes:
            return text
    return "Consulta el código y los sucesos relacionados. Sin más evidencia no se puede atribuir una causa única."
