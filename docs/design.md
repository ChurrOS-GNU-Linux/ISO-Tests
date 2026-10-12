# Diseño, estados y criterios de evidencia

## Límite de responsabilidad

ISO-Tests recibe una ISO ya construida; no modifica ChurrOS ni monta imágenes en el host. Bajo `--check-installer`, `xorriso` extrae solo el SquashFS live a un temporal y `unsquashfs` inspecciona los archivos de Calamares configurados, sin ejecutar código del sistema live. QEMU es el único componente que arranca el artefacto. La VM corre en TCG, sin interfaz gráfica visible, sin NIC, sin disco del anfitrión y sin disco de instalación por defecto. Se copian las variables UEFI a un archivo de evidencia temporal y se borran tras el test; el firmware CODE es solo lectura.

Las llamadas a herramientas usan vectores `argv` sin shell, entorno mínimo sin variables secretas, logs limitados, timeout y terminación del grupo de procesos. Un timeout sin marcador no es éxito. Los tests unitarios mockean procesos exclusivamente para comprobar el comportamiento del arnés; esos resultados no cuentan como arranque.

## Niveles

### A. ISO estática

1. Verifica archivo normal/no vacío y límites 64 MiB–16 GiB; en descargas compara bytes recibidos con `Content-Length` cuando el servidor lo informa.
2. Calcula SHA-256 en streaming.
3. Compara contra `--expected-sha256` o una entrada inequívoca de `--sha256sums`. Sin referencia: `published_checksum=SKIP`.
4. Valida `CD001`, tamaño de bloque y campos dual-endian del descriptor primario ISO9660 en el bloque 16; el volumen declarado no puede exceder el tamaño físico del artefacto.
5. `xorriso -report_el_torito plain` debe encontrar entradas boot El Torito para cada modo del perfil; `-report_system_area plain` conserva diagnóstico.
6. Valida el mecanismo UEFI declarado. Para `embedded_eltorito`, el catálogo debe reportar imagen UEFI; `efi_bootloader_file=SKIP` porque el ESP va dentro de esa imagen y QEMU debe verificar el arranque. Para `fallback_file`, busca el cargador EFI en el árbol ISO.
7. Extrae a temporal el archivo de configuración Syslinux/GRUB por modo y exige el parámetro `console=<serial_device>` antes de usar marcadores serie. Si falta, el arranque QEMU queda `BLOCKED` en vez de consumir el timeout sin una consola observable.
8. Opcionalmente `--check-installer` extrae el SquashFS y comprueba el binario/lanzador/configuración Calamares; no ejecuta nada del entorno Live.

El formato/catálogo no es por sí solo prueba de arranque; el nivel B debe aprobar para afirmar boot.

### B. Arranque QEMU

| Perfil | Firmware/máquina | Criterio PASS |
|---|---|---|
| x86_64 BIOS | QEMU `q35`, BIOS predeterminada de QEMU, TCG | Consola serie contiene mensaje systemd de target Graphical Interface o Multi-User System |
| x86_64 UEFI | QEMU `q35`, pareja OVMF CODE/VARS conocida (tamaños pueden diferir); copia desechable de VARS | Mismo marcador en serie |
| aarch64 UEFI | QEMU `virt`, CPU `cortex-a72`, par AAVMF/firmware compatible | Mismo marcador en PL011 serie `ttyAMA0` |

El perfil refleja solo los modos declarados por `profiledef.sh`; BIOS aarch64 se rechaza. Antes de QEMU se verifica que la configuración del modo dirija la consola del kernel al dispositivo serie esperado; si no, se registra `FAIL` en esa configuración y el arranque queda `BLOCKED`, sin insinuar que el sistema operativo falló. La VM se detiene cuando se ve un marcador, si sale antes o al alcanzar el límite de tiempo. La falta de emulador o firmware es `BLOCKED`; fallo de proceso o timeout sin marcador es `FAIL`. Si el perfil declara `login_prompt_patterns` y se ve un prompt de login sin el marcador exigido, el informe añade `qemu_login_prompt_* = PASS` como evidencia de progreso, pero mantiene `qemu_boot_* = FAIL`: ese prompt no sustituye el criterio systemd.

**Alcance de la evidencia:** el marcador confirma que el kernel arrancó y systemd alcanzó uno de esos targets; no confirma que el compositor o greeter funcione visualmente. La configuración GRUB auditada pasa `console=ttyS0`, pero la configuración BIOS de Syslinux no, por lo que BIOS queda `BLOCKED` antes de QEMU. En el run real [38182833982](https://github.com/ChurrOS-GNU-Linux/ISO-Tests/actions/runs/38182833982), QEMU UEFI sí ejecutó la ISO Niri 1.2 y llegó a `churros login:`; el log también registra `serial-getty@ttyS0.service`. No se observó el marcador systemd en 900 s, así que el criterio estricto sigue `FAIL`; es evidencia de progreso hasta un login serie, no de target systemd ni de escritorio gráfico. El arnés ahora puede reportar ese prompt como una señal separada, sin convertirla en éxito de arranque. Una prueba visual del escritorio requeriría captura/control de pantalla y aserciones visibles.

### C. Instalador

`--check-installer` verifica estáticamente en `airootfs.sfs` que exista `/usr/bin/calamares`, el wrapper `/usr/local/bin/calamares`, el `.desktop`, `settings.conf`, branding y el módulo `unpackfs.conf` con ruta de raíz acorde a la arquitectura. El resultado `PASS` significa solo que el payload/configuración esperada está presente; no se lanza Calamares ni se prueba la GUI. Una instalación completa sigue pendiente: requiere disco virtual desechable, GUI controlable, bootloader por arquitectura, instalación, revisión de resultados y segundo arranque; nunca se adjuntan discos físicos.

### D. Pruebas del arnés

`tests/test_iso_tests.py` crea fixtures sintéticos temporales (nombres arbitrarios con espacios, descriptor aislado no booteable), ejecutables falsos para xorriso/QEMU y firmware falso para comprobar robustez. Comprueba artefactos ausentes/vacíos/corruptos, falta de herramientas, perfiles/modos inválidos, entradas de catálogo, ruta EFI, checksum, timeout, errores de QEMU, informe incompleto/estados, manejo de nombres con espacios, terminación de procesos e interrupción/limpieza de descarga. **No usa ni representa una ISO real.**

## Informes

`report.json` tiene schema_version 1, run_id, fechas, artefacto (nombre/tamaño/hash), perfil/arquitectura/modos, versiones, pruebas individuales con estado/duración/mensaje/log y resumen de los cuatro estados. Se escribe incluso tras errores de configuración o interrupción. `evidence/` mantiene transcript xorriso y logs de QEMU/serie. No se anota un token en el informe; las descargas rechazan credenciales embebidas en URL y el informe conserva solo esquema, host y ruta, quitando query y fragmento.

## Integración

La comprobación PR corre los tests del harness. El smoke test manual exige URL HTTPS y perfil; si existe checksum publicado se pasa como SHA-256. Sin referencia, solo se descarga con una opción explícita y el reporte mantiene `published_checksum=SKIP`. El runner necesita conectividad para obtener la ISO, pero la VM no tiene red. Los artifacts upstream de Actions son de vida limitada y cross-repo; integración automática necesita un cambio coordinado aprobado en el repositorio builder o un evento/token con `actions:read` de mínimo alcance. Ningún cambio upstream se hizo.
