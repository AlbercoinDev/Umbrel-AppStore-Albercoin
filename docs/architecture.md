# Mapa del repositorio

[Volver al catálogo](../README.md)

## Dónde está cada cosa

| Ruta | Contenido |
| --- | --- |
| `umbrel-app-store.yml` | Identidad de la tienda |
| `<app>/umbrel-app.yml` | Identidad, versión, dependencias y ficha de cada app |
| `<app>/docker-compose.yml` | Servicios, imágenes, volúmenes y configuración de ejecución |
| `<app>/README.md` | Guía específica de cada app |
| `.github/workflows/` | Construcción y publicación de imágenes propias |
| `docs/` | Documentación transversal; no contiene configuración de ejecución |

## Código y formas de despliegue

| App | Código principal | Despliegue actual |
| --- | --- | --- |
| Frigate | Python/HTML y shell dentro de `docker-compose.yml`; `exports.sh` y `torrc` | Imágenes base; descarga y verifica el binario de Frigate al preparar el runtime |
| Onion Rotator | `src/`; pruebas en `tests/`; herramientas en `scripts/` | Python con `src/` montado desde `${APP_DATA_DIR}`; el Compose no construye su Dockerfile |
| Machine Doctor | `src/main.py`, `src/modules/cpu/`, `src/static/` | Imagen propia de GHCR, construida desde su Dockerfile |
| SimpleX Chat CLI | `src/`, `scripts/restore.py`, `integrations/hermes/` | Imagen propia de GHCR, fijada por digest en Compose |
| SimpleX Server (vista previa) | `src/server.py`, plantillas y recursos; SMP oficial sin modificar | Imagen derivada de SMP oficial, publicada para AMD64/ARM64 y fijada por digest |
| Umbrel Tunnel | Código, instalador y releases en [AlbercoinDev/umbrel-tunnel](https://github.com/AlbercoinDev/umbrel-tunnel); aquí solo manifiesto, Compose e icono | Imagen propia de GHCR para AMD64/ARM64, fijada por digest en Compose |

Modificar fuentes locales no actualiza por sí solo una instalación Umbrel.
Los módulos `disk`, `thermal` y `ram` de Machine Doctor contienen documentación
de trabajo futuro, no diagnósticos completos implementados.

## Puertos y permisos

| App | Puerto de la ficha Umbrel | Otros puertos | Accesos sensibles |
| --- | --- | --- | --- |
| Frigate | `2125` | Electrum `50005` publicado; web interna `80` | Credenciales RPC de Bitcoin y datos Tor |
| Onion Rotator | `2180` | Web interna `8900` | Socket Docker, escritura en datos Tor, lectura de app-data |
| Machine Doctor | `2190` | Web interna `2190` | `privileged`, montajes del host `/proc`, `/sys`, `/dev` |
| SimpleX Chat CLI | `2191` | Conector interno `2192`; cliente local `5225` | Identidad, mensajes, contraseñas y token del conector |
| SimpleX Server (desarrollo) | `2193` | TCP `5223` reservado; SMP interno `5224`, SOCKS local `9050` | Certificados SMP, identidad onion, colas y credenciales; red Docker propia |
| Umbrel Tunnel | `8642` | Web interna `8081`; puertos públicos elegidos en el VPS | `NET_ADMIN`, `SYS_MODULE`, `/lib/modules` y lectura de `${UMBREL_ROOT}/app-data` |

Los puertos internos no equivalen a puertos publicados en el host. El detalle
autoritativo está en cada Compose y manifiesto. No publiques conectores internos
ni compartas datos o copias con credenciales.

## Datos persistentes

`${APP_DATA_DIR}` lo proporciona Umbrel para cada instalación:

- Frigate: `data/frigate/`; el manifiesto excluye del backup índices y otros
  archivos regenerables. El servicio Tor utiliza `${TOR_DATA_DIR}`.
- Onion Rotator: opera sobre datos Tor y apps del nodo; no tiene un volumen
  propio de historial persistente declarado en Compose.
- Machine Doctor: `data/`, con informes en `reports/`.
- SimpleX: `data/simplex/`, `data/manager/` y `data/backups/`.
- SimpleX Server: `data/manager/` (incluido Tor), `data/smp/config/`, `data/smp/state/`.
- Umbrel Tunnel: `data/wireguard/` (clave privada `600`), `data/config.json`
  (VPS vinculado, exposiciones y ajustes) y `data/haproxy.cfg`.

Consulta el README de cada app antes de copiar, restaurar o eliminar datos.
