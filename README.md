# Tienda Umbrel - Albercoin

Tienda de aplicaciones personalizada para [umbrelOS](https://umbrel.com).

## Cómo añadir esta tienda a tu Umbrel

1. Ve a **Settings → App Store** en tu panel de Umbrel
2. En **Custom App Repositories**, añade:
   ```
   https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin
   ```
3. La tienda aparecerá automáticamente en tu App Store

## Apps disponibles

### Umbrel Tunnel
Expón cualquier app de tu Umbrel en internet (clearnet) a través de un túnel WireGuard hacia tu propio VPS.
- Túneles ilimitados
- HTTPS automático con Let's Encrypt
- No necesitas abrir puertos en tu router
- Host local automático: `umbrel.local`
- Descarga o copia tu configuración WireGuard

**Requiere:** Un VPS con Ubuntu/Debian donde instalar el servidor Umbrel Tunnel.

```bash
curl -sL https://github.com/AlbercoinDev/umbreltunnel/raw/main/install.sh | bash
```

[Ver repositorio VPS →](https://github.com/AlbercoinDev/umbreltunnel)

### FRP Client
Expón servicios TCP de tu Umbrel (Bitcoin P2P, Electrs, etc.) mediante túneles FRP ligeros hacia tu VPS.
- CRUD completo de proxies desde interfaz web
- Sincronización automática con el VPS vía HTTPS
- Encriptación forzada en cada túnel
- `network_mode: host` para servicios de red
- Configuración atómica — sin corrupción de datos

**Requiere:** VPS con FRP Server instalado (`vps/install.sh`).

[Ver repositorio →](https://github.com/AlbercoinDev/umbrel-frp)
