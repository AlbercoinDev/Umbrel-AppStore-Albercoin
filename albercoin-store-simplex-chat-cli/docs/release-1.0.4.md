# SimpleX Chat CLI 1.0.4 · actualización de la tienda

El gestor pasa a 1.0.4; el binario SimpleX permanece en v7.0.3. Esta revisión
no cambia los permisos del conector ni la configuración de Hermes.

## Cambios

- Menús para vaciar historial local, eliminar contactos, salir de grupos y
  borrar mensajes localmente o solicitar borrado para todos.
- Borradores por conversación en memoria, protección contra respuestas atrasadas
  y contra pérdida de texto durante un envío.
- Historial paginado, búsqueda de mensajes antiguos, filtro por nombre y no leídos,
  fijados, vista previa opcional desactivada inicialmente y remitentes en grupos.
- Copiar, responder con cita, editar mensajes propios compatibles y detalles de entrega.
- Canal SSE autenticado de avisos sin contenido; resincronización con SimpleX,
  respaldo mediante consultas periódicas y notificaciones privadas opcionales.
- Espacio usado y libre, eliminación individual de copias, programación opcional
  y cifrado opcional con claves públicas age X25519.
- Retención **obligatoria de 10 días**, también para copias existentes y cifradas.
  Se usa la fecha UTC del nombre generado por la aplicación, no la fecha de modificación.
  Comprobación al arrancar, cada minuto y al listar o descargar. No se afectan
  copias descargadas ni archivos ajenos al patrón de la app. Con la app detenida
  no hay limpieza; se ejecuta al siguiente arranque.

## Advertencias de actualización

La limpieza puede eliminar inmediatamente copias de más de diez días del volumen.
Descarga antes las que quieras conservar. Borrar un mensaje o vaciar un chat no
borra copias anteriores ni garantiza borrar datos en dispositivos ajenos.

Los borradores se pierden al recargar o cerrar sesión: no se guardan en localStorage.
Las notificaciones solo avisan de actividad, no contienen mensajes y requieren
permiso del navegador y contexto seguro. No hay notificaciones push con el panel cerrado.

## Cifrado y restauración

Genera la clave **fuera del nodo** con `age-keygen -o simplex-identity.txt`.
Obtén el destinatario con `age-keygen -y simplex-identity.txt` y pega únicamente
la clave pública `age1…` en el panel. Conserva la clave privada por separado.
No perderla: el gestor no puede recuperar una copia sin ella.
Las copias antiguas no se recifran automáticamente.

Para restaurar una copia cifrada, instala `age` en el equipo que restaura y usa
el procedimiento offline habitual añadiendo:

```sh
sudo python3 ./albercoin-store-simplex-chat-cli/scripts/restore.py \
  --data /home/umbrel/umbrel/app-data/albercoin-store-simplex-chat-cli/data \
  --archive /ruta/copia.tar.gz.age \
  --identity /ruta/segura/simplex-identity.txt \
  --confirm APP-STOPPED-RESTORE
```

El descifrado temporal necesita espacio adicional y se elimina al terminar.
La copia sigue incluyendo credenciales y ajustes del gestor.

## Publicación

Publicada el 25 de octubre de 2026 para AMD64 y ARM64 mediante
[GitHub Actions](https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin/actions/runs/40179963294),
con las pruebas Python y DOM correctas. La imagen procede del commit `2078481`.
El manifiesto Umbrel anuncia 1.0.4 y el Compose fija la imagen pública por digest:

```text
ghcr.io/albercoindev/simplex-chat-cli:1.0.4@sha256:3a367038894d8845cdecd885b0a244c4d36618d975d70cec7338383a19cd1eda
```

Se han comprobado el índice multi-arquitectura, su descarga anónima y la versión
del gestor y presencia de age en la imagen AMD64 publicada. No se ha modificado
la instalación de ningún nodo Umbrel.

Para probar las fuentes localmente sin publicar:

```sh
docker build --build-arg TARGETARCH=amd64 -t simplex-chat-cli:1.0.4-test albercoin-store-simplex-chat-cli
docker run --rm --entrypoint /opt/venv/bin/python \
  -v "$PWD/albercoin-store-simplex-chat-cli:/work:ro" -w /work \
  simplex-chat-cli:1.0.4-test -m unittest discover -s tests -v
```

`tests/integration.py` prueba identidades desechables en el contenedor, necesita
acceso a SMP público y no debe apuntarse a datos o puertos de producción.
`tests/ui.cjs` utiliza jsdom (26) para comprobar respuestas fuera de orden,
borradores, envío lento, cancelación de borrado y limpieza al cerrar sesión.

Para futuras entregas: reconstruir las fuentes finales para AMD64 y ARM64,
ejecutar las pruebas, publicar por el workflow manual y actualizar conjuntamente
la imagen, el digest, el manifiesto Umbrel y las notas de versión.

## Verificación realizada en esta revisión

- Imagen local AMD64 construida con el binario oficial 7.0.3 verificado por SHA256.
- 18 pruebas Python correctas: acciones, validación, retención, cifrado fallido,
  espacio insuficiente, programación, SSE autenticado y diagnóstico privado.
- Pruebas de interfaz DOM con respuestas fuera de orden y envío lento.
- Integración real dentro de contenedores desechables: historial de más de cien
  mensajes, búsqueda, edición, citas, borrado local, vaciado, salida de grupo,
  dos identidades con texto bidireccional, borrado de contacto y lectura por IDs.
- Copia age sin archivo descargable en claro y restauración offline verificadas.
- Revisión visual en navegador, escritorio y marco móvil de 390 px, sin desbordamiento.

La imagen ARM64 se ha construido y publicado, pero no se ha probado funcionalmente
en hardware ARM64. No se ha actualizado ningún nodo Umbrel ni probado Hermes
completo. El conector mantiene sus permisos anteriores.
