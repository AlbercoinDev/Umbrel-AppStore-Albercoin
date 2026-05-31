# Disk Module Placeholder

Future SSD / HDD diagnostics should live here.

Planned scope:

- Detect disks from `/host/dev` and `/host/sys/block`
- Read SMART data with `smartctl`
- Report disk temperature, power-on hours, reallocated sectors and errors
- Detect SSD/NVMe life percentage when available
- Produce disk health reports without destructive writes

This module is intentionally not implemented in v0.1.0. CPU Check is the first priority.
