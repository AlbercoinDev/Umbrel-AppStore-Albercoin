# Publicación y versiones

[Volver al catálogo](../README.md)

Esta guía describe el proceso actual; no publica nada por sí misma.
GitHub es la referencia del repositorio. Los cambios locales no llegan a la
tienda hasta que se integran en la rama que consume Umbrel.

## Fuentes de referencia

- Versión de la app: `umbrel-app.yml`.
- Imagen y, cuando está fijado, digest: `docker-compose.yml`.
- Binario upstream y verificaciones: Dockerfile o arranque de Frigate.
- Automatización: `.github/workflows/`.

## Procesos existentes

### Frigate

No tiene un workflow de imagen propia. Su Compose descarga una versión fija
del binario y verifica hashes para AMD64 y ARM64. La versión del paquete
Umbrel puede incluir una revisión adicional a la versión upstream.
Un cambio de versión requiere revisar conjuntamente manifiesto, versión del
binario y hashes; el README de la app explica las implicaciones de los datos.

### Onion Rotator

El Compose ejecuta las fuentes distribuidas con la app usando una imagen base
de Python. No utiliza una imagen propia construida por GitHub Actions.
Tiene un script `scripts/publish.sh`: debe revisarse antes de utilizarlo,
porque no es una prueba local y puede modificar el repositorio remoto.
El script ejecuta `git push origin master` y reconoce las URLs SSH y HTTPS
del repositorio. Publica los commits de la rama `master` del repositorio
completo, no únicamente los cambios de Onion Rotator. No lo ejecutes si hay
otros commits locales que todavía no deben publicarse.

### Machine Doctor

El workflow `machine-doctor-image.yml` se activa por cambios en la carpeta de
la app o en el propio workflow en `master`, y también manualmente.
Construye y publica para AMD64 y ARM64. Las etiquetas están escritas en el
workflow; deben revisarse junto al manifiesto, Dockerfile y Compose.
Actualmente publica también `latest` y puede reemplazar etiquetas existentes.

### SimpleX Chat CLI

El workflow `simplex-chat-cli-image.yml` es manual. Con `publish=false`
construye sin publicar; con `publish=true` publica para AMD64 y ARM64.
El Compose fija el digest: publicar una etiqueta no cambia automáticamente
el contenido que descarga Umbrel. El workflow permite reemplazar la etiqueta.
Consulta el README de la app para construcción local y compatibilidad de copias.

## Comprobaciones antes de una futura publicación

SimpleX Server dispone de un workflow manual independiente
`simplex-server-image.yml`, con publicación desactivada por defecto. Su Compose
fija el digest multiarch publicado para la vista previa 0.1.0. El workflow prueba
las imágenes en runners nativos AMD64/ARM64 antes de componer el índice. Antes
de declararla estable, completar las verificaciones de su README en un nodo real.
No se ha modificado la imagen de SimpleX Chat CLI.

La compilación `tor-check-1` afina SimpleX Server sin incrementar la versión
0.1.0. Conserva su propia etiqueta/digest y añade pruebas con Tor independiente.
Como la detección de actualizaciones de Umbrel compara versiones, una instalación
existente puede necesitar actualizarse explícitamente mediante su CLI. Consulta
la [guía de actualización sin borrar datos](../albercoin-store-simplex-server/docs/tor-check-1.md).

1. Confirmar el alcance y revisar el diff para no incluir secretos ni datos.
2. Revisar versiones, notas de versión, referencias de imágenes y documentación.
3. Ejecutar las pruebas disponibles y registrar qué no se ha validado.
4. Si aplica, construir/publicar la imagen y comprobar ambas arquitecturas.
5. Verificar que la referencia del Compose corresponde a la imagen prevista.
6. Probar instalación o actualización en un nodo de prueba con copia previa.

No realizar downgrades de datos ni prometer reversión sin verificar la
compatibilidad de la app y del binario upstream.

## Lista de comprobación documental

Antes de integrar una futura actualización:

- [ ] El catálogo raíz enlaza las apps y guías vigentes.
- [ ] El README de la app describe solo funciones implementadas y separa los planes.
- [ ] Puertos y dependencias coinciden con manifiesto, Compose y exports.
- [ ] Imágenes, versiones y hashes citados coinciden con los archivos de ejecución.
- [ ] Las rutas de datos distinguen host, contenedor y repositorio.
- [ ] Los permisos y riesgos descritos corresponden al despliegue actual.
- [ ] Las instrucciones de copia y restauración indican límites y datos sensibles.
- [ ] Los comandos indican directorio de trabajo, requisitos y efectos secundarios.
- [ ] La publicación descrita coincide con la rama y los disparadores del workflow.
- [ ] Se distingue entre pruebas disponibles, verificaciones documentadas y pruebas
  ejecutadas de nuevo; no se atribuye a la documentación una validación funcional.
- [ ] Los enlaces locales funcionan y el diff no incluye datos privados.

Para un cambio exclusivamente documental, revisar además que solo se incluyen
archivos Markdown. Crear un commit local no publica en GitHub: el push requiere
autorización independiente.
