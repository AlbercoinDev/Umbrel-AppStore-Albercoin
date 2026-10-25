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

## Publicación y verificación

- Imagen: `ghcr.io/albercoindev/simplex-server:0.1.0-tor-check-1`.
- Digest del índice AMD64/ARM64: `sha256:32571f01b8fa52cbfcaf7824287609e7467d2a4abe9031604c97b565d9944836`.
- Código de la imagen: `fa60ad92122c86f8067ff5ed649ce3b0af0ef2db`.
- [Workflow nativo de publicación](https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin/actions/runs/40198924013).

En ambas arquitecturas pasaron 34 pruebas unitarias, el smoke test SMP/Tor y la
instalación inicial **Solo Tor** mediante el panel, con el entrypoint real, UID
1000 y capacidades reducidas. Desde otro Tor se completó TLS y la prueba oficial
SMP. Contra la misma identidad se probó una contraseña incorrecta: se detectó
`AUTH` en `createqueue` sin modificar la configuración del relay.

También se comprobó en AMD64 la actualización de la imagen inicial a la imagen
publicada conservando certificados y claves SMP, huella, dirección/clave onion,
contraseña SMP, hash administrativo y clave de sesión. El manifiesto del registro
se consultó anónimamente y se verificó su SHA-256. La imagen publicada se descargó
con configuración Docker de credenciales vacía y se comprobó su arranque. Las
pruebas utilizan datos desechables, no los del nodo del usuario.

Durante el afinado hubo pruebas online fallidas con servicios onion recién
creados: se añadieron reintentos de transporte acotados y más diagnóstico. La
prueba de CI se consolidó en el contenedor real (no en una sucesión de fixtures
root con servicios nuevos), manteniendo positivos y negativos contra una misma
identidad. No se ocultó la necesidad de comprobar desde un Tor independiente ni
se relajó la verificación TLS. Sigue pendiente la prueba en tu Umbrel y móvil.
