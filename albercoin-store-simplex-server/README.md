# SimpleX Server · vista previa 0.1.0

**Versión preliminar para instalar y probar desde la tienda Albercoin, no una
versión estable validada en un nodo Umbrel real.** SMP upstream v7.0.1;
panel propio en español. XFTP queda para otra entrega, sin servicio ni controles
ficticios en esta versión.

**Afinado de la misma 0.1.0: compilación `tor-check-1`.** Añade comprobación
onion con Tor independiente, TLS verificado y prueba oficial SMP desde el panel.
No regenera identidades. Consulta [cambios y actualización sin borrar datos](docs/tor-check-1.md).

## Instalar y probar en Umbrel

Actualiza la tienda Albercoin, busca **SimpleX Server** e instala la versión
`0.1.0`. Abre el panel con usuario `admin` y la contraseña que muestra Umbrel.
Empieza con **Solo Tor** y perfiles SimpleX desechables. No necesitas abrir
puertos del router. Consulta la [guía de prueba real](docs/testing-umbrel.md).

La imagen de esta compilación se publica en `ghcr.io/albercoindev/simplex-server:0.1.0-tor-check-1` para
AMD64 y ARM64; el Compose fija el digest del índice multiarch.
Detalles y límites de la verificación: [publicación 0.1.0](docs/release-0.1.0.md).

## Funciones

- Acceso web mediante el proxy Umbrel, puerto de ficha `2193`.
- Usuario `admin` y contraseña `APP_PASSWORD` de Umbrel, almacenada como hash.
- Asistente SMP con Tor predeterminado, clearnet o ambos.
- Restricción de creación de colas con contraseña aleatoria, distinta del panel.
- Dirección seleccionable para copiar y QR SVG generado localmente.
- Conservación de identidad SMP y onion al cambiar de modalidad.
- Aplicación de ajustes con parada SIGINT de SMP; sin socket Docker.
- Botón de diagnóstico asíncrono por Tor independiente, con resultados seguros por fase.
- Estado Tor consultado en vivo mediante control Unix autenticado con cookie;
  actualización del panel cada cuatro segundos y recuperación de procesos caídos.

El QR representa la URI SMP; su importación debe comprobarse en cada cliente.
El estado Tor distingue procesos, bootstrap y circuitos. **No equivale a una
prueba de disponibilidad onion**: utiliza el botón de comprobación, que verifica
conexión onion, identidad TLS y creación/asegurado/borrado de una cola temporal
mediante el CLI oficial del asset de la release v7.0.3 (el binario declara
internamente `v7.0.0.12`). Esa instancia Tor tiene su propio directorio,
estado, SOCKS local y circuitos; no comparte los del Tor que publica el servicio.
La misma máquina/red sigue siendo el origen físico: no sustituye una prueba
desde otro dispositivo ni valida su configuración Android.

La prueba tarda hasta unos siete minutos, no reinicia el relay ni modifica
certificados. Si se interrumpe tras crear una cola, puede quedar una cola de
prueba sin eliminar. Solo se permite una prueba concurrente y 60 segundos entre
inicios. Los resultados no incluyen URI, contraseñas ni salida cruda del CLI.
La conexión onion se reintenta durante un máximo aproximado de 90 segundos y
se informa de los intentos; la creación de colas no se reintenta automáticamente.
El control Tor no se publica como puerto TCP: utiliza socket Unix y cookie
privados. El healthcheck detecta procesos SMP/Tor detenidos, pero no ejecuta
la prueba end-to-end. El supervisor reintenta recuperar procesos con backoff,
sin cambiar identidades ni reiniciar mientras hay una comprobación en curso.

## Red

Tor genera un servicio onion normal, no single-hop, que redirige su `5223` al
transporte SMP interno `5224`. El proxy SOCKS `9050` solo escucha en loopback.
En modo solo Tor, el proxy SMP saliente utiliza SOCKS para todos los destinos y
prefiere direcciones onion. En modo mixto usa SOCKS para destinos onion, y puede
conectar directamente a destinos públicos. Esto no configura Tor para otras
apps ni para todo el nodo.
Los clientes necesitan su propia conectividad Tor. SimpleX Chat CLI de esta tienda
no la configura automáticamente; no se modifica esa app desde este paquete.

Clearnet: dominio o IPv4 accesible y **TCP 5223** permitido/redirigido hasta Umbrel.
DNS dinámico es posible; CGNAT requiere otra solución de entrada. No hace falta
Let's Encrypt ni abrir 80/443 para TLS nativo SMP. IPv6 directo y puertos externos
personalizados no se incluyen. La conexión mantiene TLS; la IP del servidor no se
oculta. La app nunca configura el router.

Compose **reserva/publica TCP 5223 incluso en modo Tor**, porque el mapeo Docker
no puede cambiarse desde el panel sin controlar Docker. En modo Tor no existe
listener de la puerta pública: solo se activa al elegir clearnet. Cambiar el modo
cierra sus conexiones existentes. Si el puerto está ocupado, la instalación debe
resolver ese conflicto aunque se vaya a usar solo Tor.

El servidor pertenece a una red Docker propia. En versiones con sidecar,
`app_proxy` conecta esa red con la red predeterminada Umbrel; las versiones con
gateway integrado pueden resolver el contenedor desde el anfitrión. SMP upstream
escucha `5224` en todas las interfaces del contenedor, **no se publica al host**.
No conectes otras apps a esa red privada. Validar la combinación de redes y
resolución de nombres con el proxy inyectado por la versión de Umbrel utilizada.

El panel HTTP no debe exponerse directamente a Internet. El proxy de Umbrel y la
autenticación del gestor son capas distintas. Para acceso remoto usa un túnel o
proxy HTTPS de confianza. Cookie HttpOnly/SameSite Strict, CSRF y límite global
de 10 intentos de acceso por minuto; no se confía en cabeceras de IP del cliente.
No se establece Secure por defecto para conservar el acceso HTTP local de Umbrel.

## Datos y recuperación

```text
${APP_DATA_DIR}/data/
├── manager/         # hash admin, sesión, contraseña SMP, ajustes y Tor
│   └── tor/smp/     # identidad onion persistente
└── smp/
    ├── config/      # INI, certificados, fingerprint y ca.key
    └── state/       # colas y mensajes pendientes
```

`manager/probe-tor/` conserva únicamente el estado/caché de la instancia Tor de
diagnóstico. No publica otro onion. Sus procesos solo están activos durante una
prueba. La base efímera del CLI se elimina al terminar. Se incluye solo el binario
oficial del cliente y su licencia, no otra app cliente ni tus perfiles.

El contenedor prepara permisos y baja a UID/GID 1000. No requiere `privileged`,
red host ni capacidades de administración de red. Credenciales, certificados y
datos no están cifrados adicionalmente en disco. Salida SMP descartada para no
registrar la URI con contraseña. Tor guarda diagnóstico local en
`data/manager/tor/notice.log`; puede contener información de red. Se rota a 1 MiB
con una copia anterior.

Para una copia consistente, **detén la app desde Umbrel**, copia el directorio
`data` completo y almacénalo cifrado fuera del nodo. Para restaurar, con la app
detenida, conserva primero los datos actuales y repón esa copia completa. No
ejecutes simultáneamente dos instancias de la misma identidad. La copia restaura
también las credenciales; la contraseña mostrada por otra instalación de Umbrel
podría no coincidir. No hay descarga/restauración de copias desde el panel todavía.

Custodia `smp/config/ca.key` offline: permite renovar las credenciales sin cambiar
la identidad. La app no elimina esa clave automáticamente. Desinstalar puede
borrar datos. Nunca borres certificados para resolver un error de arranque.
Si falta el INI y hay datos previos, se bloquea la regeneración de identidad.
Para recuperar el acceso administrativo, con la app detenida elimina únicamente
`manager/admin-hash`: al iniciar se genera desde el APP_PASSWORD actual de Umbrel.

## Construcción y pruebas

Desde la raíz del repositorio:

```sh
docker build -t simplex-server:dev albercoin-store-simplex-server
docker run --rm --entrypoint python3 -v "$PWD/albercoin-store-simplex-server:/workspace:ro" simplex-server:dev -m unittest discover -s /workspace/tests -v
docker run --rm --entrypoint python3 -v "$PWD/albercoin-store-simplex-server:/workspace:ro" simplex-server:dev /workspace/tests/smoke_runtime.py
python3 albercoin-store-simplex-server/tests/smoke_packaged.py
# Pruebas online: salida a Tor necesaria, datos efímeros, sin puertos publicados.
docker run --rm --entrypoint python3 -v "$PWD/albercoin-store-simplex-server:/workspace:ro" simplex-server:dev /workspace/tests/smoke_diagnostics.py
PACKAGED_TOR_PROBE=1 python3 albercoin-store-simplex-server/tests/smoke_packaged.py
```

Para arrancar un entorno aislado con datos de prueba:

```sh
docker run --name simplex-server-dev -e APP_PASSWORD=CAMBIA-ESTA-CONTRASENA -p 127.0.0.1:2193:2193 -p 5223:5223 simplex-server:dev
```

Esta instancia no conserva datos al eliminar el contenedor: añade volúmenes de
prueba si necesitas ensayar reinicios/recreación. No uses identidades productivas.
La prueba opcional `tests/probe_client.py` requiere una imagen existente de
nuestro cliente que contenga el binario y `/opt/venv/bin/python` con aiohttp:

```sh
CLIENT_IMAGE=simplex-chat-cli:1.0.4-test python3 albercoin-store-simplex-server/tests/probe_client.py
# Prueba opcional mediante circuitos Tor reales; requiere salida a la red Tor.
CLIENT_IMAGE=simplex-chat-cli:1.0.4-test PROBE_TOR=1 python3 albercoin-store-simplex-server/tests/probe_client.py
```

Crea y retira contenedores temporales sin publicar puertos al host. Comprueba
la contraseña SMP correcta/incorrecta y el intercambio de texto real entre dos
identidades efímeras. No modifica la app cliente ni utiliza sus datos existentes.
Usa la imagen indicada en CLIENT_IMAGE: la referencia del ejemplo es local,
no una imagen publicada o una promesa de la versión del binario que contiene.
La variante Tor utiliza un proxy SOCKS de prueba en el mismo espacio de red de
los contenedores. No prueba un cliente instalado en otro dispositivo ni la
integración Tor del sistema Umbrel.

El workflow manual construye y prueba AMD64/ARM64 en runners nativos y solo
publica si se solicita. Publica las imágenes nativas probadas y después compone
el índice multiarch. No publica `latest`. Las futuras versiones deben fijar el
digest resultante en Compose antes de actualizar la tienda.

### Verificado durante este desarrollo (AMD64)

- Construcción local de la imagen y pruebas unitarias de acceso, CSRF, validación y secretos.
- Entrypoint real con capacidades reducidas: gestor UID 1000, acceso web, CSS,
  configuración inicial, arranque SMP y QR servidos correctamente.
- Procesos reales SMP/Tor, handshake TLS local, cambio entre modos y conservación de identidades.
- Rechazo de regeneración cuando falta el INI y existen datos.
- Dos CLI reales de la imagen local `simplex-chat-cli:1.0.4-test` (binario que
  declara `v7.0.0.12`): texto recibido en ambas direcciones mediante SMP local.
- Prueba equivalente mediante la dirección onion y circuitos Tor reales,
  usando el SOCKS compartido del entorno aislado: texto en ambos sentidos.
- Contraseña válida aceptada e incorrecta rechazada en `CreateQueue` con `AUTH`.

Además, el [workflow de publicación](https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin/actions/runs/40184873857)
ejecuta las 14 pruebas unitarias, smoke test de SMP/Tor y prueba del entrypoint
con capacidades reducidas en runners nativos AMD64 y ARM64. El intercambio
entre clientes descrito arriba se comprobó en AMD64, no en ARM64.

Estas pruebas no validan aún un cliente onion en otro dispositivo, router,
CGNAT ni instalación en Umbrel. La prueba real en el nodo corresponde a esta
versión preliminar; no se presenta como validación de producción.

## Antes de publicar como estable

- [ ] Instalación real Umbrel: proxy, redes privadas, permisos y credenciales.
- [x] Dos clientes reales aislados usando colas del relay: texto en ambas direcciones.
- [ ] Tor: bootstrap actual, prueba onion desde otra máquina y reinicio con identidad estable.
- [ ] Clearnet y modo mixto probados desde fuera de la LAN.
- [x] Contraseña incorrecta rechazada al crear colas en el entorno aislado.
- [ ] Recuperación de mensajes pendientes tras SIGINT y copia/restauración completas.
- [x] Construcción y ejecución nativas AMD64 y ARM64 en CI.
- [ ] Supervisión de hijos y límites bajo carga.
- [ ] Capturas reales sin secretos; galería del manifiesto y digest publicado.

SMP + XFTP es el objetivo de mensajes y archivos propios. Alojar SMP no migra
contactos existentes, no controla las colas de otros participantes y no sustituye
STUN/TURN ni las plataformas push. XFTP tendrá identidad, almacenamiento, cuotas
y configuración separados, sin reutilizar la contraseña SMP.

## Fuentes y licencia

Servidor oficial: https://github.com/simplex-chat/simplexmq (AGPL-3.0).
Se distribuye su binario sin modificar; referencia exacta en Dockerfile.
Cliente de diagnóstico del asset oficial de la release v7.0.3 (cadena interna
`v7.0.0.12`): https://github.com/simplex-chat/simplex-chat,
binario sin modificar copiado desde la imagen fijada por digest en Dockerfile;
licencia completa en `/usr/share/doc/simplex-chat/LICENSE` dentro del contenedor.
Gestor propio: AGPL-3.0-or-later, véase LICENSE. El icono geométrico de esta app
es propio y no representa afiliación oficial con SimpleX.
