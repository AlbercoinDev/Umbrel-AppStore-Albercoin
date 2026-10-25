# Detección de red y comprobaciones

Las URI SMP/XFTP personalizadas se validan antes de guardar. Cada servidor
puede anunciar varios nombres separados por comas. Si anuncia onion v3,
se selecciona esa dirección y se elimina la alternativa pública de los
argumentos del cliente: no hay fallback silencioso a clearnet. Los demás
servidores usan conexión directa. Los valores predeterminados del CLI no
se enumeran ni se comprueban en este panel.

Tor se incluye en la imagen, escucha SOCKS únicamente en 127.0.0.1:9050 y
usa control Unix autenticado mediante cookie. El supervisor recupera su
proceso si termina; el cliente mantiene sus mecanismos de reconexión.
No se configura el Tor de Umbrel ni el de SimpleX Server. Un proxy manual
sustituye el automático para todo el tráfico y se muestra como SOCKS5:
su mera configuración no demuestra que sea Tor.

Al guardar y al arrancar se comprueban los servidores personalizados. El
botón permite repetir las pruebas con 60 segundos entre inicios y una
única ejecución concurrente. El panel consulta el resultado cada 4 segundos
sin sobrescribir cambios del formulario. Los resultados son de la última
prueba, no una garantía permanente de conectividad.

Cada prueba utiliza una base temporal separada de las conversaciones y el
comando oficial `/smp test` o `/xftp test`, con routing SMP privado desactivado
para comprobar el destino indicado. El CLI valida transporte, identidad y
operaciones del protocolo. Tor puede tardar 180 segundos en disponer de
circuitos; la prueba de protocolo tiene un máximo de 120 segundos por
servidor. No se reintentan automáticamente operaciones que crean recursos.
Una interrupción puede dejar una cola o archivo temporal remoto sin borrar.

Los logs rotativos existentes incorporan componente `network`, fases
`configuration_saved`, `tor_start`, `tor_ready`, `tor_start_failed`,
`probe_start`, `probe_result`, `probe_failed`, `probe_cancelled` y
`probe_finished`. Incluyen nivel, ID del servidor, ID de comprobación, ruta,
protocolo, fase fallida, código, etapa del CLI y duración según proceda.
No se guarda la URI, contraseña ni salida cruda del CLI. El filtro
«Red / Tor / servidores» permite localizarlos en Diagnóstico.

## Despliegue

Estos cambios requieren construir y publicar una imagen nueva y actualizar
el digest del manifiesto antes de distribuirlos por Umbrel. El manifiesto
publicado todavía apunta a la imagen anterior; editar el código no actualiza
una instalación existente.
