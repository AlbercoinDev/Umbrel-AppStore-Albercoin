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
3. Abre la app. **Configuración y diagnóstico** te pide la IP pública del VPS y genera
   el comando exacto que debes ejecutar en el VPS como root.
4. Ejecuta ese comando. El instalador imprime un bloque de emparejamiento con
   `Código: ut1.…` y lo guarda en `/root/umbrel-tunnel-pairing.txt`.
5. Pega el bloque completo en la app y pulsa **Vincular VPS**.
6. La app levanta WireGuard, registra su clave pública en el VPS y pasa a
   **Servicios y puertos**, donde puedes publicar TCP y UDP.

La app genera su propio par de claves WireGuard y el token de la API. El código
`ut1` no contiene ninguna clave privada y la app comprueba que coincide con el
comando que generó antes de vincular el VPS.

## Detección de servicios

La app **no monta el socket de Docker**. Lee en modo solo lectura los
manifiestos, Compose, `.env` y `exports.sh` de `${UMBREL_ROOT}/app-data`, y usa
los nombres DNS de la red Docker como destino. Cada puerto se clasifica por tipo
(web/HTTP, RPC, P2P, ZMQ, Electrum, Lightning, métricas, base de datos, TCP o
UDP) combinando lo declarado en Compose con variables de entorno, flags del
comando y un escaneo TCP/HTTP acotado a la red privada de Docker. Los puertos
marcados como internos o sensibles se pueden ocultar con un filtro. Los
servicios detectados como web se publican por subdominio en 80/443; el resto usa
un puerto público personalizable. Los servicios del sistema (SSH/panel de
umbrelOS) se detectan aparte. Si una app no aparece, puedes añadir un
servicio manual desde **Servicios y puertos → Añadir servicio manual**.

## VPS compartido: varios Umbrel, cada uno con su dominio

Desde 1.5.0 un mismo VPS puede dar servicio a varios Umbrel. Cada uno tiene su
propio dominio público, su token y su IP en el túnel. Dos Umbrel pueden
publicar la misma app en el mismo puerto y el VPS decide por el dominio:

| Publicación | ¿Mismo puerto en dos Umbrel? |
| --- | --- |
| HTTPS (443) y HTTP | Sí, con dominios distintos (`Host`/SNI) |
| TLS añadido (p. ej. Electrs 50002) | Sí, con dominios distintos (SNI) |
| TCP o UDP directo | No: cada puerto es de un solo Umbrel |

Cada Umbrel solo ve y cambia sus propias publicaciones, solo usa su dominio y
no puede alcanzar a los demás por el túnel. Crea para cada dominio un registro A
(o wildcard) hacia la IP del VPS.

Añadir otro Umbrel: en su app escribe la IP del VPS y rellena **VPS compartido:
dominio de este Umbrel**; ejecuta en el VPS el comando `umbrel-tunnel peer add …`
que genera y pega el bloque `ut1` resultante.

Convertir el Umbrel que ya usaba el VPS (para que deje de tener el token de
administración):

```sh
umbrel-tunnel peer list
sudo umbrel-tunnel peer claim <ID> --domain casa.example.com   # pega el ut1 en su app
sudo umbrel-tunnel token rotate
```

Detalles en el [README del proyecto](https://github.com/AlbercoinDev/umbrel-tunnel#vps-compartido-varios-umbrel-con-dominio-propio).

## Actualizar a 1.5.0

Actualiza primero el VPS y después esta app desde la tienda de Umbrel.

```sh
sudo umbrel-tunnel backup --output /root/umbrel-tunnel-before-update.tar.gz
sudo umbrel-tunnel update
umbrel-tunnel version   # 1.5.0
sudo umbrel-tunnel doctor
```

`update` verifica el checksum, comprueba que el binario nuevo se ejecuta,
actualiza las unidades systemd y reinicia el servicio. Si vienes de antes de
1.2.0, usa el instalador del tarball para actualizar también `tmpfiles`, `sysctl`
y la unidad. No uses `--force` ni `--purge`. Se mantienen configuración, claves,
token y publicaciones; los servicios se reinician brevemente. No desinstales ni
vuelvas a vincular.

Si vienes de un VPS anterior a 1.3.2, su `update` aún exige `--from`:

```sh
sudo umbrel-tunnel update \
  --from "https://github.com/AlbercoinDev/umbrel-tunnel/releases/latest/download/umbrel-tunnel-linux-$(dpkg --print-architecture)"
```

La revisión 1.5.0 permite compartir un VPS entre varios Umbrel (sección anterior).
Actualiza **primero el VPS a 1.5.0**.

Desde 1.4.2 se refuerza la seguridad: el panel rechaza (403) las peticiones
que llegan por el túnel WireGuard, el instalador del VPS está fijado al tag de la
release y verifica el SHA256 del binario, un `config.json` corrupto se recupera
de la última copia válida en lugar de sustituirse por valores por defecto, y una
rotación interrumpida por un reinicio se confirma o se revierte al arrancar.

Desde 1.4.1 la app coordina la rotación WireGuard por el túnel anterior y confirma
el nuevo; si no se confirma, el VPS restaura la clave anterior en aproximadamente
un minuto. Desvincular limpia las
publicaciones antes de retirar el peer y conserva la conexión si falla. Si el
VPS es irrecuperable, la segunda confirmación permite olvidar la conexión solo
en Umbrel; debes comprobar y retirar las reglas/peer remotos por SSH.
Rotar el token de API no exige volver a vincular: guárdalo en
**Configuración y diagnóstico → Opciones avanzadas** de Umbrel.

La pantalla unificada permite publicar, pausar, reanudar y retirar publicaciones.
TCP + UDP crea dos reglas independientes. Los servicios web se publican por
subdominio en 80/443 y los puertos HTTP/HTTPS no se editan; el puerto interno de
la app nunca se expone como público. TCP, UDP y TLS mantienen un puerto público
personalizable. Los cambios desde la CLI del VPS se detectan para adoptar o
restaurar, sin sobrescribirlos silenciosamente.
Las ayudas se abren con ratón, teclado o toque. Configura el dominio base en
**Configuración y diagnóstico → Dominio base y certificados**. En cada servicio,
**Añadir publicación** reúne subdominio, puerto y cifrado opcional. Puedes crear
varias publicaciones del mismo destino: por ejemplo Electrs con el mismo nombre
en 50001 sin TLS añadido y 50002 con TLS, ambas hacia el puerto interno 50001.
Cada entrada permite editar, pausar o retirar por separado. Los dominios también
se pueden usar sin certificados; HTTP enruta nombres distintos en el mismo puerto.
Cambiar el dominio base no reescribe las entradas existentes. Los certificados
de Let's Encrypt se emiten y renuevan automáticamente, sin credenciales DNS.
Si el nombre no resuelve, la app muestra `DNS: ... no resuelve` con una sugerencia
en vez de `no viable challenge type found`; si el VPS no puede escuchar en los
puertos del reto, muestra `Puerto: ...` y no gasta intentos de la CA.
**Reintentar emisión** comprueba de inmediato sin esperar el límite automático y
la app sondea además 80/443 desde tu red (`Puertos desde Umbrel: 80 ✓ · 443 ✗`),
que es donde se ve un firewall del proveedor. El proveedor debe permitir DNS
wildcard apuntando al VPS en **Solo DNS** y el puerto TCP 80 debe permanecer
accesible para emisión y renovación. El puerto del servicio TLS dedicado y el 443
de HTTPS también deben permitirse en el firewall.

Consulta los comandos de actualización con backup/checksum y los requisitos DNS en
[HTTPS y TLS](https://github.com/AlbercoinDev/umbrel-tunnel/blob/main/docs/TLS.md).
Los certificados no sustituyen la autenticación de las apps.

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
