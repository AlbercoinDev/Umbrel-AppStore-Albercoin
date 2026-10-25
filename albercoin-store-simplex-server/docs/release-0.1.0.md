# Publicación preliminar 0.1.0

## Identificación

- Paquete Umbrel: `albercoin-store-simplex-server`.
- Imagen: `ghcr.io/albercoindev/simplex-server:0.1.0`.
- Índice fijado: `sha256:4a9c0324ea93a3ccbf5a5efd0d70d2936c073aa57c90a498b71ce1052e65cfb6`.
- Plataformas: Linux AMD64 y ARM64.
- Código de la imagen: commit `01839ce56645df26f9a12774429b7923de35117e`.
- [Workflow](https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin/actions/runs/40184873857).

Se construyeron y probaron las imágenes en runners nativos de ambas
arquitecturas. Se publicaron esas imágenes y se compuso el índice multiarch,
sin reconstrucción posterior de su código. La primera subida AMD64 fue rechazada
por el registro; la repetición de ese job y el ensamblado posterior terminaron
correctamente. El job ARM64 pasó en el primer intento.

## Comprobaciones

- 14 pruebas unitarias en cada arquitectura.
- Arranque SMP/Tor, TLS local, cambio de modalidad, identidad estable y negativa
  a regenerar identidad incompleta en ambas arquitecturas.
- Entrypoint real con capacidades reducidas, gestor UID 1000, acceso web,
  configuración inicial y QR en ambas arquitecturas.
- Lectura anónima del manifiesto del registro y comprobación de su SHA-256.
- Descarga Docker AMD64 con configuración de credenciales vacía.
- Intercambio entre dos clientes reales, por TCP local y por onion/Tor,
  realizado previamente en AMD64; contraseña errónea rechazada.

## Pendiente de prueba en tu nodo

La publicación permite instalarla; **no acredita una prueba en Umbrel real**.
Comprueba instalación/proxy, acceso onion desde otro dispositivo, reinicios,
mensajes pendientes y copia/restauración siguiendo la
[guía de pruebas](testing-umbrel.md). XFTP no está incluido.

No se han cambiado ni actualizado las imágenes de otras apps de la tienda.
