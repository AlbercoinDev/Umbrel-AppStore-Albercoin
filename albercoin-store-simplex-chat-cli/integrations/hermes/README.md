# Hermes Agent oficial de Umbrel

La app SimpleX es independiente. Esta integración opcional conecta el adaptador nativo de Hermes mediante su API WebSocket y no requiere Hermes en el manifiesto Umbrel.
Referencia de configuración: `hermes-agent` de la tienda oficial, versión `2026.9.24`. No se ha probado la conexión en un nodo real.

## Configuración desde SimpleX

1. Conecta tu móvil con la nueva app SimpleX y acepta su solicitud en **Perfil y contactos**.
2. Consulta el ID numérico del contacto en **Conversaciones**.
3. En **Integraciones**, activa el conector y guarda.
4. Introduce ese ID (o varios separados por comas) y descarga `simplex-hermes.json`.

La configuración exportada desactiva aceptación automática y grupos, exige una lista de contactos y selecciona su primer contacto como destino de notificaciones de Hermes.
El archivo incluye una URL privada con credenciales. No lo subas a GitHub. La dirección utiliza DNS interno Docker, no `127.0.0.1` ni `umbrel.local`.

## Aplicar en Hermes

### Qué dato corresponde a cada campo

| Variable/campo del adaptador | Dato de esta app |
| --- | --- |
| `SIMPLEX_WS_URL` | **Integraciones → URL privada de conexión**, completa, con usuario, token, host, puerto 2192 y `/ws`. |
| `SIMPLEX_ALLOWED_USERS` | ID numérico del contacto de tu móvil en **Conversaciones**; varios separados por comas, sin `@`. |
| `SIMPLEX_HOME_CHANNEL` | Uno de esos ID, destino de notificaciones; la exportación usa el primero. |
| `SIMPLEX_ALLOW_ALL_USERS` | `false`. |
| `SIMPLEX_AUTO_ACCEPT` | `false`; acepta primero tu contacto en el panel. |
| `SIMPLEX_GROUP_ALLOWED` | Vacío en esta configuración. |

La dirección pública/QR de **Perfil y contactos** sirve para conectar el móvil, no para configurar el WebSocket de Hermes. El JSON descargado es entrada del asistente descrito abajo: no se presupone que la interfaz de Hermes lo importe. Si tu versión muestra campos para estas variables, utiliza la correspondencia de la tabla; si no, aplica el asistente compatible con la versión de referencia.

Esto se realiza manualmente desde una terminal del nodo. Copia al nodo el archivo exportado y esta carpeta de integración. Sitúate en la raíz del repositorio o ajusta las rutas de los comandos.
El nombre de contenedor esperado por el paquete oficial es `hermes-agent_web_1`; si tu instalación usa otro, sustituye el nombre por el real.

El adaptador necesita `websockets` en el Python del gateway. Las imágenes que ya lo incluyan no necesitan instalar nada. Si el asistente informa de su ausencia, instala la dependencia en ese entorno:

```sh
docker exec --user 1000:1000 hermes-agent_web_1 \
  uv pip install --python /opt/hermes/.venv/bin/python 'websockets==15.0.1'
```

Esta instalación modifica el entorno del contenedor, por lo que puede perderse al actualizar Hermes; la configuración de `/opt/data` sí es persistente. Si una futura versión de Hermes fija otra versión de `websockets`, utiliza sus requisitos en lugar de imponer esta versión. No instales en el Python del sistema ni en `/app/venv`, que no es la ruta de este paquete.

Aplica la configuración con el asistente incluido:

```sh
docker cp ./albercoin-store-simplex-chat-cli/integrations/hermes/configure.py \
  hermes-agent_web_1:/tmp/configure-simplex.py

docker exec --user 1000:1000 -i hermes-agent_web_1 \
  /opt/hermes/.venv/bin/python /tmp/configure-simplex.py < ./simplex-hermes.json
```

El asistente valida el archivo, conserva `.env` y `config.yaml` en `/opt/data/simplex-config-backups/FECHA/`, habilita `simplex-platform` con el comando propio de Hermes y actualiza únicamente las variables `SIMPLEX_*` exportadas dentro de `.env`. No imprime las credenciales ni modifica el código del adaptador. No configura proveedores, claves de modelos ni otros canales.
Ejecuta una sola configuración a la vez y evita guardar ajustes simultáneamente desde la interfaz de Hermes.
Reinicia Hermes desde su menú en Umbrel para que el gateway lea los cambios.
Hermes debe tener ya un proveedor de modelos configurado para responder: esa configuración queda fuera de SimpleX y no se incluye ninguna clave de API.

## Información operativa

En la sección **Integraciones** de SimpleX aparecen el número de clientes conectados, las órdenes recibidas y la hora de la última orden. Los valores parten de cero al reiniciar el gestor.
Esto permite observar el conector; no demuestra que Hermes haya generado o entregado una respuesta. La entrega se observa en la conversación SimpleX. **Registros y diagnóstico** permite filtrar las órdenes del conector y copiar el informe. Se ha probado el conector con un cliente WebSocket autenticado y el intercambio entre identidades SimpleX, no el gateway completo de Hermes.
La integración soportada es texto. No se comparten volúmenes entre las apps para imágenes, audios o adjuntos.
Hermes recibe eventos de la identidad administrada por la web; no se crea una identidad separada para él. La lista de contactos controla quién puede invocar al agente, pero un agente autorizado conserva las herramientas que ya tenga habilitadas en Hermes.

## Desconectar y revertir

Para cortar inmediatamente la conexión, desactiva el conector o revoca sus credenciales desde SimpleX. Otros clientes que usen el mismo token también se desconectarán. Tras una rotación, descarga y aplica otra vez la configuración.
Para retirar el canal de Hermes, elimina sus variables `SIMPLEX_*` mediante su configuración y deshabilita `simplex-platform`, sin tocar otros canales.
Si necesitas revertir la aplicación del asistente, detén Hermes desde Umbrel y recupera `.env` y `config.yaml` desde el subdirectorio de copia correspondiente en `app-data/hermes-agent/data/hermes/simplex-config-backups/`. `manifest.json` indica si cada archivo existía: si no existía, conserva aparte el nuevo en vez de restaurar un archivo inexistente. Arranca después Hermes.
Las copias contienen secretos y deben conservar sus permisos privados. No restaures una copia antigua completa si has cambiado otros ajustes después: en ese caso, revierte solo el canal SimpleX.
