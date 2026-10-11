# Auditoría de ChurrOS

**Fecha de consulta:** 2026-10-11. Auditoría remota de ramas públicas y endpoints de GitHub con el token conectado. La fuente activa inspeccionada es `Hoyuse/ChurrOS`, rama `main`, commit `f5c8097f25a361859c86a1ade425a3340c2dae0e` (SHA observado por GitHub API el 2026-10-11); repetir la auditoría antes de automatizar publicación.

## Repositorios y permisos observados

| Recurso | Evidencia observada | Consecuencia |
|---|---|---|
| Organización `ChurrOS-GNU-Linux` | Al inicio, los repositorios públicos accesibles eran `Brand-Book`, `Installer` y `Software`; una consulta previa devolvió 404 para ISO-Tests. | Se continuó comprobando permisos con el token guardado antes de crear nada. |
| `ChurrOS-GNU-Linux/ISO-Tests` | La creación autenticada con GitHub API tuvo éxito el 2026-10-11 y el repo quedó público. | URL: `https://github.com/ChurrOS-GNU-Linux/ISO-Tests`. |
| Permisos efectivos en `Installer`, `Software` y `Brand-Book` | GitHub API mostró `pull: true`, `push/admin: false`; la operación de creación del repositorio sí fue aceptada. | Puedo crear el nuevo repo, pero no modificar esos repos existentes ni habilitar por mi cuenta un evento en el builder upstream. |
| `Hoyuse/ChurrOS` | GitHub API mostró `push: true`, rama predeterminada `main`. | Se auditó; no se modificó. |

La cuenta conectada responde como `CRISTOP-bot`. La implementación inicial quedó en `feat/iso-test-framework` con PR #2 abierto hacia `main`. El PR #1 inicial fue fusionado por su mantenedor; el merge commit observado es `135a603758f993463342518ca54def6c9243646b`. Las mejoras de este informe se trabajan en la rama del PR #2; no se modifica ni se fusiona `main` desde esta tarea.

## Build y artefactos

La documentación de `Hoyuse/ChurrOS` describe una distribución Arch Linux basada en ArchISO y la CLI `./churros`.

- Construcción: `./churros build`, por defecto edición Niri; `./churros build --edition xfce|kde|server`; arquitectura seleccionable mediante `--arch arm64`/`x86_64`. El build prepara paquetes AUR/locales, Calamares, apps Rust y finalmente ejecuta `mkarchiso` sobre `archiso/`; la salida documentada es `out/ChurrOS-*.iso`.
- Build ARM: `./churros build --container --arch arm64`, usando `Containerfile.aarch64`; el workflow de ISO aarch64 corre en runner ARM y sube un artifact temporal. Build x86_64 no se ejecuta desde el CI común; las ISO de publicación descritas en `docs/release.md` se publican manualmente en `download.churroslinux.org` y/o GitHub Release.
- En la consulta de Actions del 2026-10-11 había dos artifacts `churros-aarch64-iso`, cada uno de 2,166,294,702 bytes, creados el 2026-10-09. Son artifacts de CI caducables, no una publicación estable.
- Ese mismo día se ejecutó una prueba real en GitHub Actions con la ISO oficial Niri x86_64 1.2 (véase [resultado detallado](results/2026-10-11-niri-1.2.md)). La inspección local de este sandbox no dispone de `xorriso`/QEMU, pero eso no impidió usar el runner de GitHub para esa ejecución.
- Releases observados: v0.6 tiene archivos 7z multipartes x86_64 y `SHA256SUMS`; el manifiesto es referencia válida solo tras obtener el ISO/partes y comprobar que la entrada corresponde al archivo probado. Otras releases tienen distintos paquetes. La documentación del proyecto dice que la publicación actual de ISO y `SHA256SUMS` es manual; no se encontró un workflow que suministre ISO x86_64 automáticamente como artifact.
- No se encontró un procedimiento probado para acceder desde un workflow nuevo a artifacts de otra organización/repo con `GITHUB_TOKEN` propio. Requiere acuerdo/permisos upstream o ingreso manual URL+hash; no se descarga silenciosamente “latest”.
- El índice HTTPS oficial `https://download.churroslinux.org/` muestra ChurrOS 2026.09.24 x86_64 Niri/XFCE 1.2 (aprox. 2 GB). El `sha256sums.txt` del índice está fechado 2026-07-23, así que no es un checksum publicado vigente para esas imágenes; no se debe afirmar integridad autenticada con ese fichero.

## Arquitecturas, ediciones y arranque

El árbol de `main` contiene `archiso/profiledef.sh`, workflows `ci.yml`, `iso-arm64.yml`, `rust.yml` y tests internos.

| Arquitectura/perfil | Ediciones cableadas en build | Modos declarados |
|---|---|---|
| x86_64 | Niri, XFCE, KDE, server | BIOS Syslinux + UEFI GRUB |
| aarch64 | Niri solamente | UEFI GRUB |

`build.sh` rechaza variantes aarch64 distintas de Niri y arquitecturas que no sean x86_64/aarch64. `profiledef.sh` declara `bios.syslinux` + `uefi.grub` en x86_64 y solo `uefi.grub` en aarch64. `archiso/grub/grub.cfg` pasa consola serie `ttyS0` en x86 y `ttyAMA0` en ARM; la configuración BIOS de Syslinux (`archiso/syslinux/archiso_sys-linux.cfg`) no pasa `console=ttyS0`. La ejecución real Niri x86_64 observó la carga de kernel e initramfs en BIOS, pero no el marcador systemd por serie: se registra timeout de observabilidad, no prueba de fallo del sistema operativo. El harness ahora extrae la configuración por modo y bloquea QEMU con estado explícito cuando falta el parámetro serie. Para ARM el kernel publicado es `Image`, repuesto con los nombres GRUB que espera el perfil; el port usa QEMU `virt`/`cortex-a72` según `docs/vm.md`.

Las cuatro ediciones x86 usan la sesión indicada por edición (Niri, XFCE, Plasma Wayland, server); server Live conserva XFCE mínimo para ejecutar el instalador gráfico. ARM actual es Niri. El instalador es Calamares 3.4 con configuración, branding y scripts propios; no es un instalador independiente.

## Automatización y pruebas existentes

- `./churros check` más `ci.yml`: chequeos estáticos del perfil y algunas pruebas de servicios. `rust.yml`: compila/tests/clippy de aplicaciones Rust en runners x86_64/ARM.
- `iso-arm64.yml`: compila ISO arm64 de forma manual y para cambios del perfil; sube un artifact. No es actualmente un check requerido de arranque.
- El repositorio contiene tests de integración unitarios para apps/servicios y scripts (polkit, redes, wallpaper, tour, KDE); no cubren la inspección ISO/arranque para todas las variantes.
- `docs/release.md` todavía prescribe compilar, probar con `./churros run` y publicar manualmente. `docs/installer.md` declara que la instalación de extremo a extremo no está probada en CI; `docs/vm.md` documenta QEMU, UEFI y un checklist aarch64 manual.
- La prueba real de `ChurrOS-2026.09.24-x86_64-niri-1.2.iso` terminó con 9 PASS, 2 FAIL, 1 SKIP y 1 BLOCKED. No se declara esa ISO ni ChurrOS validado: el cargador EFI visible no fue hallado (criterio luego corregido para el diseño embebido), BIOS no produjo el marcador serie y UEFI quedó bloqueado por la detección de firmware. El checksum confiable se omitió; el digest computado no está autenticado. Véase [el informe de resultados](results/2026-10-11-niri-1.2.md).
- El static payload checker para Calamares verifica archivos/configuración dentro del SquashFS, pero no abre su GUI ni completa instalación. La prueba real del 11 de octubre no estableció que Calamares funcione; una instalación de extremo a extremo continúa pendiente.

## Referencias inspeccionadas

- [Proyecto ChurrOS](https://github.com/Hoyuse/ChurrOS)
- [Instalador de la organización](https://github.com/ChurrOS-GNU-Linux/Installer)
- [Software de la organización](https://github.com/ChurrOS-GNU-Linux/Software)
- [Brand-Book de la organización](https://github.com/ChurrOS-GNU-Linux/Brand-Book)
- Documentación fuente de build, boot, VM, release e instalador bajo `docs/` y `archiso/` de `Hoyuse/ChurrOS`.

Los repositorios `Installer`, `Software` y `Brand-Book` son responsabilidades ya separadas; ISO-Tests no duplica sus archivos. El instalador GitHub contiene licencia y README; el build del sistema vive en el repo Hoyuse, no en el repo Installer reducido a licencia/README.
