# Afinado de 0.1.0 · compilación tor-check-2

La versión del manifiesto sigue siendo **0.1.0**. Esta compilación tiene su
propia etiqueta de imagen y digest; no reemplaza la etiqueta `tor-check-1`.

## Cambios

- SMP upstream pasa de `v7.0.1` a `v7.1.0`.
- El CLI de diagnóstico pasa de la imagen `1.0.4` (SimpleX 7.0.3) a la `1.0.6`
  (SimpleX 7.1.2).
- Se mantienen el panel, la prueba onion con Tor independiente, la verificación
  TLS, el estado en vivo y la recuperación de procesos.
- No se regeneran identidades, certificados, contraseña SMP ni colas.

## Actualizar sin borrar datos

Como la versión del manifiesto no cambia, Umbrel puede no mostrar un botón de
actualización. Espera a que sincronice la tienda y, si tu umbrelOS incluye el
CLI, usa:

```sh
sudo umbreld client apps.update.mutate --appId albercoin-store-simplex-server
```

No desinstales la app ni uses una reinstalación que borre el directorio de
datos. Conserva una copia offline antes de actualizar una app preliminar.

## Verificar

1. El encabezado del panel debe mostrar **compilación tor-check-2**.
2. La dirección onion, la huella TLS y la contraseña SMP deben seguir siendo las
   mismas.
3. Pulsa **Comprobar servidor por Tor** y revisa las cinco fases. La prueba
   puede tardar varios minutos y no demuestra disponibilidad indefinida.

## Publicación

- Imagen: `ghcr.io/albercoindev/simplex-server:0.1.0-tor-check-2`.
- El Compose fija el digest del índice multiarch publicado por el workflow.
- Pruebas del workflow: unitarias, smoke SMP/Tor, instalación inicial y
  actualización conservando datos.
