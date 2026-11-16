# Afinado de 0.1.0 · compilación tor-check-3

La versión del manifiesto sigue siendo **0.1.0**. Esta compilación tiene su
propia etiqueta y digest; las instalaciones existentes siguen fijadas por el
digest anterior hasta que se actualicen.

## Cambios

- El CLI de diagnóstico pasa de la imagen `1.0.6` (SimpleX 7.1.2) a la `1.0.8`
  (SimpleX 7.1.4).
- SMP upstream permanece en `v7.1.0`, la última etiqueta publicada.
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

1. El encabezado del panel debe mostrar **compilación tor-check-3**.
2. La dirección onion, la huella TLS y la contraseña SMP deben seguir siendo las
   mismas.
3. Pulsa **Comprobar servidor por Tor** y revisa las cinco fases. La prueba
   puede tardar varios minutos y no demuestra disponibilidad indefinida.

## Publicación

- Imagen: `ghcr.io/albercoindev/simplex-server:0.1.0-tor-check-3`.
- Digest del índice multiarch: `sha256:40e232aaffddf4ea29c40a92813fdbf48ccc781efb01f51d9666f80f75fc6fae`.
- Pruebas del workflow: unitarias, smoke SMP/Tor, instalación inicial y
  actualización conservando datos.
