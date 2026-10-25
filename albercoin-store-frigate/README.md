# Frigate — Umbrel App

Servidor Electrum para Bitcoin Silent Payments (BIP352), basado en
[Sparrow Wallet Frigate](https://github.com/sparrowwallet/frigate).

[Catálogo](../README.md) · [Mapa del repositorio](../docs/architecture.md) ·
[Publicación](../docs/releasing.md)

## Índice

- [Funcionamiento y dependencias](#funcionamiento-y-dependencias)
- [Instalación y acceso](#instalación-y-acceso)
- [Configuración](#configuración)
- [Persistencia y copias](#persistencia-y-copias)
- [Código y despliegue](#código-y-despliegue)
- [Permisos y diagnóstico](#permisos-y-diagnóstico)
- [Problemas frecuentes](#problemas-frecuentes)
- [Pruebas y actualización](#pruebas-y-actualización)

## Funcionamiento y dependencias

Requiere las apps Umbrel `bitcoin` y `electrs`. Utiliza Bitcoin Core para
el escaneo y Electrs como backend para peticiones Electrum no relacionadas
con Silent Payments. La versión vigente y las notas de revisión están en
[`umbrel-app.yml`](umbrel-app.yml).

## Instalación y acceso

Añade la tienda siguiendo el [README principal](../README.md) e instala
Frigate desde Umbrel con sus dependencias disponibles.

- Panel web: puerto de la app `2125`, a través del proxy de Umbrel.
- Wallets Electrum compatibles: conexión directa al puerto `50005` del nodo.
- Tor: servicio onion Electrum en `50005`, configurado en `torrc`.
- La dirección onion se exporta desde `exports.sh` leyendo el hostname del
  servicio; antes de generarse puede aparecer `notyetset.onion`.

La compatibilidad Silent Payments depende también del cliente utilizado.

## Configuración

### Primer arranque: confirmación obligatoria

En una instalación nueva se inicia el panel, pero **Frigate no se descarga ni
arranca hasta confirmar la configuración**. Abre la app y elige:

- **Historial completo**: deja la altura vacía. Usa el inicio oficial: bloque
  `709632` (Taproot) en mainnet y `0` en redes de prueba.
- **Bloque concreto**: introduce una altura inicial.
- **Fecha**: selecciona una fecha UTC y pulsa «Buscar bloque». La altura propuesta
  se copia al campo editable; compruébala antes de confirmar.

Pulsa **«Confirmar e iniciar indexación»** para empezar. Una altura posterior
puede omitir pagos anteriores. La confirmación queda guardada; reiniciar la app
no vuelve a mostrar el asistente. Las instalaciones anteriores con ajustes
existentes no necesitan confirmarlo y conservan sus valores.

La búsqueda por fecha consulta Bitcoin Core y Electrs locales **en paralelo** y
devuelve el primer resultado válido, sin servicios externos. Usa búsqueda binaria
sobre el tiempo mediano de los últimos 11 bloques (las fechas de bloque individuales
no son ordenadas) y retrocede 144 bloques, sin bajar del inicio permitido. Este
margen es conservador, no una correspondencia exacta fecha/altura. Se muestra la
fecha UTC del bloque finalmente seleccionado. Core debe haber terminado su
sincronización; en mainnet se rechaza Electrs si su cabecera más reciente tiene
más de 24 horas. Electrum no permite certificar por sí solo que su servidor esté
completamente sincronizado: comprueba el estado del nodo. Las fechas futuras o
posteriores a la punta disponible se rechazan. Si ambos servicios fallan o la
búsqueda supera 30 segundos, el panel informa del error sin iniciar Frigate.

### Memoria y perfil para uso pequeño

Las instalaciones nuevas tienen los siguientes valores:

| Parámetro | Indexación inicial | Funcionamiento normal |
| --- | --- | --- |
| Caché scriptPubKey | `10M` (~4 GB según Frigate) | `1M` (~400 MB estimados) |
| Pool DuckDB | `2GB` | `1GB` |
| Hilos DuckDB | `2` | `2` |
| Backend | `CPU` | `CPU` |
| Suscripciones Silent Payments por conexión | `5` | `5` |
| Etiquetas por suscripción | `10` | `10` |

El aumento inicial solo se aplica a los valores de caché y memoria sin modificar
en el asistente. Si el equipo no tiene suficiente RAM para esa fase, configura
valores diferentes antes de confirmar. Al detectar que Frigate está listo,
se guarda `.efficient-profile` y se realiza **un único reinicio controlado** sin
borrar el índice. Los siguientes arranques usan los valores normales. Guardar
ajustes manuales después del asistente cancela el aumento automático, respetando
la configuración elegida.

**El límite DuckDB no limita la memoria total de Frigate**: hay que sumar Java,
la caché y las asignaciones nativas, además de Bitcoin Core y Electrs. No se
impone un límite duro Docker ni se garantiza una cifra total de consumo. Hay que
medir picos de indexación y escaneos simultáneos en el hardware de destino.

El paquete limita a 15 conexiones globales, 15 por IP, 10.000 suscripciones
scripthash por IP y 25.000 globales. Las suscripciones scripthash no son las cinco
suscripciones Silent Payments por conexión. Son límites orientativos para hasta
cinco personas con varios dispositivos: **no son autenticación ni un límite de
usuarios**, y pueden necesitar ajustes para wallets grandes. Tor sigue exento
de límites por IP; los límites globales sí se aplican.

El panel lee como máximo los últimos 128 KiB del log por petición y muestra las
últimas 35 líneas. Esto limita los picos de RAM del panel, pero no rota el log.

El panel permite consultar estado y modificar parámetros como altura inicial,
caché, backend de cómputo y límites. Los ajustes se guardan en
`data/frigate/settings.env`; al guardarlos se solicita reiniciar Frigate.
Las credenciales RPC y direcciones de las dependencias las proporciona Umbrel.

**Cambiar la altura inicial puede eliminar y reconstruir el índice.** No lo
trates como un simple ajuste visual. Revisa las advertencias del panel antes
de guardar. Los archivos `config.toml` se generan a partir de estos ajustes.

## Persistencia y copias

El volumen principal es `${APP_DATA_DIR}/data/frigate`:

- `settings.env`: ajustes del panel.
- `runtime/`: binario descargado y verificado.
- `db/` y subdirectorios de red: índices.
- `config.toml`, logs y banner: archivos de ejecución/configuración.

Las redes distintas de mainnet utilizan subdirectorios de red. El manifiesto
contiene `backupIgnore` para índices, runtime, cachés, configuraciones generadas
y logs: una copia de Umbrel no implica conservar el índice completo.
No se incluye un asistente propio de restauración. Protege las configuraciones
generadas porque pueden contener credenciales RPC.

## Código y despliegue

| Archivo | Responsabilidad |
| --- | --- |
| `docker-compose.yml` | Proxy, panel Python/HTML, descarga/arranque del binario y servicio Tor |
| `exports.sh` | Direcciones de red y exportación del hostname onion |
| `torrc` | Servicio onion Electrum |
| `umbrel-app.yml` | Ficha, dependencias, revisión y exclusiones de backup |
| `icon.png`, `icon.svg` | Recursos gráficos |

No hay Dockerfile ni imagen propia de esta app. El arranque prepara el runtime
con un binario fijo para AMD64 o ARM64, comprobado por SHA256.

## Permisos y diagnóstico

El panel lee los datos Tor; el servicio Tor escribe en `${TOR_DATA_DIR}`.
Frigate recibe credenciales RPC de Bitcoin y accede a Electrs por la red Docker.
No se declara acceso al socket Docker ni modo privilegiado.

Ante un fallo, consulta primero el panel y los logs de los servicios mediante
las herramientas de tu instalación Umbrel. Comprueba dependencias, red Bitcoin
y espacio disponible antes de considerar reconstruir el índice. No compartas
configuraciones o logs sin revisar credenciales y datos privados.

## Problemas frecuentes

| Situación | Qué comprobar |
| --- | --- |
| No conecta con Bitcoin o Electrs | Que ambas dependencias estén instaladas, arrancadas y accesibles en la red de Umbrel |
| Progreso inesperado o indexación lenta | Red Bitcoin seleccionada, sincronización del nodo, estado/logs y recursos disponibles; no borrar el índice como primera medida |
| La wallet no conecta | Host del nodo, puerto Electrum `50005` y compatibilidad del cliente; `2125` es el panel, no Electrum |
| No aparece dirección onion | Estado del servicio Tor y generación de su hostname; el valor provisional no es una dirección utilizable |
| Cambiar altura exige reconstrucción | Es un efecto previsto del ajuste; revisar la advertencia antes de guardar y planificar el tiempo de reindexación |

## Pruebas y actualización

Pruebas locales sin dependencias externas, desde la raíz del repositorio:

```sh
python3 -m unittest discover -s albercoin-store-frigate/tests -v
```

Comprueban el código Python y shell real integrado en Compose: asistente,
preservación de ajustes anteriores, confirmación HTTP, lectura acotada del log,
búsqueda por fecha y carrera entre backends, protocolo Electrum simulado y
transición de perfiles con un binario simulado. No descargan ni ejecutan Frigate.
Una prueba funcional requiere un entorno Umbrel con Bitcoin y Electrs: verificar
instalación detenida, consulta de fechas, primer arranque, transición y
reconexión de cinco wallets, picos de RAM y actualización sin reindexar.
Consulta [desarrollo](../docs/development.md) y
[publicación](../docs/releasing.md) para el proceso existente.
