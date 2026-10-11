# Diseño, estados y criterios de evidencia

## Límite de responsabilidad

ISO-Tests recibe una ISO ya construida; no modifica ChurrOS ni monta imágenes en el host. Bajo `--check-installer`, `xorriso` extrae solo el SquashFS live a un temporal y `unsquashfs` inspecciona los archivos de Calamares configurados, sin ejecutar código del sistema live. QEMU es el único componente que arranca el artefacto. La VM corre en TCG, sin interfaz gráfica visible, sin NIC, sin disco del anfitrión y sin disco de instalación por defecto. Se copian las variables UEFI a un archivo de evidencia temporal y se borran tras el test; el firmware CODE es solo lectura.

Las llamadas a herramientas usan vectores `argv` sin shell, entorno mínimo sin variables secretas, logs limitados, timeout y terminación del grupo de procesos. Un timeout sin marcador no es éxito. Los tests unitarios mockean procesos exclusivamente para comprobar el comportamiento del arnés; esos resultados no cuentan como arranque.

## Niveles

### A. ISO estática

1. Verifica archivo normal/no vacío y límites 64 MiB–16 GiB.
2. Calcula SHA-256 en streaming.
3. Compara contra `--expected-sha256` o una entrada inequívoca de `--sha256sums`. Sin referencia: `published_checksum=SKIP`.
4. Exige `CD001` en el descriptor primario ISO9660 del bloque 16.
5. `xorriso -report_el_torito plain` debe encontrar entradas boot El Torito para cada modo del perfil; `-report_system_area plain` conserva diagnóstico.
6. En perfiles UEFI, busca el cargador fallback específico (BOOTX64.EFI o BOOTAA64.EFI) en el árbol ISO. Archivo con nombre conocido sin catálogo no implica arrancabilidad: también se exigen el reporte El Torito y posteriormente la evidencia QEMU.

El formato/catálogo no es por sí solo prueba de arranque; el nivel B debe aprobar para afirmar boot.

### B. Arranque QEMU

| Perfil | Firmware/máquina | Criterio PASS |
|---|---|---|
| x86_64 BIOS | QEMU `q35`, BIOS predeterminada de QEMU, TCG | Consola serie contiene mensaje systemd de target Graphical Interface o Multi-User System |
| x86_64 UEFI | QEMU `q35`, par OVMF CODE/VARS igualado; copia desechable de VARS | Mismo marcador en serie |
| aarch64 UEFI | QEMU `virt`, CPU `cortex-a72`, par AAVMF/firmware compatible | Mismo marcador en PL011 serie `ttyAMA0` |

El perfil refleja solo los modos declarados por `profiledef.sh`; BIOS aarch64 se rechaza. La VM se detiene cuando se ve un marcador, si sale antes o al alcanzar el límite de tiempo. La falta de emulador o firmware es `BLOCKED`; fallo de proceso, configuración de QEMU o timeout es `FAIL`.

**Alcance de la evidencia:** el marcador confirma que el kernel arrancó y systemd alcanzó uno de esos targets; no confirma que el compositor o greeter funcione visualmente. Esta evidencia mínima es verificable desde la configuración serie presente en GRUB upstream. Está pendiente comparar en QEMU real que las versiones actuales de systemd/QEMU impriman el patrón en el log; de no aparecer, se debe actualizar el mecanismo de señal con evidencia del sistema, no relajar a “QEMU siguió vivo”. Una prueba de escritorio necesitaría captura/control de pantalla con aserciones visibles y perfiles de edición.

### C. Payload estático del instalador

Con `--check-installer`, la prueba toma de `profiles/churros.json` la ruta del SquashFS live (`/churros/{architecture}/airootfs.sfs`), coherente con `install_dir="churros"` y `airootfs_image_type="squashfs"` en el perfil Archiso upstream. `xorriso` extrae ese archivo a un directorio temporal; no monta la ISO ni ejecuta el sistema live. `unsquashfs` comprueba que puede leer el payload `usr/bin/calamares`, extrae solo los archivos de texto declarados y valida sus marcadores esperados: lanzador live, entrada `.desktop`, ajustes y configuración de módulos.

`installer_live_payload=PASS` acredita únicamente la presencia legible de esos archivos y los marcadores configurados. No comprueba que el ejecutable sea compatible con las bibliotecas del sistema, que permisos/polkit permitan iniciarlo, que la GUI abra ni que una instalación pueda completarse. Las rutas y marcadores se basan en el árbol de build upstream inspeccionado; una compilación puede omitir Calamares si no está disponible durante el build. Si faltan herramientas el estado es `BLOCKED`; sin `--check-installer`, se registra `SKIP`.

### D. Lanzamiento de Calamares e instalación

No se lanza Calamares ni se instala ChurrOS. La validación completa sigue pendiente: necesita una VM visualmente controlable con un disco virtual desechable, y debe comprobar ruta de módulos, bootloader por arquitectura, instalación sin red, estado del sistema instalado y segundo arranque. La GUI de escritorio no se valida por serie. Abrir Calamares por sí solo tampoco equivaldría a una instalación satisfactoria; nunca se adjuntan discos físicos.

### E. Pruebas del arnés

`tests/test_iso_tests.py` crea fixtures sintéticos temporales (nombres arbitrarios con espacios, descriptor aislado no booteable), ejecutables falsos para xorriso/QEMU y firmware falso para comprobar robustez. Incluye fixtures de SquashFS simulados para verificar únicamente la lógica del chequeo estático del payload. Comprueba artefactos ausentes/vacíos/corruptos, falta de herramientas, perfiles/modos inválidos, entradas de catálogo, ruta EFI, checksum, timeout, errores de QEMU, informe incompleto/estados, manejo de nombres con espacios, terminación de procesos e interrupción/limpieza de descarga. **Los fixtures unitarios no usan ni representan la ISO oficial; la prueba estática de fixtures tampoco es evidencia de que un ISO real contenga Calamares.**

## Informes

`report.json` tiene schema_version 1, run_id, fechas, artefacto (nombre/tamaño/hash), perfil/arquitectura/modos, versiones, pruebas individuales con estado/duración/mensaje/log y resumen de los cuatro estados. Se escribe incluso tras errores de configuración o interrupción. `evidence/` mantiene transcript xorriso y logs de QEMU/serie. No se anota un token en el informe; las descargas rechazan credenciales embebidas en URL y el informe conserva solo esquema, host y ruta, quitando query y fragmento.

## Integración

La comprobación PR corre los tests del harness. El smoke test manual exige URL HTTPS y perfil; si existe checksum publicado se pasa como SHA-256. Sin referencia, solo se descarga con una opción explícita y el reporte mantiene `published_checksum=SKIP`. El runner necesita conectividad para obtener la ISO, pero la VM no tiene red. Los artifacts upstream de Actions son de vida limitada y cross-repo; integración automática necesita un cambio coordinado aprobado en el repositorio builder o un evento/token con `actions:read` de mínimo alcance. Ningún cambio upstream se hizo.
