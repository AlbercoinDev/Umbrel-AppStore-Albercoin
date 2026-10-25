# Afinado de 0.1.0 · compilación tor-check-1

La versión del manifiesto sigue siendo **0.1.0**, a petición del usuario. La
compilación tiene su propia etiqueta de imagen, sin reemplazar las etiquetas ni
el digest inicial. No se cambia el SMP upstream v7.0.1 ni se implementa XFTP.

## Comprobar disponibilidad

1. Tras actualizar, el encabezado debe mostrar **compilación tor-check-1**.
2. Mantén tu configuración actual; no regeneres el onion ni los certificados.
3. Pulsa **Comprobar servidor por Tor** y espera al resultado de las cinco fases:
   SMP/TLS local, Tor independiente, conexión onion, TLS remoto y prueba oficial
   de creación/asegurado/eliminación de una cola SMP temporal.
   Puede tardar hasta siete minutos. El acceso onion tiene reintentos limitados,
   visibles en el resultado; no se repite automáticamente la creación de colas.
   Si falla la prueba del CLI se muestra también su fase, cuando está disponible.
4. Si falla, comparte solo el resultado textual de esa sección (o su captura),
   sin incluir la dirección ni el QR de la sección de conexión.

El panel actualiza el estado automáticamente. Un resultado tiene fecha y hora:
no garantiza disponibilidad indefinida ni configura Tor en Android. Si todas las
fases pasan pero el móvil falla, la conectividad del relay está comprobada por
un Tor independiente; el siguiente diagnóstico es la ruta/configuración del móvil.

## Actualizar una instalación existente conservando datos

**Como no cambia la versión, Umbrel puede no mostrar un botón de actualización.**
Reiniciar por sí solo tampoco descarga la revisión: la instalación conserva el
Compose y digest anteriores. No desinstales ni uses una reinstalación que borre datos.

Espera a que Umbrel sincronice la tienda (en versiones actuales, unos cinco
minutos). En versiones que incluyen el CLI `umbreld`, mediante SSH al nodo:

```sh
sudo umbreld client apps.update.mutate --appId albercoin-store-simplex-server
```

Esta operación utiliza el actualizador de apps de Umbrel: recupera la definición
de la tienda, descarga el nuevo digest y reinicia la app sin eliminar su directorio
de datos. Se ha contrastado la ruta en el código oficial actual de Umbrel; **no se
ha ejecutado en tu nodo**. Si el comando no existe o falla, guarda el error y la
versión de umbrelOS; no pruebes comandos de desinstalación como alternativa.
Para rutas de datos no estándar, el CLI puede necesitar la configuración del nodo.

Comprueba el identificador de compilación en el panel después de actualizar y
que la huella y dirección onion sigan siendo las anteriores. Conviene conservar
una copia offline de los datos antes de actualizar una app preliminar.

## Qué se corrige

- El bootstrap ya no se deduce únicamente de una línea antigua de log: se
  consultan el progreso y los circuitos actuales mediante control Tor privado.
- Se distingue Tor conectado de un servicio onion realmente alcanzable.
- El estado Docker detecta procesos SMP/Tor muertos y el supervisor reintenta
  recuperarlos con backoff, conservando sus datos.
- Se añade una prueba desde otro proceso Tor con estado independiente y
  verificación criptográfica de la CA frente a la huella anunciada.
- Los fallos de autorización SMP, TLS y conectividad se separan sin publicar secretos.

No se ha confirmado todavía la causa del error del móvil en el nodo del usuario.
Esta revisión permite identificarla; no promete resolver una configuración Tor
ausente en el cliente.
