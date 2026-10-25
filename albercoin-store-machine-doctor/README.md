# Machine Doctor — Umbrel App

Diagnóstico de CPU para nodos Umbrel: prueba controlada con `stress-ng`, carga
por núcleo, sensores disponibles y un informe JSON persistente.

[Catálogo](../README.md) · [Arquitectura](../docs/architecture.md) ·
[Desarrollo](../docs/development.md) · [Publicación](../docs/releasing.md)

## Índice

- [Funciones y límites](#funciones-y-límites)
- [Instalación y acceso](#instalación-y-acceso)
- [Uso y configuración](#uso-y-configuración)
- [Datos, permisos y seguridad](#datos-permisos-y-seguridad)
- [Problemas frecuentes](#problemas-frecuentes)
- [Desarrollo y mapa del código](#desarrollo-y-mapa-del-código)

## Funciones y límites

- Prueba CPU de cinco minutos por defecto, cancelable y de una en una.
- Carga total y por núcleo; temperaturas y frecuencias cuando el host las expone.
- Inventario de sensores térmicos y hwmon.
- Resultado `PASS`, `WARNING` o `FAIL` e informe con muestras.
- El diagnóstico puede ralentizar otras apps, especialmente en equipos modestos.
- Temperaturas por núcleo y contadores de throttling no están disponibles en
  todos los equipos. La ausencia de un sensor no demuestra una avería.

Los diagnósticos de disco, RAM y análisis térmico completo están pendientes.
Sus carpetas contienen propuestas, no implementaciones. El orden previsto es
disco (SMART con `smartctl`), análisis térmico completo y RAM (herramienta por
decidir entre `memtester` y `stress-ng --vm`); no es un compromiso de entrega.

## Instalación y acceso

Añade la tienda según el [catálogo](../README.md) e instala **Machine Doctor**
desde Umbrel. No requiere otras apps. Abre su icono en el panel; el puerto
de la ficha y del servidor es `2190`.

La versión está en [`umbrel-app.yml`](umbrel-app.yml). Umbrel descarga la imagen
de GHCR indicada en [`docker-compose.yml`](docker-compose.yml); no construye
el Dockerfile al instalar.

## Uso y configuración

Pulsa **Start CPU test**, observa las muestras y consulta **View latest report**
al terminar. Puedes cancelar la prueba. Planifica el diagnóstico cuando el nodo
no esté ejecutando tareas sensibles al rendimiento.

Variables de ejecución declaradas en Compose:

| Variable | Valor predeterminado | Significado |
| --- | --- | --- |
| `TEST_DURATION_SECONDS` | `300` | Duración; el código la limita a 5–3600 segundos |
| `CPU_WARNING_TEMP` | `80` | Umbral de advertencia térmica, °C |
| `CPU_CRITICAL_TEMP` | `90` | Umbral crítico, °C |
| `CPU_FAIL_TEMP` | `100` | Umbral de fallo térmico, °C |
| `CPU_MIN_EXPECTED_LOAD` | `90` | Carga mínima esperada, % |

Son parámetros del contenedor, no controles editables desde el panel.
Modificar variables de ejecución no exige reconstruir la imagen, pero sí
recrear el servicio con el entorno actualizado. Un simple reinicio no cambia
el entorno de un contenedor existente. No edites el despliegue de producción
para una prueba de desarrollo.

## Datos, permisos y seguridad

`${APP_DATA_DIR}/data` se monta como `/data`; los informes se guardan en
`/data/reports`. Incluyen metadatos, inicio y fin, CPU y núcleos, máximos y medias,
advertencias, errores, muestras por segundo y salida de `stress-ng`.
No hay un asistente propio de copia/restauración. Conserva los JSON que necesites
fuera del nodo antes de desinstalar; revisa su información de hardware antes de
compartirlos.

El servicio se ejecuta como root con `privileged: true` y estos montajes:

- `/proc:/host/proc:ro`: CPU y estadísticas del host.
- `/sys:/host/sys:ro`: sensores, frecuencias y señales del kernel.
- `/dev:/host/dev:ro`: reservado para futuros diagnósticos de disco.

Los montajes de solo lectura no anulan los permisos amplios de `privileged`.
No se expone una shell ni se aceptan órdenes arbitrarias desde la interfaz.
La orden diagnóstica es `stress-ng --cpu <trabajadores> --timeout <duración>s
--metrics-brief`: los trabajadores se calculan según afinidad y cuotas CPU
detectadas, con un mínimo de uno. Al cancelar o cerrar se intenta terminar el
proceso hijo.

## Problemas frecuentes

| Situación | Qué comprobar |
| --- | --- |
| No aparecen temperaturas | Disponibilidad de sensores en el host y montaje de `/sys`; algunos equipos no los exponen |
| Carga inferior a la esperada | Cuotas CPU, núcleos disponibles, otras cargas y advertencias del informe; no concluir una avería solo por este dato |
| Resultado `WARNING` o `FAIL` | Advertencias, errores, temperaturas y salida de `stress-ng` del JSON; el resultado resume esta prueba, no certifica toda la máquina |
| No hay informe reciente | Si la prueba terminó, espacio disponible y escritura en `/data/reports` |
| El nodo se ralentiza | Cancela la prueba y repítela en una ventana de mantenimiento |

No borres datos ni cambies umbrales para ocultar un resultado sin investigar.

## Desarrollo y mapa del código

| Ruta | Responsabilidad |
| --- | --- |
| `src/main.py` | Servidor FastAPI y rutas |
| `src/modules/cpu/service.py` | Estado, muestras y coordinación de la prueba |
| `src/modules/cpu/stress.py` | Proceso `stress-ng` y cálculo de trabajadores |
| `src/modules/cpu/sensors.py` | Lectura de CPU, sensores y frecuencias |
| `src/modules/cpu/report.py` | Construcción y almacenamiento de informes |
| `src/modules/{disk,thermal,ram}/README.md` | Alcance futuro |
| `src/static/` | Interfaz web |

No hay una suite automatizada incluida. Para una comprobación manual en un
entorno de desarrollo, establece `TEST_DURATION_SECONDS=30` al crear el
contenedor. Reconstruye la imagen únicamente si has cambiado su código o
dependencias. Observa inicio, muestras, cancelación e informe final. Consulta
[desarrollo](../docs/development.md) y el
[workflow de imagen](../.github/workflows/machine-doctor-image.yml).

Esta documentación no acredita una nueva prueba en hardware.
Soporte: [incidencias del repositorio](https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin/issues).
