# Prueba de SimpleX Server 0.1.0 en Umbrel

Esta es una versión preliminar. Usa perfiles SimpleX desechables; no migres aún
conversaciones importantes ni publiques el relay como servicio para terceros.

## Instalación

1. Actualiza la tienda Albercoin en Umbrel y busca **SimpleX Server**.
2. Comprueba que TCP `5223` no esté reservado por otra app. No tienes que abrirlo
   en el router para probar Tor.
3. Instala y abre la app. Usuario `admin`; contraseña de la ficha de Umbrel.
4. Mantén **Solo Tor** y la protección de creación de colas activada. Pulsa
   **Aplicar configuración** una sola vez y espera a que termine.
5. Actualiza la página hasta ver SMP activo y bootstrap Tor completado. La
   publicación del servicio onion puede tardar unos minutos más.

La app incluye su propio Tor: no requiere instalar otra app Tor en Umbrel. Los
clientes sí necesitan conectividad Tor propia. El servidor no convierte
automáticamente en clientes Tor las otras apps del nodo.

## Desde el cliente

1. Crea un perfil de prueba y configura Tor según tu plataforma.
2. Abre los ajustes de servidores de mensajes/SMP y añade la dirección completa
   `smp://…` del panel. Incluye la contraseña, si existe.
3. Ejecuta la prueba de servidor del cliente.
4. En los dos perfiles de prueba, desactiva otros servidores de recepción y crea
   una conexión nueva entre ellos. No alteres los servidores de perfiles reales.
5. Envía texto en ambas direcciones. Al menos un dispositivo debe estar fuera de
   la LAN, por ejemplo con datos móviles.

Si el QR no se importa en tu versión de cliente, copia y pega la dirección.
No actives XFTP: esta entrega aloja mensajes SMP, no un servidor propio de archivos.

## Reinicios

- Anota localmente la huella SMP y la dirección onion; no compartas la URI con contraseña.
- Reinicia la app desde Umbrel: ambos valores deben seguir iguales.
- Repite después de reiniciar el nodo.
- Desconecta un cliente, envíale texto, reinicia limpiamente el servidor y vuelve
  a conectar el cliente: comprueba si recupera los mensajes pendientes.
- Una prueba con contraseña incorrecta debe fallar al crear colas.

No pruebes cortes eléctricos ni desinstales para reiniciar: desinstalar puede
borrar la identidad y todos los datos. La copia/restauración se describe en el
[README](../README.md#datos-y-recuperación).

## Clearnet (opcional)

Primero valida Tor. Para clearnet necesitas dominio o IPv4 accesible y TCP `5223`
permitido/redirigido hasta Umbrel. Con CGNAT puede no ser posible directamente.
Selecciona la modalidad, indica el host y sigue la ayuda desplegable del panel.
Prueba desde una red externa, no solo desde tu Wi-Fi. En modo mixto prueba
por separado el host público y el onion. Volver a Solo Tor debe cerrar la entrada
clearnet; las identidades deben conservarse.

## Qué comunicar si algo falla

- Modelo/arquitectura del equipo, versión de umbrelOS y versión de la app.
- Modalidad elegida y estados SMP/Tor mostrados en el panel.
- Plataforma y versión del cliente; texto del error sin direcciones ni secretos.
- Paso que falla y si ocurre antes o después de reiniciar.

No envíes contraseñas, enlaces de invitación, claves CA/Tor, copias de datos ni
capturas que muestren el QR o la dirección completa del servidor privado.
