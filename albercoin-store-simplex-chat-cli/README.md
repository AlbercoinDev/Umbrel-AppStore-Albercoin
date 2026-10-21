# SimpleX Chat CLI · Albercoin · v1.0.3

Aplicación independiente para la tienda Umbrel Albercoin. Cliente oficial SimpleX Chat CLI **v7.0.3** y gestor web propio. Hermes Agent y otros clientes pueden conectarse opcionalmente mediante la API WebSocket.

**Estado de esta revisión:** versión 1.0.3 publicada para AMD64 y ARM64, fijada por digest en la tienda Umbrel. Se ha verificado el intercambio de texto en ambos sentidos entre dos identidades reales SimpleX 7.0.3 en contenedores de prueba, la aceptación de solicitudes, el conector autenticado y la exclusión de mensajes/credenciales del informe. No se ha ejecutado el gateway de Hermes ni desplegado esta revisión en tu nodo.

## Instalación

Esta carpeta debe situarse en la raíz de `AlbercoinDev/Umbrel-AppStore-Albercoin`, junto a las demás apps. También debe añadirse el nuevo workflow `simplex-chat-cli-image.yml` a `.github/workflows/` sin reemplazar otros workflows.

La imagen `ghcr.io/albercoindev/simplex-chat-cli:1.0.3` está publicada y fijada por su digest en `docker-compose.yml`; Umbrel la descarga sin credenciales. Para publicarla, utiliza el workflow manual **Build SimpleX Chat CLI** con `publish=true`. No se dispara por push. Las plataformas de construcción son `linux/amd64` y `linux/arm64`. El binario original se descarga de una versión fija y se comprueba contra su SHA256 oficial.

La imagen está fijada por el digest `sha256:52a4a5d5358829027f926adbfbda72e1962b22ea54fc147a194daa523198ba8e`. Si se republica `1.0.3`, actualiza el digest en este `docker-compose.yml`; el workflow no impide reemplazar la etiqueta.

Para construcción local alternativa, desde la raíz del repositorio y con Docker Buildx:

```sh
docker buildx build --platform linux/amd64 --load -t ghcr.io/albercoindev/simplex-chat-cli:1.0.3 ./albercoin-store-simplex-chat-cli
```

Este comando construye para AMD64 sin publicar. Para construir localmente ARM64 cambia `linux/amd64` por `linux/arm64`. El despliegue de Umbrel descarga una imagen: no construye el Dockerfile de esta carpeta.

## Acceso y funciones

- Interfaz mediante el proxy de Umbrel, puerto público de la app `2191`.
- Usuario `admin`, contraseña aleatoria por instalación (`APP_PASSWORD`), mostrada por Umbrel gracias a `deterministicPassword: true` y conservada como hash scrypt en el primer arranque.
- La pantalla de acceso permite cambiar la contraseña indicando la actual. Tras cambiarla, la ficha de Umbrel sigue mostrando la original.
- Recuperación: si olvidas la contraseña, detén la app, elimina `data/manager/admin-auth.json` y vuelve a iniciarla. Se generará de nuevo desde el `APP_PASSWORD` que muestra Umbrel.
- Si falta esa variable, contraseña aleatoria en `data/manager/initial-password.txt`, permisos `0600`; también puede establecerse una propia desde la pantalla de acceso.
- Sesiones de 12 horas, cookies HttpOnly y SameSite, controles CSRF y límite de intentos de acceso.
- Perfil editable; dirección persistente con QR generado localmente; conexiones y solicitudes con aceptación manual.
- Chats de texto directos y de grupo. Lista de hasta 200 conversaciones recientes y 100 entradas por historial; la consola permite otras consultas.
- El estado técnico de entrega del cliente aparece junto al mensaje. Aceptación de una orden no significa recepción en el otro dispositivo.
- Copias consistentes mediante parada del cliente; descarga y restauración offline.
- Consola de órdenes SimpleX, con permisos administrativos completos, accesible solo tras autenticarse.
- Servidores y proxy modificables desde la interfaz. Al guardar se reinicia el cliente, sin reiniciar otras apps. El proxy SOCKS debe ser una IPv4 con puerto entre 1 y 65535; se valida antes de aplicarlo.

No hay adjuntos en la interfaz, llamadas, integración automática de Tor ni promesas de tráfico exclusivamente onion. La configuración XFTP queda disponible para el cliente y usos avanzados. No se aceptan archivos automáticamente.

## Procesos, red y permisos

`app_proxy` dirige las peticiones a `albercoin-store-simplex-chat-cli_server_1:2191`.
El servicio `server` contiene el gestor y el proceso SimpleX. El cliente escucha únicamente en `127.0.0.1:5225` de ese contenedor. Un único cliente interno distribuye las respuestas por correlación y los eventos a cada integración.

El conector utiliza `2192/ws`, HTTP Basic para WebSocket, desactivado inicialmente. No se publica el puerto `2192` en el host. En Umbrel, Hermes accede por el nombre Docker `albercoin-store-simplex-chat-cli_server_1` dentro de la red que Umbrel proporciona a las apps. La URL no es una dirección para la LAN. Si una instalación cambia los nombres o segmenta esa red, se debe adaptar el host de la URL a su DNS o red privada; no se añaden permisos globales ni `network_mode: host` para resolverlo.

Las credenciales del conector conceden acceso completo a la API SimpleX. La lista de contactos de Hermes limita quién puede interactuar con Hermes; no limita los permisos técnicos del conector ni crea otra identidad SimpleX. Hermes verá los eventos de la misma identidad que administra la web.

Para clientes fuera del nodo es necesario un proxy TLS autenticado o un túnel seguro. No publiques el WebSocket nativo ni envíes su tráfico o sus credenciales por Internet sin protección.
Revocar las credenciales cierra los clientes activos. El contador de conexiones no identifica inequívocamente al cliente como Hermes; cualquier cliente autorizado habla el mismo protocolo.
Las colas son limitadas; un cliente demasiado lento se desconecta para que no reciba una secuencia de eventos incompleta sin advertencia. Los eventos en tránsito no constituyen una cola durable; el historial persistido por SimpleX es la referencia al reconectar. No se reenvían automáticamente mensajes cuyo resultado sea incierto.

El contenedor inicia brevemente como root para preparar la propiedad del volumen y baja a UID/GID `1000:1000`. Sus capacidades se limitan a esa preparación y al cambio de usuario. No utiliza `privileged`, el socket Docker, red host ni volúmenes de otras apps. Su salida normal CLI no se registra porque puede contener mensajes o enlaces. Los errores de stderr se clasifican mediante patrones conocidos (permisos, disco, argumentos, base de datos o puerto); el texto original se omite.

## Persistencia

```text
${APP_DATA_DIR}/data/
├── simplex/              # Bases de datos y carpeta files del cliente
├── manager/              # Ajustes y hash de contraseña; token del conector en settings.json
└── backups/              # Archivos tar.gz, sin borrado automático
```

Recrear el contenedor no elimina estos datos. Desinstalar la app en Umbrel puede eliminarlos. Las bases de datos y las copias no reciben cifrado adicional por parte de este gestor: protege el disco y las copias. La confidencialidad del protocolo SimpleX no equivale al cifrado de estos archivos en reposo.

Las copias incluyen identidad, mensajes, archivos y credenciales del gestor. Descárgalas y guárdalas en almacenamiento privado fuera del nodo. Antes de una migración de versión el gestor conserva una copia offline si detecta una base existente con un marcador de versión diferente. No se realiza un downgrade automático de bases de datos.

## Restaurar una copia

La restauración es una operación manual. Utiliza una copia descargada de esta app y conserva el archivo original. Requiere Python 3 en el host y la app completamente detenida desde Umbrel.
Los ejemplos suponen que los datos están bajo `/home/umbrel/umbrel`; ajusta esa raíz al directorio real de tu equipo.

```sh
sudo python3 ./albercoin-store-simplex-chat-cli/scripts/restore.py \
  --data /home/umbrel/umbrel/app-data/albercoin-store-simplex-chat-cli/data \
  --archive /home/umbrel/20261020T120000-123456.tar.gz \
  --confirm APP-STOPPED-RESTORE
```

Sustituye la ruta de archivo de ejemplo por la de tu copia. El asistente no comprueba mediante Docker si la app está detenida: debes detenerla antes.
Valida el contenido de la copia, rechaza enlaces y rutas externas, comprueba espacio y preserva los directorios actuales en `data/before-restore-FECHA/`. Admite copias de este paquete `1.0.0`, `1.0.1`, `1.0.2` y `1.0.3` con SimpleX `v7.0.3`.
Arranca después la app desde Umbrel. Se recuperan también la contraseña y el token anteriores. Si eran distintos, actualiza los clientes y usa la contraseña del momento de la copia.
Para deshacer la restauración, detén la app y recupera los directorios `simplex` y `manager` de `before-restore-FECHA`, conservando primero los restaurados en otra carpeta. No ejecutes dos instancias simultáneas con una misma identidad recuperada de una copia.

## Hermes

Consulta [integrations/hermes/README.md](integrations/hermes/README.md). La referencia inicial es el paquete oficial Umbrel `hermes-agent` con versión declarada `2026.9.24`. Su configuración persistente está en `/opt/data`. No se asume que las futuras versiones mantengan las mismas rutas o dependencias.

## Funcionamiento y diagnóstico

```text
Panel web → gestor → WebSocket local :5225 → SimpleX CLI → SMP → contacto
Hermes → WebSocket autenticado :2192/ws → gestor → misma identidad SimpleX
```

1. En **Perfil y contactos**, crea tu dirección y ábrela desde SimpleX en tu móvil.
2. Acepta aquí la solicitud. SimpleX 7.0.3 puede representarla como un contacto directo con `contactRequestId` y sin `activeConn`; el panel reconoce ese formato y el anterior. El ID de solicitud puede permanecer tras aceptar: por eso también se comprueba la conexión activa.
3. Elige el contacto en **Conversaciones** y prueba texto en los dos sentidos. Las solicitudes pendientes no se ofrecen como conversaciones enviables.
4. Los botones ⓘ explican cada campo y funcionan mediante foco, ratón o pulsación. El esquema desplegable al final de la página explica el recorrido y los datos de Hermes.

**Cliente local conectado** significa que el gestor llega al cliente, no que SMP o el destinatario estén disponibles. El historial distingue pendiente, enviado al servidor, recibido y error. No se repite automáticamente un envío fallido o incierto.

### Compartir un fallo

Abre **Registros y diagnóstico** después de reproducirlo. Filtra por gravedad u operación; despliega la incidencia y pulsa **Copiar incidencia y contexto**, o utiliza **Copiar informe general / Descargar informe**. Si el navegador no permite copiar, se muestra y selecciona el informe para copiarlo manualmente.

El informe contiene estado del proceso y WebSocket, versiones, uso de proxy/servidores personalizados, conexiones al conector, fechas UTC, IDs de seguimiento, duración, códigos estructurados de error, referencias de conversación/mensaje y estados de entrega. Los eventos recibidos y las consultas de historial permiten diferenciar recepción interna de actualización del panel. Los fallos HTTP del panel se anotan cuando el navegador consigue comunicarlos al servidor.

Los errores incluyen una comprobación sugerida; no se atribuye una causa de red sin evidencia. Los informes no contienen el cuerpo de las órdenes, mensajes, nombres, enlaces o credenciales. El texto libre de los errores se omite: se conservan códigos y valores técnicos permitidos. Un error de stderr no clasificado requerirá investigación adicional, no una conclusión automática.

Persistencia: `data/manager/diagnostics.jsonl`, permisos `0600`, rotación de cuatro archivos de hasta aproximadamente 1 MiB cada uno. La API exporta las últimas 500 entradas del archivo actual y el anterior; los más antiguos permanecen en las rotaciones hasta sobrescribirse. Se necesita iniciar sesión. Las copias completas incluyen estos registros.

### Verificación de esta revisión

Pruebas de regresión (dependencias de `requirements.txt` instaladas):

```sh
python3 -m unittest discover -s tests -v
```

Cubren correlación de respuestas fuera de orden, limpieza de timeouts, redacción de errores, extracción de estados sin contenido, rotación/persistencia y enlaces cortos HTTPS de servidores SimpleX. Además se ha comprobado la interfaz en un DOM de prueba y el recorrido real entre dos identidades con SMP. La prueba del conector confirma autenticación, correlación y registros, pero no sustituye una prueba con el gateway y el modelo de Hermes.

## Fuentes y licencia

- SimpleX: https://github.com/simplex-chat/simplex-chat/tree/v7.0.3
- Código fuente correspondiente al binario: https://github.com/simplex-chat/simplex-chat/archive/refs/tags/v7.0.3.tar.gz
- API: https://github.com/simplex-chat/simplex-chat/blob/v7.0.3/bots/api/COMMANDS.md
- Hermes: https://github.com/NousResearch/hermes-agent/tree/v2026.9.24/plugins/platforms/simplex
- Paquete de Umbrel: https://github.com/getumbrel/umbrel-apps/tree/master/hermes-agent

El código del gestor de este paquete se distribuye bajo AGPL-3.0-only; véase `LICENSE`. El cliente SimpleX conserva su licencia original, incluida en la imagen en `/usr/share/doc/simplex-chat/LICENSE`. Las dependencias conservan sus respectivas licencias.
No es una publicación oficial de SimpleX ni de Umbrel. Los hashes y la versión están fijados en el Dockerfile; cambiar la versión requiere revisar también esos valores y la compatibilidad de la API.
