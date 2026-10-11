# Política de seguridad

ISO-Tests recibe y arranca imágenes de sistema operativo en QEMU. No ejecutes una ISO desconocida fuera de una máquina virtual aislada ni habilites red, carpetas compartidas o passthrough de dispositivos.

Para reportar una vulnerabilidad en ChurrOS o en sus componentes, usa el canal privado indicado en la [política de seguridad del proyecto upstream](https://github.com/Hoyuse/ChurrOS/blob/main/SECURITY.md); no publiques detalles explotables en un issue abierto. Para un problema específico de este arnés, abre un reporte privado con los mantenedores del futuro repositorio ISO-Tests cuando exista. No adjuntes tokens, claves, imágenes de usuarios ni datos de instalación.

Al informar, incluye versión/commit, sistema anfitrión, perfil, estado y logs relevantes redactados. No hace falta adjuntar la ISO completa; aporta SHA-256 y una descripción reproducible.
