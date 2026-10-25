# Desarrollo y pruebas

[Volver al catálogo](../README.md)

Esta guía describe las herramientas existentes. No requiere modificar un nodo
Umbrel ni publicar imágenes. Ejecuta pruebas en un entorno separado y no uses
datos de producción, identidades reales ni claves Tor como fixtures.

## Entorno

- Python: revisa el Dockerfile y los requisitos de la app correspondiente.
- Usa un entorno virtual por app para no mezclar dependencias.
- Los comandos siguientes parten de la carpeta de la app indicada.
- Docker Buildx es necesario para las construcciones descritas en sus README.

## Frigate

La interfaz y el arranque están integrados en Compose. Sus pruebas offline usan
solo la biblioteca estándar Python y backends/binario simulados:

```sh
python3 -m unittest discover -s albercoin-store-frigate/tests -v
```

El despliegue real depende de variables de Umbrel, Bitcoin y Electrs. No ejecutes
el Compose sin preparar un entorno de prueba. Consulta su
[guía](../albercoin-store-frigate/README.md).

## Onion Rotator

Desde `albercoin-store-onion-rotator/`:

```sh
./scripts/test.sh
```

El script instala requisitos y pytest con `pip` antes de ejecutar las pruebas;
activa primero un entorno virtual para no modificar el Python del sistema.

El README también documenta `./scripts/dev.sh` y el modo
`ONION_ROTATOR_DRY_RUN=true`. Revisa el script antes de ejecutarlo. No conectes
el entorno de desarrollo al socket Docker ni a las claves Tor de producción:
la rotación real elimina claves y reinicia servicios.

## Machine Doctor

No hay una suite de pruebas automatizadas incluida. Su README describe una
comprobación manual con un test CPU corto. El diagnóstico real utiliza
`stress-ng` y puede afectar al rendimiento del equipo; no lo ejecutes como
parte de una comprobación documental o de estructura.

## SimpleX Chat CLI

La construcción local sin publicación se describe en la
[guía de la app](../albercoin-store-simplex-chat-cli/docs/development.md).

Desde `albercoin-store-simplex-chat-cli/`, con las dependencias de
`requirements.txt` instaladas en el entorno virtual:

```sh
python3 -m unittest discover -s tests -v
```

El README detalla el alcance de las pruebas y las verificaciones documentadas
de la versión. Las pruebas del conector no equivalen a validar Hermes completo.

## SimpleX Server (vista previa)

El [README de desarrollo](../albercoin-store-simplex-server/README.md) describe la
construcción Docker, pruebas web y el smoke test con procesos reales. Son pruebas
aisladas: no equivalen a validar clientes externos ni instalación en Umbrel.

## Límites actuales

No existe un workflow común de validación de toda la tienda. Los workflows
de construcción no sustituyen pruebas funcionales en Umbrel. Esta documentación
no afirma que se hayan vuelto a ejecutar pruebas o despliegues.
