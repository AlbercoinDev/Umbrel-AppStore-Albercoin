# Onion Rotator — Umbrel App

Consulta y rotación selectiva de las direcciones Tor onion de aplicaciones Umbrel.

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

La app muestra servicios onion detectados y permite seleccionar cuáles rotar.
El resultado esperado es una dirección nueva tras reiniciar los servicios
necesarios, pero el reinicio o la regeneración pueden fallar.

**La rotación elimina las claves que dan identidad a la dirección anterior.**
Actualiza las referencias externas después de confirmar la nueva dirección.
No se incluye un asistente de recuperación de claves eliminadas.

- La detección normal busca servicios `app-<id>` y los relaciona con apps instaladas.
- Los servicios auxiliares, como `app-electrs-rpc`, se asignan a su app propietaria.
- Requiere servicios onion disponibles; solo se admite Tor v3.
- La espera de regeneración es de hasta 120 segundos.
- No declara dependencias de otras apps en el manifiesto, pero necesita datos
  y servicios del nodo para operar.

## Instalación y acceso

Añade la tienda según el [catálogo](../README.md), instala **Onion Rotator** y
ábrela desde Umbrel. El puerto de la ficha es `2180`; el servidor interno
escucha en `8900`. Consulta la versión en [`umbrel-app.yml`](umbrel-app.yml).

La instalación usa Python con `src/` montado desde los archivos de la app.
El Compose no construye ni utiliza una imagen propia de su Dockerfile.

## Uso y configuración

1. Actualiza la lista con **Refresh** y revisa los servicios detectados.
2. Copia las direcciones que necesites con **Copy** y localiza sus referencias externas.
3. Selecciona los servicios que quieres rotar.
4. Pulsa **Rotate selected onion addresses** y confirma la advertencia.
5. Espera el resultado, comprueba la nueva dirección y actualiza tus clientes.

La rotación valida el ID y la disponibilidad de contenedores reiniciables;
elimina `hostname`, `hs_ed25519_secret_key` y `hs_ed25519_public_key`;
reinicia contenedores de la app e intenta reiniciar los de Tor de Umbrel.
Después espera la nueva dirección. Eliminar solo `hostname` no cambia una
identidad Tor v3, porque deriva de la clave secreta.

### Servicios onion del sistema

La sección avanzada permite limpiar servicios onion del sistema Umbrel
(web/auth). Es distinta de la rotación de apps y requiere **reiniciar el servidor
Umbrel completo** para regenerarlos. Puede invalidar el acceso remoto actual:
asegura acceso local antes de operar.

### Variables

- `ONION_ROTATOR_DRY_RUN=true`: simula sin borrar archivos ni reiniciar apps.
- `ONION_ROTATOR_DEBUG=true`: activa mensajes de depuración.
- `TOR_DATA_DIR`: ruta de datos Tor vista por el proceso.
- `UMBREL_APP_DATA_DIR`: ruta de apps instaladas vista por el proceso.

Los valores del despliegue están en [`docker-compose.yml`](docker-compose.yml).
No cambies rutas hacia datos reales para probar el modo de desarrollo.

## Datos, permisos y seguridad

El Compose monta datos del nodo:

| Origen | Destino | Acceso |
| --- | --- | --- |
| `${UMBREL_ROOT}/app-data` | `/app-data` | Lectura; identifica apps instaladas |
| `${UMBREL_ROOT}/tor/data` | `/tor-data` | Lectura/escritura; consulta y elimina archivos de identidad |
| `/var/run/docker.sock` | `/var/run/docker.sock` | Control de contenedores para reinicios |

La raíz Umbrel varía según instalación; `/home/umbrel/umbrel` es un ejemplo,
no una ruta universal. El acceso al socket Docker es un permiso muy amplio.
No se declara un volumen propio de historial persistente: los datos modificados
son los servicios Tor del nodo. Nunca compartas sus claves secretas.

Las operaciones de la app son locales; no incluye telemetría ni envío externo
de direcciones onion. Esto no significa que Tor o el equipo no usen la red.

## Problemas frecuentes

| Situación | Qué comprobar |
| --- | --- |
| Lista vacía o incompleta | Servicios Tor habilitados, directorio correcto, permisos y detección de apps instaladas |
| Servicio auxiliar inesperado | App propietaria y correspondencia del prefijo del ID antes de seleccionarlo |
| Fallo de reinicio | Acceso al socket Docker y existencia de contenedores de la app propietaria |
| No aparece una dirección nueva | Estado de Tor, permisos del directorio y tiempo de espera; no repitas borrados a ciegas |
| Se limpió un servicio del sistema | Completar el reinicio del servidor previsto y comprobarlo desde acceso local |
| Parece no cambiar nada | Si está activo `ONION_ROTATOR_DRY_RUN`, la operación es simulada |

Si falla después de eliminar claves, la dirección anterior puede haber dejado
de estar disponible. Investiga el resultado antes de intentar otra rotación.

## Desarrollo y mapa del código

Requisito: Python 3.12 o superior. Desde esta carpeta, revisa
`scripts/dev.sh` antes de ejecutarlo: crea datos ficticios en
`/tmp/onion-rotator-dev-tor` y activa simulación y depuración por defecto, pero
respeta los valores de entorno existentes. No lo conectes a datos de producción.

```sh
ONION_ROTATOR_DRY_RUN=true ./scripts/dev.sh
```

Los ejemplos ficticios son `bitcoin`, `electrs`, `lnd` y `cln`. La lista visible
también depende de la detección de apps instaladas; los hostname ficticios por
sí solos no garantizan que aparezcan todos los ejemplos.

Con un entorno virtual activo:

```sh
./scripts/test.sh
```

El script instala requisitos y pytest. Alternativa, desde esta carpeta:

```sh
python -m pip install -r requirements.txt pytest
PYTHONPATH=src python -m pytest tests/ -v
```

| Ruta | Responsabilidad |
| --- | --- |
| `src/main.py` | Servidor HTTP y API |
| `src/config.py` | Configuración |
| `src/detector.py` | Detección de servicios Tor |
| `src/rotator.py` | Rotación de servicios de apps |
| `src/system_onions.py` | Operaciones sobre servicios del sistema |
| `src/restarter.py` | Detección y reinicio mediante Docker |
| `src/models.py` | Referencia de modelos de API |
| `src/i18n.py` | Traducciones de la interfaz |
| `src/static/index.html` | Interfaz web |
| `tests/` | Pruebas y fixtures |
| `scripts/` | Desarrollo, pruebas y publicación |
| `Dockerfile`, `requirements.txt` | Recursos de construcción; sin dependencias Python de ejecución declaradas |

El script de publicación afecta al repositorio completo: consulta
[publicación](../docs/releasing.md) antes de usarlo. Las pruebas disponibles no
certifican una rotación real en todas las instalaciones Umbrel y no se han
ejecutado de nuevo como parte de esta revisión documental.

Soporte: [incidencias del repositorio](https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin/issues).
