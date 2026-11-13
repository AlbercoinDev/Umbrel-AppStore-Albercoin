# e-lite para Umbrel

Servidor Electrum 1.4–1.7 autónomo con Bitcoin Core incluido. Puede ejecutar
**mainnet, testnet4 y signet a la vez**, con activación y configuración
independientes por red.

- App: `albercoin-store-e-lite`
- Versión: `0.1.0-alpha.29.12.3-01`
- Upstream: [AlbercoinDev/e-lite](https://github.com/AlbercoinDev/e-lite)
- Desarrollador: Albercoin

> **Aviso:** e-lite está en fase alpha. No está pensado para custodiar fondos sin
> verificar su funcionamiento. Ejecutar varias redes multiplica el consumo de
> disco, CPU y memoria. Los puertos Electrum no tienen autenticación de clientes.

## Qué incluye

- e-lite `0.1.0-alpha.29.12.3`, verificado por SHA256. Compatible con Electrum 1.4–1.7.
- Bitcoin Core `32.1` oficial, verificado por SHA256.
- Tor `16.0` (bundle oficial) verificado por SHA256.
- Un Core, un e-lite, un índice, un certificado TLS y un onion por red.
- `public-servers.toml` por red para proveedores Esplora, sembrado desde el
  template oficial de e-lite. Si el archivo existe, no se sobrescribe.
- Rate limiting configurable por red (límite por segundo y ráfaga).
- Panel web multi-red con activación/desactivación y configuración por red.
- Lotes JSON-RPC para Sparrow (banner compatible con ElectrumX) y sonda de
  proveedores en paralelo: primera carga de wallet mucho más rápida en LAN y
  por onion.
- Asistente de primer inicio para elegir las redes activas.

## Puertos

| Red | Plain | TLS | Métricas internas |
| --- | --- | --- | --- |
| mainnet | `55011` | `55022` | `9184` |
| testnet4 | `55111` | `55122` | `9185` |
| signet | `55211` | `55222` | `9186` |

Además:

| Puerto | Uso |
| --- | --- |
| `2194` | Panel web de Umbrel (`app_proxy`) |
| `3000` | Web interna del contenedor, no publicada |

Los seis puertos Electrum se publican en el host. TCP no cifra la conexión y
ninguno de los dos autentica clientes.

e-lite 29.12 pone el listener plano en `127.0.0.1` por defecto; esta app lo
mantiene en `0.0.0.0` para no romper wallets de la LAN. Si quieres que el
puerto plano solo escuche en localhost, desactiva **Electrum sin TLS en la
LAN** en Configuración → Avanzado (`server.plain_lan`); desde otros equipos
deberás usar TLS.

## Primera carga de Sparrow

La app viene configurada para que la primera carga de una wallet Sparrow sea lo
más corta posible en los dos transportes:

- **`server.advertise_electrumx_compat = true`**: el banner de `server.version`
  es `e-lite <versión> (electrumx-compatible)`, que Sparrow reconoce. Entonces
  envía **lotes JSON-RPC** (páginas de 100) en vez de una petición por dirección:
  en onion reduce decenas de viajes de varios segundos a unos pocos; en LAN
  también baja mucho el número de idas y vueltas. Se puede desactivar en
  Configuración → Avanzado si otra wallet se comporta mal.
- La sonda de proveedores es **en paralelo** y la puerta de Electrum abre unos
  2 s después del primer proveedor sano.
- **`rawtx.tor_request_timeout_seconds = 20`**: cada bloque por Tor crea su
  circuito y descarga el bloque completo; un timeout corto enfriaba
  proveedores justo durante la primera carga.
- Ajustes opcionales de velocidad en Avanzado: `rawtx.prefetch` (precarga el
  historial; más ancho de banda), `rawtx.decoys = 0` (menos señuelos, menos
  privacidad) y `rawtx.max_parallel_fetches` (más descargas simultáneas; puede
  saturar Tor). Los valores por defecto priorizan privacidad y estabilidad.
- Para medir una primera carga **en frío**, vacía la caché rawtx desde
  Configuración → **Caché rawtx** y reinicia la red (o marca la casilla al
  vaciarla). Sin ese reinicio, los ficheros desaparecen pero el bloque en RAM
  sigue caliente. Los detalles de red muestran el estado real de los lotes
  (`Lotes JSON-RPC`) y la caché (`Caché rawtx`).

Las fuentes externas de bloques siguen intentando **Tor primero**; permitir
clearnet (`rawtx.allow_clearnet`) no hace que se prefiera directo mientras Tor
funcione. La mejora para una wallet en LAN es el batching y la sonda paralela.

## Caché rawtx

e-lite mantiene una única caché en disco: bloques de reparación de poda y
transacciones verificadas. Está limitada por `rawtx.cache_mib` (256 MiB por
defecto: una cuarta parte para bloques, el resto para transacciones) y queda
**excluida** de las copias de seguridad.

En **Configuración → Caché rawtx** puedes ver:

- directorio, ficheros y bytes de bloques y transacciones, con sus límites;
- total frente a la capacidad y espacio libre del sistema de ficheros;
- entradas ignoradas (nunca se tocan) y si la ruta fue rechazada.

El botón **Vaciar caché** ejecuta `e-lite cache clear`, que solo borra `*.blk`
de la raíz y `tx/<xx>/*.tx`; rechaza cualquier ruta que toque el índice, Core,
TLS u onion. No borres `rawtx-cache` a mano: el comando es seguro e idempotente.
La caché en RAM no se libera al borrar los ficheros; marca **Reiniciar la red
al terminar** (o desactiva y activa la red desde Estado) para medir en frío.

## Primer inicio

1. Instala la app desde la tienda.
2. Abre el panel. La app descarga e-lite, Bitcoin Core y Tor.
3. Marca las redes que quieres activar:
   - **mainnet**: cadena real, requiere confirmación explícita.
   - **testnet4**: red de pruebas.
   - **signet**: red de pruebas estable, activada por defecto.
4. Pulsa **Iniciar e-lite**.

Cada red activa arranca su propio Bitcoin Core, su e-lite y su Tor. Las redes
desactivadas conservan sus datos y pueden activarse después desde el panel.

## Panel de estado

Cada red tiene una tarjeta con:

- estado y progreso de Core y del índice;
- altura, lag, clientes y espacio libre;
- conexiones clearnet, TLS y onion;
- botón **Activar / Desactivar**;
- configuración y logs propios.

Al desactivar una red se detienen su Core, su e-lite y su Tor. El puerto de host
sigue mapeado por Docker, pero no hay servicio escuchando: el wallet recibe
conexión rechazada.

## Configuración

La versión 0.1.0-alpha.29.12.3 conserva las rutas explícitas de Umbrel y no migra
datos ni identidades onion. Los valores automáticos de poda ahora son
`prune_defer_free_gib = 20` y `min_gain_blocks = 5000`; los ajustes manuales
guardados se conservan. TLS sigue usando la CA local del wrapper y sus
certificados por red, sin regenerar la CA en la actualización. Para wallets
que requieren confianza explícita (incluido Sparrow/Java), importa la CA de
`/tls/ca.crt`, no el certificado servidor ni su cadena completa.

Estado y Configuración presentan pestañas en el orden **mainnet, testnet4,
signet**. Cada red tiene su propia configuración y la casilla **Usar valor
predeterminado** por campo. La activación y desactivación se realiza únicamente
en Estado; Configuración no duplica la gestión de redes. Los cambios se aplican
solo a la red editada.

En Estado, las conexiones muestran el hostname local y la dirección onion una
sola vez, sin puerto, con los puertos Plain y TLS separados. El recuadro de
detalles incluye Core, poda, indexación, caché, rate limiting y versiones.
Los registros e-lite y Core siempre están visibles al final, en recuadros de
altura fija, con hasta 1000 líneas recientes (máximo 128 KiB por archivo).
Se actualizan cada 10 segundos sin recargar la página: al desplazarse hacia
arriba se conserva la lectura; **Ir al final** recupera el seguimiento en vivo.
No es un visor del archivo histórico completo ni de los logs rotados.

En **Servidor y red** están `server.rate_limit` y `server.rate_burst`. Su valor
predeterminado es `auto`: 200 peticiones/s y burst 5000 para todos los clientes.
Las peticiones esperan cuando se agotan los tokens, en lugar de rechazarse.
`rate_limit = 0` desactiva el límite. La caché rawtx predeterminada es 256 MiB,
con un bloque señuelo y rotación del log de e-lite a los 16 MiB. Los ajustes
manuales guardados se conservan.

Cada red tiene además un campo de **hostnames públicos TLS**. Los nombres
añadidos se incluyen en el SAN de su certificado. Ejemplo:

```text
elite.umbrel.nodobitcoin.org
```

El certificado de cada red incluye también su propia dirección onion, el dominio
del dispositivo y `umbrel.local`.

## Tor

Tor lo gestiona e-lite dentro del mismo contenedor, una instancia por red:

- el onion de una red solo existe mientras esa red está activa;
- los puertos onion son los mismos que los del listener:
  - mainnet `55011/55022`
  - testnet4 `55111/55122`
  - signet `55211/55222`;
- las claves onion viven en `data/config/networks/<red>/tor` y se incluyen en el
  backup;
- si Tor falla, el panel muestra el error. Las peticiones externas ya **no**
  caen a clearnet por defecto (AUD-128): con Core podado, las transacciones de
  bloques podados no están disponibles hasta que Tor funcione. Se puede
  reactivar el fallback en Configuración → Avanzado (`rawtx.tor_fallback_clearnet`),
  sabiendo que expone tu IP a los proveedores.

No se usa el contenedor Tor de Umbrel ni un `torrc` externo.

## Rendimiento en Umbrel

- Los datos van por un **bind mount** (`$APP_DATA_DIR/data`), no por overlay:
  la E/S es prácticamente nativa. TLS se termina dentro de e-lite y no hay
  proxy intermedio.
- e-lite 29.7.1 ajusta el presupuesto de memoria: `caché + memtables + runtime`
  siempre cabe en el techo detectado, y lee límites de cgroup v2/v1.
- El plan de máquina usa **núcleos físicos** (no hilos SMT) y clasifica el
  disco de datos y el de Core (NVMe, SSD SATA, HDD, virtual, red). De ahí salen
  lectores, jobs de RocksDB, caché y el intervalo de commit. El panel muestra
  CPU y disco detectados, y `/status.machine` expone el plan completo.
- Los commits en bloque son más grandes y menos frecuentes (250k txs, 512
  bloques, 20–60 s según disco, con tope de bytes), y la poda se ejecuta en su
  propio hilo después de la barrera de commit: ya no bloquea la indexación.
- El límite de backlog se mide en **bytes** (`max_bulk_lag_gib`, 30 GiB) y la
  reanudación en `core_resume_ahead_gib` (7 GiB). Los antiguos límites por
  bloques siguen disponibles como legado: si fijas uno en el panel, el wrapper
  escribe el GiB correspondiente a 0 para que tu valor siga mandando.
- `pruning.scheduler` permite `overlap` (por defecto) o `alternate`, donde Core
  y e-lite se turnan para escribir. `alternate` requiere
  `pause_core_during_bulk`.
- El panel muestra el backlog sin indexar y el modo del planificador.
- El techo de caché dinámico ya no cuenta dos veces memtables/runtime: usa
  `MemAvailable` y una reserva dinámica. El panel muestra memoria efectiva,
  memtables y table readers reales.
- `index.max_open_files` en **Avanzado** (0 = 8192, limitado por el `ulimit`
  del contenedor). 2048/4096 reduce los table readers en equipos de 8–16 GB.
- Diagnóstico de compactación: `/status.rocksdb` ya suma la caché de bloques
  real entre column families y expone ficheros L0 y compactaciones activas.
  El panel muestra compactaciones/L0 y la caché real; los picos de commit
  quedan explicados en el log.
- 29.8.7 corrige la lectura de la caché de bloques (antes ×7 por sumarla en
  todas las familias) y el ARC en ZFS (columna incorrecta del kstat).
- El índice ya alcanza el último bloque: el estanque final con lag de 1–2
  bloques ya no detiene la indexación y `READY` exige que Core haya terminado
  su descarga inicial. El porcentaje se limita a 99.9 % hasta el 100 % real.
- El plan de compactación usa núcleos físicos en SSD/NVMe (2–8 jobs); HDD
  mantiene 2. Con ARC sin límite en ZFS, e-lite avisa y sugiere un tope.
- `logging.level` en **Avanzado**: `info`, `debug` (por defecto durante la
  alpha) o `trace`. Se recarga con SIGUSR1, como el resto de campos calientes.
- Al llegar a la punta, la poda ignora el aplazamiento por espacio libre y
  corta hasta el máximo seguro. En vivo agrupa con `min_gain_live_blocks`
  (144 bloques ≈ una poda diaria, ~220 MiB retenidos); 0 usa
  `min_gain_blocks`.
- El plan de compactación aplica hilos físicos también al arrancar (SSD/NVMe
  2–8, ZFS/virtual 4, HDD/red 2).
- `index.zfs_arc_dynamic` (activado) gestiona el tope del ARC por fase:
  `zfs_arc_max_mib` durante la descarga (0 = mitad de RAM) y
  `zfs_arc_min_mib` (1024) una vez sincronizado. Solo actúa si puede escribir
  el parámetro de ZFS; sin permiso queda en modo aviso y lo reintenta cada
  10 minutos. **Dentro del contenedor de Umbrel no puede escribir
  `zfs_arc_max`**: limita el ARC en el host (por ejemplo, 25 % de la RAM) para
  que `MemAvailable` y la caché de e-lite no se confundan. `e-lite arc-setup`
  es para instalaciones nativas, no para Umbrel. El panel avisa si detecta
  ZFS sin permisos.
- Presupuesto desde `MemAvailable` (no RAM instalada) y `-dbcache` de Core
  ajustado a la memoria libre; con varias redes el wrapper sigue fijando su
  propio tope de `-dbcache`.
- Opciones en **Avanzado**: `core.io_priority` (ionice 0–7),
  `core.cgroup_io_weight` (io.weight 1–10000, requiere delegación),
  `index.adaptive_memtables`, `index.prefetch_deadline_ms` (50–60000 ms),
  `core.network` (`auto`/`private`/`clearnet`),
  `rawtx.tor_fallback_clearnet`, `rawtx.prefetch`,
  `rawtx.tor_request_timeout_seconds`, `rawtx.max_parallel_fetches`,
  `server.advertise_electrumx_compat` y `server.plain_lan`. Se mantienen los
  valores privados de e-lite 29.12 (Tor-only y sin precarga); el listener plano
  sigue en la LAN por compatibilidad y se puede mover a localhost. La
  compatibilidad ElectrumX está activada para Sparrow.
- La poda normal reserva 20 GiB libres y avanza cada 5.000 bloques; la poda de
  emergencia por poco disco sigue saltándose ambos límites.
- Discos ZFS y overlay se detectan como clase propia y usan el perfil SSD.
- El pipeline de indexación es continuo: las acciones del supervisor se
  ejecutan en los límites de commit en vez de reiniciar lectores cada 10 s.
  La poda solo hace `SyncWAL` antes de `pruneblockchain`, y `/status.pipeline`
  permite ver bloques preparados, aplicados y descartados.
- Core se arranca con `-listenonion=0`: no publica un onion propio. El Tor
  gestionado no tiene `ControlPort`, así que Core no reintenta
  `127.0.0.1:9051` cada minuto. Con `core.network = "auto"` (por defecto),
  e-lite añade `-onion=<SOCKS de la red>` para que Core alcance pares `.onion`
  a través de su Tor; el modo `private` usa solo redes privadas y es más
  lento. El panel muestra el SOCKS por red y que el ControlPort no está
  gestionado.
- El Compose fija `nofile = 8192`. e-lite reserva descriptores para sockets,
  RPC, proveedores y logs, y publica el valor efectivo en
  `/status.file_descriptors`. Con el límite por defecto de Docker (1024) se
  quedaría en ~960 y perdería la mejora de índices.
- Cada red tiene su propio proceso, Core y Tor. Con varias redes activas el
  wrapper escribe `index.memory_fraction = 0.60 / n` (solo si no has fijado un
  valor manual), para que la suma de techos no supere el 60 % de la RAM.
  Una sola red mantiene el valor automático de e-lite.
- Con varias redes, el wrapper también fija `-dbcache` por Core
  (`clamp(RAM/(4·n), 450, 2048)` MiB) salvo que hayas definido
  `core.extra_args`. Evita que cada Core planifique su propia caché sobre la
  RAM total.
- **Mínimo de RAM**: mainnet necesita 7500 MiB efectivos; testnet4, signet y
  regtest, 3800 MiB. En **Avanzado** existe `index.allow_low_memory`, que omite
  la comprobación; no está soportado para uso real.
- **Primera sincronización**: e-lite toma un `flock` por usuario
  (`/tmp/e-lite-<uid>/ibd.lock`). En este contenedor las redes comparten ese
  bloqueo: la primera que arranca hace el IBD y las demás esperan, sirviendo su
  índice si ya existe. En una instalación nueva con tres redes, testnet4 y
  signet empiezan cuando mainnet está a 1000 bloques de Core.
- El panel muestra un aviso cuando hay más de una red activa. Para máximo
  rendimiento deja solo mainnet; testnet4 y signet comparten CPU, RAM y disco.
- No se modifican los binarios de e-lite: todas las mitigaciones son del wrapper.

## Copias de seguridad

La pestaña **Copias** de la navegación centraliza todo el panel: pestañas por
red, destino, exportación, importación, progreso y limpieza. La copia incluye el
índice de e-lite y los **datos de cadena** de Bitcoin Core (`blocks/`,
`chainstate/`, `indexes/`) en tres archivos verificables. El proceso detiene la
red si estaba activa, ejecuta el trabajo y la reactiva al terminar.

- **Destino**: `/exports` si has concedido una carpeta del disco externo en
  Ajustes de la app → Almacenamiento; si no, `data/exports` (muévelo desde
  Files → Apps → e-lite).
- **Exportar**: hace el snapshot del índice y empaqueta los datos de cadena de
  Core con `tar` tras parar la red, **sin arrancar ninguna sonda de Core**.
  Nunca copia wallets, claves onion/I2P, `bitcoin.conf` ni `settings.json`:
  guárdalos aparte si los usas. Por defecto el índice se guarda **sin
  comprimir** (formato 3, más rápido); la copia ocupa casi el tamaño del
  índice. Puedes elegir gzip 3/6, que tarda más y apenas reduce un índice ya
  comprimido.
- La punta, altura de poda y versión de Core se leen de `/status` antes de
  parar y se guardan en `core-facts.json` por red. Si la red ya está parada se
  usan esos datos; si no existen, se extraen de `debug.log`. La sonda de Core
  se ha eliminado por completo.
- La copia son tres archivos: `.index.tar.gz`, `.core.tar` y `.umbrel.json`
  (con tamaños exactos, altura, punta/poda de Core, versión y digest del
  índice). El snapshot del índice usa el **formato 3** de e-lite (manifiesto
  al principio y al final, índice almacenado); el Core va en un `.core.tar`
  sin comprimir, solo con datos de cadena. La importación valida red, tamaños
  y coherencia antes de restaurar, y pasa ese digest a e-lite con
  `--expect-digest`. Los bundles nuevos necesitan e-lite 29.12 o posterior;
  los antiguos (formato 2) se siguen importando.
- El espacio necesario usa el tamaño real del índice y de Core más un margen
  del 10 % (mínimo 256 MiB). Con el índice sin comprimir, reserva
  aproximadamente el tamaño del índice + Core.
- El progreso es **por fase**. Exportar: medir, parar, empaquetar Core,
  preparar índice (apertura + digest), empaquetar índice, validar y reactivar.
  Importar: parar, restaurar Core, restaurar índice, verificar el digest,
  validar y reactivar. Cada fase tiene su barra, contador y tiempo; el total
  del trabajo se muestra aparte.
- La exportación lee altura y hash de la salida de e-lite, sin descomprimir el
  archivo recién creado. Para listar snapshots de formato 3, el panel lee el
  manifiesto inicial y no recorre el archivo completo.
- Se puede **cancelar** un trabajo; se mata el grupo de procesos, se limpian
  parciales y se reactiva la red.
- Tras importar se invalidan `core-facts.json` y la caché de tamaños.
- **Importar**: comprueba el espacio necesario en el disco de datos. Los datos
  anteriores se conservan por defecto (`.pre-import-*`) y se pueden eliminar
  desde el panel cuando la red funciona. El botón de limpieza se bloquea
  mientras hay una copia en curso.
- e-lite extrae en un directorio oculto `.e-lite.import-partial` y lo
  renombra al final; el panel mide los bytes escritos por el proceso, muestra
  la fase de verificación del digest y limpia el staging si se cancela.
- Si un corte de luz interrumpe una importación, al arrancar se recuperan
  automáticamente los datos anteriores y se avisa en el panel.
- Los trabajos tienen límite de tiempo y cancelación segura (mata el grupo de
  procesos). Los `.tmp` y el staging parcial se limpian al terminar o al
  arrancar la app.
- El progreso muestra la salida del proceso en vivo y la duración de cada fase.
- Los snapshots de e-lite se pueden importar con un digest esperado opcional;
  los bundles usan el que guardaron al exportar. Los bundles antiguos siguen
  siendo válidos.
- La barra muestra fase, bytes copiados, total estimado y tiempo restante.
- Las bases de datos y las copias quedan **excluidas** del backup de Umbrel
  (`data/networks`, `data/exports`); la configuración, CA, TLS y onion sí se
  incluyen. Al restaurar no se toca `data/config`, así que se conservan la CA,
  los certificados y la identidad onion.
- La red se para durante la copia: el índice abre RocksDB en exclusiva y Core
  debe estar detenido para que el datadir sea consistente.

## Datos persistentes

```text
data/
  config/
    settings.json
    tls/ca.crt, ca.key              # CA local compartida
    networks/<red>/
      e-lite.toml
      public-servers.toml           # proveedores Esplora de la red
      tls/                          # certificado y clave de la red
      tor/                          # claves onion y estado de Tor
  networks/<red>/
    e-lite/                         # índice, logs y pid de Core
    core/                           # datadir de Bitcoin Core
  exports/                          # copias exportadas (excluidas del backup de Umbrel)
  runtime/                          # e-lite, Core y Tor verificados
  logs/                             # logs del controlador y consolas
```

El manifiesto excluye del backup índices, cadenas, runtime y logs. Conserva
ajustes, certificados y claves onion.

### Actualización desde versiones de una sola red

Esta versión **empieza con un diseño de datos nuevo y no migra** los índices,
datadirs ni onions anteriores. Al actualizar:

- aparece de nuevo el asistente de redes;
- no se reutilizan índices, cadenas ni onions antiguos;
- los directorios antiguos (`data/bitcoin`, `data/e-lite`,
  `data/config/e-lite.toml`) quedan en disco sin usarse;
- la CA existente en `data/config/tls` puede reutilizarse para firmar los nuevos
  certificados por red; los certificados de servidor antiguos no se usan;
- puedes borrar los directorios antiguos manualmente si necesitas recuperar
  espacio.

## Imagen

La app usa una imagen propia publicada en GHCR:

```
ghcr.io/albercoindev/e-lite:0.1.0-alpha.29.12.2-01@sha256:e7a79b77509d72c14ab76f91ffdff8f0898f60ac2a3916cf9f3bb16209c47763
```

El workflow `.github/workflows/e-lite-image.yml` construye y publica la imagen
para `linux/amd64` al hacer push a `master` que toque esta app, o manualmente con
`workflow_dispatch`. El paquete de GHCR debe ser público.

Después de publicar, fija el digest en `docker-compose.yml`:

```yaml
image: ghcr.io/albercoindev/e-lite:0.1.0-alpha.29.12.2-01@sha256:e7a79b77509d72c14ab76f91ffdff8f0898f60ac2a3916cf9f3bb16209c47763
```

La imagen base `python:3.12-slim-bookworm` está fijada por digest.

## Pruebas

```sh
cd albercoin-store-e-lite
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Panel en modo demostración:

```sh
docker build -t e-lite:dev albercoin-store-e-lite
docker run --rm -p 3000:3000 -e E_LITE_DEMO=1 -v "$PWD/data-demo:/data" e-lite:dev
```

Prueba real con testnet4 y signet:

```sh
docker run --rm -p 3000:3000 \
  -p 55011:55011 -p 55022:55022 \
  -p 55111:55111 -p 55122:55122 \
  -p 55211:55211 -p 55222:55222 \
  -e DEVICE_DOMAIN_NAME=umbrel.local \
  -v "$PWD/data-test:/data" ghcr.io/albercoindev/e-lite:0.1.0-alpha.29.12.2-01
```

La primera vez descarga los tres componentes y pide elegir redes.

## Limitaciones

- **Solo amd64**. e-lite solo publica el binario `x86_64`; arm64 no está
  soportado.
- **Consumo**. Tres redes activas implican tres Bitcoin Core, tres índices, tres
  e-lite y tres Tor. Mainnet puede necesitar mucho disco y días de sincronización.
- **Alpha**. e-lite no autentica clientes y su protocolo puede cambiar.
- **Rate limiting**. e-lite aplica por defecto 200 req/s y burst 5000 para todos
  los clientes; `server.rate_limit` y `server.rate_burst` se pueden
  cambiar por red o desactivar con `rate_limit = 0`.
- **TLS autofirmado**. Algunas wallets no aceptan la CA local; el TCP sigue
  disponible.
- **Puertos desactivados**. Docker mantiene el mapeo, pero sin servicio detrás.
- **TLS público**. Si un proxy externo termina el TLS, el certificado que ve el
  wallet es el del proxy, no el de e-lite.

## Actualizaciones

1. Actualiza la versión de e-lite, Core o Tor en `src/elite/runtime.py` con
   versión, URL y SHA256.
2. Sube la versión de `umbrel-app.yml`.
3. Publica la imagen y fija el digest en Compose.
4. Instala/actualiza desde Umbrel.

Los ajustes, los índices, las cadenas y las claves onion se conservan entre
actualizaciones de la misma versión multi-red.
