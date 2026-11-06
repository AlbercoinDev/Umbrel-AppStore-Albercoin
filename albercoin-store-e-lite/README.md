# e-lite para Umbrel

Servidor Electrum 1.4 autónomo para Umbrel. Incluye su propio **Bitcoin Core 32.1**
y no depende de las apps **Bitcoin** ni **Electrs** de Umbrel.

- App: `albercoin-store-e-lite`
- Versión: `0.1.0-alpha.25.2-02`
- Upstream: [AlbercoinDev/e-lite](https://github.com/AlbercoinDev/e-lite)
- Desarrollador: Albercoin

> **Aviso:** e-lite está en fase alpha. No está pensado para custodiar fondos sin
> verificar su funcionamiento. La primera sincronización de mainnet puede tardar
> días y ocupar mucho disco. Los puertos Electrum no tienen autenticación de
> clientes.

## Qué incluye

- e-lite `0.1.0-alpha.25.2`, descargado y verificado por SHA256.
- Bitcoin Core `32.1` oficial, descargado y verificado por SHA256.
- Panel web con estado, progreso, conexiones, registro y configuración.
- Asistente de primer inicio para elegir `mainnet`, `testnet4` o `signet`.
- Configuración por parámetro con opción de usar el valor predeterminado de e-lite.
- Servicio onion mediante el contenedor Tor de Umbrel.
- Certificado TLS local generado automáticamente.

No usa la app Bitcoin de Umbrel ni la app Electrs. Bitcoin Core se ejecuta dentro
de esta app con su propio datadir y su propia sincronización.

## Puertos

| Puerto | Uso |
| --- | --- |
| `2194` | Panel web de Umbrel (`app_proxy`) |
| `55011` | Electrum TCP clearnet/LAN |
| `55022` | Electrum TLS |
| `3000` | UI interna del contenedor, no publicada |
| `9184` | Estado interno de e-lite, solo loopback del contenedor |

Los puertos `55011` y `55022` se publican en el host. El puerto TCP no cifra la
conexión y ninguno de los dos autentica clientes.

Umbrel reserva listeners internos en el rango `50000+` para su ingress LAN. Los
puertos `55011/55022` se eligieron porque estaban libres en el nodo y no
coinciden con los puertos internos que estaban bloqueando el arranque anterior.
El panel web se inicia antes que TLS, e-lite y Bitcoin Core para que Umbrel
pueda crear la ruta del panel aunque la descarga o el certificado tarden.

## Primer inicio

1. Instala la app desde la tienda.
2. Abre el panel. La app descarga e-lite y Bitcoin Core (unos 100 MB) con
   verificación SHA256.
3. Elige la red:
   - **mainnet**: cadena real. Requiere confirmación explícita y mucha paciencia.
   - **testnet4**: red de pruebas.
   - **signet**: red de pruebas estable, recomendada para validar la instalación.
4. Pulsa **Iniciar e-lite**.

La descarga se hace una sola vez y queda en `data/runtime`. La app no empieza a
sincronizar hasta que hayas elegido la red.

## Conexiones

Para una wallet en la red local:

- TCP: `umbrel.local:55011`
- TLS: `umbrel.local:55022` con SSL activado

El certificado TLS es autofirmado y está firmado por una CA local. Puedes
descargar `ca.crt` desde el panel e importarlo en la wallet si quieres validar el
certificado. Si la wallet no acepta la CA, usa el puerto TCP dentro de tu red de
confianza.

Por Tor:

- `dirección.onion:55011`
- `dirección.onion:55022`

El servicio onion se publica con el contenedor Tor de Umbrel. La dirección puede
tardar unos segundos en aparecer. El target del servicio oculto usa el hostname
del contenedor (`e-lite`), no el alias de servicio, porque Tor necesita
resolverlo desde su propio contenedor.

Para TLS con un dominio público, añade el hostname en **Configuración → TLS para
dominio público**. El certificado incluirá también ese nombre en el SAN. Si el
dominio público termina el TLS en otro proxy, el certificado que verá el wallet
es el del proxy, no el de e-lite.

## Configuración

La pestaña **Configuración** permite fijar manualmente los parámetros de e-lite.
Cada campo tiene una casilla **Usar valor predeterminado**:

- marcada: el parámetro no se escribe en el TOML y e-lite usa su propio valor;
- desmarcada: se escribe el valor manual.

Grupos disponibles:

- **Rendimiento**: perfil, adaptive, caché, memoria, lectores, commit, batch y reorg.
- **Servidor y red**: clientes, rawtx, P2P, Esplora y clearnet.
- **Poda y disco**: poda de Core, mínimos de espacio, lag y reparación.
- **Bitcoin Core y registro**: timeout, argumentos extra, reinicio y rotación.
- **Avanzado**: opciones que conviene cambiar solo si sabes lo que haces.
- **TLS para dominio público**: hostnames adicionales para el SAN del certificado.

Al guardar se regenera `/data/config/e-lite.toml` y se reinicia e-lite. Bitcoin
Core también se reinicia, pero la cadena y el índice se conservan.

Cambiar de red conserva los datos de cada red. Cada red tiene su propio índice en
`data/e-lite/<red>` y su propia cadena dentro de `data/bitcoin`.

## Datos persistentes

| Ruta | Contenido |
| --- | --- |
| `data/config/` | Ajustes, TOML generado y certificados |
| `data/config/tls/` | CA local, certificado y clave |
| `data/e-lite/<red>/` | Índice RocksDB y logs de e-lite |
| `data/bitcoin/` | Datadir de Bitcoin Core |
| `data/runtime/` | Binarios verificados de e-lite y Core |
| `data/logs/` | Log del controlador y consola de e-lite |

El manifiesto excluye del backup el índice, la cadena, el runtime y los logs.
Los ajustes y certificados sí se incluyen.

## Imagen

La app usa una imagen propia publicada en GHCR:

```
ghcr.io/albercoindev/e-lite:0.1.0-alpha.25.2-02
```

El workflow `.github/workflows/e-lite-image.yml` construye y publica la imagen
para `linux/amd64` al hacer push a `master` que toque esta app, o manualmente con
`workflow_dispatch`. La primera vez, comprueba que el paquete de GHCR queda como
público; Umbrel debe poder descargarlo sin credenciales.

Para construir y probar en local:

```sh
docker build -t ghcr.io/albercoindev/e-lite:0.1.0-alpha.25.2-02 albercoin-store-e-lite
```

Después de la primera publicación, fija el digest en `docker-compose.yml`:

```yaml
image: ghcr.io/albercoindev/e-lite:0.1.0-alpha.25.2-02@sha256:<digest>
```

La imagen base `python:3.12-slim-bookworm` y la imagen de Tor están fijadas por
digest.

## Build de e-lite y Core

La imagen no contiene los binarios: el controlador los descarga en el primer
arranque desde:

- `https://github.com/AlbercoinDev/e-lite/releases`
- `https://bitcoincore.org/bin/bitcoin-core-32.1/`

Ambas descargas verifican el SHA256 fijado en `src/elite/runtime.py`. Los
binarios se instalan en `data/runtime` y se validan ejecutando `--version` antes
de arrancar.

Si cambias la versión de e-lite o de Bitcoin Core, actualiza:

1. `src/elite/runtime.py`: versión, URL y SHA256.
2. `umbrel-app.yml`: versión y release notes.
3. `README.md` y `docs/`.

## Pruebas

```sh
cd albercoin-store-e-lite
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Prueba local del panel en modo demostración:

```sh
docker build -t e-lite:dev albercoin-store-e-lite
docker run --rm -p 3000:3000 -e E_LITE_DEMO=1 -v "$PWD/data-demo:/data" e-lite:dev
```

Abre `http://localhost:3000`.

Prueba de que la UI y e-lite arrancan de verdad:

```sh
docker run --rm -p 3000:3000 -p 55011:55011 -p 55022:55022 \
  -e DEVICE_DOMAIN_NAME=umbrel.local \
  -v "$PWD/data-test:/data" ghcr.io/albercoindev/e-lite:0.1.0-alpha.25.2-02
```

La primera vez descarga los binarios y espera la selección de red.

## Limitaciones

- **Solo amd64**. e-lite solo publica el binario `x86_64`. En Raspberry Pi
  (arm64) la app muestra un error claro y no arranca. Para soportar arm64 habría
  que compilar e-lite desde fuente.
- **Mainnet duplica datos**. Aunque Bitcoin Core use poda manual, e-lite mantiene
  su propio índice y Core conserva su cadena podada. Reserva espacio suficiente.
- **Alpha**. e-lite no tiene autenticación de clientes y su protocolo puede
  cambiar.
- **TLS autofirmado**. Algunas wallets no aceptan la CA local. El puerto 55011
  sigue disponible.
- **Dependencia de descarga inicial**. La app necesita acceso a GitHub y
  bitcoincore.org la primera vez. Después funciona con los binarios locales.

## Actualizaciones

1. Actualiza la versión de e-lite/Core en `runtime.py` y el SHA256.
2. Sube la versión de `umbrel-app.yml`.
3. Publica la imagen (push a master o workflow manual).
4. Fija el nuevo digest en `docker-compose.yml`.
5. Instala/actualiza desde Umbrel.

Los ajustes, certificados, la cadena y el índice se conservan.
