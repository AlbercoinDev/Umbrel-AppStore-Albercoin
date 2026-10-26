# Umbrel Tunnel

[Volver al catálogo](../README.md)

Expón servicios de tu Umbrel a través de un VPS con IP pública fija usando un
túnel WireGuard. La app detecta las aplicaciones instaladas y los servicios de
umbrelOS, te deja elegir qué puertos abrir y sincroniza los *forwards* con el
VPS. La API del VPS solo escucha dentro del túnel y está protegida con un token.

- Código, documentación y releases del plano de control:
  [AlbercoinDev/umbrel-tunnel](https://github.com/AlbercoinDev/umbrel-tunnel)
- Esta carpeta de la tienda contiene únicamente el manifiesto, el Compose y el
  icono; la imagen del gateway se publica desde el repositorio de código.

## Requisitos

- UmbrelOS con Docker.
- Un VPS con Debian 12/13 o Ubuntu Server 22.04/24.04+ (`amd64` o `arm64`),
  IP pública fija y acceso root.
- Puertos en el VPS: el de WireGuard (`51820/udp` por defecto) y los públicos
  que quieras exponer. El `22/tcp` del VPS queda reservado para SSH.

## Instalación

1. Añade esta tienda en UmbrelOS (**App Store → Community App Stores**) con la
   URL `https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin`.
2. Instala **Umbrel Tunnel**.
3. Abre la app. La pestaña **Conexión** te pide la IP pública del VPS y genera
   el comando exacto que debes ejecutar en el VPS como root.
4. Ejecuta ese comando. El instalador imprime un bloque de emparejamiento con
   `Código: ut1.…` y lo guarda en `/root/umbrel-tunnel-pairing.txt`.
5. Pega el bloque completo en la app y pulsa **Vincular VPS**.
6. La app levanta WireGuard, registra su clave pública en el VPS y pasa a
   **Servicios**, donde puedes abrir los puertos que necesites.

La app genera su propio par de claves WireGuard y el token de la API. El código
`ut1` no contiene ninguna clave privada y la app comprueba que coincide con el
comando que generó antes de vincular el VPS.

## Detección de servicios

La app **no monta el socket de Docker**. Lee en modo solo lectura los
manifiestos, Compose y `exports.sh` de `${UMBREL_ROOT}/app-data`, y usa los
nombres DNS de la red Docker como destino. Los servicios del sistema
(SSH/panel de umbrelOS) se detectan aparte. Si una app no aparece, puedes añadir
un servicio manual desde **Avanzado**.

## Puertos y permisos

- Puerto de la ficha Umbrel: `8642` (proxy autenticado de Umbrel).
- Web interna del gateway: `8081`.
- El contenedor necesita `NET_ADMIN` para crear `wg0` y enlazar HAProxy a la IP
  del túnel.
- `SYS_MODULE` y `/lib/modules:ro` permiten cargar el módulo WireGuard del
  kernel en el primer arranque, igual que la app oficial de WireGuard.
- Monta `${UMBREL_ROOT}/app-data` en solo lectura para detectar apps. El socket
  de Docker nunca se monta.

## Datos persistentes

En `${APP_DATA_DIR}/data`:

- `wireguard/private.key`: clave privada de Umbrel (permisos `600`). Nunca sale
  del contenedor.
- `config.json`: VPS vinculado, exposiciones y ajustes.
- `haproxy.cfg`: configuración generada del proxy local.

Al desinstalar la app, Umbrel conserva estos datos salvo que se borren
explícitamente.

## Seguridad

- La API del VPS escucha solo en la IP del túnel (`10.0.0.1:8080` por defecto).
- El login de la app usa `APP_PASSWORD` con cookie de sesión firmada, CSRF y
  límite de intentos. El proxy de Umbrel mantiene además su propia autenticación.
- La subred del túnel no puede solaparse con la red de Umbrel.
- Consulta [SECURITY.md](https://github.com/AlbercoinDev/umbrel-tunnel/blob/main/docs/SECURITY.md)
  para el modelo de amenazas completo.

## Solución de problemas

- Si el código `ut1` se rechaza con `pair_bootstrap_mismatch`, pulsa **Cambiar
  VPS**, genera un comando nuevo y ejecútalo en el VPS.
- Si WireGuard no arranca, comprueba que el kernel de Umbrel tiene el módulo
  disponible y revisa los logs del contenedor.
- Guía completa:
  [docs/TROUBLESHOOTING.md](https://github.com/AlbercoinDev/umbrel-tunnel/blob/main/docs/TROUBLESHOOTING.md).
