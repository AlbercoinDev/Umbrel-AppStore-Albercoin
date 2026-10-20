# SimpleX Chat CLI · Albercoin · v1.0.1

Aplicación independiente para la tienda Umbrel Albercoin. Cliente oficial SimpleX Chat CLI **v7.0.3** y gestor web propio. Hermes Agent y otros clientes pueden conectarse opcionalmente mediante la API WebSocket.

**Estado de esta entrega:** imagen multiarquitectura publicada en GHCR y app instalada y arrancada correctamente en Umbrel. La mensajería y la integración con Hermes no se han validado de extremo a extremo.

## Instalación

Esta carpeta debe situarse en la raíz de `AlbercoinDev/Umbrel-AppStore-Albercoin`, junto a las demás apps. También debe añadirse el nuevo workflow `simplex-chat-cli-image.yml` a `.github/workflows/` sin reemplazar otros workflows.

La imagen `ghcr.io/albercoindev/simplex-chat-cli:1.0.1` debe construirse y hacerse pública antes de instalar. Utiliza el workflow manual **Build SimpleX Chat CLI** con `publish=true`. No se dispara por push. Las plataformas de construcción son `linux/amd64` y `linux/arm64`. El binario original se descarga de una versión fija y se comprueba contra su SHA256 oficial.

Después de publicar, puede fijarse la imagen por su digest real en este `docker-compose.yml`. La etiqueta por sí sola puede reemplazarse en una publicación futura: el workflow no impide republicar `1.0.1`.

Para construcción local alternativa, desde la raíz del repositorio y con Docker Buildx:

```sh
docker buildx build --platform linux/amd64 --load -t ghcr.io/albercoindev/simplex-chat-cli:1.0.1 ./albercoin-store-simplex-chat-cli
```

Este comando construye para AMD64 sin publicar. Para construir localmente ARM64 cambia `linux/amd64` por `linux/arm64`. El despliegue de Umbrel descarga una imagen: no construye el Dockerfile de esta carpeta.

## Acceso y funciones

- Interfaz mediante el proxy de Umbrel, puerto público de la app `2191`.
- Usuario `admin`, contraseña aleatoria por instalación (`APP_PASSWORD`), mostrada por Umbrel gracias a `deterministicPassword: true` y conservada como hash scrypt en el primer arranque.
- La pantalla de acceso permite cambiar la contraseña indicando la actual. Tras cambiarla, la ficha de Umbrel sigue mostrando la original.
- Si falta esa variable, contraseña aleatoria en `data/manager/initial-password.txt`, permisos `0600`; también puede establecerse una propia desde la pantalla de acceso.
- Sesiones de 12 horas, cookies HttpOnly y SameSite, controles CSRF y límite de intentos de acceso.
- Perfil editable; dirección persistente con QR generado localmente; conexiones y solicitudes con aceptación manual.
- Chats de texto directos y de grupo. Lista de hasta 200 conversaciones recientes y 100 entradas por historial; la consola permite otras consultas.
- El estado técnico de entrega del cliente aparece junto al mensaje. Aceptación de una orden no significa recepción en el otro dispositivo.
- Copias consistentes mediante parada del cliente; descarga y restauración offline.
- Consola de órdenes SimpleX, con permisos administrativos completos, accesible solo tras autenticarse.
- Servidores y proxy modificables desde la interfaz. Al guardar se reinicia el cliente, sin reiniciar otras apps.

No hay adjuntos en la interfaz, llamadas, integración automática de Tor ni promesas de tráfico exclusivamente onion. La configuración XFTP queda disponible para el cliente y usos avanzados. No se aceptan archivos automáticamente.

## Procesos, red y permisos

`app_proxy` dirige las peticiones a `albercoin-store-simplex-chat-cli_server_1:2191`.
El servicio `server` contiene el gestor y el proceso SimpleX. El cliente escucha únicamente en `127.0.0.1:5225` de ese contenedor. Un único cliente interno distribuye las respuestas por correlación y los eventos a cada integración.

El conector utiliza `2192/ws`, HTTP Basic para WebSocket, desactivado inicialmente. No se publica el puerto `2192` en el host. En Umbrel, Hermes accede por el nombre Docker `albercoin-store-simplex-chat-cli_server_1` dentro de la red que Umbrel proporciona a las apps. La URL no es una dirección para la LAN. Si una instalación cambia los nombres o segmenta esa red, se debe adaptar el host de la URL a su DNS o red privada; no se añaden permisos globales ni `network_mode: host` para resolverlo.

Las credenciales del conector conceden acceso completo a la API SimpleX. La lista de contactos de Hermes limita quién puede interactuar con Hermes; no limita los permisos técnicos del conector ni crea otra identidad SimpleX. Hermes verá los eventos de la misma identidad que administra la web.

Para clientes fuera del nodo es necesario un proxy TLS autenticado o un túnel seguro. No publiques el WebSocket nativo ni envíes su tráfico o sus credenciales por Internet sin protección.
Revocar las credenciales cierra los clientes activos. El contador de conexiones no identifica inequívocamente al cliente como Hermes; cualquier cliente autorizado habla el mismo protocolo.
Las colas son limitadas; un cliente demasiado lento se desconecta para que no reciba una secuencia de eventos incompleta sin advertencia. Los eventos en tránsito no constituyen una cola durable; el historial persistido por SimpleX es la referencia al reconectar. No se reenvían automáticamente mensajes cuyo resultado sea incierto.

El contenedor inicia brevemente como root para preparar la propiedad del volumen y baja a UID/GID `1000:1000`. Sus capacidades se limitan a esa preparación y al cambio de usuario. No utiliza `privileged`, el socket Docker, red host ni volúmenes de otras apps. Su salida CLI no se registra porque puede contener mensajes o enlaces; la interfaz informa de conexión y código de salida del último proceso.

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
Valida el contenido de la copia, rechaza enlaces y rutas externas, comprueba espacio y preserva los directorios actuales en `data/before-restore-FECHA/`. Solo admite copias de este paquete `1.0.0` con SimpleX `v7.0.3`.
Arranca después la app desde Umbrel. Se recuperan también la contraseña y el token anteriores. Si eran distintos, actualiza los clientes y usa la contraseña del momento de la copia.
Para deshacer la restauración, detén la app y recupera los directorios `simplex` y `manager` de `before-restore-FECHA`, conservando primero los restaurados en otra carpeta. No ejecutes dos instancias simultáneas con una misma identidad recuperada de una copia.

## Hermes

Consulta [integrations/hermes/README.md](integrations/hermes/README.md). La referencia inicial es el paquete oficial Umbrel `hermes-agent` con versión declarada `2026.9.24`. Su configuración persistente está en `/opt/data`. No se asume que las futuras versiones mantengan las mismas rutas o dependencias.

## Fuentes y licencia

- SimpleX: https://github.com/simplex-chat/simplex-chat/tree/v7.0.3
- Código fuente correspondiente al binario: https://github.com/simplex-chat/simplex-chat/archive/refs/tags/v7.0.3.tar.gz
- API: https://github.com/simplex-chat/simplex-chat/blob/v7.0.3/bots/api/COMMANDS.md
- Hermes: https://github.com/NousResearch/hermes-agent/tree/v2026.9.24/plugins/platforms/simplex
- Paquete de Umbrel: https://github.com/getumbrel/umbrel-apps/tree/master/hermes-agent

El código del gestor de este paquete se distribuye bajo AGPL-3.0-only; véase `LICENSE`. El cliente SimpleX conserva su licencia original, incluida en la imagen en `/usr/share/doc/simplex-chat/LICENSE`. Las dependencias conservan sus respectivas licencias.
No es una publicación oficial de SimpleX ni de Umbrel. Los hashes y la versión están fijados en el Dockerfile; cambiar la versión requiere revisar también esos valores y la compatibilidad de la API.
