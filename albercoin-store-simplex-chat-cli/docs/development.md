# Construcción de SimpleX Chat CLI

[Guía de la app](../README.md) · [Desarrollo común](../../docs/development.md) ·
[Publicación común](../../docs/releasing.md)

Esta guía es para mantenedores; no es necesaria para instalar desde Umbrel.
El Dockerfile descarga una versión fija del cliente y comprueba su SHA256 para
AMD64 y ARM64. Revisa versiones y hashes antes de cambiar el binario.

## Construcción local sin publicación

Requiere Docker con Buildx. Desde la raíz del repositorio:

```sh
docker buildx build --platform linux/amd64 --load -t simplex-chat-cli:dev ./albercoin-store-simplex-chat-cli
```

Para ARM64 cambia `linux/amd64` por `linux/arm64`; se necesita soporte nativo
o emulación apropiada. El comando descarga dependencias, construye y carga la
imagen localmente; no la publica ni cambia la instalación Umbrel.
No montes la identidad de producción en una instancia de prueba.

## Publicación de imagen

El workflow manual [Build SimpleX Chat CLI](../../.github/workflows/simplex-chat-cli-image.yml)
construye para `linux/amd64` y `linux/arm64`; `publish=false` no publica y
`publish=true` publica en GHCR. No se dispara por push.

La etiqueta está declarada en el workflow y la referencia usada por Umbrel,
incluido su digest, en [Compose](../docker-compose.yml). Estos archivos son
la referencia para sus valores actuales; el workflow no impide reemplazar
una etiqueta. Si se publica contenido nuevo, debe revisarse el digest que
consume Umbrel: una etiqueta republicada no actualiza automáticamente una
referencia fijada por digest.

Consulta las [pruebas documentadas](../README.md#verificación-de-esta-revisión)
y los límites de compatibilidad de copias antes de plantear una actualización.
