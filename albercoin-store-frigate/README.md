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

No se incluye una suite automatizada ni se ha desplegado la app como parte de
esta revisión documental. Una prueba funcional requiere un entorno Umbrel con
Bitcoin y Electrs. Consulta [desarrollo](../docs/development.md) y
[publicación](../docs/releasing.md) para el proceso existente.
