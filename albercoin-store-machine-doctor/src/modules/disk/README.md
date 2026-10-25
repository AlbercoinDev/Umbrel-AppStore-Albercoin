# Diagnóstico de disco — pendiente

[Guía de Machine Doctor](../../../README.md)

Esta carpeta reserva el futuro diagnóstico SSD/HDD; no contiene una implementación.
Alcance propuesto:

- Detectar discos mediante `/host/dev` y `/host/sys/block`.
- Consultar SMART con `smartctl`.
- Informar de temperatura, horas de uso, sectores reasignados y errores.
- Leer vida útil SSD/NVMe cuando esté disponible.
- Generar informes sin escrituras destructivas.

Son planes, no funciones disponibles ni un compromiso de entrega.
