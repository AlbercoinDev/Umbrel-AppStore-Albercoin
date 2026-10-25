# Tienda Umbrel - Albercoin

Tienda de aplicaciones personalizada para [umbrelOS](https://umbrel.com).

## Aplicaciones

| App | Función | Documentación |
| --- | --- | --- |
| Frigate | Servidor Electrum para Bitcoin Silent Payments; requiere Bitcoin y Electrs | [Guía](albercoin-store-frigate/README.md) |
| Onion Rotator | Consulta y rotación de direcciones onion de Umbrel | [Guía](albercoin-store-onion-rotator/README.md) |
| Machine Doctor | Diagnóstico de CPU, sensores e informes | [Guía](albercoin-store-machine-doctor/README.md) |
| SimpleX Chat CLI | Mensajería con gestor web y conector opcional para agentes | [Guía](albercoin-store-simplex-chat-cli/README.md) |

Las versiones vigentes se consultan en el `umbrel-app.yml` de cada carpeta.
La presencia de una app en este repositorio no indica que esté instalada en un nodo.

## Organización y mantenimiento

- [Mapa del repositorio y despliegues](docs/architecture.md)
- [Desarrollo y pruebas](docs/development.md)
- [Publicación y versiones](docs/releasing.md)

Las carpetas de las apps permanecen en la raíz para conservar sus rutas de
distribución. Los datos de una instalación Umbrel no se guardan en este repositorio.

## Cómo añadir esta tienda a tu Umbrel

1. Ve a **Settings → App Store** en tu panel de Umbrel
2. En **Custom App Repositories**, añade:
   ```
   https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin
   ```
3. La tienda aparecerá automáticamente en tu App Store
