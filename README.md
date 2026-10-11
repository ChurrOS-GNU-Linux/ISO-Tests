# ISO-Tests para ChurrOS GNU/Linux

Herramientas de validación estática y pruebas de humo QEMU para ISOs reales de ChurrOS. No construye la distribución ni sustituye `./churros check`, la CI de Rust o los tests internos ya presentes en ChurrOS.

> **Estado de esta entrega:** el repositorio público existe y el PR inicial del arnés fue fusionado. El CI unitario pasó; ninguna arquitectura o edición se considera validada hasta que una ISO real supere las comprobaciones de estructura y arranque. Véase [`docs/audit.md`](docs/audit.md).

## Qué comprueba

- Archivo normal, presente, no vacío y de 64 MiB a 16 GiB; en descargas valida `Content-Length` y que el tamaño declarado por ISO9660 quepa en el archivo; SHA-256 calculado.
- Descriptor primario ISO9660 (`CD001`, bloque lógico 16) con tamaños dual-endian coherentes; `xorriso` examina el catálogo El Torito, el área de sistema y el árbol de archivos.
- Modos de arranque respaldados por el perfil: BIOS/Syslinux y UEFI/GRUB en x86_64; UEFI/GRUB en aarch64. Verifica el mecanismo UEFI declarado (incluido El Torito embebido) y requiere que la configuración de cada modo pase `console=<serial_device>` antes de confiar en marcadores por serie.
- Si se aporta un checksum de referencia confiable, compara el hash; si no, informa `SKIP` (no convierte el hash calculado en verificación de autenticidad).
- QEMU sin NIC, sin discos persistentes, en TCG; prueba cada modo declarado y solo aprueba al observar el marcador systemd configurado en la consola serie. El timeout sin marcador es `FAIL`.
- Con `--check-installer`, extrae el SquashFS y verifica el binario, lanzador y configuración de Calamares; esto no ejecuta la GUI ni realiza una instalación.
- Informe JSON, salida de herramientas y logs serie/QEMU con estados `PASS`, `FAIL`, `SKIP` y `BLOCKED`.

El marcador actual demuestra que systemd alcanzó un target de arranque en vivo. **No prueba visualmente el escritorio**, la conexión de red, el funcionamiento de todas las aplicaciones ni una instalación completa. Véase [`docs/design.md`](docs/design.md).

## Requisitos

- Python 3.10+; el código no requiere paquetes Python externos.
- Para validación ISO: `xorriso`.
- Para comprobar el contenido Calamares: `squashfs-tools` (`unsquashfs`) además de `xorriso`.
- Para QEMU x86_64: `qemu-system-x86_64`; para aarch64: `qemu-system-aarch64` y firmware AAVMF/UEFI compatible.
- Para UEFI x86_64: firmware OVMF compatible. El script detecta rutas usuales o acepta rutas explícitas.
- Espacio temporal para logs y variables UEFI; la ISO permanece de solo lectura y no se monta en el host.

Ejemplo en Arch Linux: `sudo pacman -S --needed xorriso qemu-full edk2-ovmf edk2-aarch64` (verifica los nombres de paquete disponibles en tu repositorio). En Ubuntu/Debian, instala `xorriso`, `qemu-system-x86`, `qemu-system-arm`, `ovmf` y el paquete de firmware AArch64 publicado por tu versión.

## Uso local

Usa una ISO producida por el build de ChurrOS (`out/*.iso`) o un archivo de publicación confiable. Los comandos de construcción oficiales auditados son `./churros build` y sus variantes; ISO-Tests no adivina ni reconstruye esos artefactos.

Prueba todos los modos de arranque implementados por el perfil:

```bash
python3 scripts/iso_tests.py '/ruta/con espacios/ChurrOS-....iso' \
  --profile x86_64-niri --report-dir artifacts/niri
```

Perfiles disponibles: `x86_64-niri`, `x86_64-xfce`, `x86_64-kde`, `x86_64-server` y `aarch64-niri`. La tabla se mantiene en [`profiles/churros.json`](profiles/churros.json).

Validación estática solamente: los arranques aparecerán como `SKIP`, nunca como aprobados.

```bash
python3 scripts/iso_tests.py ./out/imagen.iso --profile x86_64-niri \
  --no-boot --report-dir artifacts/static
```

Para añadir una comprobación estática del payload de Calamares dentro de SquashFS (no prueba su interfaz ni instala):

```bash
python3 scripts/iso_tests.py ./out/imagen.iso --profile x86_64-niri \
  --no-boot --check-installer --report-dir artifacts/installer-static
```

Compara contra un manifiesto publicado que contenga una línea con el **nombre exacto** del ISO:

```bash
python3 scripts/iso_tests.py ./out/imagen.iso --profile x86_64-niri \
  --sha256sums ./SHA256SUMS --report-dir artifacts/release
```

Para descargar por HTTPS y verificar con el hash confiable publicado por un canal independiente:

```bash
python3 scripts/iso_tests.py --iso-url 'https://servidor/imagen.iso' \
  --expected-sha256 '<64 caracteres hexadecimales>' \
  --profile aarch64-niri --report-dir artifacts/arm64
```

Si no existe un checksum confiable publicado, puedes optar **explícitamente** por probar el archivo servido por HTTPS sin atribuirle autenticidad criptográfica:

```bash
python3 scripts/iso_tests.py --iso-url 'https://servidor/imagen.iso' --allow-unverified-download \\
  --profile x86_64-niri --report-dir artifacts/unverified
```

En este modo se calcula y registra SHA-256 pero `published_checksum` queda `SKIP`; el hash calculado no autentica el archivo. La URL debe ser HTTPS, no puede incluir credenciales, las redirecciones deben permanecer en el mismo host HTTPS y la descarga falla si el cuerpo no coincide con `Content-Length`. También se valida que el tamaño del volumen ISO declarado quepa en el archivo. La descarga temporal se elimina al fallar. Límite: 16 GiB.

### Firmware UEFI no detectado

Pasa ambos archivos CODE/VARS de la misma pareja OVMF/AAVMF; sus tamaños pueden ser distintos. El arnés comprueba que existan y no estén vacíos, y copia VARS a un archivo temporal desechable:

```bash
python3 scripts/iso_tests.py ./out/imagen.iso --profile x86_64-niri \
  --uefi-code /ruta/OVMF_CODE.fd --uefi-vars /ruta/OVMF_VARS.fd
```

Nunca se modifica el archivo VARS original. El modo `BIOS` usa firmware BIOS de QEMU y no necesita OVMF.

## Resultados y códigos de salida

`artifacts/.../report.json` registra la ejecución, artefacto/hash, perfil, arquitectura, versiones de herramientas, pruebas, tiempos, estado, motivos de bloqueo y rutas de evidencia. Los logs están bajo `evidence/`.

- `0`: todas las pruebas ejecutadas pasaron o se omitieron explícitamente; revisa los `SKIP`.
- `1`: al menos un `FAIL` o `BLOCKED`.
- `2`: perfil/argumentos/configuración inválidos.
- `130`: interrupción; el arnés intenta finalizar el grupo de procesos QEMU y escribe el informe.

Un estado `BLOCKED` nunca equivale a éxito. Si falta `xorriso`, QEMU o firmware, el resultado correspondiente queda bloqueado y el proceso termina distinto de cero.

## GitHub Actions

- `unit-tests.yml`: tests del arnés en cada pull request; permiso `contents: read`.
- `iso-smoke.yml`: ejecución manual (`workflow_dispatch`) con URL HTTPS y perfil; acepta SHA-256 confiable o requiere marcar explícitamente la opción de descarga sin checksum. En este último caso el checksum queda `SKIP`; instala `xorriso`, `squashfs-tools`, QEMU y firmware, y ejecuta la inspección estática de Calamares.

La acción manual prueba el artefacto indicado por quien la dispara. Si hay un checksum publicado, debe proporcionarse; sin él, solo se puede proceder con la opción explícita de HTTPS no verificado y el informe mantiene el checksum como `SKIP`. No se configuró descarga automática desde otro repositorio: el build upstream tiene artifacts de Actions aarch64 de vida limitada; una integración futura requiere coordinar en `Hoyuse/ChurrOS` un evento confiable y permisos mínimos.

## Tests del arnés

Las pruebas unitarias usan archivos sintéticos pequeños y ejecutables falsos bajo directorios temporales para ejercitar errores, timeouts y parsing. **No son ISO de ChurrOS, no pasan por un arranque real ni cuentan como evidencia de arranque.**

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/iso_tests.py tests/test_iso_tests.py
```

## Limitaciones actuales

- No hay perfil gráfico visual automatizado; la salida serie comprueba solo un target systemd.
- No se ejecuta la interfaz de Calamares ni una instalación completa en disco virtual; `--check-installer` solo comprueba archivos y configuración, no que el instalador funcione o instale.
- No hay un checksum publicado verificado para la ISO activa en este entorno.
- En este entorno local no están instalados QEMU, `xorriso` ni firmware; la prueba real de la ISO oficial se ejecutó en GitHub Actions y su resultado está documentado en [`docs/results/2026-10-11-niri-1.2.md`](docs/results/2026-10-11-niri-1.2.md).
- El repositorio público y sus workflows están activos; la ejecución real inicial no aprobó todos los criterios y no se declara ChurrOS validado.

Detalles de auditoría y arquitectura: [`docs/audit.md`](docs/audit.md), [`docs/design.md`](docs/design.md).
