# SimpleX Chat CLI · Albercoin · v1.0.4

**Actualización 1.0.4 publicada para AMD64 y ARM64 y fijada por digest en la tienda.**
Las copias locales con más de 10 días se eliminan automáticamente al actualizar;
descarga antes las que quieras conservar fuera del nodo.
Consulta [cambios, retención de copias y restauración cifrada](docs/release-1.0.4.md).

[Catálogo](../README.md) · [Mapa del repositorio](../docs/architecture.md) · [Desarrollo](../docs/development.md) · [Publicación](../docs/releasing.md)

Aplicación independiente para la tienda Umbrel Albercoin. Cliente oficial SimpleX Chat CLI **v7.0.3** y gestor web propio. Hermes Agent y otros clientes pueden conectarse opcionalmente mediante la API WebSocket.

**Estado documentado de la versión 1.0.3:** publicada para AMD64 y ARM64 y fijada por digest en la tienda. La documentación de la entrega registra intercambio de texto en ambos sentidos entre dos identidades reales SimpleX 7.0.3 en contenedores de prueba, aceptación de solicitudes, conector autenticado y exclusión de mensajes/credenciales del informe. No registra una ejecución del gateway de Hermes ni un despliegue de esta revisión en tu nodo. Estas comprobaciones no se han repetido durante la revisión documental.

## Índice

- [Instalación](#instalación)
- [Acceso y funciones](#acceso-y-funciones)
- [Procesos, red y permisos](#procesos-red-y-permisos)
- [Persistencia](#persistencia)
- [Restaurar una copia](#restaurar-una-copia)
- [Hermes](#hermes)
- [Funcionamiento y diagnóstico](#funcionamiento-y-diagnóstico)
- [Problemas frecuentes](#problemas-frecuentes)
- [Mapa del código](#mapa-del-código)
- [Fuentes y licencia](#fuentes-y-licencia)

## Instalación

Añade la tienda siguiendo el [README principal](../README.md) e instala **SimpleX Chat CLI** desde Umbrel. Esta carpeta y su workflow ya forman parte del repositorio; no hay que copiarlos ni incorporarlos de nuevo.

Umbrel descarga la imagen pública fijada en [`docker-compose.yml`](docker-compose.yml);
no construye el Dockerfile al instalar. No requiere otras apps Umbrel.
Las instrucciones para mantenedores están en la
[guía de construcción](docs/development.md) y en [publicación](../docs/releasing.md).

## Acceso y funciones

- Interfaz mediante el proxy de Umbrel, puerto público de la app `2191`.
- Usuario `admin`, contraseña aleatoria por instalación (`APP_PASSWORD`), mostrada por Umbrel gracias a `deterministicPassword: true` y conservada como hash scrypt en el primer arranque.
- La pantalla de acceso permite cambiar la contraseña indicando la actual. Tras cambiarla, la ficha de Umbrel sigue mostrando la original.
- Recuperación: si olvidas la contraseña, detén la app, elimina `data/manager/admin-auth.json` y vuelve a iniciarla. Se generará de nuevo desde el `APP_PASSWORD` que muestra Umbrel.
- Si falta esa variable, contraseña aleatoria en `data/manager/initial-password.txt`, permisos `0600`; también puede establecerse una propia desde la pantalla de acceso.
- Sesiones de 12 horas, cookies HttpOnly y SameSite, controles CSRF y límite de intentos de acceso.
- Perfil editable; dirección persistente con QR generado localmente; conexiones y solicitudes con aceptación manual.
- Chats de texto directos y de grupo. Páginas de 200 conversaciones y 100 entradas,
  con carga de anteriores, búsqueda de mensajes, filtros y conversaciones fijadas.
- Borradores independientes en memoria, copiar, citar y editar mensajes compatibles.
  Vaciado local, eliminación de contactos y mensajes y salida de grupos con confirmación.
- Vista previa opcional desactivada inicialmente; avisos SSE sin contenido y notificaciones
  privadas opcionales del navegador, no push con el panel cerrado.
- El estado técnico de entrega del cliente aparece junto al mensaje. Aceptación de una orden no significa recepción en el otro dispositivo.
- Copias consistentes mediante parada del cliente; descarga, borrado individual,
  programación opcional, cifrado age opcional y restauración offline.
  **Retención máxima obligatoria de 10 días**, también para copias existentes.
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
└── backups/              # tar.gz o tar.gz.age; caducan a los 10 días
```

Recrear el contenedor no elimina estos datos. Desinstalar la app en Umbrel puede eliminarlos. Las bases de datos no reciben cifrado adicional por parte del gestor. Las copias solo se cifran si configuras una clave pública age; las antiguas no se recifran. Protege el disco y las claves privadas. La confidencialidad del protocolo SimpleX no equivale al cifrado de estos archivos en reposo.

La limpieza de copias se realiza al arrancar, cada minuto y al listar o descargar;
se basa en la fecha UTC del nombre generado por la app. Si la app está detenida,
se limpiarán al siguiente arranque. Descarga fuera del nodo las copias que quieras
conservar más de diez días. Vaciar un chat no borra las copias anteriores.

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
Valida el contenido de la copia, rechaza enlaces y rutas externas, comprueba espacio y preserva los directorios actuales en `data/before-restore-FECHA/`. Admite copias de este paquete `1.0.0` a `1.0.4` con SimpleX `v7.0.3`. Para `.tar.gz.age` requiere `age` instalado y `--identity /ruta/clave-privada`; consulta la guía enlazada al principio.
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

Pruebas de regresión desde `albercoin-store-simplex-chat-cli/`, con las dependencias de `requirements.txt` instaladas en un entorno virtual:

```sh
python3 -m unittest discover -s tests -v
```

Cubren correlación de respuestas fuera de orden, limpieza de timeouts, redacción de errores, extracción de estados sin contenido, rotación/persistencia y enlaces cortos HTTPS de servidores SimpleX. Además se ha comprobado la interfaz en un DOM de prueba y el recorrido real entre dos identidades con SMP. La prueba del conector confirma autenticación, correlación y registros, pero no sustituye una prueba con el gateway y el modelo de Hermes.

## Problemas frecuentes

| Situación | Qué comprobar |
| --- | --- |
| Cliente local desconectado | Estado del proceso y códigos en **Registros y diagnóstico**; no borrar bases de datos como primera medida |
| Mensaje pendiente o resultado incierto | Estado de entrega, conexión con SMP y disponibilidad del destinatario; no repetir automáticamente el envío |
| Llegan mensajes pero no aparecen en el panel | Historial y eventos del informe para distinguir recepción interna de actualización de interfaz |
| Contraseña distinta a la ficha Umbrel | Si se cambió desde el panel, la ficha sigue mostrando la original; consulta la recuperación en **Acceso y funciones** |
| Conector activo pero Hermes no responde | Un cliente conectado no demuestra que Hermes esté configurado ni que su gateway/modelo funcione; revisar la guía de integración |
| Una copia no se restaura | Compatibilidad de versiones, espacio y app detenida; conservar el original y no modificar bases manualmente |

Al compartir una incidencia utiliza el informe técnico, no bases de datos,
copias ni archivos de configuración con credenciales.

## Mapa del código

| Ruta | Responsabilidad |
| --- | --- |
| `src/main.py` | Gestor web, autenticación, API y conector |
| `src/runtime.py` | Ciclo de vida del cliente SimpleX |
| `src/backups.py` | Inventario, espacio y caducidad obligatoria de copias |
| `src/simplex_client.py` | Comunicación y correlación de respuestas |
| `src/config.py` | Configuración persistente |
| `src/diagnostics.py` | Registros técnicos y redacción de información sensible |
| `src/static/` | Interfaz web |
| `tests/` | Pruebas de regresión |
| `scripts/restore.py` | Restauración offline |
| `integrations/hermes/` | Documentación y asistente de integración opcional |

## Fuentes y licencia

- SimpleX: https://github.com/simplex-chat/simplex-chat/tree/v7.0.3
- Código fuente correspondiente al binario: https://github.com/simplex-chat/simplex-chat/archive/refs/tags/v7.0.3.tar.gz
- API: https://github.com/simplex-chat/simplex-chat/blob/v7.0.3/bots/api/COMMANDS.md
- Hermes: https://github.com/NousResearch/hermes-agent/tree/v2026.9.24/plugins/platforms/simplex
- Paquete de Umbrel: https://github.com/getumbrel/umbrel-apps/tree/master/hermes-agent

El código del gestor de este paquete se distribuye bajo AGPL-3.0-only; véase `LICENSE`. El cliente SimpleX conserva su licencia original, incluida en la imagen en `/usr/share/doc/simplex-chat/LICENSE`. Las dependencias conservan sus respectivas licencias.
No es una publicación oficial de SimpleX ni de Umbrel. Los hashes y la versión están fijados en el Dockerfile; cambiar la versión requiere revisar también esos valores y la compatibilidad de la API.
# Red automática y diagnóstico por servidor

Los servidores personalizados onion activan Tor local automáticamente. El panel
de ajustes muestra la red anunciada, ruta seleccionada y resultado de las pruebas
SMP/XFTP, con registros clasificados y sin credenciales. Consulta
[funcionamiento, límites y despliegue](docs/network.md).
